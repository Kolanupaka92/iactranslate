# 0083. Nobody's projects are deleted to make room for someone else's

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-24

## Context

The project store kept the newest `IACTRANSLATE_MAX_PROJECTS` (200) projects on
the whole deployment and deleted the oldest beyond that, files included. That
rule dates from the single-tenant, in-memory server, where it stopped a demo
filling its disk.

With accounts and open registration it became a way to destroy other people's
work: one account creating 200 projects permanently deleted every other
customer's. Reproduced against both stores before the fix. It needed no
attacker either, only enough customers.

## Decision

- **Only unowned projects are evicted.** The cap now counts and removes
  single-tenant projects (no owner) alone.
- **An account has a quota.** Creating a project beyond
  `IACTRANSLATE_MAX_PROJECTS_PER_ACCOUNT` (50) is refused with 409 and a message
  saying to delete one. Nothing is deleted on the account's behalf.
- Projects in an organisation count against the person who created them, who
  remains their owner (ADR 0080).

## Consequences

- Tested on both stores: a stranger's projects don't evict a customer's or touch
  their files, unowned projects don't evict owned ones, the single-tenant cap
  still holds, and an account at its limit is refused, keeps everything, and can
  create again after deleting one.
- Total storage is now bounded by accounts × quota, not by one number.
  Registration is rate limited per address and IP, which slows an abuser
  without stopping a determined one. Monitoring total usage is worth adding.
- At its limit, an account can't switch a project's cloud in the console, which
  creates the new project before deleting the old one.
- Two requests at the same moment can exceed the quota by one.
