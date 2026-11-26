# 0093. Encrypted AWS disks, SUSE on Oracle, and running the output in CI

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-26

## Context

ADR 0090 left four defects open:

- AWS disks made by Pulumi, CloudFormation and CDK weren't encrypted. Terraform's
  are.
- CloudFormation and CDK create data disks at launch, which AWS deletes when the
  instance ends.
- Oracle publishes no SUSE image, so the lookup for a SUSE machine found nothing
  and the whole plan failed.
- CI read Pulumi, CDK and Bicep output as text and never ran it.

Running the output for the last one found a fifth, worse than the others:
**CloudFormation silently dropped machines.** Its logical ids drop punctuation, so
"uat cache 01" and "uat-cache-01" both became `InstanceUatCache01` and one
overwrote the other: 23 of 25 machines on the realistic estate. CDK refused to
build the same estate.

And **the Bicep output had never compiled.** Its load balancer named its own
parts through itself, which Bicep forbids (BCP079), and separated array items
with commas across lines (BCP238). Every inventory with a load balancer failed.

## Decision

- **Every AWS disk is encrypted** in Pulumi, CloudFormation and CDK, with AWS's
  default key. Unlike Terraform, these renderers don't take a customer-managed
  key; that stays a gap.
- **CloudFormation and CDK data disks outlive their instance**
  (`DeleteOnTermination: false`). Pulumi's are separate volumes already.
- **Each machine gets a distinct logical id.** The first of a clash keeps the plain
  id, so a stack that holds it isn't renamed; later ones get a number. CDK's
  Python variable names follow the same ids.
- **On Oracle, a SUSE machine gets Oracle Linux 9**, and the plan says it was
  substituted, as for any other OS without an image.
- **Bicep load balancer rules refer to their parts by `resourceId`**, and array
  items are separated by new lines. All five inventories compile without errors
  or warnings.
- **A CI job runs the output in its own tools:** every Pulumi program against the
  real Pulumi libraries with mocked clouds, CDK synthesis on Node 22, and `bicep
  build`. Versions are pinned; the Bicep binary is checked against its published
  checksum.

## Consequences

- **An existing CloudFormation or CDK stack replaces its instances on the next
  update:** encryption and the disk setting can't be changed on a running
  instance. `UpdateReplacePolicy: Retain` (ADR 0090) keeps the old instance and its
  disks; moving the data across is manual. A new stack is unaffected.
- A SUSE workload on Oracle needs testing on Oracle Linux before it moves, as the
  plan says.
- Run locally and in CI: Pulumi on three clouds, CDK and Bicep, on three
  inventories each. Compiling isn't deploying: none of these has been applied to a
  real cloud.
