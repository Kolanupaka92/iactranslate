# 0006 — Validation is a hard gate before rendering

**Status:** Accepted

## Context

Decisions upstream — whether from the rule engine or an LLM — can be wrong: an
instance type that doesn't exist, overlapping subnet CIDRs, a security group
referenced but never defined, duplicate resource names. If any of these reach the
renderer, the tool emits Terraform that fails (or worse, applies incorrectly).
For an infrastructure tool, emitting invalid IaC is the cardinal sin.

## Decision

Insert a **validation layer between the agents and the renderer** that never
trusts upstream output. It checks catalog membership, CIDR overlap/containment,
duplicate names, and referential integrity. `assert_valid(plan, target)` raises
`PlanValidationError` (with the specific issues) and **no files are written** on
failure — "fail fast, never emit invalid Terraform."

## Consequences

- Invalid plans are impossible to render; the failure is explicit and explains
  what/why/where.
- The AI provider ([0004](0004-ai-provider-interface.md)) can be swapped or
  upgraded freely — validation backstops it, so a worse or better model changes
  quality, not safety.
- Errors surface as `422` with an `issues[]` list on the API, and a non-zero exit
  with a readable message on the CLI.
- We accept that validation and the catalogs must be kept in step with each
  target's real capabilities.
