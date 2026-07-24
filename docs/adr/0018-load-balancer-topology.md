# 0018 — Load balancer topology: model once, render six ways

**Status:** Accepted

## Context

Every renderer modeled a tier's instances as independent VMs with no
relationship between them. That's a real gap, not a cosmetic one: a
two-instance web tier in AWS/Azure/GCP is not two unfronted public instances
in production — it sits behind a load balancer. Modeling that only in the
diagram (or only for one cloud) would be half-measures; the point of this
change is that the *decision* ("this tier needs a load balancer") is made
once, in one deterministic place, and every renderer + the diagram draws
from the same decision.

## Decision

1. **The decision lives in `agents/network.py`, not per-renderer.**
   `_plan_load_balancers` groups `plan.compute` by `(tier, environment,
   subnet_tier)`; any group with **more than one instance** gets a
   `LoadBalancerPlan` (`models.py`) on `NetworkPlan.load_balancers`. A
   single-instance tier gets nothing to front — there is no ambiguity to
   resolve here, unlike genuinely close calls elsewhere in the pipeline.
2. **Listeners come from the tier's own security group**, not invented ports.
   `LoadBalancerPlan.listeners` is built directly from the fronted tier's
   `SecurityGroup.ingress` — the exact ports the tier already declared it
   accepts traffic on. Protocol (`HTTP`/`HTTPS`) is derived from the port
   (443 → HTTPS), the same simple rule everywhere it's used.
3. **The graph gets a fourth node kind and a new edge kind.**
   `NodeKind.LOAD_BALANCER` and `EdgeKind.FRONTS` (load balancer → instance)
   join the existing `PLACED_IN`/`SECURED_BY` edges; a load balancer is
   `PLACED_IN` every subnet of its tier (it spans AZs, unlike a single
   instance) and `SECURED_BY` the same security group its listeners came
   from. This is the same seam every graph-consuming renderer already reads.
4. **Every cloud gets its own idiomatic resource, not a forced-uniform one.**
   AWS: Application Load Balancer (`aws_lb`/`ElasticLoadBalancingV2`/
   `elbv2.CfnLoadBalancer`/`aws.lb.LoadBalancer` depending on renderer).
   Azure: Standard Load Balancer (L4) — chosen over Application Gateway
   because our listeners are generic TCP/port forwards, not path-based HTTP
   routing, and Standard LB is the simpler, more commonly deployed match.
   GCP: **two genuinely different resource families**, not one — an
   internet-facing tier gets a classic external Network Load Balancer
   (`google_compute_target_pool`, which takes raw instances directly), while
   an internal tier gets a regional internal Load Balancer
   (`google_compute_region_backend_service`, which requires an instance
   group even for unmanaged instances). This isn't inconsistency; GCP's own
   product boundary runs exactly along that internal/external line.
5. **A fronted instance loses its own public IP.** Across every renderer
   that previously gave a public-subnet instance its own public IP/EIP
   (Bicep, Terraform/Pulumi Azure, Terraform/Pulumi GCP), that assignment is
   now suppressed when the instance is a load-balancer target — the LB is
   the public entry point; keeping a redundant per-instance public IP would
   bypass it. (AWS assigns public IPs at the subnet level, not per-instance,
   so there was nothing to suppress there.)
6. **Kubernetes gets one Service per load balancer, not one per instance.**
   Instances fronted by a `LoadBalancerPlan` share a single `Service`
   selecting on their `tier`/`environment` labels; only instances with no
   load balancer keep the original one-`Service`-per-VM behavior. This
   mirrors the same "front the group" decision every other renderer makes.
7. **HTTPS needs an operator-supplied certificate — stated, not
   papered over.** No ACM certificate ARN can be known at generation time.
   CloudFormation/CDK/Terraform/Pulumi all emit the HTTPS listener with an
   operator-supplied `acm_certificate_arn` (template Parameter, CDK
   parameter, Terraform variable, or Pulumi config value respectively) and
   say so in the generated README/comments, rather than hand-waving a
   default value that would fail at deploy time with no explanation.

## Consequences

- Closes a real architectural gap present since the very first Terraform
  renderer: a multi-instance tier now gets modeled as what it actually is in
  production.
- The graph seam is proven a fifth time (after CloudFormation, Bicep, CDK,
  Kubernetes) — this time carrying a decision (which tiers get fronted) that
  Terraform/Pulumi also consume via the same `NetworkPlan.load_balancers`,
  not a separately-derived one.
- GCP's two-resource-family split is the right call, not a shortcut: forcing
  GCP onto one shape (either always target-pool or always backend-service)
  would either silently break internal load balancing (target pools are
  external-only) or add unnecessary instance-group overhead to a simple
  external case.
- **Honest limitation:** health checks are minimal (a TCP/HTTP probe on the
  target port with sensible defaults), and GCP's legacy Network LB target
  pools are HTTP-health-check-only even for TCP traffic — a real GCP product
  quirk, called out in the generated Terraform comment rather than hidden.
- New tests lock in the properties that matter: the network agent's
  grouping logic, the graph's `FRONTS`/`PLACED_IN` shape for load balancer
  nodes, the diagram's depiction, and — per renderer — that fronted
  instances don't also get an individual public IP where one previously
  existed.
