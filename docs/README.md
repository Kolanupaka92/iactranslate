# docs/

| File | What it is |
|---|---|
| [`operations-guide.md`](operations-guide.md) | **Operations reference** — running, extending, operating, troubleshooting; CLI/API/config; performance & security. The comprehensive source of truth. |
| [`architecture.md`](architecture.md) | **Architecture & Design** — design principles, the canonical model, request-flow diagrams, scope, assumptions, "why not …". |
| [`adr/`](adr/) | **Architecture Decision Records** — the *why* behind load-bearing decisions. |
| [`deployment.md`](deployment.md) | **Deployment & Execution** — execution model, stages/state machine, single-node (shipped) + a reference architecture for scale. |
| [`roadmap.md`](roadmap.md) | **Roadmap** — shipped vs planned. |

The short entry point is the [repo README](../README.md).

## Shared web version (keep in sync)

`operations-guide.md` also has a rendered, shareable web page (an Artifact) for
handing to teammates or leadership:

- **URL:** https://claude.ai/code/artifact/926d0f20-5a9f-4e4b-ba98-89670477d531

**When you edit `operations-guide.md`, re-publish the web page so the two don't
drift.** The page is generated from the doc's content; ask Claude Code to "update
the operations-guide Artifact" (it republishes to the **same URL** — the link
never changes). Do this in the same change as the doc edit.

Maintenance rule of thumb: new source/target → update §3, §7, §12; new env var →
§8; new endpoint → §9 — in **both** the doc and the page. New design decision →
add an [ADR](adr/); new user-facing capability → check the [README](../README.md)
and [roadmap](roadmap.md).
