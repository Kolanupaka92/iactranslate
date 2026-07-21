"""IaCTranslate command-line interface.

    iactranslate translate <input> --target aws --out DIR [--zip] [--name NAME]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import run_pipeline
from .validation import PlanValidationError


def _cmd_translate(args: argparse.Namespace) -> int:
    if args.target != "aws":
        print(f"error: target '{args.target}' is not supported yet (MVP supports: aws)", file=sys.stderr)
        return 2

    project_name = args.name or Path(args.input).stem

    try:
        result = run_pipeline(
            input_path=args.input,
            project_name=project_name,
            out_dir=args.out,
            region=args.region,
            make_zip=args.zip,
        )
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iactranslate",
        description="Convert infrastructure discovery reports into production-ready Terraform.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("translate", help="Translate a discovery export into a Terraform project.")
    t.add_argument("input", help="Path to an RVTools .xlsx or VMware .csv export.")
    t.add_argument("--target", default="aws", help="Target IaC/cloud (MVP: aws).")
    t.add_argument("--out", required=True, help="Output project directory.")
    t.add_argument("--region", default="us-east-1", help="Target AWS region.")
    t.add_argument("--name", default=None, help="Project name (defaults to input filename).")
    t.add_argument("--zip", action="store_true", help="Also write a <out>.zip archive.")
    t.set_defaults(func=_cmd_translate)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
