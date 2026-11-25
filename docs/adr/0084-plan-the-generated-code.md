# 0084. Plan the generated code, don't just validate it

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-24

## Context

Every target's Terraform was checked with `validate` against the real provider.
`validate` treats variables as unknown, so it never compares a real name with a
provider's limits. Preparing a first real deployment showed what that missed:

- **Names with spaces couldn't be applied on AWS or Azure.** The project name
  went into resource names as typed, and the console allows spaces. "Acme
  Migration" made IAM roles AWS refuses. Google, OCI, DigitalOcean, Nutanix and
  Proxmox already made it safe.
- **Long names couldn't be applied on Google.** A service account ID is 6 to 30
  characters. The console's own example, `acme-datacenter-migration`, is too
  long once a tier is added.
- **Every generated pipeline failed at its first step.** They run
  `terraform fmt -check`, and no target's output passed it: how far `=` moves
  depends on the longest neighbouring name, which templates can't know.

## Decision

- **CI plans the code.** `tests/test_e2e_plan.py` runs `tofu test` with
  `command = plan` and mocked providers, on all seven targets and both layouts,
  with the console's example name, and with awkward names on the classic layout.
  Mocking needs no account, and each provider's own argument checks still run.
- **CI runs the pipelines' own `fmt -check`** on the same output.
- **`resource_prefix`** makes a name safe once, for Terraform on AWS and Azure
  and for the Bicep, Pulumi Azure, CDK and CloudFormation names. A usable name
  is returned unchanged, so regenerating an estate never renames its resources.
- **Google service accounts** shorten a long name and add a short hash.
- **`generator/hclfmt.py`** aligns `=` as `terraform fmt` does, judged by the
  real `tofu fmt` on every target.
- The Google README enables the two APIs a new project has off.

## Consequences

- All 14 target and layout combinations plan and pass `fmt`; names tried are the
  example, one character, 128 characters, with a space, and with brackets.
- **Applied for real on Google Cloud** (2026-11-24): three small Linux machines,
  16 resources, named "Proof Run". Applied on the first attempt in 48 seconds,
  with no drift; every machine booted to a login prompt; only the web machine
  had a public address. Against that live state, regenerating planned no
  changes, and switching to the for_each layout planned 0 to add, change or
  destroy with all 3 machines moved. Then destroyed, and Google listed nothing
  left.
- **Still not proven on a real cloud:** every other target, Windows, data disks,
  load balancers, names over the service account limit, egress through NAT, and
  an estate large enough to meet quotas.
- CloudFormation, Bicep, CDK, Pulumi and Kubernetes output isn't planned.
- A Google project whose name is longer than 48 characters is shortened, which
  would rename resources if it had been applied before.
- On Google, a name long enough to be refused before was never applied, so
  changing its service account IDs replaces nothing; one where only the longest
  tier was refused could have been applied in part.
