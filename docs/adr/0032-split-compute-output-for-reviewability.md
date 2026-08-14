# 0032 — Split generated compute output so a human can actually review it

**Status:** Accepted

## Context

Stress-testing the documented `MAX_VMS = 5000` ceiling produced a good result
and a bad one. Good: the pipeline handled 5,000 workloads in ~5 seconds and the
output passed real `tofu validate`. Bad: it was a **single 95,001-line
`compute.tf`**.

That file is valid and unreviewable. The product's central claim is
*reviewable, auditable* Infrastructure-as-Code — the thing that distinguishes
it from "an LLM wrote some Terraform" — and a file no engineer will ever open
does not honour that claim. This is the failure mode the Terraform community
already names: the monolithic `main.tf` that nobody wants to touch, where code
review becomes impossible because every change lands in the same giant file.

It is also, notably, the exact weakness of the incumbent in the adjacent space:
Terraformer (now archived) was widely described as producing working code with
hardcoded values and minimal structure. Output *structure* is a real axis of
product quality, not a cosmetic one.

## Decision

Above a threshold (`IACTRANSLATE_SPLIT_COMPUTE_ABOVE`, default 50 workloads),
the compute output is split into one file per **environment + tier**:

```
compute-production-web.tf     compute-development-web.tf
compute-production-app.tf     compute-development-app.tf
compute-production-database.tf     …
```

Three properties made this safe and worth doing:

1. **It is purely organizational.** Terraform loads every `.tf` in a directory
   as a single configuration, so splitting changes no resource address, no
   dependency, and no state. There is nothing to migrate, and an existing
   project re-generated after this change plans identically.
2. **The compute templates contain nothing global.** Verified before
   implementing: in all five cloud templates, 100% of emitted content sits
   inside a per-VM loop. Had any shared resource been rendered outside the
   loop, splitting would have duplicated it and broken the config.
3. **Small projects are left alone.** Below the threshold a single short file
   is genuinely nicer than six near-empty ones, so the seven-VM sample is
   unchanged.

Grouping by environment then tier follows the conventional split (by
environment, then by component) and matches the two signals the wave planner
already sequences migrations on ([ADR 0024](0024-migration-wave-planning.md)) —
so the files line up with the order the work is actually executed and reviewed
in. A reviewer approving "production web" reviews one file.

## Consequences

- The 5,000-VM estate becomes **12 files of ~8,000 lines** instead of one of
  95,001, and still validates as one configuration with all 5,000 instances
  present — both asserted.
- A test proves the split is lossless: the same estate rendered split and
  unsplit contains exactly the same resource declarations, with no duplicates.
- ~8,000 lines per file is better, not small. The remaining size is inherent to
  5,000 machines at ~19 lines each; the win is that a reviewer can now take one
  environment/tier slice at a time. Emitting reusable *modules* with a
  `for_each` over a data structure would compress this dramatically and is the
  natural next step — it is a much larger change to the templates and to what
  the customer reads, so it is deliberately not bundled with this one.
- The generated `main.tf` file listing now says `compute*.tf`, which stays true
  whether or not the split applied.
- The threshold is an environment variable so an operator can tune it, or set
  `0` to restore the previous single-file behaviour.
