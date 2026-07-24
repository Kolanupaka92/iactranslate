# 0015 — AWS CDK renders from the Infrastructure Graph via L1 constructs, reusing CloudFormation's AMI logic

**Status:** Accepted

## Context

CloudFormation ([0013](0013-cloudformation-from-graph.md)) and Bicep
([0014](0014-bicep-from-graph.md)) each proved the Infrastructure Graph seam
for a template-shaped IaC format. AWS CDK is a different shape again: a real
imperative program (Python, in this codebase's case) that *synthesizes* a
CloudFormation template rather than being one. It is also the closest existing
renderer to CloudFormation itself — both target the same AWS resource model —
which raises a specific design question: does CDK get its own AMI-resolution
logic, or does it reuse CloudFormation's?

CDK's `aws_ec2` module offers two families of constructs: **L2** (`ec2.Vpc`,
`ec2.Instance`) which make opinionated infrastructure decisions on the
caller's behalf (auto-created NAT gateways per AZ, default subnet
configurations, an implicit security group unless overridden), and **L1**
(`ec2.CfnVpc`, `ec2.CfnInstance`, …) which are a near 1:1 mirror of the
CloudFormation resource model — the same shape `renderers/cloudformation.py`
already builds.

## Decision

1. **Render from the graph, using L1 (`Cfn*`) constructs.** `renderers/cdk.py`
   walks `build_graph(plan)` exactly like the CloudFormation renderer —
   VPC → subnets → security groups (`ingress` attribute) → instances via
   `placed_in`/`secured_by` — and emits `ec2.CfnVpc`, `ec2.CfnSubnet`,
   `ec2.CfnSecurityGroup`, `ec2.CfnInstance`, etc. **L2 constructs were
   deliberately rejected**: their opinionated defaults (e.g. `ec2.Vpc`
   auto-creates a NAT gateway per AZ and its own subnet layout) would silently
   diverge from what the plan actually specifies, defeating the point of a
   renderer that is supposed to express the plan faithfully.
2. **Reuse CloudFormation's AMI-resolution helpers verbatim** —
   `_ami_dynamic_ref`/`_ami_parameter_name` from `renderers/cloudformation.py`
   are imported, not reimplemented. `CfnInstance.image_id` accepts the same
   SSM dynamic-reference string CloudFormation does, and `CfnParameter` is the
   CDK-native equivalent of a template `Parameter` — so the two renderers
   solve image resolution identically, because at the L1 level they are
   solving the identical problem (there is no CDK-specific mechanism to
   invent around).
3. **Two files, the standard CDK app layout**: `app.py` (entry point,
   instantiates the stack with the target region as the environment) and
   `stack.py` (the `Stack` subclass with the actual resources) — plus
   `requirements.txt` and `cdk.json` so the output is `cdk deploy`-ready
   without hand-editing.
4. **AWS-only.** `build_cdk_files` raises `RendererNotSupportedError` for
   `azure`/`gcp`.

## Consequences

- Confirms the graph seam holds across three structurally different renderer
  shapes: a JSON template (CloudFormation), a DSL that compiles to one
  (Bicep), and an imperative program that synthesizes one (CDK) — the same
  `build_graph(plan)` call feeds all three with no changes to `MigrationPlan`.
- Concretely demonstrates that "renders from the graph" and "reuses
  cloud-specific mapping logic where it genuinely is the same logic" are not
  in tension: CDK's AMI handling isn't independently invented, it calls
  CloudFormation's functions directly — a real code-reuse relationship
  between two renderers, not just a shared IR.
- **Honest limitation:** there is no `aws-cdk-lib`/`cdk` CLI in this
  environment, so unlike Terraform (`tofu validate`), the output is not
  locally synthesized. Tests instead `compile()` the generated Python
  (proving it's syntactically valid) and assert structural properties —
  construct counts, correct AMI wiring matching the CloudFormation renderer's
  own resolution for the same image keys. The generated README tells the
  operator to run `cdk synth` before `cdk deploy`.
- Kubernetes, when built, remains the one graph-consuming renderer target not
  yet attempted; it is a different resource model entirely (Pods/Services/
  Deployments, not VPCs/instances) and would need its own mapping from the
  graph's VM-shaped nodes rather than reusing AWS-specific logic.
