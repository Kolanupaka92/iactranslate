# 0079. Email verification

**Status:** Accepted
**Date:** 2026-11-23

## Context

Anyone could register any address. That made three things weaker than they look:

- **Reset email** (ADR 0078) goes to an address nobody has shown they own.
- **SSO linking.** Single sign-on provisions or links accounts by email. If
  someone registers `ceo@corp.test` with a password before the CEO's first
  SSO sign-in, that sign-in links to the squatter's account, and the squatter's
  password still opens it. This is the "pre-account takeover" class.
- **Squatting.** An address claimed by someone else blocks its owner from
  registering.

## Decision

**What counts as proof.** An account is verified once its owner has shown they
receive mail at the address:

- the verification link sent at sign-up;
- a completed password reset, because the link arrived at the address;
- an identity provider asserting `email_verified`.

Accounts from before this change are stored with no verification time and read
as unverified; nothing is rewritten.

**The link.** It is single-use, valid for 24 hours, stored only as a hash, and
read and deleted in one transaction, like reset tokens. A new link replaces the
old one. It needs no session, because it is often opened on another device.

**Confirmed by a click, not on load.** Mail scanners and link previewers fetch
URLs. A page that verified on load would let a scanner confirm an address its
owner never saw. `/verify-email` strips the token from the address bar and
asks for one click.

**Sending.** The link goes through the same background sender as reset email
(ADR 0078), so registration takes the same time with or without a mailer. With
no mailer, the request is logged and the link is not (unless the same opt-in as
for resets is set). The UI says "we sent a link" only when `/auth/me` reports
`verification_email: true`.

**Enforcement is a switch.** `IACTRANSLATE_REQUIRE_VERIFIED_EMAIL=1` refuses to
create projects for unverified accounts. It is off by default: turning it on at
deploy would stop every pre-existing account from starting work until it found
a link it was never sent. Existing projects keep working. Sign-in is never
blocked, so an unverified user can always reach the resend button.

**SSO reclaims squatted accounts.** When a provider asserts the email and the
matching account is unverified, its password is replaced with a random one and
all of its sessions are revoked before the SSO session is created. A verified
account is left alone. When the operator has set `trust_unverified_email` and
the provider did not assert the address, nothing is verified and nothing is
revoked, because the provider proved nothing.

## Consequences

- Tested, 13 tests: a link at registration; the link works once; expired and
  replaced links are refused; resend needs a session and stops once verified;
  a reset verifies; the gate is off by default and lifts on verification; old
  accounts migrate as unverified; deletion removes pending links; SSO reclaims
  a squatted account, leaves a verified one alone, and treats an unasserted
  email as proof of nothing; the confirmation email over SMTP has a link that
  works.
- A squatter can still sit on an address until its owner acts. The owner's
  remedies are "Forgot your password?" (proves the address, revokes the
  squatter's sessions) or SSO. Expiring unverified accounts is possible later.
- The gate covers project creation only. Uploads and runs belong to projects,
  so they follow from it.
