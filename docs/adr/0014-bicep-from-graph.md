# 0014 — Bicep renders from the Infrastructure Graph, subscription-scope + module

**Status:** Accepted

## Context

[ADR 0013](0013-cloudformation-from-graph.md) proved the Infrastructure Graph
seam with one non-Terraform-shaped consumer. A second, independently-built
renderer targeting a different cloud is the real test of whether that seam
generalizes, or whether CloudFormation just happened to fit.

Bicep is Azure's native IaC DSL (compiles to ARM JSON) and has no cross-cloud
equivalent, same as CloudFormation for AWS. Unlike AWS AMIs, Azure Marketplace
images are already resolved by a static publisher/offer/sku triple —
`targets/azure/mapping.py`'s `IMAGE_REFS` / `image_reference()` — which the
Terraform and Pulumi renderers already call. There is no CloudFormation-style
"AMI resolution" problem to solve for Bicep; `image_reference()` is reused
as-is.

## Decision

1. **Render from the graph, not the plan.** `renderers/bicep.py` calls
   `build_graph(plan)` and walks VPC → subnets → security groups (their
   enriched `ingress` attribute) → instances via `placed_in`/`secured_by`
   edges, exactly like the CloudFormation renderer. It does not read
   `plan.network`/`plan.compute` for topology, only per-instance scalars
   (`instance_type`, `tier`, `environment`) already carried on
   `ComputePlan`/on the graph node.
2. **Two files, the standard subscription-scope pattern.** `main.bicep` is
   `targetScope = 'subscription'`: it creates the resource group and calls
   `resources.bicep` as a `module` scoped to that group. Bicep does not allow
   subscription- and resource-group-scoped resources in the same file, so a
   single flat file (mirroring how Terraform's `azurerm_resource_group` +
   everything else sit in one file) is not idiomatic Bicep — this ADR chose
   the real two-file convention over forcing parity with Terraform's shape.
3. **NSG association at the NIC**, not a separate association resource —
   Bicep's `Microsoft.Network/networkInterfaces` takes
   `properties.networkSecurityGroup.id` directly, simpler than the Pulumi
   Azure renderer's classic association resource.
4. **Secrets require no insecure default.** `adminPassword` is `@secure()`
   with no default (empty string only as the Bicep-required placeholder;
   deploy fails without `--parameters adminPassword=...`); `adminSshPublicKey`
   is likewise required for Linux instances. This is stricter than the Pulumi
   Azure renderer's `config.get_secret` fallback default — a genuine
   improvement, not a inconsistency to paper over.
5. **Azure-only.** `build_bicep_files` raises `RendererNotSupportedError` for
   `aws`/`gcp`.

## Consequences

- Confirms the graph seam generalizes across two independently-built,
  differently-shaped renderers (JSON template vs. a DSL that compiles to one),
  not just CloudFormation's specific shape.
- **Honest limitation:** there is no `bicep`/`az` CLI in this environment, so
  unlike Terraform (`tofu validate`) or CloudFormation (`cfn-lint`), the
  output is not locally compiled/linted. Tests instead assert structural
  properties — balanced braces/brackets, one resource block per graph node,
  correct `image_reference()` wiring, no insecure secret defaults. The
  generated README tells the operator to run `az bicep build --file
  main.bicep` before deploying.
- CDK/Kubernetes renderers, when built, follow the same pattern: consume the
  graph, resolve images/credentials by whatever mechanism that platform
  actually offers, and don't force Terraform's file layout where the
  platform's own conventions differ.
