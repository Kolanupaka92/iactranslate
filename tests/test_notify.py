"""Run notifications to Slack, Teams and email.

The webhook and SMTP senders are exercised over real sockets: a local HTTP
server stands in for Slack/Teams, and a minimal SMTP server for a mail relay.
What is not exercised here is a real vendor endpoint and a TLS handshake.
"""
import json
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from iactranslate.api.events import Event, EventBus, EventType
from iactranslate.api.notify import (
    DeliveryError,
    EmailChannel,
    Notification,
    Notifier,
    NotifyConfigError,
    SlackChannel,
    SmtpSettings,
    TeamsChannel,
    channels_from_env,
    create_notifier,
    outcomes_from_env,
)
from iactranslate.cli import main as cli

# --- fakes ------------------------------------------------------------------------


class _Hook:
    """A local webhook receiver that answers with a scripted list of statuses."""

    def __init__(self, statuses=(200,), retry_after=None):
        self.bodies, self.statuses, self.retry_after = [], list(statuses), retry_after
        hook = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                hook.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status = hook.statuses.pop(0) if len(hook.statuses) > 1 else hook.statuses[0]
                self.send_response(status)
                if hook.retry_after is not None:
                    self.send_header("Retry-After", str(hook.retry_after))
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hook():
    h = _Hook()
    yield h
    h.close()


