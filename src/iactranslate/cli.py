"""IaCTranslate command-line interface.

    iactranslate translate <input> --target aws --out DIR [--zip] [--name NAME]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .normalize import normalize
from .parsers import parse
from .pipeline import run_pipeline
from .recommend import recommend
from .targets import UnknownTargetError, list_targets
from .validation import PlanValidationError


def _cmd_translate(args: argparse.Namespace) -> int:
    project_name = args.name or Path(args.input).stem

    try:
        result = run_pipeline(
            input_path=args.input,
            project_name=project_name,
            out_dir=args.out,
            target=args.target,
            region=args.region,
            make_zip=args.zip,
        )
    except UnknownTargetError as e:
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
        vms = normalize(parse(args.input))
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2
    if not vms:
        print(f"error: no virtual machines found in {args.input}", file=sys.stderr)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iactranslate",
        description="Convert infrastructure discovery reports into production-ready Terraform.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("translate", help="Translate a discovery export into a Terraform project.")
    t.add_argument("input", help="Path to an RVTools .xlsx or VMware .csv export.")
    t.add_argument("--target", default="aws", help=f"Target cloud ({', '.join(list_targets())}).")
    t.add_argument("--out", required=True, help="Output project directory.")
    t.add_argument("--region", default=None, help="Target region/location (defaults per cloud).")
    t.add_argument("--name", default=None, help="Project name (defaults to input filename).")
    t.add_argument("--zip", action="store_true", help="Also write a <out>.zip archive.")
    t.set_defaults(func=_cmd_translate)

    r = sub.add_parser("recommend", help="Compare all clouds and recommend the best fit.")
    r.add_argument("input", help="Path to an RVTools .xlsx or VMware .csv export.")
    r.set_defaults(func=_cmd_recommend)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
