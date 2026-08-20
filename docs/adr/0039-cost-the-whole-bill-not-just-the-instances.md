# 0039. Cost the whole bill, not just the instances

**Status:** Accepted
**Date:** 2026-08-20

## Context

`MigrationPlan.total_estimated_monthly_cost_usd` is literally
`sum(c.estimated_monthly_cost_usd for c in self.compute)` — instance cost, and
nothing else. The executive report headlined that figure as "est. cloud spend".

Measured on the realistic 25-VM RVTools estate targeting AWS, the report quoted
**$16,030/mo**. The actual list-price bill for what the plan provisions is
**$21,866/mo**. Compute was **73%** of it. Three lines were missing, and the
plan already knew all three:

| Line | Missing | Why the plan already knew |
| --- | --- | --- |
| Block storage | $868.80 | `root_volume_gib` + `extra_volumes_gib`, 10,860 GiB |
| Windows licensing | $4,835.52 | `image_key` identifies 8 Windows workloads |
| Load balancers | $131.44 | the plan *generates* 8 of them |

This is not a rounding problem. It is a 27% understatement, and **low is the
dangerous direction**. A high estimate loses an argument in a meeting. A low one
gets written into a budget, overruns six months later, and the consultant who
presented it wears that. The tool's entire claim is that its numbers are
reproducible from the source inventory; a headline number that omits a fifth of
the bill undermines every other figure in the document.

## Decision

A new `costing.py` produces a `CostBreakdown` — compute, storage, Windows
licensing, load balancers, total — from list-price, on-demand rates.

**It is an analysis engine.** Like assessment, confidence, waves and the
diagram, it reads the immutable plan and never mutates it (ADR 0007). A test
asserts the plan's serialization is byte-identical before and after.

**No committed-use discount is applied.** Reserved Instances, Savings Plans and
CUDs routinely cut compute 30–60%, and applying them would produce a much more
attractive number. Quoting a discount the customer has not actually purchased is
how an estimate becomes wrong in the customer's favour, so the report states
the discount exists and that it is deliberately excluded.

**The estimate states its own boundaries.** A "not included" list ships with
every breakdown: egress, backup/snapshots, support plans, post-migration managed
services, and the migration project itself. None are derivable from an inventory
export — nothing in an RVTools file says how much data an application egresses —
so the honest move is to name them rather than to let the total imply a quote.

**The tier table is relabelled.** It sums to compute only. Left titled "Cost
breakdown by tier" next to a larger headline, its total reads as a contradiction,
so it is now "Compute by tier", its shares are of the compute subtotal, its
footer says "Compute subtotal", and a line states the shares are not of the
headline total.

The narrative quotes the same total, so the prose and the table cannot disagree.

## Consequences

Every quoted figure rises. On the realistic estate, AWS moves $16,030 →
$21,866, Azure $12,498 → $17,633, GCP $11,033 → $16,026, OCI $8,914 → $13,098.

Compute's share varies by cloud (68–84%), so the correction is not a flat
multiplier and the relative ranking of clouds can shift — which is the point.

The recommendation engine still ranks on compute cost alone. Bringing the full
breakdown into `recommend()` would change the ranking and is the natural next
step, deliberately kept out of this change so the cost model can be reviewed on
its own before it moves a recommendation.

Rates are a maintenance burden: five clouds × three rate tables, hardcoded and
dated August 2026. They will drift. That is accepted for now — a wrong-by-drift
number beats a wrong-by-omission one, and every rate carries its source in a
comment. Live pricing already exists for compute (`--live-pricing`); extending
it to storage is the eventual fix.
