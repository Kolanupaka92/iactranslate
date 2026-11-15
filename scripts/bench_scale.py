"""Measure where estate size actually starts to hurt.

Written because a review asserted that whole-file pandas parsing put a "hard
ceiling" on estate size and that millions of resources were "three orders of
magnitude beyond this design". Neither claim survived measurement: memory is not
the constraint, and the ceiling is CPU time on `.xlsx` specifically.

Run it before changing `MAX_VMS` or claiming anything about scale.

    python scripts/bench_scale.py [--sizes 1000,5000,20000]
    python scripts/bench_scale.py --xlsx-strategies [--sizes 20000]

`--xlsx-strategies` exists because the xlsx cost gets re-proposed as a rewrite
about once per review, always with the same two premises: that the scaling is
superlinear, and that openpyxl's memory model is the cause. Measured, both are
false — see `_xlsx_strategies` below. Reproduce before rewriting the parser.
"""
from __future__ import annotations

import argparse
import csv
import gc
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iactranslate.agents import build_migration_plan  # noqa: E402
from iactranslate.normalize import normalize  # noqa: E402
from iactranslate.parsers import parse  # noqa: E402
from iactranslate.targets import get_target  # noqa: E402

HEADER = ["VM", "CPUs", "Memory", "Provisioned MiB",
          "OS according to the configuration file", "Powerstate", "DNS Name",
          "Primary IP Address", "Cluster", "Datacenter"]


def _row(i: int) -> list:
    tier = ("web", "app", "db")[i % 3]
    return [f"prod-{tier}-{i:06d}", 2 + (i % 8), 4096 * (1 + i % 4), 40960 * (1 + i % 3),
            "Ubuntu Linux (64-bit)" if i % 4 else "Microsoft Windows Server 2019 (64-bit)",
            "poweredOn", f"prod-{tier}-{i:06d}.corp",
            f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}", "CL-1", "DC1"]


def make_csv(n: int, path: Path) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for i in range(n):
            writer.writerow(_row(i))


def make_xlsx(n: int, path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("vInfo")
    ws.append(HEADER)
    for i in range(n):
        ws.append(_row(i))
    wb.save(path)


def _peak_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return peak / 1024 if sys.platform.startswith("linux") else peak / 1024 / 1024


def _xlsx_strategies(sizes) -> None:
    """Compare ways of reading the same sheet, and check the scaling exponent.

    Findings, on this machine, at 5,000-100,000 rows:

      * **Scaling is linear.** ~123 microseconds per row, flat to within 1% over
        a 20x range. Not superlinear.
      * **Memory is not the constraint.** Peak RSS is identical between
        `pandas.read_excel` and `openpyxl(read_only=True)` streaming, and CSV
        parsing uses slightly *more*. Growth is ~1 KB/row, linear.
      * **read_only streaming buys ~1.14x.** The cost is inherent to
        decompressing and parsing XML with per-cell type coercion, not to how
        openpyxl is invoked. Hand-rolling XML across the four source modules
        would be a rewrite of working code for single-digit percent.

    For scale: at the `MAX_VMS` ceiling of 20,000 workloads, reading the sheet
    costs about 2.5 seconds. It is a real difference from CSV and it is not a
    bottleneck.
    """
    import pandas as pd
    from openpyxl import load_workbook

    tmp = Path(tempfile.mkdtemp())
    print(f"{'rows':>8}  {'strategy':<34}  {'seconds':>8}  {'us/row':>7}  {'peak RSS':>9}")
    for n in sizes:
        xlsx, csvp = tmp / f"{n}.xlsx", tmp / f"{n}.csv"
        make_xlsx(n, xlsx)
        make_csv(n, csvp)

        def _pandas_xlsx(xlsx=xlsx):
            return len(pd.read_excel(xlsx, engine="openpyxl"))

        def _readonly_stream(xlsx=xlsx):
            wb = load_workbook(xlsx, read_only=True, data_only=True)
            rows = wb[wb.sheetnames[0]].iter_rows(values_only=True)
            next(rows)
            count = sum(1 for _ in rows)
            wb.close()
            return count

        def _pandas_csv(csvp=csvp):
            return len(pd.read_csv(csvp))

        for label, fn in (
            ("pandas.read_excel (current)", _pandas_xlsx),
            ("openpyxl read_only=True stream", _readonly_stream),
            ("pandas.read_csv (baseline)", _pandas_csv),
        ):
            gc.collect()
            t0 = time.perf_counter()
            fn()
            dt = time.perf_counter() - t0
            print(f"{n:>8}  {label:<34}  {dt:>7.2f}s  {dt / n * 1e6:>6.1f}  {_peak_mb():>8.0f}MB")
        xlsx.unlink()
        csvp.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="1000,5000,20000")
    ap.add_argument("--xlsx-strategies", action="store_true",
                    help="Compare xlsx reading strategies and check the scaling exponent.")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]

    if args.xlsx_strategies:
        _xlsx_strategies(sizes)
        return 0

    tmp = Path(tempfile.mkdtemp())
    target = get_target("aws")
    print(f"{'VMs':>8}  {'format':>6}  {'parse+norm':>11}  {'plan':>8}  {'peak RSS':>10}")
    for n in sizes:
        for fmt, build in (("csv", make_csv), ("xlsx", make_xlsx)):
            path = tmp / f"{n}.{fmt}"
            build(n, path)
            gc.collect()
            t0 = time.perf_counter()
            vms = normalize(parse(str(path)))
            t1 = time.perf_counter()
            build_migration_plan(vms, "bench", target)
            t2 = time.perf_counter()
            print(f"{n:>8}  {fmt:>6}  {t1 - t0:>10.2f}s  {t2 - t1:>7.2f}s  {_peak_mb():>9.0f}MB")
            del vms
            gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
