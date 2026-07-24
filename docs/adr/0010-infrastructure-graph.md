# 0010 — An Infrastructure Graph IR between plan and renderers

**Status:** Accepted

## Context

`MigrationPlan` is a good *planning* artifact, but it is organized for planning
(a list of compute + a network block), not for walking a topology. Consumers that
care about structure — the architecture diagram today, and future renderers like
CloudFormation, Bicep, CDK, or Kubernetes — each re-derive the same graph
(what's in which subnet, what's secured by what) from the plan. That is duplicated
logic and couples every such consumer to the plan's shape.

## Decision

Introduce an **Infrastructure Graph** (`graph.py`): a renderer-neutral topology IR
of typed **nodes** (VPC, subnet, security group, instance) and **edges**
(`contains`, `placed_in`, `secured_by`), derived deterministically from the plan
by `build_graph(plan)`. It carries no cloud syntax. The architecture diagram now
renders **from the graph** (its natural consumer), and the graph ships as
`graph.json` in every package.

## Consequences

- Structure is defined once. A renderer walks the graph instead of re-deriving
  topology from the plan — proven by the CloudFormation
  ([0013](0013-cloudformation-from-graph.md)), Bicep
  ([0014](0014-bicep-from-graph.md)), and AWS CDK
  ([0015](0015-cdk-from-graph.md)) renderers, all three of which consume
  `build_graph(plan)` rather than the plan directly. Kubernetes is the same
  seam. Terraform/Pulumi now share the graph's placement decision too — see
  [0016](0016-terraform-pulumi-placement-from-graph.md), which also documents
  a real bug that auditing this shared seam caught.
- `graph.json` is a reproducible, tool-agnostic description of the target
  topology — useful for diffing, external visualization, or policy tooling.
- **Scope, honestly:** Terraform and Pulumi still render from the `MigrationPlan`
  today (they work and are proven by `tofu validate`); migrating them onto the
  graph is incremental and unforced. This ADR establishes the IR and proves it
  with a real consumer (the diagram), not a big-bang renderer rewrite.
- The graph is derived, not authored — it stays consistent with the plan by
  construction, and inherits the [immutable-plan](0007-immutable-plan.md) guarantee.
