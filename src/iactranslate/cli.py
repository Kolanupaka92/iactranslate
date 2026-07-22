"""IaCTranslate command-line interface.

    iactranslate translate <input> --target aws --out DIR [--zip] [--name NAME]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

from .assessment import assess, to_html, to_json
from .normalize import normalize
from .pipeline import run_pipeline
from .recommend import recommend
from .sources import UnknownSourceError, list_sources, resolve_source
from .targets import UnknownTargetError, list_targets
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
        result = run_pipeline(
            input_path=args.input,
            project_name=project_name,
            out_dir=args.out,
            target=args.target,
            source=args.source,
            column_map=_parse_column_map(args.map),
            region=args.region,
            make_zip=args.zip,
        )
    except (UnknownTargetError, UnknownSourceError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    except PlanValidationError as e:
        print(f"error: generated plan failed validation:\n{e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    plan = result.plan
    print(f"Project:   {plan.project_name}")
    print(f"Migration: {plan.source_platform} -> {plan.target} ({plan.region})")
    print(f"VMs:       {plan.vm_count}")
    print(f"Est. cost: ${plan.total_estimated_monthly_cost_usd:.2f}/month")
    print(f"Output:    {result.project_dir}")
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
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
