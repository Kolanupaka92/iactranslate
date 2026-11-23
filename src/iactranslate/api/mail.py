"""SMTP transport, shared by run notifications and password-reset email.

One place decides how this server talks to a mail relay, so a deployment
configures `IACTRANSLATE_SMTP_*` once, and the rule against sending a password
over an unencrypted connection is enforced once.

  * `starttls` (default, port 587): upgrade, verifying the certificate.
  * `ssl` (port 465): implicit TLS, verifying the certificate.
  * `none`: allowed only to a relay on this machine with no login, e.g. a
    local Postfix that does its own TLS onward.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Mapping, Optional

ENV_SMTP_HOST = "IACTRANSLATE_SMTP_HOST"
ENV_SMTP_PORT = "IACTRANSLATE_SMTP_PORT"
ENV_SMTP_USER = "IACTRANSLATE_SMTP_USER"
ENV_SMTP_PASSWORD = "IACTRANSLATE_SMTP_PASSWORD"  # nosec B105 — an env var name, not a password
ENV_SMTP_TLS = "IACTRANSLATE_SMTP_TLS"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TIMEOUT = 10


class MailConfigError(ValueError):
    """Mail or notification settings are present but cannot work as written."""


class DeliveryError(RuntimeError):
    """One attempt to deliver one message failed."""

    def __init__(self, message: str, retryable: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class SmtpTransport:
    host: str
    port: int
    user: Optional[str] = None
    password: Optional[str] = None
    tls: str = "starttls"

    def __post_init__(self) -> None:
        if self.tls not in ("starttls", "ssl", "none"):
            raise MailConfigError(f"{ENV_SMTP_TLS} must be starttls, ssl or none")
        if self.tls == "none" and (self.user or self.host not in LOCAL_HOSTS):
            raise MailConfigError(
                f"{ENV_SMTP_TLS}=none sends mail (and any password) in clear text; it is only "
                "allowed to a relay on this machine without a login")
        if self.user and not self.password:
            raise MailConfigError(f"{ENV_SMTP_USER} is set without {ENV_SMTP_PASSWORD}")

    def send(self, msg: EmailMessage) -> None:
        """Deliver one message. Raises DeliveryError; retryable on SMTP 4xx and network errors."""
        context = ssl.create_default_context()
        try:
            if self.tls == "ssl":
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=_TIMEOUT, context=context)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=_TIMEOUT)
            with client:
                if self.tls == "starttls":
                    client.starttls(context=context)
                if self.user:
                    client.login(self.user, self.password or "")
                client.send_message(msg)
        except smtplib.SMTPResponseException as e:
            # 4xx is "try again later" in SMTP; 5xx is permanent.
            raise DeliveryError(f"SMTP {e.smtp_code}", retryable=400 <= e.smtp_code < 500) from None
        except (smtplib.SMTPException, OSError) as e:
            raise DeliveryError(f"SMTP: {type(e).__name__}", retryable=True) from None


def transport_from_env(env: Mapping[str, str] = os.environ) -> Optional[SmtpTransport]:
    """The configured relay, or None when IACTRANSLATE_SMTP_HOST is unset."""
    def get(key: str) -> str:
        return (env.get(key) or "").strip()

    if not get(ENV_SMTP_HOST):
        if get(ENV_SMTP_USER) or get(ENV_SMTP_PASSWORD):
            raise MailConfigError(f"SMTP credentials are set without {ENV_SMTP_HOST}")
        return None
    tls = (get(ENV_SMTP_TLS) or "starttls").lower()
    try:
        port = int(get(ENV_SMTP_PORT) or (465 if tls == "ssl" else 587))
    except ValueError:
        raise MailConfigError(f"{ENV_SMTP_PORT} must be a number") from None
    return SmtpTransport(host=get(ENV_SMTP_HOST), port=port, user=get(ENV_SMTP_USER) or None,
                         password=get(ENV_SMTP_PASSWORD) or None, tls=tls)
