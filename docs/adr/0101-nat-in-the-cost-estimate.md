# 0101. The cost estimate prices NAT

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-28

## Context

Since ADR 0100, every cloud that has private machines gets a NAT gateway: AWS,
Azure, Google Cloud and Oracle. The cost estimate priced compute, storage, Windows
licensing and load balancers, but not NAT (KI-090). A quote for private machines
came out about $36 a month low per estate, which is the dangerous direction.

## Decision

The estimate gets a **NAT** line, priced at each cloud's list rate, read from its
own price list on 27 November 2026:

| Cloud | Gateway | Address | Monthly (730 h) | Source |
|---|---|---|---|---|
| AWS | $0.045/h | Elastic IP, in-use public IPv4, $0.005/h | $36.50 | AWS VPC pricing page (its US East example); AWS public price file for the IPv4 address |
| Azure | $0.045/h ("Standard Gateway") | Standard static public IP, $0.005/h | $36.50 | Azure Retail Prices API |
| Google Cloud | $0.0014 per machine-hour for the machines using it; $0.044/h from 32 machines | $0.005/h | $1.02 per machine + $3.65, at most $35.77 | Cloud NAT pricing page |
| Oracle | not priced | — | — | Oracle publishes no NAT gateway rate we could read |
| DigitalOcean | no NAT gateway is generated | — | — | ADR 0023 |

- Machines using NAT are every machine without its own public address (the same
  rule the templates use, `graph.public_instances`).
- Data processed through the gateway ($0.045 per GB on all three clouds) depends on
  traffic the inventory doesn't record, so it is named on the estimate's list of
  what it leaves out, next to data transfer.
- On Oracle the estimate says NAT is not priced instead of showing nothing. A
  missing line must not read as free.
- NAT always uses these catalog rates. When live pricing is asked for, the pricing
  basis says so.

## Consequences

- Estimates on AWS, Azure and Google with private machines go up by the NAT line:
  $36.50 a month on AWS and Azure. For the 12-machine sample estate with 11
  private machines, Google's line is $14.89.
- The CLI summary and the executive report show the NAT line. The API's totals
  include it.
- KI-090 is fixed.
