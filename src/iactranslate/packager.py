"""Assemble the generated Terraform into a downloadable project tree + ZIP."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from .assessment import assess, to_html, to_json
from .confidence import score_plan
from .diagram import architecture_mermaid, architecture_svg
from .exec_report import build_executive_report
from .models import MigrationPlan, NormalizedVM
from .renderers import render
from .targets.base import Target


def migration_summary(plan: MigrationPlan, vms: Optional[List[NormalizedVM]] = None) -> str:
    """Human-readable migration report (documentation/migration-summary.md)."""
    right_sized = [c for c in plan.compute if c.right_sized]
    conf = score_plan(plan, vms)
    lines = [
        f"# Migration Summary — {plan.project_name}",
        "",
        f"- **Source:** {plan.source_platform}",
        f"- **Target:** {plan.target} ({plan.region})",
        f"- **VMs migrated:** {plan.vm_count}",
        f"- **Estimated monthly cost:** ${plan.total_estimated_monthly_cost_usd:.2f} "
        f"({'live market prices' if plan.pricing_source == 'live' else 'curated static rates'}, on-demand)",
        f"- **Translation confidence:** {conf.overall * 100:.0f}% ({conf.level})"
        + (f" — {len(conf.low_confidence())} workload(s) need review" if conf.low_confidence() else ""),
    ]
    if right_sized:
        lines.append(
            f"- **Right-sized from utilization:** {len(right_sized)} of {plan.vm_count} "
            "workloads sized to observed usage instead of raw allocation."
        )
    lines += ["", "## Applications", ""]
    for group in plan.app_groups:
        lines.append(f"### {group.name} ({group.environment.value})")
        lines.append("")
        for vm_name, tier in sorted(group.members.items()):
            lines.append(f"- `{vm_name}` — {tier.value}")
        lines.append("")

    lines += [
        "## Compute mapping",
        "",
        "| Source VM | Allocated | Instance (vCPU/GiB) | Root (GiB) | Data vols | $/mo |",
        "|---|---|---|---:|---|---:|",
    ]
    for c in plan.compute:
        vols = ", ".join(str(v) for v in c.extra_volumes_gib) or "-"
        if c.right_sized and c.source_vcpu is not None:
            alloc = f"{c.source_vcpu} vCPU / {c.source_memory_gib:g} GiB ↓"
        else:
            alloc = "—"
        lines.append(
            f"| {c.vm_name} | {alloc} | {c.instance_type} ({c.vcpu}/{c.memory_gib:g}) | "
            f"{c.root_volume_gib} | {vols} | {c.estimated_monthly_cost_usd:.2f} |"
        )
    lines.append("")

    net = plan.network
    lines += [
        "## Network",
        "",
        f"- **VPC CIDR:** {net.vpc_cidr}",
        "- **Subnets:** " + ", ".join(f"{s.name} ({s.cidr})" for s in net.subnets),
        "- **Security groups:** " + ", ".join(s.name for s in net.security_groups),
        f"- **NAT gateway:** {'yes' if net.nat_gateway else 'no'}",
        "",
    ]
    return "\n".join(lines) + "\n"


def build_project(
    plan: MigrationPlan,
    out_dir: str | Path,
    target: Target,
    vms: Optional[List[NormalizedVM]] = None,
    renderer: str = "terraform",
) -> Path:
    """Write the full project tree to `out_dir` and return its path.

    `renderer` selects the IaC output ('terraform' | 'pulumi'). When `vms` is
    supplied, a pre-migration assessment is written alongside (assessment.json +
    documentation/assessment.html).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: Dict[str, str] = render(renderer, plan, target)
    for filename, content in files.items():
        (out / filename).write_text(content)

    docs = out / "documentation"
    docs.mkdir(exist_ok=True)
    (docs / "migration-summary.md").write_text(migration_summary(plan, vms))

    if vms:
        a = assess(vms, project_name=plan.project_name, source_platform=plan.source_platform)
        (out / "assessment.json").write_text(to_json(a))
        (docs / "assessment.html").write_text(to_html(a))

    confidence = score_plan(plan, vms)
    (out / "confidence.json").write_text(
        json.dumps(confidence.model_dump(mode="json"), indent=2)
    )

    # Architecture diagram — SVG (portable) + Mermaid (renders in GitHub/markdown).
    (docs / "architecture.svg").write_text(architecture_svg(plan))
    (docs / "architecture.md").write_text(
        f"# Architecture — {plan.project_name}\n\n"
        f"![architecture](architecture.svg)\n\n"
        f"```mermaid\n{architecture_mermaid(plan)}```\n"
    )

    # Client-facing executive report (chosen-target focus; no 3-cloud compare here).
    (docs / "executive-report.html").write_text(build_executive_report(plan, vms))

    # Placeholder for future module extraction (kept in the tree for structure).
    modules = out / "modules"
    modules.mkdir(exist_ok=True)
    (modules / ".gitkeep").write_text("")

    return out


def zip_project(project_dir: str | Path, zip_path: str | Path) -> Path:
    """Zip an already-built project directory. Returns the zip path."""
    project_dir = Path(project_dir)
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(project_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(project_dir))
    return zip_path
