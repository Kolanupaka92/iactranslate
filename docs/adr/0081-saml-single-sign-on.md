# 0081. SAML single sign-on

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-23

## Context

OIDC single sign-on exists (ADR 0054), but many enterprises' identity providers
speak SAML and not OIDC: older ADFS farms, and Okta, Ping or Entra set up that
way by policy. SAML is also where most real-world SSO bypasses have come from.
XML signatures sign a *part* of a document, and an implementation that verifies
one element and then reads another is the classic failure (signature wrapping).

## Decision

**A service provider on signxml, not python3-saml.** python3-saml is the usual
choice, but it depends on the `xmlsec` C library. That library has no wheel for
Python 3.15 (the image's base and a CI job), and it has a history of libxml2
version clashes with lxml. signxml is pure Python over lxml and cryptography.
python3-saml's value beyond signature checking is protocol validation, which is
written here as explicit, individually tested rules.

**SP-initiated only.** The request goes by HTTP-Redirect and the response comes
back by HTTP-POST to `/auth/saml/acs`.

**Validation rules,** each closing a known class of bypass:

- **Only the signed copy is read.** signxml returns the element its signature
  covers (the assertion, or a response containing it), and identity is read
  from that element only.
- **Exactly one assertion and no `EncryptedAssertion`.** This closes the
  wrapping variant that adds a second, unsigned assertion.
- **A pinned certificate.** With the configured certificate passed in, signxml
  verifies with it and ignores any certificate carried in the message (checked
  in its source). Signature algorithms are limited to RSA or ECDSA with SHA-256
  or stronger; signxml already refuses SHA-1. Several certificates can be
  configured for a key rotation.
- **No DTDs.** A DOCTYPE is refused, and the parser resolves no entities and
  fetches nothing (XXE, entity expansion).
- **Bound to the browser and the request.** `/auth/saml/login` stores the
  request id in the database (any instance can receive the response) and in a
  Secure, HttpOnly, SameSite=None cookie. The assertion's `InResponseTo` must
  match that cookie, and the request is consumed before validation. This
  refuses unsolicited (IdP-initiated) responses, replays, and login CSRF, where
  an attacker's valid assertion is posted from the victim's browser.
- **Issuer, audience, recipient and time.** Each is checked, with 2 minutes of
  clock skew. A missing `NotOnOrAfter` counts as expired.
- **Trusted email domains.** `IACTRANSLATE_SAML_EMAIL_DOMAINS` is required, and
  assertions for any other domain are refused. Without it, a configured IdP
  could assert any address, a personal Gmail account included, and sign in as
  that person.

**Accounts work as with OIDC.** The shared `_federated_user` helper provisions
the account just in time and treats the assertion as proof of the address. It
also reclaims an unverified account someone registered first (ADR 0079).

**Failure reveals nothing.** A refused response sends the browser to
`/console?sso=failed` with a generic message. The specific reason goes to the
log, where it helps the operator and a prober doesn't see which check they
tripped.

**Console.** `GET /auth/methods` tells the sign-in screen whether to show
"Continue with single sign-on".

## Consequences

- **35 tests against a mock IdP that signs real assertions.** They cover:
  - sign-in with a signed assertion, with a signed response, and with the email
    in an attribute;
  - certificate rotation;
  - unsigned, foreign-key and altered assertions;
  - wrapping, a weak algorithm, DOCTYPE/XXE and an encrypted assertion;
  - wrong issuer, audience, recipient or time window;
  - a foreign domain, a lookalike domain suffix and a missing email;
  - an IdP error status;
  - a response to another request, an unsolicited response, login CSRF and
    replay;
  - reclaiming a squatted account;
  - metadata, configuration and time parsing.
- **The refusal reasons were checked to be the intended ones,** not just "a
  refusal".
- **Interoperability with Mock SAML (BoxyHQ, mocksaml.com)**, run in a browser
  against a local API: it parsed our AuthnRequest, and its signed response
  signed in `jackson@example.com` with the address verified.
- **Not done:** encrypted assertions, signed AuthnRequests, IdP-initiated
  sign-in and single logout. None is needed for the sign-in path, and each can
  be added when an IdP requires it.
- **A pinned certificate that expires stops sign-in** until it's rotated, which
  is visible in the log.
- **Not yet configured on the hosted deployment,** which would need a real
  customer IdP.
- **The OIDC console flow isn't wired.** Its callback still returns JSON, and
  its login state is per-instance memory, where SAML's is in the database.
  That's a follow-up for whenever OIDC is enabled on a multi-instance
  deployment.
