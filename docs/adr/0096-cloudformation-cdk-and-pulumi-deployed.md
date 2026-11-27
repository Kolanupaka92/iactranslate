# 0096. CloudFormation, CDK and Pulumi deployed for real

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-27

## Context

Only the Terraform output had ever been applied to a real cloud (ADR 0084, 0092,
0095). On 26–27 November 2026 the same free-tier estate as ADR 0095 (six t3.small
machines, two of them Windows, four data disks, three load balancers, NAT) was
deployed to AWS us-east-2 with each of the other three AWS generators in turn,
checked, and removed.

## What it found

- **CloudFormation and CDK left boot disks small and unencrypted.** They mapped the
  boot disk to `/dev/xvda`; Ubuntu, Red Hat, SUSE and Windows images boot from
  `/dev/sda1`. So each machine kept its image's own boot disk (8 GB for Ubuntu,
  unencrypted) and got an extra empty disk it never used: 16 disks instead of 10,
  6 unencrypted, and the database's 60 GB boot disk at 30. ADR 0093's encryption
  only reached the unused disk.
- **SUSE took the wrong image.** The name pattern also matched SUSE's container-host
  build that needs your own subscription, and its ECS build.
- **CentOS 7 found no image at all.** It is end of life and AWS hides its images,
  so any CentOS machine made the whole AWS plan fail.
- **Pulumi's load balancer and target group names were refused:** Pulumi names them
  after the label, and AWS allows no underscore.
- **Deleting a CloudFormation or CDK stack leaves its data disks**, as ADR 0093
  intended, and they keep billing. The README didn't say so.

## Decision

- The boot disk is mapped where its image boots (`/dev/xvda` for Amazon Linux,
  `/dev/sda1` for the rest), read from AWS's images.
- SUSE: a pattern with a service-pack digit and an eight-digit date.
- CentOS on AWS gets Rocky Linux 9, and the plan states the substitution.
- Pulumi names AWS load balancers and target groups as Terraform does.
- The CloudFormation and CDK READMEs say what a deleted stack leaves and how to find
  it. The CDK README no longer says it was never synthesized.

## Results, after the fixes

| | CloudFormation | CDK | Pulumi |
|---|---|---|---|
| Deploy | 3 min, first time after the disk fix | 2.5 min, first time (after `cdk bootstrap`) | 2.5 min, after the naming fix |
| Disks | 10, all encrypted, planned sizes | Same | Same |
| Public addresses | None | None | None |
| HTTPS without a certificate | No listener | No listener | No listener, with a warning |
| Windows | Ready | — | — |
| Run again unchanged | "No changes"; drift detection in sync | "no changes" | 42 unchanged |
| Removed | Stack deleted; 4 kept data disks deleted by hand | Same, plus CDK's bootstrap stack, its bucket (kept by AWS) | 42 deleted |

The account was empty after each. Traffic through the load balancers was proven
with Terraform (ADR 0095) and not repeated: they are the same AWS resources.

## Consequences

- **An existing CloudFormation or CDK stack replaces its instances** on the next
  update, because the boot disk mapping changes; the old ones are kept
  (ADR 0090) and their data is moved by hand.
- A CentOS 7 workload on AWS needs testing on Rocky Linux before it moves.
- Still not deployed for real: Bicep (Azure), and every generator on Azure, Oracle,
  DigitalOcean, Nutanix and Proxmox.
