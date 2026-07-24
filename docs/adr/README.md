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

See also the [Architecture & Design](../architecture.md) overview.
