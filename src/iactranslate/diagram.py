"""Architecture diagrams from a MigrationPlan — deterministic, no dependencies.

Two renderers, both pure functions of the plan:

- `architecture_svg(plan)` → a self-contained SVG of the target topology
  (VPC → public/private subnets → instances grouped by tier). Embeds in the
  executive report and ships as documentation/architecture.svg.
- `architecture_mermaid(plan)` → a Mermaid `graph` definition for docs/READMEs
  (GitHub renders it natively).

No AI, no network, no layout engine — positions are computed arithmetically so
the same plan always yields the same diagram.
"""
from __future__ import annotations

import html
from collections import defaultdict
from typing import Dict, List

from .models import MigrationPlan, SubnetTier, Tier

# Tier → fill colour (semantic, not the app accent).
_TIER_FILL: Dict[Tier, str] = {
    Tier.WEB: "#2563eb",
    Tier.APP: "#7c3aed",
    Tier.DATABASE: "#db2777",
    Tier.CACHE: "#d97706",
    Tier.OTHER: "#64748b",
}

# Layout constants (px).
_BOX_W, _BOX_H = 150, 46
_GAP = 16
_COLS = 4                 # instances per row within a lane
_MAX_PER_LANE = 12        # cap drawn boxes per lane; rest summarised
_PAD = 24


def _esc(s: object) -> str:
    return html.escape(str(s))


def _lane_instances(plan: MigrationPlan, tier: SubnetTier) -> List:
    return [c for c in plan.compute if c.subnet_tier == tier]


def _lane_height(n: int) -> int:
    rows = max(1, (min(n, _MAX_PER_LANE) + _COLS - 1) // _COLS)
    return rows * _BOX_H + (rows + 1) * _GAP + 34  # + lane title strip


def _draw_lane(x: int, y: int, w: int, title: str, subtitle: str, instances: List) -> str:
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{_lane_height(len(instances))}" '
        f'rx="10" fill="#ffffff" stroke="#cbd5e1" stroke-dasharray="4 3"/>',
        f'<text x="{x + 14}" y="{y + 22}" font-size="13" font-weight="700" fill="#334155">{_esc(title)}</text>',
        f'<text x="{x + w - 14}" y="{y + 22}" font-size="11" fill="#94a3b8" text-anchor="end">{_esc(subtitle)}</text>',
    ]
    shown = instances[:_MAX_PER_LANE]
    for i, c in enumerate(shown):
        col, row = i % _COLS, i // _COLS
        bx = x + _GAP + col * (_BOX_W + _GAP)
        by = y + 34 + _GAP + row * (_BOX_H + _GAP)
        fill = _TIER_FILL.get(c.tier, _TIER_FILL[Tier.OTHER])
        parts.append(
            f'<rect x="{bx}" y="{by}" width="{_BOX_W}" height="{_BOX_H}" rx="7" fill="{fill}"/>'
        )
        parts.append(
            f'<text x="{bx + _BOX_W // 2}" y="{by + 19}" font-size="11" font-weight="600" '
            f'fill="#ffffff" text-anchor="middle">{_esc(_truncate(c.vm_name, 20))}</text>'
        )
        parts.append(
            f'<text x="{bx + _BOX_W // 2}" y="{by + 34}" font-size="10" '
            f'fill="#e2e8f0" text-anchor="middle">{_esc(c.instance_type)} · {_esc(c.tier.value)}</text>'
        )
    hidden = len(instances) - len(shown)
    if hidden > 0:
        parts.append(
            f'<text x="{x + _GAP}" y="{y + _lane_height(len(instances)) - 12}" '
            f'font-size="11" fill="#94a3b8">+{hidden} more instance(s)</text>'
        )
    return "\n".join(parts)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def architecture_svg(plan: MigrationPlan) -> str:
    """Render the plan's topology as a standalone SVG string."""
    public = _lane_instances(plan, SubnetTier.PUBLIC)
    private = _lane_instances(plan, SubnetTier.PRIVATE)
    net = plan.network

    lane_w = _PAD + _COLS * _BOX_W + (_COLS + 1) * _GAP
    width = lane_w + 2 * _PAD
    vpc_x, vpc_y = _PAD, 70
    lane_x = vpc_x + _PAD

    pub_h = _lane_height(len(public))
    priv_h = _lane_height(len(private))
    pub_y = vpc_y + 46
    priv_y = pub_y + pub_h + _GAP + 24  # + connector strip
    vpc_h = (priv_y + priv_h + _PAD) - vpc_y
    height = vpc_y + vpc_h + _PAD

    nat = "NAT gateway" if net.nat_gateway else "no NAT"
    igw = "Internet gateway" if net.internet_gateway else "no IGW"

    # Tier legend.
    legend_items = []
    tiers_present = [t for t in _TIER_FILL if any(c.tier == t for c in plan.compute)]
    for i, t in enumerate(tiers_present):
        lx = _PAD + i * 118
        legend_items.append(
            f'<rect x="{lx}" y="{height - 26}" width="12" height="12" rx="3" fill="{_TIER_FILL[t]}"/>'
            f'<text x="{lx + 18}" y="{height - 16}" font-size="11" fill="#64748b">{_esc(t.value)}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="-apple-system,Segoe UI,Roboto,sans-serif">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc"/>
  <text x="{_PAD}" y="30" font-size="17" font-weight="700" fill="#0f172a">{_esc(plan.project_name)} — {_esc(plan.target.upper())} architecture</text>
  <text x="{_PAD}" y="50" font-size="12" fill="#64748b">{_esc(plan.region)} · {plan.vm_count} instances · {_esc(igw)} · {_esc(nat)}</text>

  <rect x="{vpc_x}" y="{vpc_y}" width="{width - 2 * _PAD}" height="{vpc_h}" rx="12" fill="none" stroke="#334155" stroke-width="1.5"/>
  <text x="{vpc_x + 14}" y="{vpc_y + 22}" font-size="13" font-weight="700" fill="#334155">VPC {_esc(net.vpc_cidr)}</text>

{_draw_lane(lane_x, pub_y, lane_w, "Public subnet", igw, public)}
{_draw_lane(lane_x, priv_y, lane_w, "Private subnet", nat, private)}

  {"".join(legend_items)}
</svg>"""


def architecture_mermaid(plan: MigrationPlan) -> str:
    """Render the plan's topology as a Mermaid graph definition."""
    lines = ["graph TD", f'  subgraph VPC["VPC {plan.network.vpc_cidr}"]']
    by_lane: Dict[SubnetTier, list] = defaultdict(list)
    for c in plan.compute:
        by_lane[c.subnet_tier].append(c)

    node_id = 0
    for lane, label in ((SubnetTier.PUBLIC, "Public subnet"), (SubnetTier.PRIVATE, "Private subnet")):
        insts = by_lane.get(lane, [])
        if not insts:
            continue
        lines.append(f'    subgraph {lane.value}["{label}"]')
        for c in insts[:_MAX_PER_LANE]:
            node_id += 1
            safe = c.vm_name.replace('"', "'")
            lines.append(f'      n{node_id}["{safe}<br/>{c.instance_type} · {c.tier.value}"]')
        hidden = len(insts) - min(len(insts), _MAX_PER_LANE)
        if hidden > 0:
            node_id += 1
            lines.append(f'      n{node_id}["+{hidden} more"]')
        lines.append("    end")
    lines.append("  end")
    return "\n".join(lines) + "\n"
