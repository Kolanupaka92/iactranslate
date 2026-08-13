# 0027 — Multi-tenancy: user accounts, session auth, and per-project ownership

**Status:** Accepted

## Context

The product direction is a hosted, multi-tenant SaaS. Two things made that
impossible, and one of them was a defect in what we had already shipped.

**There was no notion of who was calling.** [ADR 0025](0025-persistent-store-and-bearer-auth.md)
added a single shared bearer token. That is a real improvement over no
authentication at all, but it authenticates a *deployment*, not a *person*:
everyone holding the token sees every project. `Project` had no owner field, so
there was nothing to scope a query by even if we had wanted to.

**The bearer token could not secure the product's own UI.** The web app exposes
the generated Terraform and the executive report as ordinary links — `<a href>`
navigations the browser performs itself. There is no `fetch` call on those, so
there is nowhere to attach an `Authorization` header. Turning on
`IACTRANSLATE_API_KEY` therefore 401'd the entire web UI. This was not a missing
line of code; it is a structural property of bearer tokens, and it means the
scheme in ADR 0025 could never have covered the whole product.

## Decision

**Session cookies, not bearer tokens.** A cookie is attached by the browser to
navigations as well as `fetch` calls, which is exactly the property the download
and report links need. Cookies are `httponly` (XSS cannot read them),
`samesite=lax` (CSRF-resistant, while still allowing the top-level navigations
that make the links work), and `secure` unless explicitly disabled for local
http testing.

1. **Accounts** (`api/accounts.py`). Passwords are PBKDF2-HMAC-SHA256 with a
   per-user random salt at OWASP's recommended 600k iterations — stdlib
   `hashlib`, no new dependency. The stored format is self-describing
   (`pbkdf2_sha256$<iterations>$<salt>$<hash>`) so the cost can be raised later
   without invalidating existing passwords. **Session tokens are stored
   hashed**, so a database leak yields no usable session.
   - Login failures do not distinguish unknown-email from wrong-password, and
     the KDF runs even for unknown emails so response time doesn't leak account
     existence either. Duplicate registration returns the same generic error
     rather than confirming the address is taken.
2. **Ownership** (`Project.owner_id`). Every project belongs to one user, and
   `_require_project` refuses anything owned by someone else.
   - **404, not 403.** A 403 would confirm the id exists, letting an attacker
     enumerate other tenants' projects. The caller cannot tell "no such
     project" from "not yours".
3. **Backward compatible by construction.** `IACTRANSLATE_AUTH=session` turns
   multi-tenancy on; unset (the default) leaves the CLI and single-user
   self-hosted deployments exactly as they were, with `owner_id = None` acting
   as a single implicit operator. Existing SQLite databases are migrated
   additively on open (`ALTER TABLE … ADD COLUMN owner_id`), so a file written
   before this change keeps working and its projects come back as
   single-tenant.

### Three cross-tenant leaks the tests caught

Writing the boundary tests first found three places where the check was missing
entirely. All three are the same mistake — reaching for a resource by id
*without* going through the ownership check — and all three are worth recording
because they are the shape of bug this ADR exists to prevent:

- **`DELETE /projects/{id}` called `store.delete(pid)` directly.** Any signed-in
  user could destroy another tenant's project by guessing its id. This was
  destructive, not just a read leak.
- **`GET /jobs/{job_id}` returned the job's project summary** without checking
  who owned that project. A job id is a handle to a project and now inherits
  its access check.
- **`GET /audit` returned the whole trail**, naming every tenant's project ids
  and activity. It is now filtered to projects the caller owns.

## Consequences

- Two customers can share one deployment without seeing each other's
  infrastructure inventory — verified by unit tests, and by a live server where
  a second registered user got 404 on another user's project for read, delete,
  run, assess, recommend, report, download, and jobs.
- The web UI works with auth on for the first time. Verified end-to-end in a
  real browser: register → create → upload → generate → **download a 22KB ZIP
  over a plain navigation**, the exact case the bearer scheme could not serve.
- `allow_credentials=True` is now required on CORS, which means
  `IACTRANSLATE_CORS_ORIGINS` must name real origins — browsers reject
  credentialed requests against a `*` wildcard. This is a deployment constraint,
  not an optional tightening.
- **Explicitly not done, and not claimed:** OIDC/SSO (this is
  username/password), organizations or teams (one user is one tenant, with no
  sharing), password reset and email verification, and per-user rate limiting.
  Generated *files* also still live in local temp directories — object storage
  remains the open item from ADR 0025, and it matters more now that multiple
  tenants share a node.
