# 0075. CI/CD pipelines for four systems, from one credentials table

**Status:** Accepted
**Date:** 2026-11-14

## Context

`--gitops` generated a GitHub Actions workflow and nothing else. Teams on
GitLab, Azure DevOps or Jenkins rewrote it by hand, which is the kind of
translation this tool exists to remove. That was milestone v2.5.

Building it surfaced two defects in what already shipped:

1. **A pipeline that could never run.** Any renderer other than Pulumi got the
   Terraform workflow. So `--renderer cloudformation --gitops` produced a
   pipeline running `terraform init` against a JSON template. The same was
   true for Bicep, CDK and Kubernetes.
2. **No credentials for four of seven targets.** The credential table covered
   AWS, Azure and GCP. OCI, DigitalOcean, Nutanix and Proxmox pipelines had
   none, so plan and apply would fail on their first run.

## Decision

- **`--ci github|gitlab|azure-devops|jenkins`** (default `github`, so existing
  output is unchanged; naming a system implies `--gitops`). Each system gets
  the same shape: format check and validate, then plan on a pull/merge
  request, then apply on main, serialised (`resource_group`,
  `disableConcurrentBuilds`). Terraform (both layouts) and Pulumi.
- **One credentials table per cloud**: the variable the provider reads, and
  the secret it comes from. Each system renders it through its own secret
  store: `${{ secrets.X }}`, GitLab masked variables, an Azure DevOps variable
  group mapped into `env:` explicitly (secret variables are not otherwise
  visible to scripts), and Jenkins `credentials('X')`. The four providers that
  authenticate through Terraform variables receive `TF_VAR_*`.
- **OCI's API key is a file**, supplied through each system's own job-private
  mechanism: GitHub writes it to the job's `runner.temp` (mode `600`), GitLab
  takes a File-type variable (whose value is already a path), Azure DevOps
  downloads it from Secure Files, and Jenkins binds a "Secret file" credential
  (which yields a path). The first version wrote it to a fixed
  `/tmp/oci_api_key.pem`. Bandit rejected that in CI, and rightly: on a shared
  self-hosted runner, which is what the on-premises targets need, another job
  could read or race a predictable path. Azure DevOps' Terraform download moved
  out of `/tmp` for the same reason, since a swapped archive there would be
  someone else's binary on the PATH.
- **Nutanix and Proxmox** pipelines say they need a runner inside the
  customer's network (GitHub's uses `runs-on: self-hosted`), because a
  cloud-hosted runner cannot reach Prism Central or a Proxmox endpoint.
- **Renderers with their own deployment tooling are refused** with an
  explanation (CLI exit 2, before any work), instead of receiving a pipeline
  that cannot work.

## Verification

- GitHub output for AWS, Azure and GCP is byte-identical to before (Terraform
  and Pulumi). Only the four previously credential-less targets changed.
- All 30 GitHub, GitLab and Azure DevOps pipelines (7 clouds × Terraform, plus
  Pulumi for the 3 it supports) validate against the official JSON schemas:
  SchemaStore's `github-workflow`, GitLab's `ci.json`, and Microsoft's
  `azure-pipelines` service schema. The schemas are fetched, not vendored, so
  this check was run when building and is not part of the offline suite.
- Jenkins publishes no schema and its declarative linter needs a live Jenkins.
  Jenkinsfiles are covered by structural tests only.
- None of the pipelines was run on a real CI system here. That remains the
  check that matters, and the first customer run on each system is it.