class _Smtp(socketserver.ThreadingTCPServer):
    """Just enough SMTP for smtplib.send_message: EHLO, MAIL, RCPT, DATA, QUIT."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.messages = []
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 localhost test\r\n")
                envelope = {"rcpt": []}
                while True:
                    line = self.rfile.readline().decode()
                    if not line:
                        return
                    verb = line[:4].upper()
                    if verb in ("EHLO", "HELO"):
                        self.wfile.write(b"250 localhost\r\n")
                    elif verb == "MAIL":
                        envelope["from"] = line
                        self.wfile.write(b"250 ok\r\n")
                    elif verb == "RCPT":
                        envelope["rcpt"].append(line)
                        self.wfile.write(b"250 ok\r\n")
                    elif verb == "DATA":
                        self.wfile.write(b"354 go\r\n")
                        data = []
                        while True:
                            chunk = self.rfile.readline().decode()
                            if chunk in (".\r\n", ""):
                                break
                            data.append(chunk)
                        server.messages.append({"data": "".join(data), **envelope})
                        self.wfile.write(b"250 queued\r\n")
                    elif verb == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"502 no\r\n")

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


def _note(ok=True):
    return Notification(title="Translation completed: project p1", ok=ok,
                        facts=[("Target", "aws"), ("Workloads", "3")], link="https://app.example/console")


# --- formats ----------------------------------------------------------------------


def test_slack_payload_has_fallback_text_and_escapes_mrkdwn():
    n = Notification(title="Done <b>&", ok=True, facts=[("Name", "a<b>&c")], link="https://x/console")
    p = SlackChannel.payload(n)
    assert p["text"] == "Done <b>&"  # plain fallback, shown in push notifications
    section = p["blocks"][0]["text"]["text"]
    assert "&lt;b&gt;&amp;" in section and "a&lt;b&gt;&amp;c" in section
    assert p["blocks"][1]["elements"][0]["text"] == "<https://x/console|Open in IaCTranslate>"


def test_teams_payload_is_an_adaptive_card_message():
    p = TeamsChannel.payload(_note(ok=False))
    assert p["type"] == "message"
    (attachment,) = p["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["body"][0]["color"] == "Attention"
    assert card["body"][1]["facts"] == [{"title": "Target", "value": "aws"}, {"title": "Workloads", "value": "3"}]
    assert card["actions"][0] == {"type": "Action.OpenUrl", "title": "Open in IaCTranslate",
                                  "url": "https://app.example/console"}


# --- delivery over sockets ---------------------------------------------------------


@pytest.mark.parametrize("channel_cls", [SlackChannel, TeamsChannel])
def test_webhook_is_delivered(hook, channel_cls):
    channel = channel_cls(hook.url)
    assert Notifier([channel]).send_now(_note()) == {channel.name: None}
    assert hook.bodies == [channel_cls.payload(_note())]


def test_transient_failures_are_retried_and_honour_retry_after():
    h = _Hook(statuses=(429, 503, 200), retry_after=7)
    slept = []
    try:
        result = Notifier([SlackChannel(h.url)], sleep=slept.append).send_now(_note())
    finally:
        h.close()
    assert result == {"slack": None}
    assert len(h.bodies) == 3 and slept == [7, 7]


def test_a_revoked_webhook_is_not_retried():
    h = _Hook(statuses=(404,))
    try:
        result = Notifier([SlackChannel(h.url)], sleep=lambda s: None).send_now(_note())
    finally:
        h.close()
    assert result == {"slack": "HTTP 404"} and len(h.bodies) == 1


def test_an_unreachable_channel_reports_instead_of_raising():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listens here
    result = Notifier([SlackChannel(f"http://127.0.0.1:{port}/x")], sleep=lambda s: None).send_now(_note())
    assert result["slack"].startswith("unreachable")


def test_email_is_delivered_over_smtp():
    server = _Smtp()
    try:
        channel = EmailChannel(SmtpSettings(host="127.0.0.1", port=server.server_address[1],
                                            sender="iac@example.com", recipients=["ops@example.com", "b@example.com"],
                                            tls="none"))
        assert Notifier([channel]).send_now(_note()) == {"email": None}
    finally:
        server.shutdown()
        server.server_close()
    (msg,) = server.messages
    assert "Subject: [IaCTranslate] Translation completed: project p1" in msg["data"]
    assert "Workloads: 3" in msg["data"] and len(msg["rcpt"]) == 2


def test_one_failing_channel_does_not_stop_the_others(hook):
    class Broken:
        name = "broken"

        def send(self, n):
            raise DeliveryError("nope")

    result = Notifier([Broken(), SlackChannel(hook.url)]).send_now(_note())
    assert result == {"broken": "nope", "slack": None} and len(hook.bodies) == 1


# --- never in the request path -----------------------------------------------------


def test_publishing_never_waits_on_a_slow_channel():
    release = threading.Event()

    class Slow:
        name = "slow"

        def __init__(self):
            self.sent = 0

        def send(self, n):
            release.wait(5)
            self.sent += 1

    slow = Slow()
    bus = EventBus()
    notifier = Notifier([slow])
    notifier.attach(bus)
    start = time.monotonic()
    bus.publish(Event(EventType.RUN_COMPLETED, project_id="p", detail={"target": "aws", "vm_count": 1}))
    assert time.monotonic() - start < 0.5
    release.set()
    assert notifier.flush(5) and slow.sent == 1


def test_only_configured_outcomes_notify(hook):
    bus = EventBus()
    notifier = Notifier([SlackChannel(hook.url)], outcomes={EventType.RUN_FAILED})
    notifier.attach(bus)
    bus.publish(Event(EventType.RUN_COMPLETED, project_id="p", detail={"target": "aws"}))
    bus.publish(Event(EventType.JOB_FAILED, project_id="p"))
    bus.publish(Event(EventType.RUN_FAILED, project_id="p", detail={"target": "aws", "failure": "invalid input"}))
    assert notifier.flush(5)
    assert [b["text"] for b in hook.bodies] == ["Translation failed: project p"]


# --- what a message says -----------------------------------------------------------


class _Project:
    name = "Acme payroll estate"
    error = "row 7: host db-payroll-01 has no CPU count " + "x" * 600


def _failed():
    return Event(EventType.RUN_FAILED, project_id="p9",
                 detail={"target": "gcp", "failure": "invalid input", "duration_s": 1.25})


def test_by_default_no_project_name_or_error_text_leaves_the_server():
    n = Notifier([], lookup=lambda pid: _Project()).describe(_failed())
    text = n.plain_text()
    assert "Acme" not in text and "db-payroll" not in text
    assert n.title == "Translation failed: project p9"
    assert ("Failure", "invalid input") in n.facts and ("Duration", "1.2s") in n.facts


def test_detail_opt_in_adds_name_and_a_truncated_error():
    n = Notifier([], include_detail=True, lookup=lambda pid: _Project()).describe(_failed())
    assert n.title == "Translation failed: “Acme payroll estate”"
    error = dict(n.facts)["Error"]
    assert error.startswith("row 7: host db-payroll-01") and len(error) == 501 and error.endswith("…")


def test_completed_message_shows_cost_only_when_priced():
    detail = {"target": "aws", "vm_count": 12, "monthly_cost_usd": 4321.5, "priced": True}
    n = Notifier([], app_url="https://app.example/").describe(
        Event(EventType.RUN_COMPLETED, project_id="p", detail=detail))
    assert ("Estimated monthly cost", "$4,321.50") in n.facts and n.ok
    assert n.link == "https://app.example/console"
    onprem = Notifier([]).describe(Event(EventType.RUN_COMPLETED, project_id="p",
                                         detail={**detail, "target": "proxmox", "priced": False}))
    assert "Estimated monthly cost" not in dict(onprem.facts) and onprem.link is None


# --- configuration ------------------------------------------------------------------


def test_nothing_configured_means_no_notifier():
    assert create_notifier(env={}) is None


def test_full_configuration_builds_every_channel():
    env = {"IACTRANSLATE_NOTIFY_SLACK_WEBHOOK": "https://hooks.slack.com/services/T/B/x",
           "IACTRANSLATE_NOTIFY_TEAMS_WEBHOOK": "https://prod.westus.logic.azure.com/workflows/x",
           "IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_SMTP_USER": "u",
           "IACTRANSLATE_SMTP_PASSWORD": "p", "IACTRANSLATE_NOTIFY_EMAIL_FROM": "iac@example.com",
           "IACTRANSLATE_NOTIFY_EMAIL_TO": "a@example.com, b@example.com",
           "IACTRANSLATE_NOTIFY_ON": "failed", "IACTRANSLATE_NOTIFY_DETAIL": "1"}
    n = create_notifier(env=env)
    assert [c.name for c in n.channels] == ["slack", "teams", "email"]
    email = n.channels[2].settings
    assert (email.port, email.tls, email.recipients) == (587, "starttls", ["a@example.com", "b@example.com"])
    assert n.outcomes == {EventType.RUN_FAILED} and n.include_detail


def test_implicit_tls_defaults_to_port_465():
    env = {"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_SMTP_TLS": "ssl",
           "IACTRANSLATE_NOTIFY_EMAIL_FROM": "a@example.com", "IACTRANSLATE_NOTIFY_EMAIL_TO": "b@example.com"}
    assert channels_from_env(env)[0].settings.port == 465


@pytest.mark.parametrize("env,match", [
    ({"IACTRANSLATE_NOTIFY_SLACK_WEBHOOK": "http://hooks.slack.com/x"}, "https"),
    ({"IACTRANSLATE_NOTIFY_TEAMS_WEBHOOK": "ftp://x"}, "https"),
    ({"IACTRANSLATE_NOTIFY_EMAIL_TO": "a@example.com"}, "need"),
    ({"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_NOTIFY_EMAIL_FROM": "a@x",
      "IACTRANSLATE_NOTIFY_EMAIL_TO": "b@x", "IACTRANSLATE_SMTP_TLS": "none"}, "clear text"),
    ({"IACTRANSLATE_SMTP_HOST": "127.0.0.1", "IACTRANSLATE_NOTIFY_EMAIL_FROM": "a@x",
      "IACTRANSLATE_NOTIFY_EMAIL_TO": "b@x", "IACTRANSLATE_SMTP_TLS": "none",
      "IACTRANSLATE_SMTP_USER": "u", "IACTRANSLATE_SMTP_PASSWORD": "p"}, "clear text"),
    ({"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_NOTIFY_EMAIL_FROM": "a@x",
      "IACTRANSLATE_NOTIFY_EMAIL_TO": "b@x", "IACTRANSLATE_SMTP_USER": "u"}, "without"),
    ({"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_NOTIFY_EMAIL_FROM": "a@x",
      "IACTRANSLATE_NOTIFY_EMAIL_TO": "b@x", "IACTRANSLATE_SMTP_PORT": "abc"}, "number"),
])
def test_a_half_configured_channel_refuses_to_start(env, match):
    with pytest.raises(NotifyConfigError, match=match):
        create_notifier(env=env)


def test_unknown_outcome_is_refused():
    with pytest.raises(NotifyConfigError, match="unknown outcome"):
        outcomes_from_env({"IACTRANSLATE_NOTIFY_ON": "failed,started"})


# --- the API publishes run outcomes ---------------------------------------------------


@pytest.fixture
def run_events():
    from iactranslate.api import main
    seen = []
    main.bus.subscribe(lambda e: seen.append(e) if e.type in (EventType.RUN_COMPLETED,
                                                              EventType.RUN_FAILED) else None)
    return seen, TestClient(main.app)


def _project(client, filename, content, mime):
    pid = client.post("/projects", json={"name": "Secret Estate", "target": "aws"}).json()["id"]
    assert client.post(f"/projects/{pid}/upload", files={"file": (filename, content, mime)}).status_code == 200
    return pid


def test_a_synchronous_run_publishes_its_outcome(run_events):
    seen, client = run_events
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        pid = _project(client, "i.xlsx", f.read(),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert client.post(f"/projects/{pid}/run").status_code == 200
    (event,) = [e for e in seen if e.project_id == pid]
    assert event.type == EventType.RUN_COMPLETED
    assert event.detail["target"] == "aws" and event.detail["vm_count"] > 0
    assert event.detail["duration_s"] >= 0 and "priced" in event.detail
    assert "Secret" not in json.dumps(event.detail)  # the bus (and audit trail) never gets the name


def test_a_failed_run_publishes_the_kind_of_failure(run_events):
    seen, client = run_events
    pid = _project(client, "i.csv", b"colour,flavour\nred,lime\n", "text/csv")
    assert client.post(f"/projects/{pid}/run").status_code in (400, 422)
    (event,) = [e for e in seen if e.project_id == pid]
    assert event.type == EventType.RUN_FAILED
    assert event.detail["failure"] in ("invalid input", "plan validation")
    assert "error" not in event.detail


# --- CLI ------------------------------------------------------------------------------


def test_cli_notify_test(hook, monkeypatch, capsys):
    monkeypatch.setenv("IACTRANSLATE_NOTIFY_SLACK_WEBHOOK", hook.url)
    assert cli(["notify-test"]) == 0
    assert "slack: sent" in capsys.readouterr().out
    assert hook.bodies[0]["text"] == "IaCTranslate test notification"


def test_cli_notify_test_without_configuration(monkeypatch, capsys):
    for key in ("IACTRANSLATE_NOTIFY_SLACK_WEBHOOK", "IACTRANSLATE_NOTIFY_TEAMS_WEBHOOK",
                "IACTRANSLATE_SMTP_HOST", "IACTRANSLATE_NOTIFY_EMAIL_TO"):
        monkeypatch.delenv(key, raising=False)
    assert cli(["notify-test"]) == 2
    assert "no notification channel" in capsys.readouterr().err
