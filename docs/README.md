# docs/

*Author and owner: Sai Kolanupaka*

| File | What it is |
|---|---|
| [`operations-guide.md`](operations-guide.md) | **Operations reference** — running, extending, operating, troubleshooting; CLI/API/config; performance & security. The comprehensive source of truth. |
| [`architecture.md`](architecture.md) | **Architecture & Design** — design principles, the canonical model, request-flow diagrams, scope, assumptions, "why not …". |
| [`adr/`](adr/) | **Architecture Decision Records** — the *why* behind load-bearing decisions. |
| [`deployment.md`](deployment.md) | **Deployment & Execution** — execution model, the hosted deployment and its step-by-step procedures (MOPs: deploy, roll back, rotate secrets, enable SAML, logs), and the scale-out architecture. |
| [`roadmap.md`](roadmap.md) | **Roadmap** — shipped vs planned. |

The short entry point is the [repo README](../README.md).

## Public site (for external sharing) 🌐

The docs + investor overview are deployed as a **public static site on Vercel** —
no login, works for anyone (investors, prospects, partners):

| Page | URL |
|---|---|
| **Documentation** (operations · architecture · deployment · roadmap · ADRs) | https://docs.iactranslate.com |
| **Investor overview** | https://docs.iactranslate.com/overview |

The site redeploys automatically on every push to `main` (Vercel Git integration);
`bash scripts/deploy_public_site.sh` deploys it by hand if ever needed. This is the recommended way to share
externally — claude.ai Artifacts published via Claude Code can't be made fully
public.

## Earlier shareable copies (superseded)

Before docs.iactranslate.com existed, the docs, the operations guide and the
investor deck were also published as claude.ai Artifacts, which are private to
the owner's Claude account. They are no longer kept in sync: share
**https://docs.iactranslate.com** (and `/overview`) instead, which rebuilds on
every push. `scripts/build_docs_site.py` and `scripts/build_guide_artifact.py`
still produce single-file HTML if a standalone copy is ever needed.

Maintenance rule of thumb: new source/target → update §3, §7, §12; new env var →
§8; new endpoint → §9. New design decision →
add an [ADR](adr/); new user-facing capability → check the [README](../README.md)
and [roadmap](roadmap.md).
