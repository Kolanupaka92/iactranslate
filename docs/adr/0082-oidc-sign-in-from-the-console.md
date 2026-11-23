# 0082. OIDC sign-in from the console

**Status:** Accepted (completes [ADR 0054](0054-oidc-single-sign-on.md))
**Author:** Sai Kolanupaka
**Date:** 2026-11-23

## Context

ADR 0054 built OIDC token validation carefully: PKCE, nonce, pinned
algorithms, issuer and audience checks, required claims. The browser flow
around it was never finished:

- **The callback answered with JSON,** so a user ended on a page of JSON with
  no way into the console, and the console had no sign-in button.
- **Pending logins lived in one process's memory.** The hosted API runs up to
  four instances, so the provider's callback could reach one that never saw
  the login, and the sign-in failed at random.
- **`state` was single-use but not tied to a browser.** An attacker could start
  a login, authenticate at the provider as themselves, and send someone the
  callback link. Opening it would sign the victim into the attacker's account,
  where anything they then uploaded would belong to the attacker (login CSRF).
- **A refused code from the token endpoint** raised an unhandled error, a 500.
- **The image had no PyJWT,** so OIDC couldn't be turned on in production.

## Decision

- **Login state in the database** (`oidc_logins`, keyed by a hash of `state`,
  single-use, 10-minute expiry) whenever the deployment has one, the same
  pattern as SAML's outstanding requests (ADR 0081).
- **A state cookie:** HttpOnly, Secure, SameSite=Lax. Lax suffices because the
  provider returns the browser with a top-level GET. The callback requires the
  cookie to equal the `state` parameter (constant-time comparison) before
  anything else happens.
- **Redirects, not JSON.** Success sets the session and sends the browser to
  `/console`. Any failure, including a provider error, a missing or mismatched
  state, a refused code or an invalid token, sends it to `/console?sso=failed`
  with a generic message. The reason is logged and never shown.
- **The redirect URI defaults** to `{APP_URL}/api/v1/auth/sso/callback`,
  through the console's `/api` rewrite, so the session cookie belongs to the
  site the user is on. `IACTRANSLATE_OIDC_REDIRECT_URI` overrides it.
- **Token-endpoint failures** (HTTP errors, an unreachable provider, invalid
  JSON) become `OidcError`, so they redirect instead of crashing.
- **Account linking** is the shared `_federated_user` (ADR 0079, 0081).
- **The sign-in screen** shows one "Continue with single sign-on" button per
  configured method, labelled OIDC or SAML only when both exist.
- **The image** installs `pyjwt[crypto]`.

## Consequences

- **12 end-to-end flow tests.** They use a real token endpoint over a socket
  that checks the PKCE verifier against the challenge the browser was sent,
  and real ID-token verification; only the JWKS download is stubbed. Covered:
  the redirect and cookie, a successful sign-in, login CSRF, a cookie from
  another login, replay, a token for another login's nonce, a foreign signing
  key, a refused code, a provider error, a login finishing on another
  instance, and expiry. Each refusal was checked to happen for the intended
  reason.
- **A test bug fixed along the way.** The SSO helper in the email-verification
  tests and the SAML replay test set cookies with `domain="testserver"`, which
  httpx silently never sends. The SAML replay test had been passing because
  the cookie was missing, not because the replay was caught. Both now send the
  cookie, and the replay is refused as "already used".
- **No real provider has been exercised yet.** That's the first check when a
  customer's OIDC is configured (MOP 5).
