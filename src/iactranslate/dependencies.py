"""Application dependencies, from observed network flows.

The sharpest product criticism in the architecture review: *you cannot correctly
group applications from a static inventory.* Tiers are currently inferred from
names and ports, which is a heuristic that misgroups real estates — and a
migration wave built on a wrong grouping breaks at cutover, which is the most
expensive moment to discover it.

The fix is not cleverer name-matching. It is **evidence**: a flow export from
whatever the customer already runs — vRealize Network Insight, Cisco Secure
Workload, a NetFlow/sFlow collector, even `ss`/`netstat` snapshots — says which
machines actually talk to which. Every such tool exports a table of
`source, destination, port, volume`, so that is what this reads.

Three findings come out, in ascending order of how much trouble they save:

**Dependencies.** `web-01` talks to `db-01:5432`. The database must exist before
the web tier is cut over, whatever the name-based tier guess said.

**Wave violations.** A workload scheduled to move before something it depends on
is a cutover that fails. Checked against the waves the tool already plans, so the
answer is "wave 3 moves `app-02` before `db-01` in wave 5", not a graph to
interpret.

**External dependencies.** Flows to addresses that are *not* in the inventory are
the ones nobody remembers: a mainframe, a licence server, a partner API. They do
not migrate, so they must stay reachable — and they are invisible to any analysis
that only looks at the inventory.

**This is an analysis engine.** It reads the plan and the flows and changes
neither (ADR 0007). It does not reclassify tiers: the evidence is presented for a
human to act on, because a flow table can be incomplete and quietly overriding a
plan with partial data would be worse than the heuristic it replaced.
"""
from __future__ import annotations

import csv
import ipaddress
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field

from .models import NormalizedVM

#: Ports where "everything talks to it" is infrastructure, not an application
#: dependency. Without this filter every workload depends on the DNS server and
#: the graph says nothing — the single most common way a dependency map becomes
#: useless.
INFRASTRUCTURE_PORTS: Set[int] = {
    53,     # DNS
    123,    # NTP
    67, 68,  # DHCP
    161, 162,  # SNMP
    514,    # syslog
    389, 636,  # LDAP — arguably an app dependency, but universal in AD estates
    88, 464,  # Kerberos
    5353,   # mDNS
}

#: Flows below this are noise: a stray probe, a port scan, one health check.
MIN_CONNECTIONS = 2

#: Column synonyms, so an export does not have to be reshaped by hand. Every
#: tool names these differently and none of them agree.
_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "src_ip": ("src_ip", "source_ip", "src", "source", "source address", "src address",
               "client_ip", "sourceipaddress"),
    "dst_ip": ("dst_ip", "dest_ip", "destination_ip", "dst", "destination",
               "destination address", "server_ip", "destinationipaddress"),
    "dst_port": ("dst_port", "dest_port", "destination_port", "port", "server_port",
                 "destinationport", "service_port"),
    "connections": ("connections", "flows", "sessions", "count", "hits", "packets"),
    "bytes": ("bytes", "total_bytes", "octets", "volume"),
}


class DependencyError(ValueError):
    pass


@dataclass(frozen=True)
class Flow:
    src_ip: str
    dst_ip: str
    dst_port: int
    connections: int = 1
    bytes_: int = 0


class Dependency(BaseModel):
    """`source` needs `target` to be up."""

    source: str = Field(description="vm_name of the dependent workload")
    target: str = Field(description="vm_name of the workload depended upon")
    ports: List[int] = Field(default_factory=list)
    connections: int = 0


class ExternalDependency(BaseModel):
    """A workload talks to something outside the inventory."""

    source: str
    address: str
    ports: List[int] = Field(default_factory=list)
    connections: int = 0
    is_private: bool = Field(
        description="A private address is probably an internal system nobody "
                    "listed; a public one is probably a third-party service."
    )


class WaveViolation(BaseModel):
    """A workload is scheduled to move before something it depends on."""

    source: str
    source_wave: int
    target: str
    target_wave: int
    ports: List[int] = Field(default_factory=list)


