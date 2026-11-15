# 0056. Grants that survive a restart

**Status:** Accepted
**Date:** 2026-08-28

## Context

ADR 0050 shipped project roles and closed by noting that grants were in-memory
"like the rest of the single-node runtime". At the time that read as a
consistent limitation. It was not — it was a security bug hiding behind a
consistency argument.

A deployment with `IACTRANSLATE_STORE=sqlite` persists its projects, its
accounts, its sessions, its audit trail and — since ADR 0053 — its jobs. It did
**not** persist who may see any of it. Restart the process and every project
survives while every grant an administrator made is silently revoked, leaving
each project visible only to its owner.

Reproduced before fixing: an owner grants `editor`, the members list shows two
entries, the process restarts, and the grant is gone. Nobody notices until a
colleague reports losing access — which is the worst way to learn that
authorization state is not durable.

**Half-durable authorization is worse than none**, because the failure is silent
and looks like a permissions mistake rather than data loss.

## Decision

`SqliteMembership` persists grants to the same file, selected by the same
`IACTRANSLATE_STORE` switch as the project store and the job queue. One switch,
so a deployment cannot end up persisting some of its state and losing the rest —
which is exactly how this defect came to exist.

Grants are cached in memory and written through. They are read on every
authorization check and are small, so SQLite is the record rather than the hot
path; the in-memory class remains the base and the durable one overrides only
the three mutating methods.

A role that fails to parse — a value written by a newer version — is skipped
rather than raised. An unreadable grant should cost that one person access, not
take the whole deployment down on startup.

## Consequences

Revocations and demotions are durable too, and both are tested. The dangerous
direction is not a grant that disappears but access that **comes back** after a
restart, and a `DELETE` that only touched the cache would do exactly that.

Deleting a project removes its grants from disk, so a recycled project id cannot
inherit access from a project that no longer exists.

ADR 0050's closing note is now wrong and has been marked superseded rather than
left to mislead.

Still open: the idempotency store and the OIDC login store remain in-process. The
consequences are much milder — a retry after a restart re-executes, which is the
behaviour before idempotency existed, and an in-flight SSO login fails its state
check and the user logs in again. Neither loses committed state, which is what
separated this case from those. Multi-*replica* remains a separate problem: one
SQLite file is durable but still single-node.
