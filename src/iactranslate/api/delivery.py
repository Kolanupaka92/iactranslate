"""Where a password-reset link goes.

**This module deliberately does not send email.** Writing an SMTP client that
has never delivered a message would mean shipping the one piece of the reset
flow nobody has exercised — and the failure would land on a user locked out of
their account, in production, at the worst moment. The rest of the flow (token
issue, expiry, single use, session invalidation) is fully tested; delivery is
the seam left open.

The default backend writes the link to the server log, which is genuinely
useful for a single-operator deployment and for local development: the operator
reads the log and passes the link on. `set_link_delivery()` replaces it with a
real sender in one call, and the natural implementation is a few lines against
whichever provider you already use (SES, SendGrid, Postmark, plain SMTP).

Whatever the backend, it must never raise into the request path — see
`deliver_reset_link`. A failure to send must not tell the caller whether the
account existed.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger("iactranslate.api.delivery")

# (email, reset_url) -> None
LinkDelivery = Callable[[str, str], None]


def _log_delivery(email: str, reset_url: str) -> None:
    """Default backend: record the link so an operator can pass it on.

    Logged at WARNING because on a real deployment this line means "a user
    asked for a reset and nothing emailed it to them" — that should be visible,
    not buried at INFO.
    """
    logger.warning(
        "password reset requested for %s — no email backend configured, "
        "deliver this link manually: %s",
        email,
        reset_url,
    )


_delivery: LinkDelivery = _log_delivery


def set_link_delivery(fn: Optional[LinkDelivery]) -> None:
    """Install a real sender (or restore the logging default with None)."""
    global _delivery
    _delivery = fn or _log_delivery


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
