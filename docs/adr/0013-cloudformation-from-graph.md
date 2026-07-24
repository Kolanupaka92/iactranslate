# 0013 — CloudFormation renders from the Infrastructure Graph, and resolves AMIs via SSM

**Status:** Accepted

## Context

[ADR 0010](0010-infrastructure-graph.md) introduced the Infrastructure Graph as
"the intended seam for future renderers" but only proved it with one consumer
(the architecture diagram) — everything that actually emits IaC (Terraform,
Pulumi) still renders from `MigrationPlan`. A second, different kind of
consumer is the real test of whether the seam is load-bearing rather than
speculative.

CloudFormation is also the first renderer with no cross-cloud equivalent
(Azure/GCP have no CloudFormation), and it lacks Terraform's `data "aws_ami"` —
there is no built-in CloudFormation construct that resolves "the newest AMI
matching a name filter" the way `AMI_FILTERS` (`targets/aws/mapping.py`) does
for Terraform/Pulumi.

## Decision

1. **Render from the graph, not the plan.** `renderers/cloudformation.py` calls
   `build_graph(plan)` and walks its nodes/edges — VPC → subnets → route
   tables/NAT, security groups with their enriched `ingress` attribute, and
   instances via `placed_in`/`secured_by` edges — to build the template. It
   does not read `plan.network` or `plan.compute` directly. This required
   enriching the graph first (ADR 0010's nodes previously carried only an
   ingress *count* and no `image_key`/volume sizes — insufficient for a
   renderer, sufficient only for the diagram).
2. **Resolve AMIs via AWS-published SSM public parameters**
   (`{{resolve:ssm:/aws/service/...}}`), a real, standard CloudFormation
   dynamic reference, for the OSes AWS/Canonical publish one for (Amazon
   Linux 2, Windows 2016/2019/2022, Ubuntu 22.04). For OSes with no public SSM
   alias (RHEL, SLES, CentOS), emit a plain `AWS::EC2::Image::Id` template
   **Parameter** with no default — the operator supplies an AMI id at deploy
   time (`--parameter-overrides AmiIdRhel9=ami-...`). This is not a
   reimplementation of `AMI_FILTERS`; CloudFormation has no equivalent to a
   name-filter lookup, so the two renderers resolve images by genuinely
   different mechanisms.
3. **AWS-only.** `build_cloudformation_files` raises
   `RendererNotSupportedError` for `azure`/`gcp`, mirroring how the Pulumi
   renderer signals unsupported targets.

## Consequences

- Confirms the graph is a real seam: a second renderer, built independently of
  Terraform/Pulumi's plan-shaped assumptions, consumes it with no changes to
  `MigrationPlan` or the diagram.
- The template is deterministic JSON (`json.dumps`, dict insertion order),
  validated in tests as structurally valid CloudFormation (every resource has
  `Type`+`Properties`, every `Ref` resolves) and checked with `cfn-lint`.
- **Honest limitation:** SSM parameter resolution happens at CloudFormation
  deploy time, not at generation time — unlike Terraform's `tofu validate`,
  there is no equivalent offline proof that a given template deploys cleanly
  short of an actual `aws cloudformation deploy` (or `cfn-lint`, which checks
  structure, not live AWS state).
- Bicep/CDK/Kubernetes renderers, when built, follow the same pattern: consume
  the graph, resolve images by whatever mechanism that platform actually offers.
