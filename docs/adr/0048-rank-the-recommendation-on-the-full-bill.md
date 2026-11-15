# 0048. Rank the recommendation on the bill the customer pays

**Status:** Accepted
**Date:** 2026-08-28

## Context

ADR 0039 made every surface quote the itemized monthly total — executive report,
generated README, `main.tf` header, CLI, API summary, budget policy — and
deliberately left the recommendation engine ranking on
`plan.total_estimated_monthly_cost_usd`, which is compute alone. The reasoning
was that changing a ranking should be a decision, not a side effect of a costing
change.

That deferral has now been sitting long enough to be its own defect: the
recommendation argued from a number the customer would never see, next to a
report quoting a different one.

The gap is not a constant that cancels out of a comparison:

| Cloud | Compute | Full | Uplift |
| --- | ---: | ---: | ---: |
| DigitalOcean | $5,976 | $7,158 | **+20%** |
| AWS | $16,030 | $21,866 | +36% |
| Azure | $12,498 | $17,633 | +41% |
| GCP | $11,033 | $16,026 | +45% |
| OCI | $8,914 | $13,098 | **+47%** |

Storage rates differ per cloud, Windows licensing scales with the Windows share,
and load-balancer pricing varies — so ranking on compute weights the clouds
differently than the customer's invoice does.

## Decision

`recommend()` ranks on `estimate_costs(plan).total`. `CloudScore` also carries
`compute_monthly_cost_usd`, so a reader can see both figures and check one
against the other rather than taking the total on faith — the same reasoning as
publishing the scoring weights (ADR 0037).

## Consequences

On both fixtures the *ordering* of eligible clouds is unchanged (OCI, GCP, Azure,
AWS) — but the scores and margins move, because cost scores are ratios and the
full-cost view compresses the gaps. The 7-VM estate now reports a margin of
**0.001** between OCI and GCP: a genuine near-tie that the existing `close`
decisiveness flag correctly surfaces, and that the compute-only ranking had
overstated.

An unchanged winner is the right outcome to report honestly. The change was made
because ranking on a number nobody is billed for is wrong regardless of whether
it happens to agree on today's fixtures, and because the gap varying from 20% to
47% across clouds means agreement is a coincidence rather than a property.

Annual cost follows the itemized total, since a 12x multiple of the wrong number
is still the wrong number.
