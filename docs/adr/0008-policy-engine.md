# 0008 — A policy engine for organization-specific rules

**Status:** Accepted

## Context

Enterprise requirements diverge exactly at policy: "all resources tagged",
"never deploy public IPs", "only approved VM families", "encrypt disks", "prod
only in these regions", "stay under this budget". If these were encoded in the
core mappings or validation, every customer would need a fork, and the
translation engine would stop being generic. But they also can't be ignored —
policy is often the gate an enterprise cares about most.

## Decision

Add a **policy engine**: a registry of small, pluggable, **read-only** policies
that a customer activates and parameterizes through a **policy config** (JSON).
The engine runs after structural validation, evaluates each configured policy
against the (immutable) plan, and returns violations. `deny` aborts before
rendering; `warn` is reported (`policy-report.json`) but doesn't block. Severity
is overridable per policy. Policies never mutate the plan
([ADR 0007](0007-immutable-plan.md)).

## Consequences

- Organizations express rules as **configuration or plugins**, never by editing
  the core pipeline — the engine stays generic.
- Policy slots cleanly into the validation phase as a second, org-specific layer
  after the universal structural checks.
- Surfaced across CLI (`--policy`), API (`policy` on create, `GET /policies`),
  and the package (`policy-report.json`).
- We accept that some desirable policies (e.g. mandatory tags) need model fields
  that don't exist yet; those await a canonical-model addition rather than a
  special case. The registry makes adding a policy trivial once the data exists.
