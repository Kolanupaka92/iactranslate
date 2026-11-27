# 0100. Azure: NAT for private machines, identities in every tool, 1-vCPU sizes

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-27

## Context

ADR 0099 proved Azure with machines behind a public load balancer and left gaps:
private machines with no public load balancer in front (KI-076), the internal load
balancer, Red Hat, identities in Bicep and Pulumi, and 1-vCPU machines sized to 2.

## Decision

- **NAT gateway.** When the plan has private machines, Azure gets a NAT gateway with
  its own public address on every private subnet, as AWS and Google have NAT. Without
  it, private machines rely on Azure's default outbound access, which Microsoft is
  retiring. Public-subnet machines keep their own address or the public load
  balancer's.
- **Identities.** Bicep and Pulumi give each tier a user-assigned identity with no
  roles, wired to its machines, as Terraform does (ADR 0057).
- **Sizes.** Standard_B1s (1 vCPU, 1 GiB, $0.0104/h) and Standard_B1ms (1 vCPU,
  2 GiB, $0.0207/h) join the catalog, prices read from the Azure Retail Prices API
  on 27 Nov 2026. A 1-vCPU, 2 GiB machine was Standard_B2s; it is now B1ms.

## Results, 27 November 2026

Two Standard_D2as_v5 app machines in westus2, Red Hat 9.9 (10 GB data disk) and
Ubuntu 22.04, private, behind the internal load balancer. The Free Trial allows no
other size (B1ms, B1s, DS1_v2 and F1s refused, KI-077) and 4 vCPUs.

| Check | Terraform | Bicep | Pulumi |
|---|---|---|---|
| Deploy | 27 resources | Succeeded | 28 resources |
| Second run | No changes | Redeployed: same machines | 28 unchanged |
| No public address on either machine | Yes | Yes | Yes |
| Internet from both, through the NAT address | Yes | Yes | Yes |
| Identity on both machines | UserAssigned | UserAssigned | UserAssigned |
| Red Hat data disk | 10 GB seen | 10 GB seen | 10 GB seen |
| Internal load balancer | Each machine reached the other through it | Same | Same |
| Destroy | Group gone | Group gone | Group gone |

Found: **Red Hat on Azure runs its own firewall** (firewalld allows only ssh,
cockpit and dhcpv6-client), so the load balancer marked it unhealthy until port
8080 was opened in the machine (KI-088). The generated infrastructure doesn't
configure the operating system; a migrated server brings its own firewall rules.

A call from a machine to its own internal load balancer fails when Azure sends it
back to that machine (KI-089); test from another machine.

## Not done

- The cost estimate leaves out NAT on every cloud, now about $36 a month on Azure
  (gateway plus address) (KI-090).
- A machine with its own public address, and Windows behind the internal load
  balancer, are still unproven on Azure.

## Consequences

- Existing Azure deployments with private machines gain a NAT gateway and its
  address on the next apply. Their outbound address changes to the NAT's.
- Existing Bicep and Pulumi Azure machines gain an identity in place.
- New plans size small Azure machines down; an existing deployment of a 1-vCPU
  machine would be resized on its next apply.
