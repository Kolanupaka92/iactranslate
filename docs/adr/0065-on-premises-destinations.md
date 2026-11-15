# 0065. On-premises destinations, and what it means to have no price

**Status:** Accepted
**Date:** 2026-09-15

## Context

Every target was a public cloud. That was the wrong shape for the market, and
the competitive analysis made it uncomfortable: most estates leaving VMware
after the Broadcom licensing changes are moving to *another hypervisor* —
71% of organisations are considering one, and Nutanix alone claims ~30,000
migrated customers — not to AWS, Azure or GCP. A "neutral" recommendation
that could only ever answer "which cloud" was not neutral about the biggest
question a migration lead actually has.

It also mattered for the moat. AWS Transform and Azure's Copilot Migration
Agent now do discovery, dependency mapping and wave planning for free; what
neither can ever do is tell a customer to stay on-premises. That is a
structural conflict with their business, not a roadmap gap. Being the tool
that *can* say it turns "which of five clouds" into "cloud, or not" — a
harder question, and one nobody else will answer honestly.

## Decision

**Nutanix AHV and Proxmox VE as first-class targets** — `targets/nutanix/`
and `targets/proxmox/`, using the same pipeline, the same right-sizing
evidence (ADR 0064) and the same real-provider validation as the clouds. The
architecture's promise that a new target touches only `targets/<name>/` held:
neither needed a change to the pipeline, the renderers or the plan.

**They carry no price, and that is a capability, not a gap.** `CAP_PRICED`
marks targets whose cost an inventory can compute. Public clouds publish
per-instance prices; a hypervisor's cost is hardware amortisation, licensing,
power and people, none of which an export contains. Rather than invent a
number, an unpriced target is:

- **excluded from cost ranking** by the recommender, and listed in a new
  `not_ranked` field with the reason — stated, so absence from the table is
  never read as "not an option";
- **given an all-zero cost breakdown flagged `priced=False`**, which every
  surface renders as *"not estimated"* and never as `$0.00`.

The second rule was learned the hard way. The cost engine's rate tables
carried defaults for unknown clouds, and the first on-premises render showed
**$1,883/month** — storage, Windows licensing and load balancers priced at
public-cloud rates for a platform where none of that is billed. A fabricated
number, produced by code that was correct for every target it had ever seen.
Had it been allowed into the recommender, a `$0` would have made the
hypervisor "cheapest" by construction and flattened every real cloud's score
against a zero baseline — the most flattering lie the tool could tell.

**Sizing uses a standard grid, not the source allocation.** Both platforms
accept any vCPU/memory value, which is freedom and a trap: carrying every
allocation across exactly would reproduce the over-provisioning the migration
exists to leave behind, and would leave nothing to right-size *to*. The shared
`_onprem.py` catalog gives `smallest_fit` a real set, exactly as OCI's Flex
shapes needed one. Every value is editable after generation.

**Windows stays Windows.** A cluster runs whatever the operator uploads, so
unlike DigitalOcean there is no substitution and the OS-family check has
nothing to report.

**Firewalling is handled where each platform's model allows it.** Proxmox has
a named, attachable security group — the exact shape the plan carries — so
rules are generated for real. Nutanix segments by category through Flow, whose
policy schema is licence-dependent and intricate enough that a wrong-but-valid
rule would pass `validate` and leave a port open; categories are generated,
the ingress intent is recorded, and the Flow policy is left to a human. Both
READMEs say which.

## Consequences

Seven targets, all provider-validated in CI. Adding the on-premises pair to
the validate run exposed that the maturity table (ADR 0064) had been
*under*-claiming OCI and DigitalOcean: the validate test already ran all five,
but the CI step was named "(aws/azure/gcp)" and the maturity test read the
label instead of the test. It now reads the test's own parameter list.

The renderer matrix (ADR 0061) confirmed `terraform` and `kubernetes` for both
new targets with no manual entry — the self-verifying design working as
intended.

The dashboard sums only priced estates and says how many are not; the console
renders *"Not estimated"* with the reason; the landing page's comparison table
gains the row that is now the sharpest one on it: *"Will recommend staying
on-premises when that is right — Yes / Never / Never / Never."*

Real-provider validation caught one wrong resource name in the Proxmox
templates before it shipped. Everything Nutanix rendered validated first time.
