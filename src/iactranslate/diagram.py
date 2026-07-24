"""Architecture diagrams — rendered from the Infrastructure Graph.

The diagram is the graph's natural consumer: `architecture_svg` / `architecture_mermaid`
walk the topology IR (`graph.build_graph`) rather than re-deriving structure from
the plan. Both are pure, dependency-free, and deterministic — the same graph
always yields the same diagram — with arithmetic layout (no layout engine).

Public functions accept a `MigrationPlan` for convenience (they build the graph
internally); `*_from_graph` variants take a graph directly, for callers that
already have one.
"""
from __future__ import annotations

import html
from collections import defaultdict
from typing import Dict, List

from .graph import GraphNode, InfrastructureGraph, NodeKind, build_graph
from .models import MigrationPlan, Tier

# Tier → fill colour (semantic, not the app accent).
_TIER_FILL: Dict[str, str] = {
    Tier.WEB.value: "#2563eb",
    Tier.APP.value: "#7c3aed",
    Tier.DATABASE.value: "#db2777",
    Tier.CACHE.value: "#d97706",
    Tier.OTHER.value: "#64748b",
}

# Layout constants (px).
_BOX_W, _BOX_H = 150, 46
_GAP = 16
_COLS = 4                 # instances per row within a lane
_MAX_PER_LANE = 12        # cap drawn boxes per lane; rest summarised
_PAD = 24
_LB_ROW_H = 24            # height of one load-balancer banner row


def _esc(s: object) -> str:
    return html.escape(str(s))


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _instances(graph: InfrastructureGraph) -> List[GraphNode]:
    return graph.nodes_of(NodeKind.INSTANCE)


def _lane(graph: InfrastructureGraph, subnet_tier: str) -> List[GraphNode]:
    return [n for n in _instances(graph) if n.attributes.get("subnet_tier") == subnet_tier]


def _load_balancers(graph: InfrastructureGraph, subnet_tier: str) -> List[GraphNode]:
    return [
        n for n in graph.nodes_of(NodeKind.LOAD_BALANCER)
        if n.attributes.get("subnet_tier") == subnet_tier
    ]


def _lb_listener_summary(node: GraphNode) -> str:
    ports = [f"{listener['protocol']}:{listener['listener_port']}" for listener in node.attributes.get("listeners", [])]
    return ", ".join(ports)


def _lane_height(n: int, lb_count: int = 0) -> int:
    rows = max(1, (min(n, _MAX_PER_LANE) + _COLS - 1) // _COLS)
    return rows * _BOX_H + (rows + 1) * _GAP + 34 + lb_count * _LB_ROW_H  # + lane title strip


def _draw_lane(
    x: int, y: int, w: int, title: str, subtitle: str, nodes: List[GraphNode], load_balancers: List[GraphNode]
) -> str:
    lb_h = len(load_balancers) * _LB_ROW_H
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{_lane_height(len(nodes), len(load_balancers))}" '
        f'rx="10" fill="#ffffff" stroke="#cbd5e1" stroke-dasharray="4 3"/>',
        f'<text x="{x + 14}" y="{y + 22}" font-size="13" font-weight="700" fill="#334155">{_esc(title)}</text>',
        f'<text x="{x + w - 14}" y="{y + 22}" font-size="11" fill="#94a3b8" text-anchor="end">{_esc(subtitle)}</text>',
    ]
    for i, lb in enumerate(load_balancers):
        ly = y + 34 + i * _LB_ROW_H
        parts.append(f'<rect x="{x + _GAP}" y="{ly}" width="{w - 2 * _GAP}" height="{_LB_ROW_H - 4}" rx="5" fill="#fef3c7" stroke="#d97706"/>')
        parts.append(
            f'<text x="{x + _GAP + 8}" y="{ly + 14}" font-size="10" font-weight="700" fill="#92400e">'
            f'⇄ {_esc(lb.name)}</text>'
        )
        parts.append(
            f'<text x="{x + w - _GAP - 8}" y="{ly + 14}" font-size="10" fill="#92400e" '
            f'text-anchor="end">{_esc(_lb_listener_summary(lb))}</text>'
        )
    for i, node in enumerate(nodes[:_MAX_PER_LANE]):
        tier = str(node.attributes.get("tier", Tier.OTHER.value))
        itype = str(node.attributes.get("instance_type", ""))
        col, row = i % _COLS, i // _COLS
        bx = x + _GAP + col * (_BOX_W + _GAP)
        by = y + 34 + lb_h + _GAP + row * (_BOX_H + _GAP)
        fill = _TIER_FILL.get(tier, _TIER_FILL[Tier.OTHER.value])
        parts.append(
            f'<rect x="{bx}" y="{by}" width="{_BOX_W}" height="{_BOX_H}" rx="7" fill="{fill}"/>'
        )
        parts.append(
            f'<text x="{bx + _BOX_W // 2}" y="{by + 19}" font-size="11" font-weight="600" '
            f'fill="#ffffff" text-anchor="middle">{_esc(_truncate(node.name, 20))}</text>'
        )
        parts.append(
            f'<text x="{bx + _BOX_W // 2}" y="{by + 34}" font-size="10" '
            f'fill="#e2e8f0" text-anchor="middle">{_esc(itype)} · {_esc(tier)}</text>'
        )
    hidden = len(nodes) - min(len(nodes), _MAX_PER_LANE)
    if hidden > 0:
        parts.append(
            f'<text x="{x + _GAP}" y="{y + _lane_height(len(nodes), len(load_balancers)) - 12}" '
            f'font-size="11" fill="#94a3b8">+{hidden} more instance(s)</text>'
        )
    return "\n".join(parts)


