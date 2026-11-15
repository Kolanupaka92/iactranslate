# 0069. Live rates for every line of the bill, not just compute

**Status:** Accepted
**Date:** 2026-10-21

## Context

`IACTRANSLATE_PRICING=live` fetched real instance prices (ADR 0059), but the
other three lines of the itemized bill (ADR 0039) — block storage, Windows
licensing and load balancers — came from tables in `costing.py` dated August
2026. Those lines are 20–47% of the total depending on the cloud (ADR 0048), so
a "live-priced" estimate was partly a catalog estimate with nothing saying so.

The tables had already drifted. The live Azure Retail Prices API, queried while
writing this, prices Standard SSD at **$0.075/GB-month** (E10: $9.60 for 128
GiB, and every tier is linear) against the table's $0.113. Windows licensing
($0.046/vCPU-hour) and the Standard Load Balancer ($18.25/month) still match.

## Decision

`pricing.live_line_rate(cloud, kind, region)` fetches one rate per line, with
the same on-disk cache as instance prices. `estimate_costs` uses it only when
the plan was built with live pricing requested, and falls back to the catalog
table **one line at a time**.

| Line | Azure (no credentials) | AWS (boto3) | GCP (`IACTRANSLATE_GCP_BILLING_API_KEY`) |
|---|---|---|---|
| Storage, $/GB-month | Standard SSD `E10 LRS Disk` ÷ 128 | `gp3` storage | `Balanced PD Capacity` |
| Windows, $/vCPU-hour | `Standard_D2s_v5` Windows − Linux, ÷ 2 | `m5.large` licence-included − Linux, ÷ 2 | Windows Server Datacenter licence (CPU) |
| Load balancer, $/month | `Standard Included LB Rules` (billed from `Global`) × 730 | ALB `Hrs` dimension × 730 | Forwarding-rule minimum charge × 730 |

AWS and Azure publish Windows as a VM price, not a licence price, so the uplift
comes from one reference instance. That is how the catalog's $0.046 was
derived, and the uplift is linear across a family.

`CostBreakdown.rate_sources` records `live`/`static` per line, and
`pricing_basis` names the lines in prose. If live was requested and nothing
arrived, it says so ("requested but unavailable") rather than reading like a
plan that never asked.

Line rates get **their own circuit breaker** (`<cloud>:rates`). They hit
different products than instance prices, and an unrecognised line SKU must not
push the next run's compute onto catalog rates.

OCI and DigitalOcean have no live source and stay on catalog rates. Their
lines report `static`.

## Consequences

- With live pricing on, an Azure estate's storage line drops by about a third.
  That is the point of the change, but it does move totals.
- The recommender still builds static plans, so the ranking stays reproducible
  and comparable across clouds (operations guide).
- Azure's queries were checked against the live API. The AWS and GCP filters
  follow each provider's published schema, but could not be exercised here
  without credentials. A mismatch fails safe: that line falls back to catalog,
  is labelled `static`, and is named in `pricing_basis`.
- The catalog tables remain the offline default and the fallback, and are still
  dated. Live pricing narrows that problem; it does not remove it.
