# 0005 — Templates (Jinja2) emit IaC, not the model or the AI

**Status:** Accepted

## Context

Something has to turn a validated plan into actual `.tf` (and `.py` for Pulumi).
The candidates: have the AI emit HCL (rejected — see
[0001](0001-deterministic-engine.md)), build HCL by string-concatenation in
Python (error-prone, hard to review), or use a templating layer.

## Decision

Render IaC with **Jinja2 templates** owned by each target, fed a context built
from the `MigrationPlan`. `build_files(plan, target)` returns a `{filename:
content}` map; the packager writes it. The model carries data; templates carry
the provider syntax. A parallel renderer (`renderers/`) consumes the *same* plan
to emit Pulumi, proving the plan is renderer-agnostic.

## Consequences

- Terraform syntax lives in reviewable `.tf.j2` files, versioned per cloud —
  changing an AWS resource is a template edit, not a code change.
- The same `MigrationPlan` drives multiple renderers (Terraform, Pulumi, future
  Bicep/CDK) with no changes to the pipeline.
- Empty-rendered files are dropped, so conditional output (e.g. brownfield
  `imports.tf`) needs no special-casing in the pipeline.
- We accept that template correctness is verified by tests and real
  `tofu validate` in CI rather than by the type system.
