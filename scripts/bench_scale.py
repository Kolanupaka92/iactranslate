"""Measure where estate size actually starts to hurt.

Written because a review asserted that whole-file pandas parsing put a "hard
ceiling" on estate size and that millions of resources were "three orders of
magnitude beyond this design". Neither claim survived measurement: memory is not
the constraint, and the ceiling is CPU time on `.xlsx` specifically.

Run it before changing `MAX_VMS` or claiming anything about scale.

    python scripts/bench_scale.py [--sizes 1000,5000,20000]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="1000,5000,20000")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]

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
