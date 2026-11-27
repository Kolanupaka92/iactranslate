# 0099. First real Azure deployment: Terraform, Bicep and Pulumi

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-27

## Context

Azure was the one big cloud never deployed for real. The account is a Free Trial,
and its limits shaped the test:

- 4 vCPUs in a region, and 3 public addresses.
- No capacity for any small B-series size (B1ms, B1s) in eight US regions:
  `SkuNotAvailable ... Capacity Restrictions`.
- Standard_A1_v2 has capacity but `cannot boot Hypervisor Generation '2'`, and every
  current image is generation 2.

So the test was two Standard_D2as_v5 machines in westus2, the size the tool picks
for them: an Ubuntu and a Windows Server 2022 web machine, with a 10 GB data disk
on Windows, behind the public load balancer. It doesn't cover the internal load
balancer, Red Hat, or a machine with no load balancer in front.

## What failed, and the fixes

| Tool | Seen on Azure | Fix |
|---|---|---|
| Terraform | Every plan after the apply wanted `vm_agent_platform_updates_enabled = true -> false` | Ignored: Azure sets it itself |
| Terraform | Linux machines had no identity; Windows ones had their tier's | Linux machines get it too, both layouts |
| Bicep | Subnets refused: "Another operation on this or dependent resource is in progress" | Subnets declared inside the network, which also stops a redeploy deleting them |
| Bicep | "Linux host name cannot ... contain" `_`; "Windows computer name cannot be more than 15 characters" | Machines named as in Terraform |
| Pulumi | "name ... plus 7 random chars is longer than maximum length 24" (disks, then probes) | Every named Azure resource named as in Terraform |
| Pulumi | Same agent-updates drift as Terraform | Ignored |

Found while fixing, before any cloud saw it:

- **Windows computer names over 15 characters** (`prod-sql-server-01`) would fail
  in all three tools. Longer names now keep 10 characters plus a 4-character hash.
- **Machines whose names clean up alike** (`DEV_WEB_01`, `dev web 01`) got one cloud
  name between them, on every target that cleans names (Azure, Google, Oracle,
  DigitalOcean, Nutanix, Proxmox). The first keeps its name and the next gets its
  own. Names that never clashed don't change, so existing deployments keep their
  machines.

## Results, 27 November 2026

| Check | Terraform | Bicep | Pulumi |
|---|---|---|---|
| Deploy | 25 resources | Succeeded | 25 resources |
| Second run | No changes | Redeployed: same machines | 25 unchanged |
| Machines have no public address | Yes | Yes | Yes |
| Public load balancer | 6 and 6 of 12 requests to each machine | Same | Same |
| Internet from Ubuntu and Windows | HTTP 200 | HTTP 200 | HTTP 200 |
| Windows data disk | 10 GB seen | 10 GB seen | 10 GB seen |
| Linux identity | Added in place after the fix | — | — |
| Destroy | Group gone | Group gone | Group gone |

Bicep's `what-if` lists Azure's defaults (`idleTimeoutInMinutes: 4`,
`sku.tier: Regional`) as changes; redeploying changed nothing.

**KI-076, private machines with no way out:** not seen. Machines behind the public
load balancer go out through its address, and the subnets still have Azure's
default outbound access. That default is being retired, and a machine with no
public load balancer in front wasn't tested, so it stays open.

## Not done

- Bicep and Pulumi give Azure machines no identity, while Terraform does.
- The Azure catalog's smallest size is Standard_B2s (2 vCPUs), so 1-vCPU machines
  are sized up.
- The internal load balancer, Red Hat and private machines without a load balancer
  are unproven on Azure.

## Consequences

- Existing Terraform deployments: Linux machines gain their identity in place.
- Existing Bicep or Pulumi Azure deployments: machine, network card and disk names
  change, so the next deploy replaces them. Neither tool had ever deployed
  successfully to Azure, so none should exist.
- Everything Azure creates is gone; Azure's own Network Watchers stay, at no cost.
