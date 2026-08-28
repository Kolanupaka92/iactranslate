# 0041. Remote state backends in generated Terraform

**Status:** Accepted
**Date:** 2026-08-27

## Context

Generated projects declared no `backend`, so Terraform silently defaulted to
**local state**: a `terraform.tfstate` written beside the config. That file *is*
the mapping between the configuration and the customer's real infrastructure. It
cannot be shared with a teammate, is not locked against two concurrent applies,
and takes the estate's manageability with it when the laptop dies.

The architecture review called this out as disqualifying, and it is: every other
quality of the generated code is irrelevant if a team cannot adopt it. The tool
validated its output with real `tofu validate` in CI while shipping a default no
professional would run twice.

## Decision

Three modes, because the honest answer depends on what the caller told us.

**Local (the default when nothing is specified) is now explicit, not silent.**
`versions.tf` carries a banner and the README a section, both saying state is
local and what to do about it. Inventing a remote backend the caller never asked
for would be a different kind of wrong; failing to *mention* it was the actual
bug.

**A named backend with no configuration emits a partial block** —
`backend "s3" {}` — plus a `backend.hcl.example`. `terraform init` then fails
until the operator supplies `-backend-config=backend.hcl`. **Failing closed is
the point.** We cannot invent a customer's bucket name, and quietly falling back
to local state is exactly how this defect existed in the first place.

**A named backend with configuration emits a complete, ready-to-init block.**

`--state-backend auto` maps to the target cloud's native backend; `--state-config
k=v,...` supplies the settings.

## Consequences

**Terraform only.** The backend is not forwarded to the other five renderers:
Pulumi keeps state in its own service or a self-managed backend, and
CloudFormation, Bicep and CDK have no client-side state at all. Emitting a
Terraform backend into them would imply a control they do not have.

**OCI and DigitalOcean use the `s3` backend** against Object Storage and Spaces,
neither of which has a first-party Terraform backend. They also get
`skip_credentials_validation`, `skip_region_validation`,
`skip_requesting_account_id`, `skip_metadata_api_check` and `use_path_style` —
not decoration: `terraform init` fails against a non-AWS endpoint without them,
with an AWS-specific error the operator has no reason to expect.

**State locking is documented rather than generated.** Terraform 1.10+ and
OpenTofu 1.9+ lock via a lockfile in the bucket; older versions need a DynamoDB
table. Since `required_version` is `>= 1.5.0` we cannot assume either, so
`backend.hcl.example` carries both as commented options. The README also insists
on bucket versioning, which is the only thing standing between a bad apply and an
unrecoverable estate.

Verified with real `tofu validate` against a generated S3-backed project.

Still open: no state migration helper for customers adopting a backend after a
first local apply (`terraform init -migrate-state` is documented, not automated),
and no per-environment state key convention — both wait on the module/`for_each`
work already on the roadmap.
