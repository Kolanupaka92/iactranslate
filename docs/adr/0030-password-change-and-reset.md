# 0030 — Password change and reset, and where we stop

**Status:** Accepted

## Context

[ADR 0027](0027-multi-tenancy-and-session-auth.md) gave users passwords but no
way to change one, and no way back in after forgetting one. For a hosted
product that is not a missing nicety — a locked-out customer has no self-service
path, and a user who believes their password is compromised has no way to act on
it.

The obvious risk in building this is that a reset flow is a *second* way to
authenticate. Done carelessly it is a better attack surface than the login it
backs up.

## Decision

**Two flows, split by what can actually be verified here.**

1. **`POST /auth/change-password`** — authenticated, requires the current
   password. Needs no email, so it is testable end to end and is the primary
   path for a user who still has access.
2. **`POST /auth/forgot-password` → `POST /auth/reset-password`** — a
   single-use token flow for a user who does not.

### The rule that shapes both: changing a password evicts every session

Both flows call `delete_sessions_for_user`. Without it a password change is
close to theatre: someone who stole a session cookie stays signed in
indefinitely, and the user who "secured" their account has done nothing to the
attacker. The two flows differ in what happens next, for good reason:

- **Reset** deletes every session and issues none. Whoever is resetting may not
  be the legitimate owner, and the legitimate owner is about to log in anyway.
- **Change** deletes every session and then issues a fresh one *for the caller*.
  The attacker is signed out; the user who did the right thing is not bounced to
  a login screen as a reward for it.

### Token handling

- **Hashed at rest**, like session tokens — reading the table must not confer
  the ability to take over an account.
- **Single use.** The row is deleted on redemption *even when expired*, so a
  token can never be replayed. One consequence is deliberate and slightly
  user-hostile: a reset that fails password validation still burns the token,
  and the user must request a new link. Refusing to burn it on failure would
  leave a valid token alive after an attacker probed it.
- **One live token per user.** Requesting a new link invalidates the previous
  one, so a stale link in an older email stops working.
- **One hour TTL.** A reset link sits in an inbox, a less trustworthy place than
  a cookie jar.
- **No enumeration.** `forgot-password` returns the same 202 and the same body
  whether or not the account exists — verified byte-for-byte. Delivery failures
  are swallowed for the same reason: a backend that threw would turn into a 500
  that distinguishes real accounts from unknown ones.
- **Rate limited** through the same strict auth limiter as login
  ([ADR 0028](0028-rate-limiting-and-security-headers.md)).

### Where this stops: no SMTP

`api/delivery.py` defines the delivery seam and ships a backend that **logs the
link at WARNING** rather than emailing it. That is a deliberate stopping point,
not an oversight.

Writing an SMTP client that has never delivered a message would mean shipping
the one part of this flow nobody has exercised, and the failure would land on a
user locked out of their account, in production. Everything up to the send —
token issue, expiry, single use, enumeration resistance, session eviction — is
tested and verified. `set_link_delivery()` installs a real sender in one call,
and the natural implementation is a few lines against whatever provider is
already in use.

For a single-operator deployment the logging backend is genuinely usable: the
operator reads the link out of the log and passes it on.

## Consequences

- A user can rotate their own password, and a compromised account can be
  recovered — with every pre-existing session dropped in both cases. Verified
  live: a second session for the same account went from 200 to 401 across a
  password change while the caller's own session stayed valid.
- **The reset flow is not usable by end users until an email backend is
  wired.** That is the honest state of it: the mechanism is complete and
  tested, the delivery is not implemented. It should not be described as
  "self-service password reset" to a customer until then.
- Not addressed: email-address verification at signup (so a reset link can be
  sent to an address nobody proved they own), account lockout after repeated
  failures, and 2FA. The first is the natural next step and is the reason this
  ADR does not claim the reset flow is production-complete.
