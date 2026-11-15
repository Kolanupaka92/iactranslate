# 0060. Hybrid connectivity, from observed flows

**Status:** Accepted
**Date:** 2026-08-29

## Context

`agents/network.py` plans a clean VPC/VNet carved from one range: subnets per
AZ, security groups, load balancers, all deterministic. That is correct about
the resources being created and quietly wrong about the world they land in.

Large VMware estates are not islands. They call an Oracle database that is not
moving, an Active Directory forest, a licence server, a mainframe, a payment
gateway inside the corporate network. Generated output that assumes a
self-contained estate applies cleanly, comes up, and does not work — and the
discovery happens at cutover, which is the most expensive possible moment.

## Decision

A new analysis engine, `hybrid.plan_hybrid`, and a `hybrid.tf` per target.

**The evidence already existed.** `dependencies.analyze_dependencies` (ADR 0055)
finds flows to addresses that are *not in the inventory* and flags the private
ones as "an unlisted internal system". Those are exactly the on-prem endpoints
that do not migrate. An earlier framing of this work proposed scanning input
records for "non-standard enterprise CIDRs", which would have been a guess about
a customer's addressing conventions; observed traffic is evidence. Reusing the
dependency report also means the two features cannot disagree about what the
estate talks to.

**Addresses are summarised to /24.** A route table with 300 host routes is
unreviewable and will hit route limits. A /24 is the unit corporate networks are
actually allocated and documented in.

**Public addresses are excluded.** A partner API reached over the internet needs
egress, not a private circuit. Putting one into a VPN route table would break
the thing it was trying to preserve.

**The overlap check is the finding worth the most.** If the estate calls
`10.0.5.11` and the landing zone was allocated `10.0.0.0/16`, that address is
inside the new VPC's own range: the VPC claims it, traffic never leaves, and no
route can fix it. The landing zone CIDR has to be reallocated, which re-plans
every subnet. Neither a greenfield generator nor the cloud's own tooling
mentions this. It is checked against the range the plan actually used, since the
CIDR can come from the customer's IPAM team (ADR 0044).

**Nothing is generated for an overlapping range.** Emitting a route that cannot
work is worse than emitting none, because it applies cleanly and fails later.

**Connectivity parameters are variables with no default.** The customer gateway
address and the IPsec pre-shared key are properties of the customer's edge
router and their secret store. `terraform plan` stops and asks — the same
deliberate failure as an incomplete state backend (ADR 0041). A plausible
placeholder that applied cleanly would build a tunnel to nowhere. Pre-shared
keys are additionally marked `sensitive` so they stay out of plan output.

**No billable physical circuit is ever generated.** Direct Connect, ExpressRoute
and Cloud Interconnect are physical cross-connects ordered from a provider with
lead times in weeks, and the resources bill from the moment they apply.
Emitting one from an inventory scan would charge a customer for hardware nobody
ordered. The generated comments say what to swap in once a circuit exists; the
routes are unchanged by that swap.

**OCI and DigitalOcean record the requirement without generating wiring.**
Silence would read as "no on-prem dependency exists", which is the opposite of
what the flows show.

**This is an analysis engine.** It reads the plan and the dependency report and
mutates neither (ADR 0007). In particular it does not move the landing zone to
resolve an overlap: silently reallocating a customer's assigned range would be a
worse failure than the one it fixed.

## Consequences

Reachable via `iactranslate translate --flows <export>`, and only then. With no
flow export the output is byte-identical to before, which is the right default:
absence of a flow export is not evidence of a self-contained estate, so the tool
says nothing rather than issuing an all-clear.

`plan_hybrid` also accepts declared CIDRs directly, for the common case of a
customer with no flow export but a network team who knows their ranges. Declared
ranges are trusted as given and not summarised — an IPAM allocation is better
data than anything inferred from sampled traffic.

The generated HCL is hand-written in templates rather than derived from a
provider schema, so string assertions prove only that the strings are present.
An opt-in test runs `tofu init && tofu validate` against the real AWS, Azure and
Google providers; all three pass.

An Azure virtual network gateway bills from creation (roughly USD 140/month) and
takes 30-45 minutes to provision. It is generated because the flows prove the
estate needs it, not because it is cheap, and the template says so at the point
of use.

Coverage is bounded by the flow export. A dependency that did not appear in the
capture window is invisible here, exactly as it is in ADR 0055 — which is why
the report states its coverage rather than implying completeness.
