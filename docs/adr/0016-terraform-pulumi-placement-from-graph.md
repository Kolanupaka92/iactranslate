# 0016 — Terraform/Pulumi get subnet placement from the graph; a real bug this surfaced

**Status:** Accepted

## Context

[ADR 0010](0010-infrastructure-graph.md) deferred migrating Terraform and
Pulumi onto the Infrastructure Graph, since they already rendered correctly
from `MigrationPlan` and there was no forcing function to change that.
Auditing the two placement algorithms that existed side by side — the one
`build_graph()` used to draw `placed_in` edges (for the diagram, and later
CloudFormation/Bicep/CDK) and the one `generator/renderer.py::_assign_subnets`
used for Terraform/Pulumi — found they **did not agree**, and one of them was
wrong:

- `generator/renderer.py::_assign_subnets` (Terraform, and Pulumi via reuse)
  correctly round-robins instances of a tier across every subnet of that tier
  — spreading instances across availability zones.
- `graph.py`'s `first_subnet_of_tier` used `dict.setdefault`, which only ever
  records the *first* subnet seen for each tier. Every instance of a tier was
  placed on that one subnet regardless of how many subnets of that tier
  existed. **Every renderer that consumes the graph — the diagram,
  CloudFormation, Bicep, and CDK — was concentrating all public instances on
  one public subnet and all private instances on one private subnet**, with
  no AZ spread, while Terraform and Pulumi output for the identical plan
  correctly spread them.

This was not a hypothetical: `tests/test_graph.py` never asserted more than
"every instance has *a* `placed_in` edge," so the collapse-to-one-subnet
behavior shipped in three renderers (0013, 0014, 0015) and the diagram without
being caught.

## Decision

1. **One placement algorithm, in one place.** `graph.assign_subnets(plan)` is
   now the single function that decides which subnet an instance lands in
   (the round-robin-across-AZs logic, moved from `generator/renderer.py`
   verbatim — it was already the correct algorithm, just not where every
   renderer could reach it). `build_graph()` calls it to draw `placed_in`
   edges.
2. **Terraform/Pulumi now read placement from the graph, not a local
   heuristic.** `generator/renderer.py::_assign_subnets` (same name, same
   signature, so `renderers/pulumi.py`'s existing import needed no change)
   now calls `build_graph(plan)` and reads back `placed_in` edges, instead of
   re-deriving the mapping from `plan.network.subnets` directly. Likewise
   `sg_resource` (security-group name → resource name) is now read from the
   graph's security-group nodes via a new `_sg_resource_map`, rather than a
   separate direct lookup against `plan.network.security_groups`.
3. **Templates and other plan-derived fields are unchanged.** Terraform's
   Jinja templates and Pulumi's per-cloud builders still read non-topology
   fields (cost, tags, `external_id`, image resolution) straight from
   `plan.compute`/`ComputePlan` — those aren't topology and don't belong on
   the graph. Only the *placement relationship* (which subnet, which security
   group) now flows through the graph.

## Consequences

- **Fixes a real, shipped bug**: CloudFormation, Bicep, CDK, and the
  architecture diagram now correctly spread instances across every subnet of
  a tier instead of concentrating them on one, for any plan with more than
  one subnet per tier (i.e. any plan with more than one AZ).
- **Verified non-regression for Terraform/Pulumi**: this migration was
  proven safe by generating output for all 3 clouds × 2 renderers before and
  after the change and diffing them — byte-identical in every case, because
  the algorithm itself didn't change, only where it lives. This is the
  strongest evidence available short of `tofu validate` (which needs registry
  network access this environment doesn't have) that nothing broke.
- Terraform and Pulumi are not fully "on the graph" in the ADR 0010 sense —
  they still render their actual resource bodies from `MigrationPlan`, not by
  walking graph nodes the way CloudFormation/Bicep/CDK do. What moved onto the
  graph is specifically the *placement decision*, which is the part that had
  drifted into two disagreeing implementations. Migrating the rest of
  Terraform/Pulumi's resource generation onto the graph remains future work,
  now with no known correctness gap forcing it.
- New regression tests lock in both properties: instances of a multi-subnet
  tier land on more than one subnet, and Terraform's `_assign_subnets` output
  is asserted equal to `graph.assign_subnets`, so the two can never silently
  diverge again.
