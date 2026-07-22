"""IaCTranslate command-line interface.

    iactranslate translate <input> --target aws --out DIR [--zip] [--name NAME]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

from .agents import build_migration_plan
from .assessment import assess, to_html, to_json
from .confidence import score_plan
from .diff import diff_inventories
from .exec_report import build_executive_report
from .normalize import normalize
from .pipeline import run_pipeline
from .policy import PolicyViolationError, UnknownPolicyError, load_policy_config
from .recommend import recommend
from .renderers import UnknownRendererError, list_renderers
from .sources import UnknownSourceError, list_sources, resolve_source
from .targets import UnknownTargetError, get_target, list_targets
from .validation import PlanValidationError


def _parse_column_map(raw: Optional[str]) -> Optional[Dict[str, str]]:
    """Parse a "canon=Header,canon2=Header 2" string into a mapping dict."""
    if not raw:
        return None
    mapping: Dict[str, str] = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        canon, header = pair.split("=", 1)
        mapping[canon.strip()] = header.strip()
    return mapping or None


def _cmd_translate(args: argparse.Namespace) -> int:
    project_name = args.name or Path(args.input).stem

    try:
        policy_config = load_policy_config(args.policy) if args.policy else None
    except (FileNotFoundError, ValueError) as e:
        print(f"error: could not load policy config '{args.policy}': {e}", file=sys.stderr)
        return 2

    try:
        result = run_pipeline(
            input_path=args.input,
            project_name=project_name,
            out_dir=args.out,
            target=args.target,
            source=args.source,
            column_map=_parse_column_map(args.map),
            region=args.region,
            make_zip=args.zip,
            renderer=args.renderer,
            gitops=args.gitops,
            policy_config=policy_config,
        )
    except (UnknownTargetError, UnknownSourceError, UnknownRendererError, UnknownPolicyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    except PlanValidationError as e:
        print(f"error: generated plan failed validation:\n{e}", file=sys.stderr)
        return 1
    except PolicyViolationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    plan = result.plan
    conf = score_plan(plan, result.vms)
    print(f"Project:    {plan.project_name}")
    print(f"Migration:  {plan.source_platform} -> {plan.target} ({plan.region})")
    print(f"VMs:        {plan.vm_count}")
    print(f"Est. cost:  ${plan.total_estimated_monthly_cost_usd:.2f}/month")
    print(f"Confidence: {conf.overall * 100:.0f}% ({conf.level})"
          + (f" — {len(conf.low_confidence())} low-confidence VM(s)"
             if conf.low_confidence() else ""))
    if result.policy and result.policy.warnings:
        print(f"Policy:     {len(result.policy.warnings)} warning(s) (see policy-report.json)")
        for w in result.policy.warnings[:5]:
            print(f"  ! [{w.policy}] {w.message}")
    print(f"Output:     {result.project_dir}")
    if result.zip_path:
        print(f"ZIP:       {result.zip_path}")
    return 0


def _cmd_recommend(args: argparse.Namespace) -> int:
    try:
        src = resolve_source(args.input, args.source)
        vms = normalize(src.parse(args.input, column_map=_parse_column_map(args.map)))
    except UnknownSourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    if not vms:
        print(f"error: no workloads found in {args.input}", file=sys.stderr)
        return 2

    rec = recommend(vms)
    print(f"Recommended cloud: {rec.recommended.upper()}\n")
    print(f"{'CLOUD':6}  {'SCORE':>6}  {'COST/MO':>11}  {'COST':>5}  {'FIT':>5}  {'OS':>5}")
    print("-" * 48)
    for s in rec.ranked:
        print(f"{s.cloud.upper():6}  {s.weighted_score:>6.2f}  "
              f"${s.total_monthly_cost_usd:>10,.2f}  {s.cost_score:>5.2f}  "
              f"{s.fit_score:>5.2f}  {s.os_score:>5.2f}")
    print()
    for s in rec.ranked:
        print(f"{s.cloud.upper()}:")
        for reason in s.reasons:
            print(f"  - {reason}")
    if rec.notes:
        print("\nNotes:")
        for note in rec.notes:
            print(f"  - {note}")
    print(f"\n{rec.summary}")
    return 0


def _cmd_assess(args: argparse.Namespace) -> int:
    try:
        src = resolve_source(args.input, args.source)
        vms = normalize(src.parse(args.input, column_map=_parse_column_map(args.map)))
    except UnknownSourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    if not vms:
        print(f"error: no workloads found in {args.input}", file=sys.stderr)
        return 2

    project_name = args.name or Path(args.input).stem
    a = assess(vms, project_name=project_name, source_platform=src.name)

    if args.json:
        print(to_json(a))
        return 0
    if args.html_out:
        Path(args.html_out).write_text(to_html(a), encoding="utf-8")

    r = a.readiness
    print(f"Assessment: {a.project_name} (source: {a.source_platform})")
    print(f"Readiness:  {r.score}/100 — {r.band.replace('-', ' ')}")
    print(f"            {r.rationale}\n")
    print(f"Portfolio:  {a.total_workloads} workloads "
          f"({a.powered_on} on / {a.powered_off} off), "
          f"{a.total_vcpu} vCPU, {a.total_memory_gib:,.0f} GiB RAM, "
          f"{a.total_storage_gib:,.0f} GiB storage")
    print(f"            {a.windows_workloads} Windows / {a.linux_workloads} Linux / "
          f"{a.unknown_os_workloads} unknown OS; "
          f"{a.utilization_coverage_pct:.0f}% utilization coverage\n")
    if not a.findings:
        print("No findings — the inventory is clean.")
    else:
        print(f"Findings ({len(a.findings)}):")
        for f in a.findings:
            aff = f" [{f.affected_count} affected]" if f.affected else ""
            print(f"  [{f.severity.value.upper():8}] {f.title}{aff}")
            print(f"             {f.detail}")
            if f.recommendation:
                print(f"             → {f.recommendation}")
    if args.html_out:
        print(f"\nHTML report: {args.html_out}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        src = resolve_source(args.input, args.source)
        vms = normalize(src.parse(args.input, column_map=_parse_column_map(args.map)))
    except UnknownSourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    if not vms:
        print(f"error: no workloads found in {args.input}", file=sys.stderr)
        return 2

    project_name = args.name or Path(args.input).stem
    try:
        target = get_target(args.target)
    except UnknownTargetError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    plan = build_migration_plan(vms, project_name=project_name, target=target, region=args.region)
    rec = None if args.no_recommend else recommend(vms)
    html = build_executive_report(plan, vms, recommendation=rec)

    Path(args.out).write_text(html, encoding="utf-8")
    print(f"Executive report written to {args.out}")
    return 0


def _load_inventory(path: str, source: str, column_map: Optional[str]):
    src = resolve_source(path, source)
    return normalize(src.parse(path, column_map=_parse_column_map(column_map)))


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        before = _load_inventory(args.before, args.source, args.map)
        after = _load_inventory(args.after, args.source, args.map)
    except UnknownSourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"error: input file not found: {e.filename}", file=sys.stderr)
        return 2
    if not before or not after:
        print("error: one of the inventories has no workloads", file=sys.stderr)
        return 2

    d = diff_inventories(before, after)
    if args.json:
        print(json.dumps(d.model_dump(mode="json"), indent=2))
        return 0

    print(f"Inventory diff: {args.before} → {args.after}\n")
    print(f"  Added:     {len(d.added)}")
    print(f"  Removed:   {len(d.removed)}")
    print(f"  Modified:  {len(d.modified)}")
    print(f"  Unchanged: {d.unchanged}\n")
    print(f"  vCPU:    {d.before.vcpu} → {d.after.vcpu} ({d.vcpu_delta:+d})")
    print(f"  Memory:  {d.before.memory_gib:g} → {d.after.memory_gib:g} GiB ({d.memory_delta:+g})")
    print(f"  Storage: {d.before.storage_gib:g} → {d.after.storage_gib:g} GiB ({d.storage_delta:+g})\n")
    for name in d.added:
        print(f"  + {name}")
    for name in d.removed:
        print(f"  - {name}")
    for wc in d.modified:
        deltas = ", ".join(f"{c.field}: {c.before}→{c.after}" for c in wc.changes)
        print(f"  ~ {wc.vm_name} ({deltas})")
    if not d.has_changes:
        print("  No changes — inventories are identical.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iactranslate",
        description="Convert infrastructure discovery reports into production-ready Terraform.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    src_help = f"Discovery source: auto (default) or {', '.join(list_sources())}."
    map_help = 'Generic-source column map, e.g. "name=Hostname,cpu=Cores,memory_gib=RAM GB,disk_gib=Storage GB,os=OS".'

    t = sub.add_parser("translate", help="Translate a discovery export into a Terraform project.")
    t.add_argument("input", help="Path to an inventory export (.xlsx/.csv): RVTools, Hyper-V, CMDB, cloud.")
    t.add_argument("--target", default="aws", help=f"Target cloud ({', '.join(list_targets())}).")
    t.add_argument("--source", default="auto", help=src_help)
    t.add_argument("--map", default=None, help=map_help)
    t.add_argument("--out", required=True, help="Output project directory.")
    t.add_argument("--region", default=None, help="Target region/location (defaults per cloud).")
    t.add_argument("--name", default=None, help="Project name (defaults to input filename).")
    t.add_argument("--renderer", default="terraform",
                   help=f"IaC output format ({', '.join(list_renderers())}). Pulumi is AWS-only.")
    t.add_argument("--gitops", action="store_true",
                   help="Include a GitOps CI/CD workflow (plan on PR, apply on merge) + .gitignore.")
    t.add_argument("--policy", default=None,
                   help="Path to a JSON policy config; `deny` violations abort, `warn` are reported.")
    t.add_argument("--zip", action="store_true", help="Also write a <out>.zip archive.")
    t.set_defaults(func=_cmd_translate)

    r = sub.add_parser("recommend", help="Compare all clouds and recommend the best fit.")
    r.add_argument("input", help="Path to an inventory export (.xlsx/.csv).")
    r.add_argument("--source", default="auto", help=src_help)
    r.add_argument("--map", default=None, help=map_help)
    r.set_defaults(func=_cmd_recommend)

    a = sub.add_parser("assess", help="Assess an estate's migration readiness (risks, cost, data gaps).")
    a.add_argument("input", help="Path to an inventory export (.xlsx/.csv).")
    a.add_argument("--source", default="auto", help=src_help)
    a.add_argument("--map", default=None, help=map_help)
    a.add_argument("--name", default=None, help="Project name (defaults to input filename).")
    a.add_argument("--json", action="store_true", help="Emit the assessment as JSON to stdout.")
    a.add_argument("--html-out", default=None, help="Also write a standalone HTML report to this path.")
    a.set_defaults(func=_cmd_assess)

    rp = sub.add_parser("report", help="Generate a client-facing executive migration report (HTML).")
    rp.add_argument("input", help="Path to an inventory export (.xlsx/.csv).")
    rp.add_argument("--target", default="aws", help=f"Target cloud ({', '.join(list_targets())}).")
    rp.add_argument("--source", default="auto", help=src_help)
    rp.add_argument("--map", default=None, help=map_help)
    rp.add_argument("--name", default=None, help="Project name (defaults to input filename).")
    rp.add_argument("--region", default=None, help="Target region/location (defaults per cloud).")
    rp.add_argument("--no-recommend", action="store_true", help="Skip the 3-cloud recommendation section.")
    rp.add_argument("--out", default="executive-report.html", help="Output HTML path.")
    rp.set_defaults(func=_cmd_report)

    d = sub.add_parser("diff", help="Compare two inventory snapshots (drift detection).")
    d.add_argument("before", help="Earlier inventory export (.xlsx/.csv).")
    d.add_argument("after", help="Later inventory export (.xlsx/.csv).")
    d.add_argument("--source", default="auto", help=src_help)
    d.add_argument("--map", default=None, help=map_help)
    d.add_argument("--json", action="store_true", help="Emit the diff as JSON.")
    d.set_defaults(func=_cmd_diff)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
