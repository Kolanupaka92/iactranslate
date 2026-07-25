# 0024 — Migration wave planning: two honest dependency signals, not a fabricated one

**Status:** Accepted

## Context

Enterprises migrate an estate in waves, not one shot — with an order that
respects dependencies (a web tier is useless without its app tier) and risk
(prove the pattern in a lower environment before touching production). The
tool had no notion of execution order at all: every workload rendered as a
peer with no sequencing information.

The obvious version of this feature is a full application-dependency graph —
"service A calls service B on port 443." This tool cannot build that
honestly. It is explicitly offline and file-in only (see architecture.md's
scope boundary): no agent, no network flow data, no live discovery. Inventing
a dependency edge it cannot observe would be worse than having none — a false
edge changes migration order and risk assessment in a way that's actively
misleading, not just incomplete.

## Decision

1. **Two dependency signals, both genuinely derivable from an inventory
   export, nothing else.**
   - **Tier depth within an application**: `database`/`cache`/`other` (0) →
     `app` (1) → `web` (2). What a layer depends on migrates (and is
     validated) first. This is the same `Tier` enum the classifier already
     assigns — no new signal, just an ordering imposed on an existing one.
   - **Environment promotion order**: development/test → staging →
     production. Prove the pattern in a lower environment before touching
     production — standard practice, and `Environment` is likewise already on
     every `ComputePlan`.
2. **A wave is exactly one (environment, tier-depth) group** — never mixed.
   `depends_on` chains to that same environment's lower-tier-depth wave(s),
   transitively (a web wave depends on both its app wave and its data wave).
   Different environments never depend on each other — they're independent
   estates, safe to run in parallel, and the report says so.
3. **`WaveReport.notes` states the boundary explicitly**: cross-application
   dependencies (app A calling app B) are real, common, and not modeled —
   "if they exist, sequence the affected waves manually rather than trusting
   this order blindly." Same honesty pattern `replatform.py` uses for its own
   scope boundary (ADR 0020).
4. **Downtime estimates are LB-aware, not per-instance-fabricated.** A wave
   fronted entirely by a load balancer (every workload in `lb.targets`) gets
   an estimate of 0 minutes — a rolling migration through the LB needs no
   hard cutover window. An unfronted wave gets a flat, tier-based planning
   estimate (30 min for data/cache, 10 min otherwise), explicitly labeled "a
   rough planning input, not a guarantee."
5. **Rollback strategy and validation checks are templated per tier depth**,
   not per instance — real, standard practices (health-endpoint checks for
   web, DB connectivity + backup verification for data) rather than invented
   specifics that would imply a level of automation this tool doesn't have.
6. **Advisory only, same as re-platforming.** `waves.json` is written
   alongside every generated project and surfaces in both
   `documentation/migration-summary.md` and the executive report. It never
   changes what's rendered — sequencing is planning information, not a
   generation input.

## Consequences

- Answers the single most consistent gap raised across two independent
  external architecture reviews of this project — but scoped to what the
  tool can actually know, not the full "application dependency discovery"
  those reviews pictured (see the module docstring in `waves.py` for the
  explicit boundary).
- Reuses two fields (`Tier`, `Environment`) that already exist on every
  `ComputePlan` — no new inventory signal, no new classifier logic, and thus
  no new failure mode. The wave planner is a deterministic *view* over data
  the plan already carries, in the same spirit as the Infrastructure Graph
  (ADR 0010) being a topology view over the same plan.
- If real dependency discovery (network flow logs, an installed agent, CMDB
  relationship data) becomes an available signal in the future, it composes
  with this rather than replacing it: an explicit `depends_on` override
  layered on top of the tier/environment default is the natural extension,
  not a rewrite.
