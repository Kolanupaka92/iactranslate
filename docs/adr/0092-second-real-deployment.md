# 0092. Second real deployment: Windows, data disks, load balancers

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-26

## Context

The first deployment (ADR 0084) was three small Linux machines. This one covered
what it left out: Windows, data disks, both kinds of Google load balancer, and
outbound traffic through NAT. Six machines (two Ubuntu web, Red Hat and Ubuntu
app, two Windows Server 2022), four data disks, 37 resources, in a separate
project, on 26 November 2026.

## What happened

**The first apply failed.** Google requires every machine in an internal load
balancer's instance group to be in the subnet its forwarding rule uses. The app
machines were spread across two subnets, as on every cloud; 34 of 37 resources
were created.

**The fix couldn't be applied either.** Moving a running machine to another
subnet, like changing its size, needs Google to stop it, and the generated code
didn't allow that. That would have blocked every later right-sizing on Google,
not only this test.

## Decision

- On Google, machines behind an internal load balancer go in the tier's first
  subnet. Google's subnets are regional and every machine is in one zone, so the
  spread added nothing there. Other clouds are unchanged: on AWS the subnets are
  availability zones.
- Google machines set `allow_stopping_for_update`: a new size or subnet stops and
  starts the machine, as on the other clouds. Pulumi too.

## Results, after the fixes

| Check | Result |
|---|---|
| Apply | Complete; 3 added and 6 changed in place, nothing replaced |
| Second plan (data disk fix, ADR 0089) | No changes to any resource |
| Switch to the `for_each` layout against the live state | 0 to add, change or destroy; 14 moved |
| Disks | Every boot and data disk at its planned size, attached to its machine; Windows boot disk raised from 40 to 50 GB (ADR 0089) |
| Linux and Windows | All booted and ran their startup scripts; each saw its data disks |
| Public load balancer | Served from both web machines; with one stopped, all traffic went to the other within about 10 seconds |
| Internal load balancer (probe fix, ADR 0089) | Both app machines healthy; reached from the web tier, both answering |
| NAT | Every private machine, Linux and Windows, reached the internet with no public address |
| Destroy | 37 destroyed; every resource type in the project lists none |

Running time 12 minutes. The test scripts were added by hand for the traffic checks
and are not part of the generated code. No Windows password was created or read.

## Consequences

- Data disks are attached empty, as the plan says. Windows shows them as raw
  disks; filling them is the data migration's job.
- HTTPS isn't proven: port 443 is open on the load balancer, and nothing listened.
- Still not proven on a real cloud: AWS, Azure, Oracle, DigitalOcean, Nutanix and
  Proxmox; adopting a running server; customer-managed keys; remote state; a large
  estate.
