# 0042. Supply-chain security for a publicly published image

**Status:** Accepted
**Date:** 2026-08-27

## Context

The project publishes a public container image to GHCR on every push to `main`.
Before this change that pipeline had **no supply-chain controls whatsoever**:

* No SBOM — and enterprise procurement asks for one in the first security review.
* No dependency vulnerability scanning.
* No container image scanning.
* No static security analysis.
* No image signing, so nothing tied `ghcr.io/.../iactranslate:latest` to this
  repository's build. A registry compromise would be undetectable.
* No `SECURITY.md`, so no private disclosure path — a researcher's only option
  was a public issue.
* No Dependabot, and `pyproject.toml` uses open `>=` ranges, so each CI run
  silently resolved whatever was newest with no PR, no review, no record.

For a tool that ingests infrastructure inventory — hostnames, IPs, OS and patch
levels for an entire estate — this is the wrong posture regardless of who is
buying.

## Decision

A dedicated `security` CI job, which `publish-image` now depends on, so nothing
unscanned is ever published:

| Control | Tool | Enforcement |
| --- | --- | --- |
| Dependency CVEs | `pip-audit` | fails the build |
| Static analysis | `bandit` (medium+) | fails the build |
| Image CVEs | Trivy (CRITICAL/HIGH, fixable) | fails the build **and** uploads SARIF |
| SBOM | Syft → CycloneDX JSON | published as a build artifact |
| Signing | `cosign` keyless via GitHub OIDC | on the pushed digest |
| Provenance | SLSA build attestation | pushed to the registry |

Trivy both uploads to the Security tab and gates the build. A report nobody is
required to read is not a control.

Signing is **keyless** — identity comes from the workflow's OIDC token, so there
is no long-lived signing key to leak. `SECURITY.md` documents the `cosign verify`
invocation.

Dependabot covers pip, npm, GitHub Actions and Docker, with patch updates grouped
into one PR: dozens of individual bumps get ignored, and an ignored dependency PR
is worth the same as no Dependabot at all.

## Consequences

Bandit found six issues on its first run. **All six were false positives**, and
each is now suppressed inline with a stated reason rather than by a global skip
list — a blanket skip would mean the next real finding is silent too:

* Two `B608` "SQL injection" in `SqliteProjectStore`: the f-strings interpolate
  only `_COLUMNS`, a class constant, and a `clause` chosen between two literals;
  every caller value is bound with `?`. The clause is selected rather than
  parameterized because SQLite cannot bind `IS NULL`.
* `B701` jinja2 `autoescape=False` in the renderer, reported HIGH. Enabling it
  would be **a bug, not a fix**: these templates emit Terraform HCL, and
  HTML-escaping would corrupt every `&`, `<` and `"` in generated configuration.
  Injection is defended at `normalize.sanitize_identifier` instead (ADR 0031).
* Two `B310` `urlopen` in `pricing.py`: scheme and host are literal `https://`;
  only urlencoded query parameters vary.
* `B113` "requests without timeout" in the Kubernetes source, where `requests` is
  the pod spec's resource-requests **dict**, not the HTTP library.

`pip-audit` runs with `--skip-editable` and without `--strict`. `iactranslate`
itself is installed with `-e` and is not on PyPI, so it cannot be audited, and
`--strict` treats that as a failure. Vulnerabilities still fail the run.

`pip-audit` also flagged `urllib3 1.26.20` locally. That turned out to be
contamination of the developer's virtualenv from a stray `boto3`, which caps
`urllib3<2` on Python 3.9 — not a project dependency. Verified against a clean
environment matching CI: **no known vulnerabilities**. Recorded because the
next person to see it will draw the same wrong conclusion.

Still open: dependencies remain `>=` ranges with no lockfile, so builds are not
yet byte-reproducible — Dependabot gives review and a record, not pinning. Trivy
ignores unfixed CVEs, which is pragmatic for a `python:3.12-slim` base but means
a known-unfixable base-image CVE will not block a release.
