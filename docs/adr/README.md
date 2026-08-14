# Architecture Decision Records

Short records of the *why* behind the load-bearing decisions in IaCTranslate.
Each captures the context at the time, the decision, and its consequences — so
the reasoning survives even when the people don't.

Format: [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Decision | Status |
|---|---|---|
| [0001](0001-deterministic-engine.md) | A deterministic engine; AI is optional and gated | Accepted |
| [0002](0002-normalizedvm-canonical-model.md) | `NormalizedVM` as the canonical inventory model | Accepted |
| [0003](0003-target-registry.md) | Clouds behind a target registry | Accepted |
| [0004](0004-ai-provider-interface.md) | AI behind a provider interface with rule-engine default | Accepted |
| [0005](0005-jinja-renderer.md) | Templates (Jinja2) emit IaC, not the model or the AI | Accepted |
| [0006](0006-validation-before-render.md) | Validation is a hard gate before rendering | Accepted |
| [0007](0007-immutable-plan.md) | The MigrationPlan is immutable after planning | Accepted |
| [0008](0008-policy-engine.md) | A policy engine for organization-specific rules | Accepted |
| [0009](0009-capability-flags.md) | Targets advertise capability flags | Accepted |
| [0010](0010-infrastructure-graph.md) | An Infrastructure Graph IR between plan and renderers | Accepted |
| [0011](0011-pipeline-stages.md) | The pipeline runs as named, timed stages | Accepted |
| [0012](0012-async-jobs-event-bus-audit.md) | Async jobs, an event bus, and an audit trail | Accepted |
| [0013](0013-cloudformation-from-graph.md) | CloudFormation renders from the graph; AMIs via SSM | Accepted |
| [0014](0014-bicep-from-graph.md) | Bicep renders from the graph; subscription-scope + module | Accepted |
| [0015](0015-cdk-from-graph.md) | AWS CDK renders from the graph via L1 constructs, reusing CloudFormation's AMI logic | Accepted |
| [0016](0016-terraform-pulumi-placement-from-graph.md) | Terraform/Pulumi placement moves onto the graph; fixes a subnet-collapse bug | Accepted |
| [0017](0017-kubernetes-from-graph.md) | Kubernetes renders VMs as KubeVirt VirtualMachines, not fabricated Deployments | Accepted |
| [0018](0018-load-balancer-topology.md) | Load balancer topology: modeled once, rendered six ways | Accepted |
| [0019](0019-kubernetes-source.md) | Kubernetes as a discovery source: containers read as workloads | Accepted |
| [0020](0020-managed-db-replatforming.md) | Managed-database re-platforming is advisory, not automated | Accepted |
| [0021](0021-ai-integration-reachable-and-honest.md) | AI made reachable end-to-end (CLI/API/web), and always honestly labeled | Accepted |
| [0022](0022-oci-target.md) | OCI target: Flex shapes need a synthetic catalog key, capabilities stay honest | Accepted |
| [0023](0023-digitalocean-target.md) | DigitalOcean target: real platform gaps (no subnets, no Windows) stated, not papered over | Accepted |
| [0024](0024-migration-wave-planning.md) | Migration wave planning: tier + environment order, not a fabricated dependency graph | Accepted |
| [0025](0025-persistent-store-and-bearer-auth.md) | A real persistent store (SQLite) and real auth (bearer token), scoped to what's buildable without Docker | Accepted |
| [0026](0026-persistent-audit-and-metrics.md) | A persistent audit trail and Prometheus metrics, both built as event-bus subscribers | Accepted |
| [0027](0027-multi-tenancy-and-session-auth.md) | Multi-tenancy: user accounts, session cookies (not bearer tokens), and per-project ownership | Accepted |
| [0028](0028-rate-limiting-and-security-headers.md) | Per-route rate limiting (per-IP *and* per-account on auth) plus baseline security headers | Accepted |
| [0029](0029-durable-artifact-workspaces.md) | Generated artifacts move off `/tmp` onto a durable volume, so files survive with their metadata | Accepted |
| [0030](0030-password-change-and-reset.md) | Password change and reset: both evict every session; delivery stops short of unverified SMTP | Accepted |
| [0031](0031-sanitize-untrusted-inventory-at-normalize.md) | Untrusted inventory is sanitized once at `normalize`, killing a proven Terraform template injection | Accepted |

See also the [Architecture & Design](../architecture.md) overview.
