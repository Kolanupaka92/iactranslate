# 0038. A cloud that cannot run the estate is not a candidate

**Status:** Accepted
**Date:** 2026-08-20

## Context

On the realistic 25-VM RVTools estate (8 Windows / 17 Linux), the recommender
returned **DigitalOcean**, scored 0.85 against a runner-up at 0.60, with the
reason *"Lowest projected cost ($5,976.31/mo)."*

DigitalOcean publishes no Windows Server image at all. We knew this — ADR 0023
records it — and the mitigation was a warning in the generated README. That was
not enough, because three separate mechanisms combined to bury it:

1. **`image_key` lied.** Every `windows-*` key in DigitalOcean's catalog maps to
   `ubuntu-22-04-x64`. The plan said `windows-2019`; the Droplet would boot
   Ubuntu. A Windows application server would be provisioned as Linux.

2. **The substitution check was blinded by the lie.** ADR 0035's
   `os_substitution_note()` compares the source OS version against the image
   *key*. Source `Microsoft Windows Server 2019` against key `windows-2019`
   matched, so the check stayed silent — on the single most consequential
   substitution the tool can make. It correctly flagged 2012 R2 → 2022 while
   saying nothing about Windows → Linux.

3. **The cost comparison rewarded the gap.** DigitalOcean skipped Windows
   licensing because it cannot run Windows. That is not a discount; it is the
   absence of the workload. It won on cost by not doing the job.

Presented to a client, this recommends a migration that would fail, and the
first cloud architect to read it would stop trusting the whole document.

## Decision

**1. A target's `image_key` must name the image that will actually be
provisioned.** DigitalOcean now returns `ubuntu-22.04` for Windows sources.
`image_key` is a statement of fact about the resulting infrastructure, not a
record of what was asked for. With the lie removed, the existing substitution
check fires on its own.

**2. An OS *family* change is reported differently from a version change.** A
version substitution warrants "verify application compatibility". A family
change means the workload will not boot, and no amount of testing fixes it, so
it says so and names the remedy (different cloud, or a custom image).

**3. Structured flags, not string prefixes.** `ComputePlan` gains `source_os`
and `os_family_changed`. The DigitalOcean README previously detected Windows
workloads with `image_key.startswith('windows')`; once `image_key` became
truthful that test found nothing and the warning would have silently vanished —
the fix deleting the warning it exists to serve. Renderers now read the flag.

**4. Eligibility gates the recommendation.** `_unsupported_count()` asks each
target's own catalog how many workloads it has no image for. A cloud with any
such workload is marked `eligible=False`, scores **0.0**, cannot be recommended,
and is excluded from the cost baseline that every other cloud's "$X more than
the cheapest" is measured against.

## Consequences

The recommendation for the realistic estate moved from **DigitalOcean** to
**OCI**, and the quoted spend range from "$71,716 (DIGITALOCEAN) to $192,363" to
"$106,966 (OCI) to $192,363" — the earlier low end was never available.

DigitalOcean still appears in the ranked table, with its cost shown "for
reference only" and its component scores intact, so a reader can see exactly
what it would have scored and why it was excluded. Suppressing it entirely would
hide a real option from anyone whose estate is all Linux — where it remains
eligible and frequently wins.

`weighted_score` is no longer `weights · components` for ineligible clouds.
ADR 0037 published the weights so the ranking could be checked by hand, so the
gate is stated rather than folded into the arithmetic: components stay published,
and the test asserts both the recompute for eligible clouds and the zeroing for
ineligible ones.

Nothing here names DigitalOcean. The count comes from each target's own catalog,
so a future target that drops an OS family is caught the same way.