class DependencyReport(BaseModel):
    schema_version: int = 1
    dependencies: List[Dependency] = Field(default_factory=list)
    external: List[ExternalDependency] = Field(default_factory=list)
    violations: List[WaveViolation] = Field(default_factory=list)
    must_move_together: List[List[str]] = Field(
        default_factory=list,
        description="Groups that call each other in both directions; splitting "
                    "one across waves breaks it in either order.",
    )
    mapped_flows: int = 0
    unmapped_flows: int = 0
    filtered_flows: int = 0
    notes: List[str] = Field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        """Share of flows attributed to a known workload. Low coverage means the
        inventory and the flow export disagree about the estate."""
        total = self.mapped_flows + self.unmapped_flows
        return round(self.mapped_flows / total * 100, 1) if total else 0.0


def _normalize_header(name: str) -> str:
    """`Destination Port` -> `destination_port`.

    Spaces matter as much as hyphens: several tools export title-case headers
    with spaces, and normalising only one of the two silently failed to match
    them — which surfaced as "no recognisable dst_port column" on an export that
    plainly had one.
    """
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def _pick(header: Sequence[str], key: str) -> Optional[str]:
    normalized = {_normalize_header(h): h for h in header}
    for candidate in _COLUMNS[key]:
        match = normalized.get(_normalize_header(candidate))
        if match is not None:
            return match
    return None


def parse_flows(path: str | Path) -> List[Flow]:
    """Read a flow export. Tolerant of column naming, strict about content."""
    rows: List[Flow] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise DependencyError(f"{path} has no header row")
        cols = {key: _pick(reader.fieldnames, key) for key in _COLUMNS}
        for required in ("src_ip", "dst_ip", "dst_port"):
            if not cols[required]:
                raise DependencyError(
                    f"{path} has no recognisable {required} column "
                    f"(looked for: {', '.join(_COLUMNS[required])})"
                )
        for row in reader:
            try:
                port = int(float(str(row[cols["dst_port"]]).strip()))
            except (TypeError, ValueError):
                continue  # a malformed row is skipped, not fatal
            src = (row.get(cols["src_ip"]) or "").strip()
            dst = (row.get(cols["dst_ip"]) or "").strip()
            if not src or not dst:
                continue
            rows.append(Flow(
                src_ip=src,
                dst_ip=dst,
                dst_port=port,
                connections=_as_int(row.get(cols["connections"]) if cols["connections"] else None, 1),
                bytes_=_as_int(row.get(cols["bytes"]) if cols["bytes"] else None, 0),
            ))
    return rows


