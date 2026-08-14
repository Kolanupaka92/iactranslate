# 0031 — Sanitize untrusted inventory at the normalize boundary

**Status:** Accepted

## Context

The product's entire job is turning a file someone uploads into code someone
else runs. That makes uploaded inventory **untrusted input on a path to code
execution**, and until now it was passed through verbatim.

Two failures, both found by feeding the pipeline something other than the tidy
fixture names (`prod-web-01`) that every existing test used.

### 1. Template injection (security)

A VM named `x-${file("/etc/passwd")}` was written unchanged into a Terraform
tag. Running `tofu` on the result **evaluated the injected function** and the
file's contents appeared in the value — confirmed locally, not theorised.

The important detail is that this needed no quote-breaking. HCL evaluates
`${...}` *inside* a string literal, so the payload never had to escape its
quotes. An earlier attempt that did try to break out produced only a syntax
error, which is exactly why the interpolation vector is the dangerous one and
the "it just breaks the file" reading would have been wrong.

Impact: a hostile row in a CMDB export — an insider, a compromised discovery
agent, or a client-supplied inventory — yields Terraform that reads local files
into tag values when the consultant runs `plan` or `apply`, with whatever
credentials that run carries.

### 2. Ordinary CMDB names broke Azure (correctness)

Separately, and arguably worse commercially: names containing spaces,
parentheses, dots, or slashes — `web server 01`, `DB-Prod (Primary)`,
`Exchange/MBX01` — violate Azure's resource-naming rules. Six such names
produced **seven `tofu validate` errors**, i.e. every VM in a realistic estate.
AWS, GCP, OCI, and DigitalOcean were unaffected because their templates already
route names through a slug; only Azure used the raw value.

The clean-name fixtures hid both problems completely. This is the more general
lesson: the fixtures tested the happy path so thoroughly that the unhappy path
was never exercised at all.

## Decision

**Sanitize once, at `normalize`, rather than in six template languages.**

`sanitize_identifier()` strips characters that are harmless in a hostname but
dangerous in generated code: control characters, quotes, backslashes,
backticks, angle brackets, braces, and `$(`. It is applied to *every*
free-text field that reaches a template — name, OS, cluster, network,
datacenter, hostname — not just the name.

The choke point matters. The alternative was correct escaping for HCL, Python
(Pulumi/CDK), JSON (CloudFormation), Bicep, and YAML (Kubernetes) — five
escaping rules, five chances to get it wrong, and a new one every time a
renderer is added. Doing it at `NormalizedVM`, the narrow waist every renderer
already reads from ([ADR 0002](0002-normalizedvm-canonical-model.md)), makes
one implementation cover all of them and cover future renderers by default.

Braces are removed outright rather than only the `${`/`%{` pairs: stripping
just the pairs leaves stray braces that make generated code confusing to read,
and no real hostname contains them.

**Azure resource names now use the existing RFC1035 slug**, the same helper GCP
already used. Tag values keep the original inventory name, so traceability back
to the source VM is preserved while the resource name obeys the cloud's rules.

## Consequences

- The injection is dead: `${file("/etc/passwd")}` becomes the inert text
  `x-$-file(-/etc/passwd-)`. Verified end to end.
- **All five clouds now `tofu validate` cleanly against both hostile payloads
  and messy real-world names** — Azure went from 7 errors to 0.
- Legitimate hostnames are untouched; a test asserts `prod-web-01`,
  `db.prod.internal`, `app_server_3`, and `SRV001` pass through unchanged.
- Sanitized names differ from the source data. That is a deliberate trade:
  correctness and safety over byte-fidelity, with the original still visible in
  tags. A name that is *entirely* stripped becomes `unnamed` rather than empty,
  because downstream de-duplication keys on the name and empty strings would
  collapse distinct rows together.
- `tests/test_hostile_input.py` keeps both regressions locked, including a
  per-cloud check that resource names never contain spaces, parens, slashes,
  or hashes.

**Related finding, not fixed here.** A 5,000-VM estate (the documented
`MAX_VMS` ceiling) runs in ~5s and produces valid Terraform — but it is a
single 95,001-line `compute.tf`. That validates, and nobody can review it. The
product promise is *reviewable* IaC, so splitting output per tier or per
application is a real usability gap; it is tracked separately rather than
folded into a security fix.
