# 0002 — `NormalizedVM` as the canonical inventory model

**Status:** Accepted

## Context

Inventory arrives in wildly different shapes: RVTools spreadsheets, Hyper-V
exports, arbitrary CMDB columns, cloud fleet CSVs. If every stage of the pipeline
(classification, right-sizing, network planning, validation, assessment) had to
understand each format, adding a source would mean touching the whole system —
an N-sources × M-stages maintenance burden.

## Decision

Define one canonical inventory type, **`NormalizedVM`** (canonical units: vCPU
count, GiB memory/disk), and require every source to map its format to it. The
`normalize` step is the **narrow waist**: everything upstream is source-specific,
everything downstream consumes only `NormalizedVM`.

## Consequences

- Adding a source is self-contained: parse the format → `NormalizedVM` → register.
  No downstream stage changes. (Symmetrically, [0003](0003-target-registry.md)
  and `MigrationPlan` do the same on the output side.)
- Every downstream feature works for all sources automatically — the generic
  CMDB source unlocked assessment/confidence/diff with zero extra work.
- We accept some lossiness: source-specific fields not on `NormalizedVM` are
  dropped (kept only as opaque tags/ids where needed, e.g. `external_id` for
  brownfield). The canonical shape must be expanded deliberately, not casually.