def _as_int(value, default: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _index_by_ip(vms: Iterable[NormalizedVM]) -> Dict[str, str]:
    """IP -> vm_name. A duplicate IP is a data-quality problem, not a crash."""
    index: Dict[str, str] = {}
    for vm in vms:
        for ip in vm.ip_addresses or []:
            index.setdefault(ip.strip(), vm.vm_name)
    return index


def _is_private(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_private
    except ValueError:
        return False


def analyze_dependencies(
    flows: Sequence[Flow],
    vms: Sequence[NormalizedVM],
    waves=None,
    min_connections: int = MIN_CONNECTIONS,
    ignore_ports: Optional[Set[int]] = None,
) -> DependencyReport:
    """Turn observed flows into dependencies, violations and external calls.

    Direction comes from the destination port: the side being connected *to* is
    the one that must already be running. That is what makes this a dependency
    rather than a symmetric association.
    """
    ignore = INFRASTRUCTURE_PORTS if ignore_ports is None else ignore_ports
    by_ip = _index_by_ip(vms)

    pairs: Dict[Tuple[str, str], Dict[str, object]] = defaultdict(
        lambda: {"ports": set(), "connections": 0}
    )
    externals: Dict[Tuple[str, str], Dict[str, object]] = defaultdict(
        lambda: {"ports": set(), "connections": 0}
    )
    mapped = unmapped = filtered = 0

    for flow in flows:
        if flow.dst_port in ignore or flow.connections < min_connections:
            filtered += 1
            continue
        source = by_ip.get(flow.src_ip)
        if source is None:
            # The *caller* is unknown, so there is no workload to attribute this
            # to. Counted, not guessed at.
            unmapped += 1
            continue
        target = by_ip.get(flow.dst_ip)
        if target is None:
            entry = externals[(source, flow.dst_ip)]
            entry["ports"].add(flow.dst_port)  # type: ignore[union-attr]
            entry["connections"] = int(entry["connections"]) + flow.connections
            mapped += 1
            continue
        if target == source:
            filtered += 1  # a workload talking to itself is not a dependency
            continue
        entry = pairs[(source, target)]
        entry["ports"].add(flow.dst_port)  # type: ignore[union-attr]
        entry["connections"] = int(entry["connections"]) + flow.connections
        mapped += 1

    dependencies = [
        Dependency(source=s, target=t, ports=sorted(v["ports"]), connections=int(v["connections"]))
        for (s, t), v in sorted(pairs.items())
    ]
    external = [
        ExternalDependency(
            source=s, address=a, ports=sorted(v["ports"]),
            connections=int(v["connections"]), is_private=_is_private(a),
        )
        for (s, a), v in sorted(externals.items())
    ]

    report = DependencyReport(
        dependencies=dependencies,
        external=external,
        must_move_together=_mutual_groups(dependencies),
        mapped_flows=mapped,
        unmapped_flows=unmapped,
        filtered_flows=filtered,
    )
    if waves is not None:
        report.violations = _wave_violations(dependencies, waves)

    report.notes = _notes(report, len(vms), by_ip)
    return report


def _mutual_groups(dependencies: Sequence[Dependency]) -> List[List[str]]:
    """Workloads that call each other in both directions.

    Whichever moves first, the other is left calling a machine that has gone —
    so they have to move together, and a wave plan that splits them is wrong in
    either order.
    """
    edges = {(d.source, d.target) for d in dependencies}
    mutual: Dict[str, Set[str]] = defaultdict(set)
    for source, target in edges:
        if (target, source) in edges:
            mutual[source].add(target)
            mutual[target].add(source)

    seen: Set[str] = set()
    groups: List[List[str]] = []
    for node in sorted(mutual):
        if node in seen:
            continue
        stack, component = [node], set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(mutual[current] - component)
        seen |= component
        if len(component) > 1:
            groups.append(sorted(component))
    return groups


def _wave_violations(dependencies: Sequence[Dependency], waves) -> List[WaveViolation]:
    """Dependencies that the planned wave order gets backwards."""
    wave_of: Dict[str, int] = {}
    for wave in getattr(waves, "waves", []):
        for name in wave.workloads:
            wave_of[name] = wave.sequence

    violations: List[WaveViolation] = []
    for dep in dependencies:
        source_wave = wave_of.get(dep.source)
        target_wave = wave_of.get(dep.target)
        if source_wave is None or target_wave is None:
            continue
        # Equal is fine: moving together in one wave is exactly right.
        if source_wave < target_wave:
            violations.append(WaveViolation(
                source=dep.source, source_wave=source_wave,
                target=dep.target, target_wave=target_wave, ports=dep.ports,
            ))
    return sorted(violations, key=lambda v: (v.source_wave, v.source))


def _notes(report: DependencyReport, vm_count: int, by_ip: Dict[str, str]) -> List[str]:
    notes: List[str] = []
    if not by_ip:
        notes.append(
            "No workload in the inventory has an IP address, so no flow can be "
            "attributed. Re-export the inventory with the IP column included."
        )
        return notes
    if report.coverage_pct < 60 and (report.mapped_flows + report.unmapped_flows):
        notes.append(
            f"Only {report.coverage_pct:.0f}% of flows matched a workload in the "
            f"inventory. The two exports probably cover different scopes — check "
            f"they were taken from the same estate and time window."
        )
    if report.violations:
        notes.append(
            f"{len(report.violations)} planned move(s) happen before a dependency "
            f"they need. Each one is a cutover that fails; reorder the waves or "
            f"move the pair together."
        )
    if report.must_move_together:
        notes.append(
            f"{len(report.must_move_together)} group(s) call each other in both "
            f"directions and cannot be split across waves in either order."
        )
    off_estate = [e for e in report.external if not e.is_private]
    if off_estate:
        notes.append(
            f"{len(off_estate)} workload-to-public-address dependencies — likely "
            f"third-party services that must remain reachable from the new network."
        )
    private_external = [e for e in report.external if e.is_private]
    if private_external:
        notes.append(
            f"{len(private_external)} dependencies on private addresses that are "
            f"not in the inventory: systems nobody listed, and the usual source of "
            f"a migration that works in test and fails in production."
        )
    return notes
