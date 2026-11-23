"""Where a password-reset link goes.

Two backends:

* **Email**, when the deployment configures a relay (`IACTRANSLATE_SMTP_*`,
  shared with run notifications) and a sender (`IACTRANSLATE_RESET_EMAIL_FROM`).
  The message goes out on a background thread, so the forgot-password response
  takes the same time whether or not the account exists. A send that waits on
  the relay only for real accounts would be an enumeration oracle measured in
  milliseconds.
* **Log**, the default without a mailer. It records that a reset was requested,
  and the link itself only when `IACTRANSLATE_RESET_LINK_TO_LOG=1`: a reset
  token in a log is a credential in a log.

Email used to be deliberately absent here. Untested delivery code in the
account-recovery path fails on a locked-out user, in production, at the worst
moment. It is here now because the transport (`mail.py`) is the one run
notifications use, exercised end to end against an SMTP server in the tests,
and the reset message itself is tested the same way.

Whatever the backend, `deliver_reset_link` never raises into the request path.
A failure to send must not tell the caller whether the account existed.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from email.message import EmailMessage
from typing import Callable, Mapping, Optional

from .accounts import _RESET_TTL_SECONDS
from .mail import DeliveryError, MailConfigError, SmtpTransport, transport_from_env

logger = logging.getLogger("iactranslate.api.delivery")

# (email, reset_url) -> None
LinkDelivery = Callable[[str, str], None]


#: Opt in to writing the reset *link* — which carries the single-use token —
#: into the log. Off unless a deployment asks, because a reset token in a log
#: is a credential in a log: on a hosted deployment, anyone with log-viewer
#: access could take over any account that requested a reset. That is the
#: class of finding an enterprise security review fails a pilot on. The
#: single-operator convenience it buys is real, and is exactly one env var away.
ENV_LINK_TO_LOG = "IACTRANSLATE_RESET_LINK_TO_LOG"

#: The From address for reset email. Setting it (with the SMTP settings) is
#: what turns reset email on. A relay configured only for notifications does
#: not start mailing users.
ENV_RESET_FROM = "IACTRANSLATE_RESET_EMAIL_FROM"

_ATTEMPTS = 3
_QUEUE_SIZE = 100


def _link_to_log_allowed() -> bool:
    return (os.getenv(ENV_LINK_TO_LOG) or "").strip().lower() in {"1", "true", "yes"}


def _log_delivery(email: str, reset_url: str) -> None:
    """Default backend: record that a reset was requested.

    Logged at WARNING because on a real deployment this line means "a user
    asked for a reset and nothing emailed it to them" — that should be visible,
    not buried at INFO.

    The link itself is only logged when the deployment has opted in. Without
    that, the reset flow genuinely does not work until an email backend is
    configured — and that is the honest state of a deployment with no mailer,
    rather than one that appears to work by writing account-takeover tokens
    somewhere an operator might not realise is shared.
    """
    if _link_to_log_allowed():
        logger.warning(
            "password reset requested for %s — no email backend configured, "
            "deliver this link manually: %s",
            email,
            reset_url,
        )
        return
    logger.warning(
        "password reset requested for %s — no email backend is configured, so "
        "the link was NOT delivered. Set %s and the IACTRANSLATE_SMTP_* settings to "
        "email it, or set %s=1 on a single-operator deployment to log the link instead.",
        email,
        ENV_RESET_FROM,
        ENV_LINK_TO_LOG,
    )


def reset_message(email: str, reset_url: str, sender: str) -> EmailMessage:
    minutes = _RESET_TTL_SECONDS // 60
    msg = EmailMessage()
    msg["Subject"] = "Reset your IaCTranslate password"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        f"Someone asked to reset the password for the IaCTranslate account {email}.\n"
        "\n"
        f"To choose a new password, open this link within {minutes} minutes:\n"
        "\n"
        f"{reset_url}\n"
        "\n"
        "The link works once. Resetting signs out every session on the account.\n"
        "\n"
        "If you didn't ask for this, ignore this email. Your password stays the same.\n"
    )
    return msg


class EmailLinkDelivery:
    """Sends reset links by email, off the request thread.

    Never logs the link. A failure is logged with the address, so an operator
    can see that a user is waiting on mail that did not go.
    """

    def __init__(self, transport: SmtpTransport, sender: str,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.transport = transport
        self.sender = sender
        self._sleep = sleep
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=_QUEUE_SIZE)
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def __call__(self, email: str, reset_url: str) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="iactranslate-reset-mail",
                                                daemon=True)
                self._worker.start()
        try:
            self._queue.put_nowait((email, reset_url))
        except queue.Full:
            logger.error("password reset email queue full; not sent to %s", email)

    def flush(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        return True

    def send_now(self, email: str, reset_url: str) -> Optional[str]:
        """Deliver on this thread: None, or why it failed."""
        msg = reset_message(email, reset_url, self.sender)
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                self.transport.send(msg)
                logger.info("password reset email sent to %s", email)
                return None
            except DeliveryError as e:
                if not e.retryable or attempt == _ATTEMPTS:
                    logger.error("password reset email to %s failed: %s", email, e)
                    return str(e)
                self._sleep(2 ** attempt)
            except Exception as e:  # noqa: BLE001 — never kill the worker
                logger.error("password reset email to %s failed: %s", email, type(e).__name__)
                return type(e).__name__
        return "not attempted"  # pragma: no cover

    def _run(self) -> None:
        while True:
            email, url = self._queue.get()
            try:
                self.send_now(email, url)
            finally:
                self._queue.task_done()


def email_delivery_from_env(env: Mapping[str, str] = os.environ) -> Optional[EmailLinkDelivery]:
    """The email backend, or None when reset email is not configured.

    A sender without a relay raises at startup. A deployment that thinks it
    emails reset links and silently does not leaves every locked-out user
    stranded.
    """
    sender = (env.get(ENV_RESET_FROM) or "").strip()
    transport = transport_from_env(env)
    if not sender:
        return None
    if transport is None:
        raise MailConfigError(f"{ENV_RESET_FROM} is set without IACTRANSLATE_SMTP_HOST")
    logger.info("password reset email enabled via %s:%s", transport.host, transport.port)
    return EmailLinkDelivery(transport, sender)


#: What `set_link_delivery(None)` restores: email when configured, else the log.
_configured: LinkDelivery = email_delivery_from_env() or _log_delivery
_delivery: LinkDelivery = _configured


def set_link_delivery(fn: Optional[LinkDelivery]) -> None:
    """Install a sender (or restore the configured default with None)."""
    global _delivery
    _delivery = fn or _configured


def reset_url(token: str) -> str:
    """Build the link a user clicks.

    `IACTRANSLATE_APP_URL` is the public origin of the web app — the API's own
    origin is usually wrong here, since the reset form lives in the frontend.
    """
    base = os.getenv("IACTRANSLATE_APP_URL", "http://localhost:3000").rstrip("/")
    return f"{base}/reset-password?token={token}"


def deliver_reset_link(email: str, token: str) -> None:
    """Hand the link to the configured backend, swallowing any failure.

    A backend that throws must not turn into a 500, because the difference
    between "sent" and "errored" would tell the caller whether the account
    exists — the enumeration leak the endpoint is careful to avoid.
    """
    try:
        _delivery(email, reset_url(token))
    except Exception:  # noqa: BLE001 — delivery must never leak account existence
        logger.exception("password-reset delivery failed for %s", email)
