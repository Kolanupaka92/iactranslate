# 0059. A circuit breaker on live pricing, and honest degradation

**Status:** Accepted
**Date:** 2026-08-29

## Context

Live pricing already fell back to catalog rates on any failure — no network, no
credentials, an API error, an unknown SKU. That part was right and is unchanged.

What was wrong is that it fell back *per call*. `live_hourly` is invoked once per
distinct instance type, so in a corporate network that blackholes
`prices.azure.com` a 1,500-workload estate with 20 distinct instance types paid
the full 8-second socket timeout twenty times, on every run, to arrive at the
catalog rates it would have used anyway. The fallback was correct and the cost
of reaching it was not.

Separately, the reporting was misleading in a way that matters more than the
latency. `MigrationPlan.pricing_source` returned `"live"` if **any** single
instance got a live price. An estate where 1,492 of 1,493 workloads silently
fell back was presented to the customer as live-priced, and a business case gets
built on that number. This sits directly against the project rule that
assumptions are visible and savings are never fabricated.

## Decision

**A per-cloud circuit breaker.** After `BREAKER_THRESHOLD` (3) consecutive
failures a cloud's fetcher is skipped without a network call until
`BREAKER_RESET_S` (300s) has passed, at which point one probe is allowed
through. A blocked endpoint now costs three timeouts per five minutes rather
than one per SKU. Three rather than one because a single blip should not cost an
estate its real prices.

**Not tripped on latency.** A 1.5-second threshold was proposed and is rejected:
the Azure Retail Prices API legitimately exceeds it under load, and a price that
arrives slowly is still a correct price. Discarding correct answers to save time
is the wrong trade when the whole feature exists to improve accuracy. Errors and
timeouts are what indicate an unusable endpoint, so those are what count — and
the existing socket timeout already bounds any single wait.

**The cache is consulted before the breaker.** A price already on disk is just
as good whether or not the endpoint is currently reachable.

**Failure and "unknown SKU" are deliberately conflated.** A fetcher returns
`None` for both. Distinguishing them would need per-fetcher error plumbing for
little gain: an unknown SKU against a healthy endpoint costs at most three
lookups before the breaker parks the rest of the run on catalog rates — which is
the answer those lookups were going to produce anyway.

**`pricing_source` becomes three-valued:** `live` only when every workload was
priced live, `degraded` when mixed, `static` when none.

**`pricing_source_degraded` is a new boolean** covering the case
`pricing_source` structurally cannot: if every endpoint was unreachable, every
workload reads `static` and the plan is indistinguishable from one that never
asked for live prices. Telling those apart needs the requested mode, so
`MigrationPlan` now records `live_pricing_requested`. Both are surfaced in the
API run summary so a dashboard can say *why* the numbers are catalog rates
instead of leaving a customer to assume they are market prices.

## Consequences

`pricing_source` can now return `"degraded"`, which is a change in the value
domain of an existing `/v1` field. Accepted deliberately: the previous value was
not merely imprecise but wrong in the direction that flatters the tool, and a
consumer treating `"degraded"` as "not live" gets the right answer, while one
that was checking `== "live"` now correctly stops trusting a mixed estate.

Breaker state is process-wide and in-memory, held under a lock. That is the
correct scope — endpoint health is a property of the network, not of one
translation — but it means a replica that has tripped its breaker keeps using
catalog rates for up to five minutes after connectivity returns, and that state
is not shared between replicas. Both are acceptable; sharing it would mean
putting network health in Redis for a five-minute window.

`reset_breakers()` exists so tests and long-lived processes can forget endpoint
health. It is not wired to anything automatic.

Nothing here makes live pricing more likely to succeed. It makes failing cost
less and makes having failed visible, which is the honest split.
