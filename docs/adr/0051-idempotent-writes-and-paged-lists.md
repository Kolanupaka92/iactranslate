# 0051. Idempotent writes, and paging that does not break the contract

**Status:** Accepted
**Date:** 2026-08-28

## Context

Two findings from the architecture review, both small and both real.

**A retried POST did the work twice.** Every HTTP client retries — load
balancers retry, SDKs retry, and a user watching a spinner for thirty seconds
clicks the button again. `POST /projects` created a second project;
`POST /projects/{id}/jobs` ran the entire pipeline again, burning CPU,
overwriting artifacts, and leaving the caller unable to say which result was
theirs.

**List endpoints returned everything.** `MAX_PROJECTS = 200` bounded the damage,
which is why it had never hurt — but a resource cap is not a page size, and the
two should not be the same number by accident.

## Decision

### Idempotency keys

`Idempotency-Key: <token>` on any POST. The first request executes and its
response is remembered; a repeat replays it, marked `Idempotency-Replayed: true`.
This is the convention Stripe popularised and most enterprise clients already
implement.

Three cases the naive version gets wrong:

* **A retry arriving while the first is still running** returns **409** with
  `Retry-After`, rather than executing concurrently.
* **The same key with a different body** returns **422**. That is a client bug —
  usually one key generated and reused across distinct requests — and silently
  replaying the first response would hide it.
* **A failed request does not burn the key.** Only 2xx responses are remembered:
  a caller retrying after a 500 wants another attempt, not the 500 played back
  for a day.

Keys are scoped by user, method and path, so one tenant cannot replay or collide
with another's request by guessing. It is **opt-in** — requiring it would break
every existing client, and a caller who sends no key gets exactly the previous
behaviour.

### Paging via headers, not an envelope

`limit` and `offset` parameters, with `X-Total-Count` and RFC 8288 `Link`
relations (`next`, `prev`, `first`).

**The first version of this returned an envelope** — `{items, total, limit,
offset, next_offset}` — which is more discoverable and is what most APIs do. The
test suite rejected it, correctly: it changes the response from an array to an
object, which is a **breaking change**, and both the `/v1` and legacy mounts
share the handler so the legacy mount could not absorb it. Shipping that one
commit after ADR 0049 — whose entire argument was not breaking clients — would
have been incoherent.

Headers give real pagination while a caller that sends no parameters sees no
change at all. The default `limit` is `MAX_PROJECTS`, the existing cap, so
"return everything" remains the default behaviour.

Offset rather than a cursor: the list is per-tenant and bounded. A cursor is the
right answer once a list can shift under a reader mid-page, and over-engineering
at this size.

## Consequences

The envelope is the better shape in the abstract and this ADR does not pretend
otherwise. It is the right thing to adopt at `/v2`, where a version boundary
exists to absorb it. Choosing the worse shape to keep a promise made one commit
earlier is the trade being made deliberately, and recorded here so it is not
quietly reversed.

Like the rate limiter before Redis, the idempotency store is in-process: a retry
that lands on a different replica is not recognised. The interface is what a
shared implementation would provide, and it moves when the store does.

`/audit` still takes only `limit`, with no `Link` relations — it is capped at
1000 and filtered per tenant, so it is bounded but not yet navigable.
