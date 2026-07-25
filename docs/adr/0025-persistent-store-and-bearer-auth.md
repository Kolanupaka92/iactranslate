# 0025 — A real persistent store and real auth, scoped to what's buildable without Docker

**Status:** Accepted

## Context

A third external architecture review of this project — more accurate than the
two before it — correctly identified two CRITICAL gaps: `ProjectStore` is an
in-memory `dict` (a process restart destroys every in-flight project's state),
and the API has zero authentication (any request with network access can
read, run, or delete any project). Both are real. The review's prescribed fix
was PostgreSQL + Redis/Celery and OIDC/SAML + RBAC — the same v2.1/v2.3
roadmap items already on the books.

This environment has no Docker, no Postgres, no Redis, and no identity
provider to build or test against. Building "Postgres support" without a
Postgres instance to run `tofu validate`-equivalent verification against
would be exactly the kind of unverified claim this project has consistently
avoided (see the honesty pattern in ADRs 0013, 0020, 0023, 0024). The
question this ADR answers: what is the most real progress on these two
CRITICAL gaps buildable and *provable* with nothing but the Python standard
library?

## Decision

1. **`SqliteProjectStore`** (`api/store.py`), selected via
   `IACTRANSLATE_STORE=sqlite` (default remains `memory`, so nothing changes
   for existing users/tests). Project *metadata* — status, error, summary,
   file paths — persists to a local SQLite file (`IACTRANSLATE_DB_PATH`).
   `sqlite3` is Python stdlib: no new dependency, no external service.
   - Same public interface as the in-memory store (`create`/`get`/`delete`),
     plus a `save(project)` method both implementations expose. The in-memory
     store's `save()` is a documented no-op (its `get()` already returns the
     same mutable object every caller shares); the SQLite store's `save()` is
     load-bearing — every place `api/main.py` mutates a `Project` in place
     now calls it explicitly, otherwise the mutation would only live in that
     request's local variable.
   - **Verified two ways, not just unit-tested**: a pytest asserting a
     *second* `SqliteProjectStore` instance against the same file sees what
     the first wrote (the literal simulation of a restart), and a live
     `uvicorn` process — killed with `pkill` and restarted — that still
     answered `GET /projects/{id}` with the pre-restart project correctly.
   - **Honest boundary, stated in the module docstring**: this persists
     *metadata*, not the generated *files* — each project's workspace
     (uploads, rendered Terraform) is still a local temp directory. A node
     being recycled still loses the files. Durable object storage (S3/GCS)
     is the natural next step once a real backend exists to build against —
     this ADR doesn't claim to have solved that.
2. **Bearer-token auth** (`api/auth.py`), via `IACTRANSLATE_API_KEY` (unset =
   disabled, identical to every prior behavior). When set, every
   project-touching endpoint requires `Authorization: Bearer <key>`, checked
   with `secrets.compare_digest` (timing-safe). `/health`, `/policies`,
   `/targets` stay open — read-only capability discovery, not project data.
   - **Explicitly not presented as OIDC/SSO/RBAC.** The module docstring says
     so directly: a single shared token is a real, immediately useful
     improvement over zero authentication, fully testable without an external
     identity provider — and a stopgap, not a substitute, for when real
     multi-user identity becomes buildable.

## Consequences

- Both CRITICAL risks the review named are now real, not aspirational: state
  survives a restart (when `sqlite` is selected) and access requires a
  credential (when one is configured) — provable with tests and a live
  kill-and-restart, not just a roadmap line item.
- Neither claims more than it delivers. This is not "the Postgres milestone
  shipped early" or "OIDC is done" — the ADR title and module docstrings say
  exactly what's real (single shared token, single SQLite file, metadata not
  files) so nobody mistakes this for the full v2.1/v2.3 enterprise story.
- When Postgres/Redis/an OIDC provider become available to build and verify
  against, they compose with this rather than replace it awkwardly:
  `create_store()`'s env-var-selected-implementation pattern already
  generalizes to a third backend, and `require_api_key`'s dependency-based
  gating is the same shape a real OIDC token-validation dependency would take.
