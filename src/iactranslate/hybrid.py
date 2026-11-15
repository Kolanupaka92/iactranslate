"""Hybrid connectivity: the on-prem networks a migrated estate still needs.

Network planning is greenfield by design — a clean VPC/VNet carved from one
range, deterministic and repeatable. That is correct for the resources being
created and quietly wrong about the estate they land in. Large VMware estates
are not islands: they call an Oracle box that is not moving, an Active Directory
forest, a licence server, a mainframe, a payment gateway inside the corporate
network. Cut those over and the workload comes up and fails to work, which is
the most expensive moment to find out.

**The evidence is already collected.** `dependencies.analyze_dependencies` finds
flows to addresses that are *not in the inventory* and marks the private ones —
"an unlisted internal system". Those are precisely the on-prem endpoints that do
not migrate and must stay reachable. This module summarises them into routable
networks rather than inferring anything new, so the finding stays anchored to
observed traffic instead of a guess about CIDR conventions.

**The finding that saves the most trouble is the overlap.** If the estate talks
to `10.0.5.11` and the landing zone was allocated `10.0.0.0/16`, that address is
inside the new VPC's own range. No route can reach the on-prem host, because the
VPC claims the range for itself. This is unfixable after the fact — the landing
zone CIDR has to change, which means re-planning every subnet — and neither a
greenfield generator nor the cloud's own tooling will mention it.

**This is an analysis engine.** It reads the plan and the dependency report and
mutates neither (ADR 0007). It does not alter the landing zone or the network
plan: an overlap is reported for a human to resolve, because silently moving a
customer's allocated range would be a worse failure than the one it fixed.
"""
from __future__ import annotations

import ipaddress
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Set

from pydantic import BaseModel, Field

from .dependencies import DependencyReport

#: Observed host addresses are summarised to this prefix length. A route table
#: with 300 /32 entries is not reviewable and will hit route limits; /24 is the
#: unit corporate networks are actually allocated and documented in.
SUMMARY_PREFIX = 24


class OnPremNetwork(BaseModel):
    """A network outside the migration that migrated workloads still call."""

    cidr: str = Field(description=f"Observed addresses summarised to a /{SUMMARY_PREFIX}")
    addresses: List[str] = Field(default_factory=list, description="Addresses actually seen")
    ports: List[int] = Field(default_factory=list)
    connections: int = 0
    dependent_workloads: List[str] = Field(
        default_factory=list, description="Migrated workloads that call into this network"
    )
    overlaps_vpc: bool = Field(
        default=False,
        description="The range collides with the planned VPC's own CIDR, so no "
                    "route to it can exist. The landing zone allocation must change.",
    )


class HybridPlan(BaseModel):
    schema_version: int = 1
    vpc_cidr: str = ""
    networks: List[OnPremNetwork] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    @property
    def required(self) -> bool:
        """Whether any on-prem connectivity is needed at all."""
        return bool(self.networks)

    @property
    def conflicts(self) -> List[OnPremNetwork]:
        """Networks that cannot be routed to as the landing zone is allocated."""
        return [n for n in self.networks if n.overlaps_vpc]

    @property
    def routable(self) -> List[OnPremNetwork]:
        return [n for n in self.networks if not n.overlaps_vpc]

    @property
    def all_ports(self) -> List[int]:
        return sorted({p for n in self.routable for p in n.ports})


def _summarize(address: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_network(f"{address}/{SUMMARY_PREFIX}", strict=False))
    except ValueError:
        return None


def _overlaps(cidr: str, vpc_cidr: str) -> bool:
    try:
        return ipaddress.ip_network(cidr).overlaps(ipaddress.ip_network(vpc_cidr))
    except ValueError:
        return False


def plan_hybrid(
    report: DependencyReport,
    vpc_cidr: str,
    on_prem_cidrs: Optional[Sequence[str]] = None,
) -> HybridPlan:
    """Summarise the on-prem networks the estate depends on.

    `on_prem_cidrs` lets a customer's network team declare ranges directly, for
    the common case where no flow export exists. Declared ranges are trusted as
    given and not summarised — an allocation from an IPAM team is better data
    than anything inferred from sampled traffic.
    """
    grouped: Dict[str, Dict[str, object]] = defaultdict(
        lambda: {"addresses": set(), "ports": set(), "connections": 0, "workloads": set()}
    )

    for external in report.external:
        # Public addresses are third-party services reached over the internet;
        # they need egress, not a private circuit, and saying otherwise would
        # put a partner API into a VPN route table.
        if not external.is_private:
            continue
        cidr = _summarize(external.address)
        if cidr is None:
            continue
        entry = grouped[cidr]
        entry["addresses"].add(external.address)      # type: ignore[union-attr]
        entry["ports"].update(external.ports)         # type: ignore[union-attr]
        entry["connections"] = int(entry["connections"]) + external.connections
        entry["workloads"].add(external.source)       # type: ignore[union-attr]

    for declared in on_prem_cidrs or []:
        try:
            ipaddress.ip_network(declared)
        except ValueError:
            continue
        grouped.setdefault(str(ipaddress.ip_network(declared)), {
            "addresses": set(), "ports": set(), "connections": 0, "workloads": set(),
        })

    networks = [
        OnPremNetwork(
            cidr=cidr,
            addresses=sorted(v["addresses"]),      # type: ignore[arg-type]
            ports=sorted(v["ports"]),              # type: ignore[arg-type]
            connections=int(v["connections"]),
            dependent_workloads=sorted(v["workloads"]),   # type: ignore[arg-type]
            overlaps_vpc=_overlaps(cidr, vpc_cidr),
        )
        # Most-used first: the route that matters most should be read first.
        for cidr, v in sorted(grouped.items(), key=lambda kv: (-int(kv[1]["connections"]), kv[0]))
    ]

    plan = HybridPlan(vpc_cidr=vpc_cidr, networks=networks)
    plan.notes = _notes(plan)
    return plan


def _notes(plan: HybridPlan) -> List[str]:
    notes: List[str] = []
    if not plan.networks:
        return notes

    conflicts = plan.conflicts
    if conflicts:
        notes.append(
            f"{len(conflicts)} on-prem network(s) overlap the planned VPC range "
            f"{plan.vpc_cidr}: {', '.join(n.cidr for n in conflicts)}. No route to "
            "them can exist while the VPC claims the same addresses — the landing "
            "zone CIDR must be reallocated before cutover, not after."
        )
    routable = plan.routable
    if routable:
        workloads: Set[str] = {w for n in routable for w in n.dependent_workloads}
        notes.append(
            f"{len(workloads)} migrated workload(s) depend on {len(routable)} network(s) "
            "that are not migrating. They need connectivity back to the corporate "
            "network on day one; without it they will start and fail to work."
        )
    if routable:
        notes.append(
            "Connectivity resources are scaffolded, not configured: the customer "
            "gateway address and BGP ASN are properties of the customer's edge "
            "router and cannot be inferred from an inventory."
        )
    return notes
