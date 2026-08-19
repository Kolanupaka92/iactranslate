# 0036 — A project workspace, not a one-way scrolling wizard

**Status:** Accepted

## Context

The web UI was five stacked steps in a single scrolling column, and every step
stayed fully expanded forever — including the ones you had already finished.

Measured rather than eyeballed, on a 720px viewport:

| Step | Height | Top |
|---|---|---|
| 1 · Create project | **496px** | 148 |
| 2 · Upload | 247px | 664 |
| 3 · Assess | 144px | 931 |
| 4 · Compare clouds | 752px | 1095 |
| 5 · Generate | 118px | 1867 |

The page was **2,097px tall**. Reaching *Generate* — the entire point of the
product — meant scrolling past ~1,900px of completed work, including a 496px
form nobody would touch again. After an action you frequently landed in dead
space below the content.

Three smaller problems came out of the same audit:

- **Stale copy.** Step 4 read *"Compare all three"* with a button labelled
  *"Compare AWS · Azure · GCP"*, and the header offered *"AWS, Azure, or GCP"* —
  while the product ships five clouds and the results table correctly showed
  all five. Visible in any demo.
- **`listProjects()` was exported and never called.** Projects have persisted
  across restarts since [ADR 0025](0025-persistent-store-and-bearer-auth.md)/
  [0029](0029-durable-artifact-workspaces.md), but the UI had no history and no
  resume: every visit began at an empty form and finished work was unreachable.
- **The report request bypassed the API client.** `handleViewReport` called
  bare `fetch()` without `credentials: "include"`, so the executive report —
  the artifact a consultant shows a client — would 401 in multi-tenant mode.

## Decision

Treat this as a **workspace over a set of projects**, not a wizard run once and
abandoned.

1. **Completed steps collapse to a summary row** — a checkmark, the title, and
   what it produced (`acme-datacenter-migration · AWS`, `rvtools_sample.xlsx`,
   `Readiness 73/100`, `Recommended: DIGITALOCEAN`). They stay re-openable
   rather than disappearing, because the point is to compress finished work,
   not hide it.
2. **The result goes first.** Once a run completes, the output card sits above
   the steps. Burying the payoff under five completed steps had it arriving
   last on a page you had to scroll to the bottom of.
3. **A project sidebar**, backed by the `listProjects()` call that already
   existed, with status and headline cost per project. Clicking one resumes it.
4. **"Start over" became "New project", and no longer deletes.** Destroying the
   current project was only defensible while history was unreachable; with a
   sidebar it would silently discard work the user can now see.
5. Width raised from `max-w-3xl` (768px) to `max-w-6xl` — a data-dense tool for
   infrastructure engineers was using ~60% of a 1280px window.
6. Stale copy corrected, and the report moved onto `fetchReportHtml()` in the
   API client so it inherits the credentialed path every other call uses.

## Consequences

- The page is **1,226px instead of 2,097px (-42%)**, and step 1 collapses from
  **496px to 46px (-91%)**. The result card is visible without scrolling —
  asserted in the browser, not assumed.
- Mobile verified: the sidebar stacks above the flow, there is no
  document-level horizontal overflow, and the instance table fits its
  `overflow-x: auto` container.
- One thing I expected to find and didn't: the per-cloud "why" reasons looked
  empty for non-recommended clouds, but that is a deliberately collapsed
  `<details>` — the API returns reasons for all five.
- Not addressed here, and still real: the score columns are bare numbers
  (`1.00` / `0.64` / `0.60`) with no indication that higher is better, and
  "Moderate Lead · Margin 0.06" is jargon. Both are information-design problems
  in `RecommendTable` rather than layout, and are better fixed alongside a
  decision about what a non-expert reader should take from that table.
