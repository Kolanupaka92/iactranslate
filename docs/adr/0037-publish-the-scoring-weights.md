# 0037 — Publish the scoring weights

**Status:** Accepted

## Context

Design principle #3 says the recommendation's *"weights are explicit and
inspectable; no vendor gets a thumb on the scale."* That property is the main
reason to trust this ranking over a cloud vendor's own migration tool, which
will never recommend a competitor.

The weights were module constants in `recommend.py` (`W_COST = 0.45`,
`W_FIT = 0.30`, `W_OS = 0.25`) and appeared in no API response. So the claim
was true of the *implementation* and not of the *product*: the one question a
cloud architect actually asks — "how are you weighting this?" — could only be
answered by reading the source, which an evaluator will not do.

The audience matters here, and it is not a generalist. This table is read by
infrastructure engineers and cloud architects. The right response to "these
scores are unclear" is therefore **more precision, not simplification** —
an architect does not want the numbers hidden behind a verdict, they want to
check the arithmetic.

Two smaller legibility problems came from the same place:

- **Bare score columns.** `1.00 / 0.64 / 0.60` with no indication that the
  scale is 0–1 or that higher is better. Unreadable for an expert too — this
  is missing information, not missing hand-holding.
- **"Margin 0.06"** named neither what it measured, against whom, nor on what
  scale.

## Decision

Ship the weights in the response and show them in the table.

- `Recommendation.weights` (`ScoringWeights`) carries the exact multipliers,
  and `Recommendation.runner_up` names the cloud `margin` is measured against.
- The table states the formula — *"Weighted score = cost × 0.45 + fit × 0.30 +
  OS × 0.25. All component scores are 0–1, higher is better. Weights are fixed
  and identical for every cloud."* — and each score column header carries its
  own multiplier (`Cost ×0.45`).
- `margin 0.06` becomes `0.06 ahead of OCI`.
- Cost shows annual alongside monthly. `annual_cost_usd` was already in the
  payload and unused, and infrastructure budgets are annual.

A test asserts every reported `weighted_score` is reproducible from the
published weights, so the two can never drift: if someone retunes the scoring
and forgets the response, the suite fails rather than the UI quietly lying.

## Consequences

- The ranking is now checkable by hand from what the API returns —
  `0.45 × 1.00 + 0.30 × 0.64 + 0.25 × 0.60 = 0.79` — which is what makes
  "unbiased" a verifiable claim rather than an assertion.
- The stated design principle is now true of the product, not just the source.
- Exposing the weights invites the obvious next request: **letting a user
  change them.** A cost-insensitive regulated estate weights fit and OS
  affinity far higher than 0.45/0.30/0.25. That is a real feature and a real
  scope decision (it changes the recommendation from one answer into a model),
  so it is deliberately not bundled here.
- One implementation note worth recording: `capitalize` was applied to the
  whole decisiveness badge, which title-cased the sentence into *"Moderate Lead
  · 0.06 Ahead Of OCI"*. It is now scoped to the single word that needs it.
