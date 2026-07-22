# 0011 — The pipeline runs as named, timed stages

**Status:** Accepted

## Context

The pipeline was a straight-line function. That works, but two enterprise needs
push toward making the stages explicit: **observability** (where does the time
go? which stage failed?) and, later, **resumable/distributed execution** (persist
after each stage, resume from the failure, run stages on workers). A monolithic
function offers no seam for either.

## Decision

Run the pipeline as an **ordered list of named stages** — `parse`, `normalize`,
`plan`, `validate`, `policy`, `package`, `zip` — each timed. The run produces a
`PipelineTrace` (per-stage `duration_ms` + total), emitted as a structured log
line and a `pipeline-trace.json` artifact. The stage names are the same ones the
docs and the [state machine](../deployment.md#state-machine) use.

## Consequences

- **Observability now:** every run reports where its time went, per stage — the
  metrics an operator asks for (parse time, plan time, package time, …).
- **A seam for later:** resumable and distributed execution build on this stage
  list — checkpoint state after each stage, resume from the failed one, dispatch
  stages to workers. Those need persistence + a queue (see
  [deployment › reference architecture](../deployment.md#reference-architecture))
  and are deliberately **not** built yet — we added the stage model and trace, not
  a workflow engine ahead of need.
- We keep the pipeline a synchronous function; the stage wrapper is a thin timing
  context manager, not a framework. Determinism and the
  [immutable plan](0007-immutable-plan.md) are unchanged.
