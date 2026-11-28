# 0102. What four real RVTools exports showed

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-28

## Context

Every RVTools test so far used files this project made. The proof plan called
for a real export. There was none of our own, so four public ones were used:
`ashburn`, `dallas`, `dallas1` and `houston` from Red Hat's
`rhpds/etx-virt_delivery` repository (MIT licence), anonymised exports from four
vCenters. They hold 795, 3,740, 1,329 and 3,031 VMs, 9,000 in all, and up to
6 PB of disk. They are not in this repository.

All four were read: `assess`, `recommend` and `translate` to AWS and Azure
Terraform, the largest in 6 seconds. The output passed `tofu validate` and
`tofu fmt` on AWS with 3,738 machines. Each total was checked against the raw
sheets. Four were wrong.

## What was wrong

1. **Templates were counted as machines.** `dallas1` has 388 templates in 1,329
   rows. Each was sized, priced and generated as a server, and counted again among
   the powered-off machines. One SRM placeholder in `houston` likewise: the stand-in
   at a recovery site for a VM that runs elsewhere.
2. **Rows with no VM name vanished without a word.** `dallas` has two.
3. **A Windows Server 2008 R2 machine was counted twice,** under "Windows Server
   2008/R2" and under "Windows Server 2008 R2".
4. **Disks larger than a cloud accepts were generated as they were.** Azure's
   `validate` refused a 5,120 GiB boot disk:
   `expected os_disk.0.disk_size_gb to be in the range (0 - 4095), got 5120`.
   On AWS, six `ashburn` disks over 16 TiB passed `validate` and would fail when
   applied.

## Decision

- **Templates, SRM placeholders and nameless rows are left out**, and the
  assessment names them: "Left out of the estate: 388 templates". `normalize()`
  returns the machines as before, with what was left out alongside.
- **Each machine gets one end-of-life label**, the most specific it matches.
- **Disk limits per cloud**, boot and data, from each provider's documented
  limits: AWS 16,384 / 16,384 GiB (EBS gp3), Azure 4,095 / 32,767 (managed
  disks), Google 65,536 / 65,536 (Persistent Disk), Oracle 32,768 / 32,768,
  DigitalOcean volumes 16,384. A larger disk is planned at the limit, so the
  project still validates, and the machine is marked `disk_too_large`. That is
  said as `undersized` is (ADR 0086): in the sizing reason, the README, the
  migration summary, the backlog ticket, the CLI, the API and the console. Its
  sizing confidence is zero and that cloud is not eligible in the recommendation.

## Consequences

- Estimates fall where templates were counted: `dallas1` from 1,329 machines to
  941, `ashburn` from 795 to 791.
- On `ashburn`, AWS is no longer eligible: 6 of 791 machines have a disk over
  16 TiB. On `houston`, Azure is not, for one boot disk.
- A disk over the limit is not split. That needs the operating system's
  volume manager, so it is left to the migration team, and the plan says so.
- **Open:** RVTools' vCPU and vMemory sheets carry CPU (`Overall`, MHz) and
  memory (`Consumed`, `Active`) use, read when the export ran. They are not read,
  so all four estates show 0% utilization coverage and are sized to allocation.
  Using them would cut estimates, but they are a single moment, not a busy
  period. A separate decision (KI-095).
- Seen and fine: names differing only by cluster (`Test` four times) get their
  own names; new "MiB" and older column names both read; 32-bit Windows 2003 and
  2008 are flagged end-of-life.
