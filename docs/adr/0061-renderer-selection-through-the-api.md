# 0061. Renderer selection reaches the API and the console

**Status:** Accepted
**Date:** 2026-08-30

## Context

The engine has shipped six renderers since [ADR 0016](0016-renderer-registry.md)
— Terraform, Pulumi, CloudFormation, Bicep, CDK and Kubernetes — and the CLI has
exposed all of them through `--renderer` the whole time.

The API never accepted a renderer at all. `_execute_run` called `run_pipeline`
without the argument, so it took the default, and the only `format` parameter
anywhere in `api/main.py` was the `html`/`pdf` switch on the executive report.
The web console had no picker either.

The result: **five of the six renderers were reachable from the CLI and nowhere
else.** A UX audit of the console surfaced it, and by then the public landing
page was already advertising "6 IaC formats" — a claim the hosted product could
not honour.

## Decision

Carry the renderer from the create request through to the pipeline, and let
clients discover which renderers are valid for a cloud.

**Per-project, chosen at creation.** A project's renderer is fixed when it is
created rather than passed at run time. Re-running a project must not silently
change what it emits, and `save()` deliberately does not update the column — the
field is immutable for the project's life. A project row written before this
existed has `NULL` and reads back as `terraform`, which is the only thing it
could have been.

**Support is per-target, and declared once.** The valid combinations are not a
cross product: CloudFormation is an AWS service, Bicep an Azure one, Pulumi
covers AWS/Azure/GCP, and only Terraform and Kubernetes serve every target. The
renderers already enforced this — each raises `RendererNotSupportedError` — but
there was no way to *ask* before running a pipeline. `renderers.supports()` and
`renderers.renderers_for()` answer that, and `/targets` publishes the answer so a
UI does not hardcode it.

**The matrix is declared, and tested against reality.** `_SUPPORTED` is a
hand-written table, and the architecture review was right that hand-maintained
matrices drift (AR-01). What makes it acceptable is
`test_the_declared_matrix_matches_actual_renderer_behaviour`, which calls every
renderer against every target and asserts the table matches what actually
happens. The table cannot go stale without a test failing.

**Invalid combinations are refused at create, not at run.** A caller asking for
Bicep on AWS gets a 400 naming the valid alternatives, before uploading an
inventory and waiting on a pipeline that cannot produce output.

## Consequences

The console offers only the formats the selected cloud accepts, and reconciles
the selection when the cloud changes — picking CloudFormation and switching to
Azure falls back to Terraform rather than sending a request the API will reject.
Result copy names the format actually produced, so "Your Bicep project is ready"
replaces a hardcoded "Terraform".

`terraform` remains the default everywhere, so every existing caller, stored
project and test behaves exactly as before.

Two defects surfaced while wiring this, both worth recording because they were
invisible to the type checker. The in-memory `ProjectStore` — the *default*
backend — accepted the new parameter and dropped it before constructing the
`Project`, so the value round-tripped as `terraform`; only an end-to-end test
that inspected the generated archive caught it. And two `useCallback` dependency
arrays omitted the new state, which would have sent the previously selected
format after a change; the lint warning was a real bug, not noise.
