# 0098. One address per Google load balancer; service accounts for Pulumi's machines

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-27

## Context

ADR 0097 left two Google issues open:

- **KI-075:** the public load balancer's HTTP and HTTPS forwarding rules each took
  their own ephemeral address, so one web name couldn't serve both. Terraform and
  Pulumi alike.
- **KI-074:** Pulumi's Windows machines went silent after a restart. Unlike
  Terraform's, they had no service account, and Google's agent logged missing
  credentials; the test had also restarted them 90 seconds after creation.

## Decision

- The public load balancer reserves one regional address and every forwarding rule
  uses it (`google_compute_address`, `gcp.compute.Address`).
- Pulumi gives each tier a service account with no roles, as Terraform's default
  identity does (ADR 0057), with the same 6-to-30-character ids.

## Results, 27 November 2026

| Check | Result |
|---|---|
| Pulumi deploy | 38 resources; preview afterwards unchanged |
| HTTP and HTTPS | One address for both, Pulumi and Terraform |
| Service accounts | Each machine has its tier's; no credential errors from Google's agent |
| Windows, restarted after booting fully | Ran its script within 30 seconds: disks 60, 15 and 20 GB seen, NAT reached |
| Load balancers, NAT | Public served both web machines; internal healthy and reached; every private machine out through NAT |
| Destroy | Everything removed, service accounts included |

KI-074's cause was the missing service account, the early restart, or both; with
the account and a restart after boot, Windows works.

## Consequences

- **An existing Google deployment's public forwarding rules are replaced** on the
  next apply, to take the reserved address: the public address changes once.
  Update DNS afterwards.
- A reserved address costs while in use, as the ephemeral ones did.
- Newer Windows images don't print "Windows is Ready to use"; the MOPs now look for
  Google's startup script runner instead.
