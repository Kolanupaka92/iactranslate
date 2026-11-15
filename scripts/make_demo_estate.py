"""Generate the demo estate: 1,500 workloads, in both the forms a customer has.

A migration conversation almost always starts with two different exports, and
the difference between them is the point:

`demo-estate-rvtools.xlsx`
    A VMware inventory. **RVTools does not export utilization** — that is a fact
    about the tool, not a gap in this fixture — so every instance is sized from
    *allocated* capacity and the report says so plainly.

`demo-estate-cmdb.csv`
    The same estate as a CMDB/monitoring export, carrying observed CPU and
    memory utilization. Now right-sizing can run against demand rather than
    allocation.

Running both through the product shows the thing worth showing: what supplying
utilization data is actually worth, in money. Enterprises over-provision heavily
— that is why right-sizing pays — so the utilization here is drawn low against
allocation, as it is in a real estate.

    python scripts/make_demo_estate.py --vms 1500 --out-dir demo/
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_rvtools_fixture import build as build_rvtools  # noqa: E402

from iactranslate.normalize import normalize  # noqa: E402
from iactranslate.parsers import parse  # noqa: E402

#: Observed utilization, by tier. Drawn from what over-provisioned estates
#: actually look like: batch and test machines idle most of the time, databases
#: and caches genuinely busy. A flat 50% everywhere would make right-sizing look
#: uniformly clever, which is not how it behaves on real data.
CPU_UTIL = {
    "web": (8, 35), "app": (10, 45), "db": (25, 70),
    "cache": (20, 60), "mq": (5, 30), "batch": (2, 20),
}
MEM_UTIL = {
    "web": (15, 45), "app": (20, 55), "db": (40, 85),
    "cache": (45, 90), "mq": (10, 35), "batch": (5, 25),
}


def _tier_of(name: str) -> str:
    lowered = name.lower()
    for tier in ("web", "app", "db", "cache", "mq", "batch"):
        if tier in lowered:
            return tier
    return "app"


def build_cmdb(inventory: Path, out: Path, seed: int = 11) -> int:
    """Write the same estate as a CMDB export carrying utilization."""
    rng = random.Random(seed)
    vms = normalize(parse(str(inventory)))
    with open(out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "Hostname", "CPU Cores", "Memory GB", "Storage GB", "Operating System",
            "Avg CPU Utilization", "Avg Memory Utilization", "IP Address",
            "Environment", "Owner",
        ])
        for vm in vms:
            tier = _tier_of(vm.vm_name)
            writer.writerow([
                vm.vm_name, vm.cpu, round(vm.memory_gib, 1), round(vm.total_disk_gib, 1),
                vm.os or "", rng.randint(*CPU_UTIL[tier]), rng.randint(*MEM_UTIL[tier]),
                (vm.ip_addresses or [""])[0], "", f"team-{rng.randint(1, 12):02d}",
            ])
    return len(vms)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vms", type=int, default=1500)
    ap.add_argument("--out-dir", default="demo")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rvtools = out_dir / "demo-estate-rvtools.xlsx"
    build_rvtools(args.vms, rvtools, seed=args.seed)
    print(f"wrote {rvtools} — VMware inventory, no utilization (RVTools does not export it)")

    cmdb = out_dir / "demo-estate-cmdb.csv"
    count = build_cmdb(rvtools, cmdb, seed=args.seed)
    print(f"wrote {cmdb} — same {count} workloads with observed CPU/memory utilization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
