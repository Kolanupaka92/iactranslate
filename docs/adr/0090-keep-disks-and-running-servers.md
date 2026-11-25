# 0090. Keep every data disk, and every running server

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-25

## Context

ADR 0089 listed three problems it left alone. They are the kind that cost data.

- **Pulumi (AWS, Azure, Google) and Bicep left out data disks** and said nothing.
  A database server came up without the disk its data belongs on.
- **AWS and Oracle look up the newest image on every run.** A different image means
  a different server, so each one a vendor published made the next run propose to
  destroy and recreate every server using it, with its boot disk. Reproduced with
  `tofu test`: deploy, then plan with a newer image.
- **Oracle's lookup didn't ask for images that fit the shape.** Oracle publishes Arm
  and GPU builds of the same Linux version, and the newest was taken.

## Decision

**Disks.** Pulumi and Bicep create and attach every data disk. The list, names and
device positions are the ones Terraform uses (`data_volumes`). Bicep's survive
their machine (`deleteOption: Detach`). AWS volumes made by Pulumi are encrypted,
as Terraform's are. A test puts a distinctive size through every renderer on every
target and fails if it isn't there exactly once.

**Images.** A server keeps the image it was created from:

- Terraform on AWS, Oracle and Google ignores changes to the image. Azure,
  DigitalOcean, Nutanix and Proxmox name an image in a way that doesn't move.
- Pulumi on AWS and Google does the same.
- CloudFormation and CDK can't ignore a change. `UpdateReplacePolicy: Retain` keeps
  the old server and its disks if an update replaces it.
- To rebuild on a newer image on purpose: `terraform apply -replace=<address>`.

**Oracle** asks for available images for the shape in use.

**Running the Pulumi programs** against the real Pulumi libraries, with mocked
clouds, to check the disks found that the Azure and Google ones had never run with
a load balancer in them: wrong argument types and names. Fixed.

## Consequences

- The next run of an existing Pulumi or Bicep project proposes the missing disks,
  empty. Nothing already there is changed.
- `ami_overrides` now affects new servers only. The README says so.
- An AWS instance adopted from a live estate no longer has to match the looked-up
  image.
- All three Pulumi programs run with mocked clouds on three inventories; the
  realistic one has 31 data disks on each. CDK synthesizes; it warns that Retain on
  replacement alone doesn't cover deleting the stack, which is intended.
- CI doesn't run Pulumi or CDK yet, so this could come back.
- Bicep was checked as text only: its compiler isn't installed here.
- **Found, not fixed:**
  - Oracle publishes no SUSE image, so the lookup for a SUSE machine finds nothing
    and the whole plan fails.
  - Root disks from Pulumi, CloudFormation and CDK on AWS aren't encrypted, and
    neither are CloudFormation's and CDK's data disks. Terraform's are.
  - CloudFormation and CDK attach data disks to the instance, where terminating
    it deletes them.
  - **Adopting a live AWS instance can still propose replacing it.** It is placed in
    a generated subnet and given an encrypted root disk, and the provider replaces an
    instance for either. Found by reading, not reproduced.
