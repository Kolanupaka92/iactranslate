# 0087. Add no headroom where there's no usage data

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-25

## Context

A machine without usage data was sized to its allocation plus 20%. Cloud sizes
double from one to the next, so 20% was rarely 20%: a 4 vCPU / 16 GiB machine
needed 4.8 / 19.2 and got 8 / 32, at twice the price. A machine that matched a
cloud size exactly could never get it.

An allocation already contains its owner's margin. Most VMware machines are
allocated more than they use, so the 20% was a margin on a margin. It also
flattered right-sizing: part of the saving shown for adding usage data was the
20% coming back off.

Partly measured machines were worse. With CPU usage alone, memory was treated as
fully used and given the 54% headroom meant for a measured number. The same went
the other way.

## Decision

- **No usage data: the allocation, as it is.** The smallest size holding it.
- **Each of CPU and memory stands alone.** Measured means usage plus headroom (to
  run near `IACTRANSLATE_TARGET_UTILIZATION`). Unmeasured means the allocation.
- The evidence says which was which, and no longer claims headroom for a number
  that has none. Confidence is unchanged: an unverified allocation is still an
  unverified allocation.
- The AI provider is given the same required vCPU and memory. Before, it wasn't
  told about usage at all.

## Consequences

- Compared on six inventories and all seven targets: 367 of 420 machines changed,
  none dearer. No machine without usage data is below its allocation. The
  inventory with full usage data is unchanged.
- Monthly totals, all lines of the bill, without usage data: the realistic estate
  falls 43% on AWS (20,602 to 11,843), 36% on Azure, 47% on Google, 43% on Oracle
  and 35% on DigitalOcean. The small sample falls 40% to 49%.
- **The recommendation changed on the small sample,** from Google to Oracle, by
  under 0.01: a close call before and after. The realistic estate is still Oracle.
- **Regenerating an existing project proposes resizing most of its machines**,
  which restarts them, and replaces a recommendation and estimate people may have
  quoted. Review the plan before applying.
- The estimate is no longer padded. A machine that was already short stays short.
  Usage data is what shows it, and the assessment still says when it is missing.
- 22 machines moved to shared or burstable CPUs (web, app and other tiers, not
  databases or caches), because those are now the cheapest that fit. They suit
  intermittent load and are throttled under a steady one.
- A cloud vCPU is usually a hyperthread. One that replaces a whole physical core
  does less work; usage data in vCPU can't show that either.
- The saving shown for adding usage data is smaller than before, and real.
