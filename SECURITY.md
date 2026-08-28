# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report privately through GitHub's [private vulnerability
reporting](https://github.com/Kolanupaka92/iactranslate/security/advisories/new)
on this repository. Include the affected version or commit, what an attacker can
do, and the smallest reproduction you have.

Expect an acknowledgement within **3 business days** and an assessment within
**10 business days**. This is a small project; those are honest targets rather
than a contractual SLA.

## What is in scope

This tool ingests **infrastructure inventory exports** — hostnames, IP
addresses, OS and patch levels, cluster topology. That corpus is a
reconnaissance map of a customer's estate, and it is treated as the most
sensitive data here. Findings involving it are triaged first.

Particularly interested in:

* **Injection into generated IaC** — any input that causes generated Terraform,
  Pulumi, CloudFormation, Bicep, CDK or Kubernetes output to execute, read or
  exfiltrate something the operator did not intend. All six renderers share one
  sanitizing choke point (`normalize.sanitize_identifier`); a bypass of it is a
  high-severity finding.
* **Cross-tenant access** — reaching another account's projects, jobs, audit
  records or artifacts. Unknown ids return 404 rather than 403 by design, so
  anything that distinguishes "not yours" from "not found" is also a finding.
* **Authentication and session handling** — session fixation, token leakage,
  password-reset flaws, rate-limit bypass.
* **Path traversal or arbitrary write** via uploaded filenames or project names.
* **Denial of service** through crafted uploads (zip bombs, pathological
  spreadsheets) beyond the documented `MAX_UPLOAD_MB` / `MAX_VMS` limits.

## What is out of scope

* Findings in a deployment's own configuration (your CORS origins, your reverse
  proxy, your object storage permissions).
* Missing hardening headers on endpoints that serve no user-controlled content.
* Automated scanner output with no demonstrated impact.
* The absence of features already documented as not yet built — SSO, RBAC beyond
  project ownership, encryption at rest, data residency. These are known gaps on
  the roadmap, not vulnerabilities.

## Supply chain

Every build runs `pip-audit` and `bandit`, scans the container image with Trivy
for CRITICAL/HIGH CVEs, and produces a **CycloneDX SBOM** as a build artifact.
Published images are **signed with cosign** (keyless, via GitHub OIDC) and carry
**SLSA build provenance**.

Verify an image before running it:

```bash
cosign verify ghcr.io/kolanupaka92/iactranslate:latest \
  --certificate-identity-regexp '^https://github\.com/Kolanupaka92/iactranslate/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Supported versions

The project is pre-1.0. Fixes land on `main` and in the next published image;
there are no maintained release branches yet.
