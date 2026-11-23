"""Notifications — tell a Slack channel, a Teams channel or a mailbox when a run ends.

A subscriber on the event bus, like the audit log and the metrics: the run code
publishes `run.completed` / `run.failed` and knows nothing about who listens.
Both the synchronous `/run` (what the console uses) and queued jobs go through
the same run function, so both notify.

**Deployment-level, configured by environment.** The operator names the
channels; users cannot point the server at a URL. That matters: a per-user
webhook field is a server-side request to an address the user chose (SSRF),
and it needs storage, a UI and verification this milestone does not build.

**What a message says.** By default: outcome, target, workload count, monthly
estimate, duration, the kind of failure, a link to the console. It does *not*
carry the project name or the error text unless `IACTRANSLATE_NOTIFY_DETAIL=1`.
On a hosted, multi-tenant deployment the operator's channel would otherwise
receive every tenant's project names, and an error message can quote the
inventory it failed on. A single-team deployment wants the detail and is one
env var away from it.

**Never in the request path.** Sends happen on one background thread fed by a
bounded queue. A slow or failing channel cannot slow a run, and a full queue
drops the notification with a warning rather than growing without bound.

Formats, from each vendor's documentation:

  * **Slack** — an incoming webhook: POST JSON with `text` (the fallback shown
    in notifications) and `blocks`; `&`, `<`, `>` escaped in mrkdwn.
  * **Teams** — a Workflows "When a Teams webhook request is received" URL (the
    replacement for retired Office 365 connectors): a `message` whose one
    attachment is an Adaptive Card (1.4).
  * **Email** — SMTP with STARTTLS on 587 or implicit TLS on 465. Credentials
    are never sent over a connection that is not encrypted.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .events import Event, EventBus, EventType
from .mail import (
    ENV_SMTP_HOST,
    LOCAL_HOSTS,
    DeliveryError,
    MailConfigError,
    SmtpTransport,
    transport_from_env,
)

logger = logging.getLogger("iactranslate.notify")

ENV_SLACK = "IACTRANSLATE_NOTIFY_SLACK_WEBHOOK"
ENV_TEAMS = "IACTRANSLATE_NOTIFY_TEAMS_WEBHOOK"
ENV_EMAIL_TO = "IACTRANSLATE_NOTIFY_EMAIL_TO"
ENV_EMAIL_FROM = "IACTRANSLATE_NOTIFY_EMAIL_FROM"
ENV_ON = "IACTRANSLATE_NOTIFY_ON"
ENV_DETAIL = "IACTRANSLATE_NOTIFY_DETAIL"

#: Which outcomes notify, by name in IACTRANSLATE_NOTIFY_ON.
OUTCOMES = {"completed": EventType.RUN_COMPLETED, "failed": EventType.RUN_FAILED}

_HTTP_TIMEOUT = 10
_QUEUE_SIZE = 200
_ATTEMPTS = 3
#: Error text included under IACTRANSLATE_NOTIFY_DETAIL is cut to this length.
_ERROR_CHARS = 500
_LOCAL_HOSTS = LOCAL_HOSTS


#: Kept under its own name for callers; the same error as a bad SMTP setting.
NotifyConfigError = MailConfigError


@dataclass
class Notification:
    title: str
    ok: bool
    facts: List[Tuple[str, str]] = field(default_factory=list)
    link: Optional[str] = None

    def plain_text(self) -> str:
        lines = [self.title, ""] + [f"{k}: {v}" for k, v in self.facts]
        if self.link:
            lines += ["", self.link]
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


def _check_webhook(url: str, env: str) -> str:
    parts = urllib.parse.urlsplit(url)
    local = parts.hostname in _LOCAL_HOSTS
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise NotifyConfigError(f"{env} must be an https:// URL")
    if not parts.hostname:
        raise NotifyConfigError(f"{env} has no host")
    return url


def _post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "iactranslate-notify"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310  # nosec B310 — https checked in _check_webhook
            resp.read()
    except urllib.error.HTTPError as e:
        retry_after = None
        try:
            retry_after = float(e.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            pass
        # 429 and 5xx are the receiver's momentary state; any other 4xx is
        # our request or a revoked URL, and repeating it changes nothing.
        raise DeliveryError(f"HTTP {e.code}", retryable=e.code == 429 or e.code >= 500,
                            retry_after=retry_after) from None
    except (urllib.error.URLError, OSError) as e:
        raise DeliveryError(f"unreachable: {getattr(e, 'reason', e)}", retryable=True) from None


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackChannel:
    name = "slack"

    def __init__(self, url: str) -> None:
        self.url = _check_webhook(url, ENV_SLACK)

    @staticmethod
    def payload(n: Notification) -> dict:
        icon = ":white_check_mark:" if n.ok else ":x:"
        body = "\n".join(f"*{_slack_escape(k)}:* {_slack_escape(v)}" for k, v in n.facts)
        blocks = [{"type": "section",
                   "text": {"type": "mrkdwn", "text": f"{icon} *{_slack_escape(n.title)}*\n{body}".rstrip()}}]
        if n.link:
            blocks.append({"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"<{n.link}|Open in IaCTranslate>"}]})
        return {"text": n.title, "blocks": blocks}

    def send(self, n: Notification) -> None:
        _post_json(self.url, self.payload(n))


class TeamsChannel:
    name = "teams"

    def __init__(self, url: str) -> None:
        self.url = _check_webhook(url, ENV_TEAMS)

    @staticmethod
    def payload(n: Notification) -> dict:
        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": n.title, "weight": "Bolder", "size": "Medium",
                 "wrap": True, "color": "Good" if n.ok else "Attention"},
                {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in n.facts]},
            ],
        }
        if n.link:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in IaCTranslate", "url": n.link}]
        return {"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}

    def send(self, n: Notification) -> None:
        _post_json(self.url, self.payload(n))


@dataclass
class SmtpSettings:
    host: str
    port: int
    sender: str
    recipients: List[str]
    user: Optional[str] = None
    password: Optional[str] = None
    #: "starttls" | "ssl" | "none". "none" is refused with credentials, and for
    #: any host but this machine.
    tls: str = "starttls"


class EmailChannel:
    name = "email"

    def __init__(self, settings: SmtpSettings) -> None:
        if not settings.recipients:
            raise NotifyConfigError(f"{ENV_EMAIL_TO} names no recipients")
        self.settings = settings
        # Validates TLS and credentials the same way reset email does.
        self.transport = SmtpTransport(host=settings.host, port=settings.port, user=settings.user,
                                       password=settings.password, tls=settings.tls)

    def message(self, n: Notification) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = f"[IaCTranslate] {n.title}"
        msg["From"] = self.settings.sender
        msg["To"] = ", ".join(self.settings.recipients)
        msg.set_content(n.plain_text())
        return msg

    def send(self, n: Notification) -> None:
        self.transport.send(self.message(n))


def channels_from_env(env: Mapping[str, str] = os.environ) -> List[object]:
    """Every channel the environment configures. Raises on a half-set one."""
    def get(key: str) -> str:
        return (env.get(key) or "").strip()

    channels: List[object] = []
    if get(ENV_SLACK):
        channels.append(SlackChannel(get(ENV_SLACK)))
    if get(ENV_TEAMS):
        channels.append(TeamsChannel(get(ENV_TEAMS)))
    # SMTP alone does not enable notification email: the relay may be there
    # only for password resets. Recipients are what turn this channel on.
    if get(ENV_EMAIL_TO):
        transport = transport_from_env(env)
        if transport is None or not get(ENV_EMAIL_FROM):
            raise NotifyConfigError(
                f"email notifications need {ENV_SMTP_HOST}, {ENV_EMAIL_FROM} and {ENV_EMAIL_TO}")
        channels.append(EmailChannel(SmtpSettings(
            host=transport.host, port=transport.port, sender=get(ENV_EMAIL_FROM),
            recipients=[r.strip() for r in get(ENV_EMAIL_TO).split(",") if r.strip()],
            user=transport.user, password=transport.password, tls=transport.tls,
        )))
    else:
        transport_from_env(env)  # still refuse a broken SMTP setting at startup
    return channels


def outcomes_from_env(env: Mapping[str, str] = os.environ) -> set:
    raw = (env.get(ENV_ON) or "completed,failed").strip().lower()
    names = {n.strip() for n in raw.split(",") if n.strip()}
    unknown = names - set(OUTCOMES)
    if unknown:
        raise NotifyConfigError(f"{ENV_ON}: unknown outcome(s) {', '.join(sorted(unknown))} "
                                f"(available: {', '.join(OUTCOMES)})")
    return {OUTCOMES[n] for n in names}


# --------------------------------------------------------------------------- #
# The subscriber
# --------------------------------------------------------------------------- #


#: Looks a project up by id; returns an object with `.name` and `.error`, or None.
ProjectLookup = Callable[[str], Optional[object]]


class Notifier:
    def __init__(
        self,
        channels: Sequence[object],
        outcomes: Optional[set] = None,
        include_detail: bool = False,
        app_url: Optional[str] = None,
        lookup: Optional[ProjectLookup] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.channels = list(channels)
        self.outcomes = outcomes if outcomes is not None else set(OUTCOMES.values())
        self.include_detail = include_detail
        self.app_url = (app_url or "").rstrip("/") or None
        self._lookup = lookup
        self._sleep = sleep
        self._queue: "queue.Queue[Notification]" = queue.Queue(maxsize=_QUEUE_SIZE)
        self._worker: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()

    # -- bus side -------------------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        if event.type not in self.outcomes or not self.channels:
            return
        self.enqueue(self.describe(event))

    def describe(self, event: Event) -> Notification:
        d = event.detail
        ok = event.type == EventType.RUN_COMPLETED
        project = self._lookup(event.project_id) if (self._lookup and event.project_id) else None
        label = (f"“{project.name}”" if (self.include_detail and project is not None)
                 else f"project {event.project_id}")
        facts: List[Tuple[str, str]] = [("Target", str(d.get("target", "?")))]
        if ok:
            title = f"Translation completed: {label}"
            facts.append(("Workloads", str(d.get("vm_count", "?"))))
            if d.get("priced") and d.get("monthly_cost_usd") is not None:
                facts.append(("Estimated monthly cost", f"${d['monthly_cost_usd']:,.2f}"))
        else:
            title = f"Translation failed: {label}"
            facts.append(("Failure", str(d.get("failure", "error"))))
            error = getattr(project, "error", None) if project is not None else None
            if self.include_detail and error:
                facts.append(("Error", error[:_ERROR_CHARS] + ("…" if len(error) > _ERROR_CHARS else "")))
        if d.get("duration_s") is not None:
            facts.append(("Duration", f"{d['duration_s']:.1f}s"))
        link = f"{self.app_url}/console" if self.app_url else None
        return Notification(title=title, ok=ok, facts=facts, link=link)

    # -- delivery ---------------------------------------------------------------

    def enqueue(self, n: Notification) -> None:
        self._ensure_worker()
        try:
            self._queue.put_nowait(n)
        except queue.Full:
            logger.warning("notification queue full; dropped: %s", n.title)

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until every queued notification has been attempted (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True

    def send_now(self, n: Notification) -> Dict[str, Optional[str]]:
        """Deliver to every channel on this thread: {channel: None | error}."""
        return {ch.name: self._deliver(ch, n) for ch in self.channels}

    def _deliver(self, channel, n: Notification) -> Optional[str]:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                channel.send(n)
                return None
            except DeliveryError as e:
                if not e.retryable or attempt == _ATTEMPTS:
                    logger.warning("notification to %s failed: %s", channel.name, e)
                    return str(e)
                self._sleep(min(e.retry_after or 2 ** attempt, 30))
            except Exception as e:  # noqa: BLE001 — a channel bug must not kill the worker
                logger.exception("notification to %s failed", channel.name)
                return type(e).__name__
        return "not attempted"  # pragma: no cover — loop always returns

    def _ensure_worker(self) -> None:
        with self._start_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="iactranslate-notify", daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            n = self._queue.get()
            try:
                self.send_now(n)
            finally:
                self._queue.task_done()


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def create_notifier(lookup: Optional[ProjectLookup] = None,
                    env: Mapping[str, str] = os.environ) -> Optional[Notifier]:
    """The deployment's notifier, or None when no channel is configured.

    A half-configured channel raises at startup. A deployment that thinks it
    alerts on failed runs, and silently does not, is worse than one that
    refuses to start and says which variable is wrong.
    """
    channels = channels_from_env(env)
    if not channels:
        return None
    notifier = Notifier(
        channels, outcomes=outcomes_from_env(env), include_detail=_truthy(env.get(ENV_DETAIL)),
        app_url=env.get("IACTRANSLATE_APP_URL"), lookup=lookup,
    )
    logger.info("notifications enabled: %s", ", ".join(c.name for c in channels))
    return notifier


def sample_notification(app_url: Optional[str] = None) -> Notification:
    """What `iactranslate notify-test` sends."""
    link = f"{app_url.rstrip('/')}/console" if app_url else None
    return Notification(title="IaCTranslate test notification", ok=True,
                        facts=[("Purpose", "Confirms this channel is configured correctly")], link=link)
