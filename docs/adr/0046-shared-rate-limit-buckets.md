# 0046. Shared rate-limit buckets, so the limit survives a second replica

**Status:** Accepted
**Date:** 2026-08-27

## Context

Token buckets lived in one process's memory. The module docstring called this an
"honest boundary", which undersold it: this is not an approximation that degrades
gracefully at scale, it is **incorrect the moment a second replica starts**, and
incorrect in the attacker's favour.

With N replicas behind a load balancer the effective limit is N times the
configured one. Worse, a caller who simply reconnects until they land on a
different replica gets a fresh bucket. On `/auth/login` — the endpoint that
accepts a password and reports whether it was right — that is the difference
between a working credential-stuffing defence and a decorative one.

The architecture review flagged the whole class of process-local shared state.
This ADR covers the rate limiter, which is the member of that class with a direct
security consequence.

## Decision

Set `IACTRANSLATE_REDIS_URL` and buckets are shared; leave it unset and the
in-process limiter is used unchanged. A single-node deployment needs no Redis,
and requiring one would undermine the "runs anywhere with no services" property
the product sells on.

**The bucket is evaluated in a Lua script.** Read-modify-write against Redis from
several replicas has exactly the race that moving to shared storage was meant to
remove. One round trip, executed atomically by Redis, is the only version that is
actually correct.

**The script reads Redis's own `TIME`.** Replica clocks drift, and a replica
running fast would refill buckets faster than the limit allows. Taking the
server's clock means every replica agrees by construction rather than by luck.

**Keys carry a TTL** of twice their period. The in-process limiter needs an
eviction policy so it does not become the memory-exhaustion vector it exists to
prevent; here Redis reclaims idle buckets for free.

**On Redis failure the limiter degrades to per-replica buckets** rather than
failing open or closed. Failing open would hand an attacker an unlimited window
precisely when infrastructure is already unhealthy. Failing closed would let a
Redis outage take down the whole API. Falling back keeps per-replica protection —
weaker than shared, far better than none — and logs once per transition, so a
degradation is visible without emitting a line per request during an outage.

Bucket namespaces derive from each limiter's environment variable rather than its
human-readable name, so rewording a message cannot silently reset every caller's
bucket on deploy.

## Consequences

The test suite asserts **the bug as well as the fix**: two `RateLimiter`
instances standing in for two replicas allow 10 requests against a limit of 5
without sharing, and exactly 5 with it. A fix with no failing case behind it is
an assertion, not evidence.

Tests run against `fakeredis` by default so the suite still works offline with no
services, and a `redis-integration` CI job repeats them against a **real Redis 7**
container. Both matter: the Lua script, server-side `TIME` and TTL semantics are
exactly what a reimplementation can get subtly wrong, and this limiter's
correctness rests on all three.

Still open, and the rest of the class the review identified: the project store,
job queue, event bus, metrics and account store remain process-local. The job
queue is the most consequential — jobs are lost on restart with no retry or
dead-letter queue — and needs a durable broker rather than shared counters, so it
is a larger change than this one. `IACTRANSLATE_STORE=sqlite` already covers
single-node persistence; Postgres does not exist yet.
