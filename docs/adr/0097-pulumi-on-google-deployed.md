# 0097. Pulumi on Google deployed; Pulumi gets the network Terraform has

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-27

## Context

Pulumi on Google was the last Google generator not deployed for real. On 27
November 2026 the estate from ADR 0092 (six machines, two Windows, four data
disks, both load balancers) was deployed to the proof project with it.

## What it found

- **Every resource name was refused.** Pulumi names a resource after its label,
  `public_0`, and Google allows only lowercase letters, digits and hyphens.
- **Data disk attachments named no project**, so Pulumi couldn't tell where they
  belonged (introduced in ADR 0090).
- **No Pulumi generator built the network Terraform builds.** Google had no router
  or NAT, so private machines had no way out. AWS had an internet gateway but no
  route tables at all: nothing could reach the public load balancer, and private
  machines had no way out. ADR 0096's AWS Pulumi deployment checked disks and
  addresses, not traffic, so it missed this.
- **The public Google load balancer gets one address for HTTP and another for
  HTTPS**, in Terraform and Pulumi alike: one web address can't point at both.
- **Windows didn't report** after the test restarted the machines 90 seconds after
  creation. Both had booted and started Google's agent first. Unlike Terraform's,
  Pulumi's machines have no service account, and the agent logged missing
  credentials. Unresolved.

## Decision

- Google resource names are lowercase and hyphenated; machines get the names
  Terraform gives them.
- Attached disks name their zone and project.
- Pulumi builds Terraform's network: on Google a router and NAT; on AWS a public
  route table to the internet gateway, a NAT gateway and a private route table.

## Results, after the fixes

| Check | Result |
|---|---|
| Deploy | 34 resources in 86 seconds |
| Preview afterwards | 34 unchanged |
| Machines | Real names, no public addresses, disks at planned sizes (Windows boot 50 GB) |
| Public load balancer | Served from both web machines |
| Internal load balancer | Both backends healthy; reached from the web tier |
| NAT | Both private app machines reached the internet |
| Windows | Booted and started the agent; after an early restart, reported nothing |
| Destroy | 34 deleted; the project lists nothing |

## Consequences

- **The AWS Pulumi routing was then proven on AWS** the same day: 50 resources in
  under 3 minutes, no changes on preview, the public route table to the internet
  gateway and the private one through NAT, the public load balancer answering from
  the internet, and a private machine with no public address reaching the internet.
- Open: Windows on Pulumi Google (KI-074), one address per protocol on the Google
  public load balancer (KI-075).
