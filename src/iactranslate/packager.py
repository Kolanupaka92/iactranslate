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
from .gitops import gitops_files
from .graph import build_graph
from .models import MigrationPlan, NormalizedVM
from .renderers import render
from .replatform import analyze_replatforming
from .targets.base import Target
from .waves import plan_waves


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

    # Explainability: why each instance was chosen.
    if any(c.reason for c in plan.compute):
        lines += ["## Why these instances", ""]
        for c in plan.compute:
            if c.reason:
                lines.append(f"- **{c.vm_name}** → `{c.instance_type}`: {c.reason}")
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
    if net.load_balancers:
        lines += ["## Load balancers", ""]
        for lb in net.load_balancers:
            scheme = "internet-facing" if lb.internet_facing else "internal"
            ports = ", ".join(f"{listener.protocol}:{listener.listener_port}" for listener in lb.listeners)
            lines.append(f"- **{lb.name}** ({scheme}) → {', '.join(lb.targets)} · listeners: {ports}")
        lines.append("")

    # Migration wave plan (advisory only — see waves.json).
    wave_report = plan_waves(plan)
    if wave_report.waves:
        lines += ["## Migration waves", "", wave_report.summary, "",
                   "| Wave | Environment | Layer | Workloads | Depends on | Est. downtime |",
                   "|---|---|---|---|---|---:|"]
        for w in wave_report.waves:
            deps = ", ".join(d.split("-", 2)[1] for d in w.depends_on) or "—"
            lines.append(
                f"| {w.sequence} | {w.environment.value} | {w.name.split('— ')[-1]} | "
                f"{', '.join(w.workloads)} | {deps} | {w.estimated_downtime_minutes} min |"
            )
        lines.append("")
        lines.append("> Ordering is inferred from tier dependency and environment promotion order — "
                     "see `waves.json` for rollback strategy and validation checks per wave, and its "
                     "notes for what this does *not* model (cross-application dependencies).")
        lines.append("")

    # Managed-DB re-platforming advice (advisory only — the plan still
    # lift-and-shifts these; see replatforming.json).
    report = analyze_replatforming(plan, vms)
    if report.candidates:
        lines += [
            "## Managed-database re-platforming (advisory)",
            "",
            report.summary,
            "",
            "| Database VM | Engine | Lift-and-shift | Managed alternative |",
            "|---|---|---|---|",
        ]
        for cand in report.candidates:
            lines.append(
                f"| {cand.vm_name} | {cand.engine} | {cand.current_instance_type} | {cand.managed_service} |"
            )
        lines.append("")
        lines.append("> The generated IaC still provisions VMs for these — re-platforming is a "
                     "data-migration project, out of scope here. See `replatforming.json` for caveats.")
        lines.append("")
    return "\n".join(lines) + "\n"


def build_project(
    plan: MigrationPlan,
    out_dir: str | Path,
    target: Target,
    vms: Optional[List[NormalizedVM]] = None,
    renderer: str = "terraform",
    gitops: bool = False,
    policy_result=None,
) -> Path:
    """Write the full project tree to `out_dir` and return its path.

    `renderer` selects the IaC output ('terraform' | 'pulumi'). `gitops` adds a
    CI/CD workflow (plan on PR, apply on merge) + .gitignore. When `vms` is
    supplied, a pre-migration assessment is written alongside (assessment.json +
    documentation/assessment.html).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: Dict[str, str] = render(renderer, plan, target)
    if gitops:
        files.update(gitops_files(plan, renderer))
    for filename, content in files.items():
        dest = out / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

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

    # Explainability — one entry per workload: what was decided, WHY, and how
    # sure we are. Joins each compute decision's reason with its confidence.
    conf_by_vm = {w.vm_name: w for w in confidence.workloads}
    decisions = []
    for c in plan.compute:
        w = conf_by_vm.get(c.vm_name)
        decisions.append({
            "vm": c.vm_name,
            "instance_type": c.instance_type,
            "tier": c.tier.value,
            "environment": c.environment.value,
            "right_sized": c.right_sized,
            "reason": c.reason,
            "confidence": {"overall": w.overall, "level": w.level} if w else None,
        })
    (out / "decisions.json").write_text(json.dumps({"decisions": decisions}, indent=2))

    # Policy report — only when policies were evaluated and produced findings
    # (deny violations abort before packaging; this captures warnings).
    if policy_result is not None and policy_result.violations:
        (out / "policy-report.json").write_text(
            json.dumps(policy_result.model_dump(mode="json"), indent=2)
        )

    # Infrastructure Graph — the renderer-neutral topology IR (see ADR 0010).
    (out / "graph.json").write_text(
        json.dumps(build_graph(plan).model_dump(mode="json"), indent=2)
    )

    # Managed-DB re-platforming advice (advisory only) — written whenever there
    # are database-tier candidates; the plan itself is unchanged.
    replatform = analyze_replatforming(plan, vms)
    if replatform.candidates:
        (out / "replatforming.json").write_text(
            json.dumps(replatform.model_dump(mode="json"), indent=2)
        )

    # Migration wave plan — deterministic sequencing by environment + tier
    # dependency depth (see waves.py). Advisory: informs execution order, does
    # not change what's rendered.
    (out / "waves.json").write_text(
        json.dumps(plan_waves(plan).model_dump(mode="json"), indent=2)
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
