# 0085. Size a database by its shape, not by being a database

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-25

## Context

Databases and caches were forced into the memory-optimized family on every
target. The first real deployment made the cost plain: a database with 1 vCPU
and 2 GiB became a 16 GiB machine, $96 of a $154 estimate. The inventory already
says how much memory the machine has. Doubling it by tier is a guess, and it is
a guess the customer pays for.

The family rule also did one useful thing: it kept databases off shared and
burstable CPUs, which are throttled under the steady load a database has.

## Decision

- A database or cache gets the **cheapest machine that fits** its vCPU and
  memory and **doesn't share CPU**, marked by `shared_cpu` in each catalog: t3,
  B-series, e2-medium and DigitalOcean's `s-` sizes.
- Memory-optimized types are chosen when the shape needs them, because then they
  are the cheapest fit.
- If only a shared type fits, it is used.
- The AI provider's prompt says the same.

## Consequences

- Compared on six inventories and all seven targets: 58 of 420 machines changed,
  all databases or caches, none dearer. On the realistic estate, database and
  cache compute fell 6% (AWS), 8% (OCI), 9% (Azure) and 15% (Google).
  DigitalOcean is unchanged: its catalog's only dedicated sizes are memory-heavy.
- **Regenerating an existing project proposes resizing its databases**, which
  restarts them. Review the plan before applying.
- Per-core licensing is unaffected: no compared machine's vCPU count changed.
- The 20% added to every allocation without usage data is a separate and larger
  effect, and is unchanged here.
