"""Password-reset links by email (the v2.3 reset sender).

End to end over a real SMTP socket: forgot-password queues a message, the
fake relay receives it, and the token in that message resets the password.
Not exercised: a real provider and the TLS handshake (`notify-test` and a
first real reset are those checks).
"""
from __future__ import annotations

import email
import email.policy
import importlib
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from iactranslate.api import delivery
from iactranslate.api.mail import DeliveryError, MailConfigError, SmtpTransport, transport_from_env
from iactranslate.api.notify import channels_from_env

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "an-entirely-different-passphrase"


@pytest.fixture()
def mailing_api(tmp_path, monkeypatch, smtp_server):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "mail.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    monkeypatch.setenv("IACTRANSLATE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("IACTRANSLATE_SMTP_PORT", str(smtp_server.server_address[1]))
    monkeypatch.setenv("IACTRANSLATE_SMTP_TLS", "none")
    monkeypatch.setenv("IACTRANSLATE_RESET_EMAIL_FROM", "IaCTranslate <no-reply@example.com>")
    monkeypatch.setenv("IACTRANSLATE_APP_URL", "https://app.example")
    import iactranslate.api.main as main
    importlib.reload(delivery)
    importlib.reload(main)
    yield TestClient(main.app), smtp_server
    monkeypatch.undo()
    importlib.reload(delivery)
    importlib.reload(main)


def _decoded(raw: str):
    """Parse the message as a mail client would (the long link line is
    quoted-printable encoded on the wire)."""
    return email.message_from_string(raw, policy=email.policy.default)


def _token_from(body: str) -> str:
    line = next(line for line in body.splitlines() if "reset-password?token=" in line)
    return line.strip().split("token=", 1)[1]


def test_forgot_password_emails_a_link_that_resets_the_password(mailing_api):
    client, relay = mailing_api
    assert client.post("/auth/register", json={"email": "pat@example.com", "password": PASSWORD}).status_code == 201
    r = client.post("/auth/forgot-password", json={"email": "pat@example.com"})
    assert r.status_code == 202
    assert delivery._configured.flush(10)

    (raw,) = relay.messages
    msg = _decoded(raw["data"])
    assert msg["Subject"] == "Reset your IaCTranslate password"
    assert msg["To"] == "pat@example.com"
    body = msg.get_content()
    assert "https://app.example/reset-password?token=" in body
    assert "within 60 minutes" in body

    token = _token_from(body)
    assert client.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}).status_code == 204
    assert client.post("/auth/login", json={"email": "pat@example.com", "password": NEW_PASSWORD}).status_code == 200
    # Single use: the same emailed link does not work twice.
    assert client.post("/auth/reset-password", json={"token": token, "password": PASSWORD}).status_code == 400


def test_an_unknown_address_gets_the_same_answer_and_no_mail(mailing_api):
    client, relay = mailing_api
    r = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert r.status_code == 202
    assert r.json() == {"status": "if that account exists, a reset link has been sent"}
    assert delivery._configured.flush(5)
    assert relay.messages == []


class _Transport:
    """Stands in for SmtpTransport: scripted failures, optional delay."""

    def __init__(self, failures=(), delay=0.0):
        self.failures, self.delay, self.sent = list(failures), delay, []

    def send(self, msg):
        time.sleep(self.delay)
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(msg)


def test_the_request_never_waits_on_the_relay():
    """Waiting on SMTP only for real accounts would make the response time an
    account-enumeration oracle."""
    slow = _Transport(delay=1.0)
    backend = delivery.EmailLinkDelivery(slow, "no-reply@example.com")
    start = time.monotonic()
    backend("pat@example.com", "https://app.example/reset-password?token=abc")
    assert time.monotonic() - start < 0.2
    assert backend.flush(5) and len(slow.sent) == 1


def test_a_temporary_relay_failure_is_retried():
    flaky = _Transport(failures=[DeliveryError("SMTP 451", retryable=True)])
    backend = delivery.EmailLinkDelivery(flaky, "no-reply@example.com", sleep=lambda s: None)
    assert backend.send_now("pat@example.com", "https://x/reset-password?token=t") is None
    assert len(flaky.sent) == 1


def test_a_failed_send_is_logged_without_the_link(caplog):
    broken = _Transport(failures=[DeliveryError("SMTP 550")])
    backend = delivery.EmailLinkDelivery(broken, "no-reply@example.com", sleep=lambda s: None)
    log = logging.getLogger("iactranslate.api.delivery")
    log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="iactranslate.api.delivery"):
            assert backend.send_now("pat@example.com", "https://x/reset-password?token=SECRET-123") == "SMTP 550"
    finally:
        log.removeHandler(caplog.handler)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "pat@example.com" in joined and "SMTP 550" in joined
    assert "SECRET-123" not in joined and "token=" not in joined


def test_worker_survives_an_unexpected_error():
    class Boom:
        def send(self, msg):
            raise RuntimeError("bug")

    backend = delivery.EmailLinkDelivery(Boom(), "no-reply@example.com")
    backend("a@example.com", "https://x/reset-password?token=1")
    backend("b@example.com", "https://x/reset-password?token=2")
    assert backend.flush(5)
    assert any(t.name == "iactranslate-reset-mail" and t.is_alive() for t in threading.enumerate())


# --- configuration -------------------------------------------------------------------


def test_reset_email_is_off_without_a_sender():
    assert delivery.email_delivery_from_env({"IACTRANSLATE_SMTP_HOST": "smtp.example.com"}) is None


def test_a_sender_without_a_relay_refuses_to_start():
    with pytest.raises(MailConfigError, match="without IACTRANSLATE_SMTP_HOST"):
        delivery.email_delivery_from_env({"IACTRANSLATE_RESET_EMAIL_FROM": "a@example.com"})


def test_gmail_style_configuration():
    env = {"IACTRANSLATE_SMTP_HOST": "smtp.gmail.com", "IACTRANSLATE_SMTP_USER": "me@gmail.com",
           "IACTRANSLATE_SMTP_PASSWORD": "app-password", "IACTRANSLATE_RESET_EMAIL_FROM": "me@gmail.com"}
    backend = delivery.email_delivery_from_env(env)
    assert backend.transport == SmtpTransport("smtp.gmail.com", 587, "me@gmail.com", "app-password", "starttls")


@pytest.mark.parametrize("env,match", [
    ({"IACTRANSLATE_SMTP_USER": "u", "IACTRANSLATE_SMTP_PASSWORD": "p"}, "without IACTRANSLATE_SMTP_HOST"),
    ({"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_SMTP_USER": "u"}, "without"),
    ({"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_SMTP_TLS": "none"}, "clear text"),
    ({"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_SMTP_TLS": "maybe"}, "starttls, ssl or none"),
])
def test_broken_smtp_settings_are_refused(env, match):
    with pytest.raises(MailConfigError, match=match):
        transport_from_env(env)


def test_a_relay_for_resets_alone_does_not_turn_on_notification_email():
    env = {"IACTRANSLATE_SMTP_HOST": "smtp.example.com", "IACTRANSLATE_SMTP_USER": "u",
           "IACTRANSLATE_SMTP_PASSWORD": "p", "IACTRANSLATE_RESET_EMAIL_FROM": "a@example.com"}
    assert channels_from_env(env) == []
