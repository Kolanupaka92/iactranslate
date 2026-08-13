# 0028 — Per-route rate limiting and baseline security headers

**Status:** Accepted

## Context

Nothing throttled any endpoint. That was already a denial-of-service problem —
`/run` does real CPU and disk work per call, and `/upload` accepts 25 MB — but
[ADR 0027](0027-multi-tenancy-and-session-auth.md) made it sharper by adding
`/auth/login`: an endpoint that accepts a password and reports whether it was
correct. An unthrottled login is an open invitation to brute-force every account
on the deployment, and it is the single most attackable surface the product has.

Adding authentication without adding a throttle would have been a net *negative*
for security: it creates a credential to guess where none existed before.

## Decision

**Token buckets, applied per route class rather than as one blanket middleware,**
because the three surfaces have genuinely different economics:

| Surface | Default | Why |
|---|---|---|
| `/auth/*` | 10/min | Accepts a password and reports whether it was right |
| Writes (upload, run, jobs, report, create, delete) | 60/min | Real CPU and disk per call |
| Reads | 240/min | Cheap; only stops runaway clients |

Four decisions worth recording:

1. **Token bucket, not a fixed window.** A fixed window lets a caller spend its
   entire quota in the last second of one window and again in the first second
   of the next — 2x the intended burst, precisely at the boundary an attacker
   would aim for.
2. **Auth is throttled per email as well as per IP.** Per-IP throttling alone
   does nothing against credential stuffing, which hits one account from many
   addresses. The per-IP bucket stops one host hammering many accounts; the
   per-email bucket stops many hosts hammering one account. Both are needed, and
   a test asserts the second case specifically by rotating the source address.
3. **`X-Forwarded-For` is trusted only when `IACTRANSLATE_TRUST_PROXY=1`.** Any
   client can send that header. Trusting it unconditionally would let an
   attacker bypass every limit by rotating a fake value — the limiter would
   look like it worked while enforcing nothing.
4. **The bucket table is bounded.** Without a cap, an attacker rotating source
   addresses grows it without limit, and the rate limiter becomes the
   memory-exhaustion vector it exists to prevent. Idle buckets are evicted
   first, then the least recently seen.

**Limits are read from the environment on every check, not at import.** This
started as a testability problem — the suite shares one process and one client
address, so import-time limits could not be varied per test — but it is the
better design regardless: an operator can retune a running deployment, or
disable a limiter with `0`, without a restart.

**Security headers** (`X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Cross-Origin-Opener-Policy`) are set on every response.
These matter most on `/projects/{id}/report`, which returns HTML rendered in the
user's browser. HSTS is sent **only over https** — asserting it on a plaintext
dev server would pin `localhost` to https in the developer's browser and break
local work in a way that is annoying to undo.

## Consequences

- Password guessing is throttled after 9 attempts with an actionable
  `Retry-After` — verified against a live server, not only in tests.
- The suite disables limits by default (`tests/conftest.py`) because every test
  shares one client address and would otherwise look like a single hammering
  client; `tests/test_ratelimit.py` opts back in explicitly, which is where the
  limits belong under test.
- **Honest boundary: buckets are per-process.** Two API replicas allow roughly
  twice the configured rate, and a restart forgets every counter. Correct
  enforcement across replicas needs shared state (Redis). This is a real
  single-node limiter, not a distributed one, and the module docstring says so.
- Not addressed here: account lockout after sustained failures (a throttle slows
  guessing, it does not stop a patient attacker), CAPTCHA, or anomaly detection.
