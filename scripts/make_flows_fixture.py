"""Generate a realistic network-flow export for the RVTools fixture estate.

Shaped like what a flow collector actually exports, and — more importantly —
like how an estate actually talks: within an environment, along the tiers, with
infrastructure noise on top. A fixture that wires every web server to every app
server across dev, staging and production would make the dependency analysis
look far more dramatic than it is, and would test nothing real.
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iactranslate.agents import build_migration_plan  # noqa: E402
from iactranslate.models import Tier  # noqa: E402
from iactranslate.normalize import normalize  # noqa: E402
from iactranslate.parsers import parse  # noqa: E402
from iactranslate.targets import get_target  # noqa: E402

PORT_FOR = {Tier.APP: 8080, Tier.DATABASE: 5432, Tier.CACHE: 6379}


def build(inventory: str, out: str, seed: int = 7) -> int:
    rng = random.Random(seed)
    vms = normalize(parse(inventory))
    ip = {v.vm_name: v.ip_addresses[0] for v in vms if v.ip_addresses}
    plan = build_migration_plan(vms, "flows", get_target("aws"))

    # Group by environment: a dev web server does not call a production database,
    # and pretending it does invents dependencies the migration does not have.
    by_env: dict = {}
    for c in plan.compute:
        if c.vm_name in ip:
            by_env.setdefault(c.environment, []).append(c)

    rows = [("source_ip", "destination_ip", "destination_port", "connections", "bytes")]
    for members in by_env.values():
        tiers = {}
        for c in members:
            tiers.setdefault(c.tier, []).append(c.vm_name)
        # web -> app -> data, the conventional call direction.
        for caller_tier, callee_tier in ((Tier.WEB, Tier.APP), (Tier.APP, Tier.DATABASE),
                                         (Tier.APP, Tier.CACHE)):
            for caller in tiers.get(caller_tier, []):
                for callee in tiers.get(callee_tier, []):
                    rows.append((ip[caller], ip[callee], PORT_FOR[callee_tier],
                                 rng.randint(60, 900), rng.randint(200_000, 9_000_000)))

    # Infrastructure every workload touches. The analysis must filter these, or
    # everything "depends on" the DNS server and the graph says nothing.
    for address in ip.values():
        rows.append((address, "10.0.9.9", 53, 4000, 180_000))
        rows.append((address, "10.0.9.8", 123, 800, 40_000))

    # Systems that are not in the inventory and will not migrate.
    app_names = [c.vm_name for c in plan.compute
                 if c.tier == Tier.APP and c.vm_name in ip]
    if app_names:
        rows.append((ip[app_names[0]], "192.168.240.11", 1414, 340, 1_200_000))
        rows.append((ip[app_names[0]], "52.94.236.248", 443, 150, 420_000))

    # A single stray probe, below the noise threshold.
    if app_names:
        rows.append((ip[app_names[0]], "10.0.7.7", 9999, 1, 120))

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return len(rows) - 1


if __name__ == "__main__":
    inventory = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/rvtools_realistic.xlsx"
    out = sys.argv[2] if len(sys.argv) > 2 else "tests/fixtures/flows_realistic.csv"
    print(f"wrote {build(inventory, out)} flow rows to {out}")
