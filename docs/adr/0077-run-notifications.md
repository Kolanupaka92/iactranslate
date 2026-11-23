# 0077. Run notifications to Slack, Teams and email

**Status:** Accepted
**Date:** 2026-11-22

## Context

Milestone v2.4 includes notifications: tell someone when a translation finishes
or fails. The runtime already has an event bus, with the audit trail and the
Prometheus metrics subscribed to it (ADR 0026). But the bus only carried
`job.*` events, and the console runs projects through the synchronous `/run`,
which publishes none. Notifications hung on job events would never fire for the
main way the product is used.

## Decision

**Publish the outcome where every run passes.** `run.completed` and
`run.failed` come from the one function both `/run` and queued jobs call. The
event carries outcome figures only: target, workload count, monthly estimate,
whether it was priced, duration, and the kind of failure (`plan validation`,
`policy violation`, `invalid input`, `unexpected error`). Every subscriber sees
the event, the audit trail included, so the project name and the error text stay
off it.

**A subscriber, configured by the operator.** `api/notify.py` subscribes like
the audit log does. Channels come from the environment: a Slack incoming
webhook, a Teams Workflows webhook, and SMTP. Users cannot supply a URL. A
per-user webhook would mean the server sending requests to addresses a user
chose (SSRF), plus storage, UI and verification. That is a separate piece of
work, not part of this one.

**Content: outcome by default, names on opt-in.** On a multi-tenant hosted
deployment, the operator's channel would otherwise receive every tenant's
project names. A failure message could also quote the inventory row it failed
on. `IACTRANSLATE_NOTIFY_DETAIL=1` adds the name and up to 500 characters of the
error, for a single-team deployment that wants them. This follows the reset-link
logging decision: the convenience is one variable away, and it is not the
default.

**Off the request path.** Sends run on one background thread fed by a bounded
queue (200). A full queue drops the message with a warning instead of growing.
Each channel is tried on its own. 429, 5xx, network errors and SMTP 4xx are
retried up to three attempts, honouring `Retry-After`. Any other 4xx (a revoked
webhook) is not retried.

**Formats follow the vendors' documentation.**

- **Slack:** `text` as the notification fallback, plus `blocks`, with `&`, `<`
  and `>` escaped.
- **Teams:** a `message` with one Adaptive Card 1.4 attachment. This is what
  Workflows webhooks accept, and they replace the retired Office 365 connectors.
- **Email:** STARTTLS on 587 or implicit TLS on 465. Plaintext is allowed only
  to a relay on the same machine without a login.

**Refuse to start when half-configured.** This covers an SMTP host without
recipients, a user without a password, an `http://` webhook, or an unknown
outcome name. A deployment that believes it alerts on failures and silently
does not is worse than one that refuses to start and names the variable.
`iactranslate notify-test` sends one message per channel and reports each
result.

## Consequences

- Both run paths notify, and no route or pipeline code knows notifications
  exist.
- The senders are tested over real sockets: a local HTTP receiver for the
  webhook channels and a minimal SMTP server, covering retries, `Retry-After`,
  no retry on 404, and an unreachable host. Not exercised: a real Slack, Teams
  or mail provider, and the STARTTLS/TLS handshake itself (smtplib's, with the
  default certificate verification). `notify-test` against the real channel is
  the first check on a deployment.
- The queue is in memory. Notifications pending at shutdown are lost. That is
  acceptable for alerts, and wrong for anything that must be delivered.
- This does not deliver password-reset email. The SMTP settings would serve it,
  but `delivery.py` deliberately has no untested sender in the account-recovery
  path. Wiring it is the v2.3 follow-up, and it now has a tested transport to
  build on.
