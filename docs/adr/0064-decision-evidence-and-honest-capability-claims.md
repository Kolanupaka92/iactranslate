# 0064. Decision evidence, and capability claims that cannot drift

**Status:** Accepted
**Date:** 2026-09-12

## Context

A 37-point product plan proposed, among much else, a `DecisionEvidence` model
and a set of honesty rules about what "validated" and "supported" mean. Most of
the plan was either already built or needed customers that do not exist yet.
These parts were neither.

The product's claim is not that it picks instance types. It is that a migration
engineer can *interrogate* the choice. That information existed and was
scattered: a free-text `reason` on `ComputePlan`, a factor score in
`confidence.py`, a `right_sized` boolean, a `price_source` string. A human
reading the console could reassemble it. Nothing else could — not the API, not
the report, not a test.

Two claims were also being made more confidently than the evidence supported.
"Validated" covered everything from "parses against the provider schema" to
what a reader hears as "will work in production". And "five clouds supported"
flattened a real difference: AWS, Azure and GCP output is checked against the
real providers on every push; OCI and DigitalOcean output never meets a provider
binary.

## Decision

**One `Decision` type, carrying what a reviewer would ask for.** Outcome,
evidence, assumptions, alternatives, unknowns, confidence, engine version.
`alternatives` and `unknowns` are as load-bearing as `evidence`: the first two
questions in any review are "what else did you consider?" and "what didn't you
know?", and a model that cannot answer them turns every review into an argument
about trust rather than about the estate.

**`basis` is the field that matters.** It separates a size computed from
*measured* utilization from one inferred from *allocation*. Both produce an
instance type; only one is evidence. `Basis.ALLOCATED` renders as
**"Allocation-based estimate"** and never as "optimal", because an
allocation-derived size cannot be known to be optimal — allocation records a
decision someone made years ago for reasons nobody wrote down, and
over-provisioning is precisely what the customer is paying to escape.

The effect is immediate and uncomfortable in the right way: the bundled sample
estate carries no utilization, so every decision in the demo now labels itself
an allocation-based estimate and lists what it could not determine.

**No timestamps on decisions.** The plan asked for them. Adding a clock to the
output would break reproducibility — the same inventory must produce the same
plan (ADR 0007). Engine version is recorded; *when* a decision was made belongs
to the run, not the decision.

**A validation ladder, with the product's real rung named.** Five levels from
syntax to application correctness, and an explicit `HIGHEST_REACHED = POLICY`.
Deployment is unproven by construction, since nothing here applies
infrastructure to an account, and application correctness is not a property an
inventory can establish. The boundary is stated in a sentence as well as a
number, because a level number means nothing to the person deciding whether to
run a pilot.

**Provider maturity is derived from CI, not declared.** `PROVIDER_VALIDATED`
means the `terraform-validate` job initialises that provider.
`test_maturity.py` reads the workflow and fails if the claim and the job
disagree — the same self-verification used for the renderer matrix (ADR 0061),
for the same reason: a hand-maintained capability table drifts, and a stale one
reaches a customer as a lie.

## Consequences

The API returns `basis`, `evidence`, `assumptions` and `unknowns` per workload
plus an estate-level `measured_sizing_count`, and `/targets` reports maturity.
The executive report gains a "What has and has not been validated" section
showing all five levels with the reached ones marked — so the artifact a
consultant hands a client states its own limits rather than relying on the
consultant to remember them.

Prompt-injection resistance is now a tested property rather than a lucky one.
De-identification (ADR 0058) was built for privacy and turns out to defeat
injection as a side effect: an instruction hidden in a hostname is made of
exactly the tokens it replaces, so `IGNORE PREVIOUS INSTRUCTIONS…` reaches the
model as `w1 w2 w3…`. That was an accident, and an accident nobody tests is a
regression waiting to happen — widening `STRUCTURAL_TOKENS` to improve
classification could quietly reopen it. The tests also record the honest limit:
the defence is lost when `IACTRANSLATE_AI_DEIDENTIFY=0`.

`reason` is unchanged, so every existing consumer keeps working; `decision` is
optional, so an older plan still loads.
