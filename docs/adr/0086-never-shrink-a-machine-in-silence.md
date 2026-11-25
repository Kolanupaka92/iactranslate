# 0086. Never shrink a machine in silence

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-25

## Context

When no size in a target's catalog was large enough, the largest by vCPU was
used, and nothing said so. A machine with 64 vCPU and 512 GiB became 48/192 on
AWS, 32/128 on Azure, Google and Oracle, and 8/64 on DigitalOcean. On the
realistic sample estate, DigitalOcean cut 6 of 25 machines below what they need.

It also made the affected cloud look cheap, and a good fit, for giving machines
less than they need.

The catalogs were the first half of the problem. They stopped well short of what
each cloud sells.

## Decision

**Larger sizes in every catalog:** up to 128 vCPU / 4,096 GiB on AWS, 128 / 3,892
on Azure, 160 / 3,844 on Google, 126 / 1,024 on Oracle, 32 / 256 and 40 / 160 on
DigitalOcean, and 128 / 2,048 as an on-premises shape.

**Headroom gives way first.** If nothing has room for the usual headroom, the
demand itself is fitted, and the reason says the headroom is missing.

**A machine that still doesn't fit is marked `undersized`.** The size that covers
the most of it is used, so there is something to review, and every surface says
it is too small: the sizing reason and `decisions.json`, the README of every
renderer, the migration summary, the backlog ticket, the CLI, the API and the
console. Its sizing confidence is zero.

**That cloud is not eligible** in the recommendation, as with an operating system
it has no image for (ADR 0038). If no cloud is eligible, the summary says so
instead of naming a winner. The console now marks clouds that aren't eligible;
it showed their price alone.

**An AI provider's choice that is too small** is replaced when something fits.
Before, it only had to exist.

## Prices

Read on 2026-11-24 from AWS, Azure, Oracle and DigitalOcean. This corrected two
existing errors: Azure's E-series was about 11% too dear (AWS's r5 rates), and Oracle's
E5 memory rate was 0.0017 and is 0.002. Google's machine types were confirmed
with gcloud, but their prices are from a third-party list.

## Consequences

- Compared on six inventories and all seven targets: 45 of 420 machines changed
  size and 26 changed price only. Most size changes are machines that were being
  shrunk or had less than the usual headroom; a few found a cheaper shape.
- Estimates rise where machines were being shrunk, DigitalOcean's most.
- **Regenerating an existing project proposes resizing those machines**, which
  restarts them. Review the plan before applying.
- Planned by Terraform (mocked providers) on all seven targets, in both Terraform layouts, with machines
  up to 400 vCPU / 9,000 GiB. Whether a region has the capacity, or the account
  the quota, is known only when applying: the largest types usually need a quota
  increase.
- Larger machines exist than the catalogs list (AWS's high-memory u-series, for
  one). They are now reported as not fitting instead of being shrunk.
- DigitalOcean's dedicated general purpose sizes start at 16 vCPU in the catalog,
  so a smaller one can be dearer than it needs to be.
- Oracle's sizes still treat 1 OCPU as 1 vCPU. It is 2, so its machines are
  larger than they need to be. That is a separate decision.
