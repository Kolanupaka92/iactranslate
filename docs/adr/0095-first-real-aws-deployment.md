# 0095. First real AWS deployment: no way around the load balancer

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-26

## Context

The generated Terraform was applied to a new AWS account (Free plan, us-east-2)
on 26 November 2026: six machines (two Ubuntu web, Red Hat and Ubuntu app, two
Windows Server 2022), four data disks, three load balancers, NAT. 54 resources.

The first attempt failed on two things: the Free plan allows only free-tier
instance types (an account limit), and the HTTPS listener had no certificate
(fixed in ADR 0094). The test was then sized to t3.small throughout.

It then found a security defect. **Every machine in a public subnet got a public
address,** because the public subnets assigned one at launch. A web machine
behind the load balancer could be reached directly from the internet, around
it: confirmed by requesting a page from its address.

Removing the address alone would have cut those machines off from the internet:
on AWS, a public subnet reaches it only with a public address.

## Decision

- **Subnets assign no addresses.** Each machine says whether it gets one: only a
  machine in a public subnet with no load balancer in front. In Terraform,
  Pulumi, CloudFormation and CDK.
- **On AWS, a machine behind a public load balancer goes in a private subnet,**
  still spread across zones, and reaches the internet through NAT, which is now
  planned whenever there is a public load balancer. The load balancer stays in the
  public subnets. Other clouds are unchanged: Google already gave such machines no
  address and routes every subnet through NAT.
- An adopted server keeps the address it has (`ignore_changes`, ADR 0091).

## Results

| Check | Result |
|---|---|
| Apply (free-tier sizes) | 54 resources in about 3 minutes |
| Second plan | No changes |
| A different system image (ADR 0090) | No changes: no server replaced |
| Switch to the `for_each` layout against the live state | 0 to add, change or destroy; 14 moved |
| Disks | All 10 encrypted; data disks kept if their machine is deleted |
| Windows | Both "Windows is Ready to use" |
| HTTPS without a certificate (ADR 0094) | Plan warns; HTTP only; port 443 has no listener |
| Public load balancer | Served from both web machines; with one stopped, all traffic moved to the other after about 60 seconds (AWS's default health check: three failures, 30 seconds apart) |
| Internal load balancer | Healthy targets, reachable from the web tier, not from outside |
| After the fix | No machine has a public address; web machines in private subnets, served through the load balancer, reaching the internet through NAT |
| Destroy, each run | Everything destroyed; nothing left in the account |

Terraform signed in with `aws login` through a `credential_process` profile
(ADR 0094). Test scripts were added by hand and are not part of the generated
code. No Windows password was created or read.

## Consequences

- **An existing AWS deployment replaces the machines behind its public load
  balancer** on the next apply: their subnet changes, and an address can't be
  removed from a running instance. They are stateless web machines by
  construction; check the plan before applying.
- The Windows internal load balancer had no healthy targets: nothing listened on
  8080 on Windows in this test. Not a defect, but not proven either.
- Not proven on AWS: larger or non-burstable instance types (the Free plan doesn't
  allow them), Pulumi, CloudFormation and CDK deployments, and adopting a running
  server.
