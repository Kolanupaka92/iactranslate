"""Assemble the generated Terraform into a downloadable project tree + ZIP."""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict

from .generator import build_files
from .models import MigrationPlan
from .targets.base import Target


def migration_summary(plan: MigrationPlan) -> str:
    """Human-readable migration report (documentation/migration-summary.md)."""
    right_sized = [c for c in plan.compute if c.right_sized]
    lines = [
        f"# Migration Summary — {plan.project_name}",
        "",
        f"- **Source:** {plan.source_platform}",
        f"- **Target:** {plan.target} ({plan.region})",
        f"- **VMs migrated:** {plan.vm_count}",
        f"- **Estimated monthly cost:** ${plan.total_estimated_monthly_cost_usd:.2f} (on-demand, approximate)",
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


def build_project(plan: MigrationPlan, out_dir: str | Path, target: Target) -> Path:
    """Write the full project tree to `out_dir` and return its path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: Dict[str, str] = build_files(plan, target)
    for filename, content in files.items():
        (out / filename).write_text(content)

    docs = out / "documentation"
    docs.mkdir(exist_ok=True)
    (docs / "migration-summary.md").write_text(migration_summary(plan))

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
