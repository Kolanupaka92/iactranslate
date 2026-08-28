# 0054. OIDC single sign-on

**Status:** Accepted
**Date:** 2026-08-28

## Context

No Fortune 500 accepts local username-and-password for a tool holding their
infrastructure topology. SSO is the first line of every security questionnaire,
and the architecture review named its absence the clearest procurement blocker.

I had filed it as **blocked on an identity provider**, which was wrong in the
same way the durable job queue was wrong. A real provider is needed to prove that
Okta's or Entra's particular quirks are handled. It is *not* needed to prove the
relying-party logic, and the relying party is where the vulnerabilities live —
an issuer we control lets us mint exactly the tokens an attacker would mint,
which is how OIDC libraries test themselves.

## Decision

Authorization-code flow with PKCE, as a relying party.

**Token validation is PyJWT's job, not ours.** The same review criticised the
existing auth for being bespoke security code; answering it by hand-rolling JWT
signature verification would be a worse version of the problem. This module
holds only what a library cannot decide: which issuer to trust, which claims to
require, and how a verified identity maps to a local account.

Each control exists for a specific attack:

| Attack | Control |
| --- | --- |
| Algorithm confusion — HS256 signed with the provider's *public* key | Fixed allow-list of asymmetric algorithms; `none` and every HMAC variant refused before a key is looked up |
| CSRF on the callback — victim logged into the attacker's account | Single-use `state`, consumed on use |
| ID-token replay | `nonce` bound to the login attempt |
| Authorization-code interception | PKCE S256, always |
| Issuer/audience confusion — a valid token from another tenant | `iss` and `aud` matched exactly |
| Account takeover via an unproven address | `email_verified` required, waivable only deliberately |
| Discovery-document spoofing | The document's `issuer` must match where it was fetched from |

**Just-in-time provisioning** creates a local user on first login with a random
password that is never used. An SSO account must not also be reachable by
password, or the identity provider stops being the only way in.

**Half-configured SSO fails at startup**, not at the callback — including a
missing PyJWT. Discovering the dependency is absent *after* the browser has been
redirected means the user authenticates successfully and then lands on an error
that looks like the provider's fault.

**Callback failures log detail and return none.** The message can name issuers,
audiences and claim contents: diagnostic for an operator, reconnaissance for
anyone probing the endpoint.

## Consequences

An optional extra (`pip install 'iactranslate[sso]'`), since a single-operator
deployment has no identity provider. `oidc.py` imports PyJWT lazily so the module
loads without it and the API can import unconditionally.

**What is proven and what is not.** Thirty tests against a mock issuer cover
every control above, each asserting a *refusal*. What they cannot cover is a real
provider: Entra does not always emit `email_verified` (hence the waiver), Okta
and Auth0 differ on `aud` when multiple audiences are present, and some providers
return `email` only from the userinfo endpoint rather than the ID token. **A
smoke test against one real provider is still outstanding**, and this ADR should
not be read as claiming otherwise.

Also still open: no SCIM, so users are provisioned on first login and never
deprovisioned — a departed employee's local account outlives their directory
entry until someone deletes it. No group or role claims are consumed, so an SSO
user lands with no project access until an admin grants it (ADR 0050). And the
`LoginStore` is in-process like the rest of the single-node runtime, so a login
that starts on one replica and returns to another will fail its `state` check.
