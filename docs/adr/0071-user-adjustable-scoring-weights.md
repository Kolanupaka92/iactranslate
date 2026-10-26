# 0071. Callers can set the scoring weights, and a custom ranking says so

**Status:** Accepted
**Date:** 2026-10-26

## Context

ADR 0037 published the recommendation's weights (cost 0.45, fit 0.30, OS
0.25) so the ranking could be checked by hand. That made the next question
obvious: what if those aren't my priorities? A regulated estate with a fixed
budget line and strict platform requirements is barely cost-sensitive. A
start-up moving off a lapsed VMware contract is almost nothing *but*
cost-sensitive. One fixed weighting answers neither well, and the only way
to ask a different question was to edit the source.

The risk in exposing weights is that they become a dial for arriving at a
predetermined answer. The tool's value is that its recommendation is neutral,
so a tuned ranking must never be passed off as that.

## Decision

- `recommend(vms, weights=...)` takes an optional `ScoringWeights`. The CLI
  `recommend` and `report` commands take `--weights cost=…,fit=…,os=…`, and
  `POST /projects/{id}/recommend` takes the same three fields as an optional
  JSON body. With no weights, behaviour is exactly as before.
- Weights must be non-negative, and at least one must be above zero. They are
  **scaled to sum to 1**, so `weighted_score` stays on a 0–1 scale and
  `cost=1,fit=2,os=2` means what it says. The response carries the scaled
  values, so what was applied is never in doubt. On the CLI, a weight left
  unnamed keeps its default.
- **Eligibility still dominates.** A cloud that cannot run the estate scores
  zero under any weighting (ADR 0038). Weights choose between candidates; they
  cannot make a non-candidate one.
- **A custom ranking is labelled everywhere it surfaces.** `custom_weights`
  is true, the first note reads "Ranked under custom weights … instead of the
  defaults …", and the summary says "Custom weights". The web console already
  renders whatever weights the response carries. Supplying the default values
  explicitly is not "custom".
- Invalid weights are a usage error: exit code 2 with one readable line on
  the CLI, and a 422 from the API.

## Consequences

- An architect can now answer "what would you recommend if cost mattered
  half as much?" with the tool, instead of reweighting a spreadsheet by hand.
- The executive report built from a custom ranking carries the custom-weights
  note, so a client reading it can see whose priorities it reflects.
- The web console has no control for setting weights yet. That is UI work and
  is on the roadmap. The API and CLI are enough to use the feature today.
