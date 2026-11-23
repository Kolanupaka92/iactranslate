# 0078. Password-reset links by email

**Status:** Accepted (amends [ADR 0030](0030-password-change-and-reset.md) on delivery)
**Author:** Sai Kolanupaka
**Date:** 2026-11-23

## Context

ADR 0030 built the reset flow (hashed, single-use, one-hour tokens; every
session evicted on reset) and deliberately left delivery as a seam that logs.
The reasoning: an SMTP client that has never delivered a message would be the
one untested piece of account recovery, and it would fail on a locked-out user
in production.

Two things changed. Run notifications (ADR 0077) brought an SMTP transport that
is exercised end to end against an SMTP server in the tests. And the web app
had no forgot-password or reset-password screens, so even a delivered link would
have opened a 404.

## Decision

**One SMTP transport.** `api/mail.py` holds the relay settings
(`IACTRANSLATE_SMTP_*`) and enforces the TLS rule once: STARTTLS or implicit TLS
with certificate verification, and plaintext only to a relay on the same machine
without a login. Notifications and reset email both use it.

**Reset email is turned on by its own sender.**
`IACTRANSLATE_RESET_EMAIL_FROM` with the SMTP settings enables it. A relay set
up only for notifications does not start emailing users. Notification email is
turned on by its recipients, so a relay for resets alone does not start emailing
the operator. Setting the sender without a relay stops the API at startup: a
deployment that thinks it emails reset links and does not leaves every
locked-out user stranded.

**Off the request thread.** `forgot-password` already gives the same answer for
any address. If it waited on SMTP only when the account exists, its response
time would reveal which accounts exist. The send is queued to a background
thread and retried on SMTP 4xx and network errors. Failures are logged with the
address and never with the link.

**The message:** plain text, the account address, the link, the one-hour
expiry, that the link is single-use and signs out every session, and to ignore
it if not requested. The link is longer than 78 characters, so it travels
quoted-printable (RFC 2045), which every mail client decodes.

**The pages.** Sign-in gains "Forgot your password?", which shows the same
confirmation whether or not the account exists. `/reset-password` reads the
token once, removes it from the address bar (out of history and screenshots),
sets `referrer: no-referrer`, asks for the password twice, and on success says
every session was signed out.

## Consequences

- Without a mailer, nothing changes: the request is logged and the link is not,
  unless `IACTRANSLATE_RESET_LINK_TO_LOG=1`.
- Tested over a real socket: forgot-password → message at the fake relay →
  token from the decoded body → reset succeeds once and fails the second time.
  Also tested: an unknown address sends nothing, a slow relay doesn't delay the
  request, failures don't log the link, and the worker survives errors. Not
  tested: a real provider and the TLS handshake. The first real reset on a
  deployment is that check.
- The queue is in memory. A reset pending at shutdown is lost, and the user
  asks again. On Cloud Run, where CPU is allocated per request, a send can be
  delayed until the next request (ADR 0077).
- Email verification at sign-up (v2.3) can reuse the same transport and
  background sender.
