# 0074. Share the decisions, not the renderer

**Status:** Accepted
**Date:** 2026-11-14

## Context

The roadmap carried "migrate the rest of Terraform/Pulumi's resource generation
onto the graph" since ADR 0010. ADR 0016 is why that mattered: placement was
decided in two places, the two disagreed, and three renderers shipped instances
collapsed onto one subnet without any test noticing.

Before doing the migration, we audited what it would actually fix. The question
is not "do Terraform and Pulumi read the graph?" but "do they re-derive anything
the graph also decides?" Most of what they read (machines, sizes, security group
rules, load-balancer listeners and targets) is data on the plan. They render it;
they don't decide it. Moving that onto the graph changes where data is read
from and fixes nothing.

One real decision *was* duplicated: **does a machine get its own public
address?** The rule is "public subnet, and no load balancer in front". It was
written out in six Terraform templates, twice in Pulumi and once in Bicep, and
the graph did not record it. Pulumi-for-Azure and Bicep tested "fronted" a
different way ("has a backend pool reference") from the templates ("is a load
balancer target"). All of them agreed today, which is exactly how ADR 0016's
bug looked before it shipped.

## Decision

- `graph.public_instances(plan)` is the one definition, alongside
  `assign_subnets`, and the graph records it on each instance node
  (`public_ip`).
- Terraform (both layouts, all clouds), Pulumi and Bicep consume it. No
  renderer re-derives it.
- The wholesale migration is **not** done. If another decision turns out to be
  re-derived in several renderers, it gets the same treatment, one decision at a
  time, each proven by byte-identical output.

## Verification

- Every renderer × every target was rendered for two estates (68 outputs plus
  the graph). After the change, all renderer output is **byte-identical**. The
  graphs differ only by the new attribute.
- That check caught a mistake in the change itself before it shipped: renaming
  Pulumi's per-machine `public` flag to a set left one test reading the set,
  which would have given every Azure NIC a reference to a public IP that didn't
  exist.
- `test_public_ip_decision.py` asserts that Terraform, the `for_each` layout,
  Pulumi and Bicep give a public address to exactly the machines
  `public_instances` names, on Azure and GCP.
