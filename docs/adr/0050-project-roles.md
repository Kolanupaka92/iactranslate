# 0050. Project roles — separation of duties, not just ownership

**Status:** Accepted
**Date:** 2026-08-28

## Context

ADR 0027 gave every project an `owner_id` and made anything else 404. That is a
**tenancy boundary**, and it was the right first thing to build — but the
architecture review named the gap accurately: *"One role. No viewer/approver/
admin, no team scoping, no separation of duties."*

Several entirely ordinary requests were unsatisfiable:

* an auditor who must read a migration plan without being able to change it
* a reviewer who approves a plan but does not run it
* a contractor who runs translations but must not delete the workspace
* two colleagues seeing the same project at all

## Decision

Four **ordered** roles — `viewer` < `editor` < `approver` < `admin`. Ordering
matters: access control that cannot be compared makes every check a special
case, and `at_least(held, required)` is the whole enforcement primitive.

`approver` is deliberately distinct from `admin`. *"May approve"* and *"may
grant access"* are the two permissions an auditor most wants separated — an
editor who can grant themselves approval defeats the point of having approval at
all.

**Every endpoint declares the role it needs**, rather than inheriting a blanket
check. Reads are `viewer`; upload and run are `editor`; delete and access
management are `admin`. `POST /recommend` is gated as `editor` despite being
read-only analysis, because it builds a plan per cloud — five full pipelines of
real CPU — and metering work matters more here than the read/write distinction.

**The owner is always admin** and cannot be demoted or removed. A project whose
last administrator revoked themselves is unrecoverable without a database edit.

**Absent access stays 404; insufficient access is 403.** The 404 reasoning from
ADR 0027 is unchanged — a 403 confirms the id exists and enables enumeration.
But a caller who already holds *some* role knows the project exists, so 404 would
tell them nothing new and would make a permissions problem look like a missing
resource. The response names both the required and the held role.

**Grants are stored separately from projects** (`roles.Membership`), because a
grant belongs to neither the project nor the user alone.

## Consequences

`GET /projects` now returns shared projects alongside owned ones, each with the
caller's role. A grant the recipient cannot see is a grant that does not work —
they would have to be told the project id out of band before they could use it.

Deleting a project drops its grants, so a recycled id cannot inherit access from
a project that no longer exists.

Single-tenant mode (`IACTRANSLATE_AUTH` unset) is unchanged: every project has
`owner_id = None`, the sole operator is implicitly an admin, and a test asserts
the historical behaviour still holds.

**Writing the tests surfaced a real hygiene problem.** The account store defaults
to `./iactranslate.db`, so a test run without `IACTRANSLATE_DB_PATH` set drops a
SQLite file — holding password hashes and session tokens — in the repository
root. One appeared during this work and was untracked but not ignored. `*.db` is
now in `.gitignore` and the fixture isolates the path.

Still open: this is **project-scoped** RBAC, not organisation-scoped. There are no
teams, no groups, and no inherited roles, so granting twenty people access to
forty projects is eight hundred grants. Orgs and group membership are the next
step, and ABAC (attribute conditions such as environment or data
classification) is beyond that.

*Superseded in part by [ADR 0056](0056-durable-project-grants.md).* This record
originally closed by noting that grants were in-memory and did not survive a
restart, framed as a consistent single-node limitation. That framing was wrong:
projects, accounts and sessions were already durable, so a restart silently
revoked every grant while keeping everything it applied to. Grants now follow
`IACTRANSLATE_STORE` like the rest.
