# docs/

| File | What it is |
|---|---|
| [`operations-guide.md`](operations-guide.md) | **Operations reference** — running, extending, operating, troubleshooting; CLI/API/config; performance & security. The comprehensive source of truth. |
| [`architecture.md`](architecture.md) | **Architecture & Design** — design principles, the canonical model, request-flow diagrams, scope, assumptions, "why not …". |
| [`adr/`](adr/) | **Architecture Decision Records** — the *why* behind load-bearing decisions. |
| [`deployment.md`](deployment.md) | **Deployment & Execution** — execution model, stages/state machine, single-node (shipped) + a reference architecture for scale. |
| [`roadmap.md`](roadmap.md) | **Roadmap** — shipped vs planned. |

The short entry point is the [repo README](../README.md).

## Public site (for external sharing) 🌐

The docs + investor overview are deployed as a **public static site on Vercel** —
no login, works for anyone (investors, prospects, partners):

| Page | URL |
|---|---|
| **Documentation** (operations · architecture · deployment · roadmap · ADRs) | https://iactranslate-docs.vercel.app |
| **Investor overview** | https://iactranslate-docs.vercel.app/overview |

Redeploy after editing any doc or the deck: `bash scripts/deploy_public_site.sh`
(needs the `vercel` CLI logged in). This is the recommended way to share
externally — claude.ai Artifacts published via Claude Code can't be made fully
public.

## Shareable web versions (Artifacts — keep in sync)

Two published, shareable web pages back this repo:

| Artifact | Source | URL |
|---|---|---|
| **Documentation site** — *for external sharing* (HTML) | all of `docs/` → `scripts/build_docs_site.py` | https://claude.ai/code/artifact/ecc2a04b-904e-431b-9ab0-7a8856963c20 |
| **Operations Guide** (HTML) | `operations-guide.md` → `scripts/build_guide_artifact.py` | https://claude.ai/code/artifact/926d0f20-5a9f-4e4b-ba98-89670477d531 |
| **Investor Deck** (HTML) | [`investor-deck.html`](investor-deck.html) | https://claude.ai/code/artifact/b44cbc29-1e33-4519-a921-2387323fa33d |

**Sharing externally:** the **Documentation site** bundles *every* doc (operations,
architecture, deployment, roadmap, all ADRs) into one page with **all links
resolved in-page** — no GitHub access needed, so it's safe to send to prospects,
investors, or partners. The standalone Operations Guide links back to the (private)
repo, so use it internally. Regenerate the site after editing any doc:

```bash
python scripts/build_docs_site.py /tmp/docs-site.html
```

then re-publish to the **same** Documentation-site Artifact URL. (Artifacts are
private until you share them from the page's **Share** menu.)

**Operations Guide:** it is published as **self-contained HTML** (not raw
Markdown) so the table-of-contents anchors and cross-document links actually
resolve in the standalone page. Regenerate after editing the Markdown:

```bash
python scripts/build_guide_artifact.py /tmp/operations-guide.html
```

then re-publish that file to the **same** Artifact URL (the link never changes).
The script gives every heading a GitHub-style id (TOC works) and rewrites
cross-doc links to absolute GitHub URLs.

**Investor Deck:** the source is [`docs/investor-deck.html`](investor-deck.html) —
edit it directly and re-publish to its Artifact URL.

Do the re-publish in the **same change** as the source edit so the pages don't drift.

Maintenance rule of thumb: new source/target → update §3, §7, §12; new env var →
§8; new endpoint → §9 — in **both** the doc and the page. New design decision →
add an [ADR](adr/); new user-facing capability → check the [README](../README.md)
and [roadmap](roadmap.md).