def architecture_svg_from_graph(graph: InfrastructureGraph) -> str:
    """Render the topology graph as a standalone SVG string."""
    public = _lane(graph, "public")
    private = _lane(graph, "private")
    public_lbs = _load_balancers(graph, "public")
    private_lbs = _load_balancers(graph, "private")
    count = len(_instances(graph))
    vpc = graph.node("vpc")
    vpc_cidr = str(vpc.attributes.get("cidr", "")) if vpc else ""
    has_igw = bool(vpc and vpc.attributes.get("internet_gateway"))
    has_nat = bool(vpc and vpc.attributes.get("nat_gateway"))

    lane_w = _PAD + _COLS * _BOX_W + (_COLS + 1) * _GAP
    width = lane_w + 2 * _PAD
    vpc_x, vpc_y = _PAD, 70
    lane_x = vpc_x + _PAD

    pub_h = _lane_height(len(public), len(public_lbs))
    priv_h = _lane_height(len(private), len(private_lbs))
    pub_y = vpc_y + 46
    priv_y = pub_y + pub_h + _GAP + 24  # + connector strip
    vpc_h = (priv_y + priv_h + _PAD) - vpc_y
    height = vpc_y + vpc_h + _PAD

    nat = "NAT gateway" if has_nat else "no NAT"
    igw = "Internet gateway" if has_igw else "no IGW"

    # Tier legend — tiers actually present, in canonical order.
    present = {str(n.attributes.get("tier")) for n in _instances(graph)}
    legend_items = []
    for i, tier in enumerate([t for t in _TIER_FILL if t in present]):
        lx = _PAD + i * 118
        legend_items.append(
            f'<rect x="{lx}" y="{height - 26}" width="12" height="12" rx="3" fill="{_TIER_FILL[tier]}"/>'
            f'<text x="{lx + 18}" y="{height - 16}" font-size="11" fill="#64748b">{_esc(tier)}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="-apple-system,Segoe UI,Roboto,sans-serif">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc"/>
  <text x="{_PAD}" y="30" font-size="17" font-weight="700" fill="#0f172a">{_esc(graph.project)} — {_esc(graph.target.upper())} architecture</text>
  <text x="{_PAD}" y="50" font-size="12" fill="#64748b">{_esc(graph.region)} · {count} instances · {_esc(igw)} · {_esc(nat)}</text>

  <rect x="{vpc_x}" y="{vpc_y}" width="{width - 2 * _PAD}" height="{vpc_h}" rx="12" fill="none" stroke="#334155" stroke-width="1.5"/>
  <text x="{vpc_x + 14}" y="{vpc_y + 22}" font-size="13" font-weight="700" fill="#334155">VPC {_esc(vpc_cidr)}</text>

{_draw_lane(lane_x, pub_y, lane_w, "Public subnet", igw, public, public_lbs)}
{_draw_lane(lane_x, priv_y, lane_w, "Private subnet", nat, private, private_lbs)}

  {"".join(legend_items)}
</svg>"""


def architecture_mermaid_from_graph(graph: InfrastructureGraph) -> str:
    """Render the topology graph as a Mermaid graph definition."""
    vpc = graph.node("vpc")
    vpc_cidr = str(vpc.attributes.get("cidr", "")) if vpc else ""
    lines = ["graph TD", f'  subgraph VPC["VPC {vpc_cidr}"]']
    by_lane: Dict[str, list] = defaultdict(list)
    for n in _instances(graph):
        by_lane[str(n.attributes.get("subnet_tier"))].append(n)

    node_id = 0
    mermaid_id: Dict[str, str] = {}
    for lane, label in (("public", "Public subnet"), ("private", "Private subnet")):
        insts = by_lane.get(lane, [])
        if not insts:
            continue
        lines.append(f'    subgraph {lane}["{label}"]')
        for n in insts[:_MAX_PER_LANE]:
            node_id += 1
            mid = f"n{node_id}"
            mermaid_id[n.id] = mid
            safe = n.name.replace('"', "'")
            itype = n.attributes.get("instance_type", "")
            tier = n.attributes.get("tier", "")
            lines.append(f'      {mid}["{safe}<br/>{itype} · {tier}"]')
        hidden = len(insts) - min(len(insts), _MAX_PER_LANE)
        if hidden > 0:
            node_id += 1
            lines.append(f'      n{node_id}["+{hidden} more"]')
        lines.append("    end")
    lines.append("  end")

    for lb in graph.nodes_of(NodeKind.LOAD_BALANCER):
        node_id += 1
        lb_mid = f"lb{node_id}"
        safe = lb.name.replace('"', "'")
        lines.append(f'  {lb_mid}{{{{"{safe}"}}}}')
        for e in graph.out_edges(lb.id):
            if e.kind.value == "fronts" and e.target in mermaid_id:
                lines.append(f"  {lb_mid} --> {mermaid_id[e.target]}")
    return "\n".join(lines) + "\n"


# --- Convenience wrappers (build the graph from a plan) ----------------------

def architecture_svg(plan: MigrationPlan) -> str:
    return architecture_svg_from_graph(build_graph(plan))


def architecture_mermaid(plan: MigrationPlan) -> str:
    return architecture_mermaid_from_graph(build_graph(plan))
