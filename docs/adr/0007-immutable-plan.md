# 0007 — The MigrationPlan is immutable after planning

**Status:** Accepted

## Context

A growing set of engines surround the plan: assessment, confidence, the policy
engine, multiple renderers, executive reports, diagrams, GitOps. If any of them
could mutate the `MigrationPlan`, two things break: the output stops being
deterministic (a report or policy could quietly change what gets deployed), and
reasoning about the system becomes a function of execution order.

## Decision

Treat the `MigrationPlan` as **immutable once `build_migration_plan` returns and
validation passes**. Everything downstream — analysis engines, the policy engine,
renderers, reports, GitOps — takes it as **read-only** input. Analysis produces
*new* artifacts (an assessment, a confidence score, a diff) that reference the
plan; it never edits it.

## Consequences

- Rendering is deterministic: the same plan always yields the same IaC and the
  same reports, regardless of what else ran.
- Execution order among analysis/rendering stages doesn't matter — they can run
  in any order or in parallel.
- Auto-remediation (e.g. "rewrite gp3 → premium SSD") is deliberately *not* done
  post-plan; such transforms must happen *during* planning, before the freeze,
  or be surfaced as policy violations for a human to resolve.
- Enforced structurally by the [decision vs analysis](../architecture.md#decision-vs-analysis)
  split and tested (`test_policy_does_not_mutate_plan`).
