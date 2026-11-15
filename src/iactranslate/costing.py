"""Full-estate cost breakdown — compute plus the lines a real bill actually has.

`MigrationPlan.total_estimated_monthly_cost_usd` sums **instance cost only**.
On a realistic 25-VM estate that understates the bill by roughly a quarter to a
third, because three things the plan already knows about are never priced:

  * **Block storage.** The plan carries every disk size. 10,860 GiB of gp3 is
    ~$869/month.
  * **Windows Server licensing.** The plan knows which workloads are Windows
    (`image_key`). AWS charges license-included capacity at $0.046 per
    vCPU-hour on top of the Linux rate — on an estate that is a third Windows,
    thousands of dollars a month.
  * **Load balancers.** The plan *generates* them, then omits their cost.

Understating is the dangerous direction. A high estimate loses an argument; a
low one gets budgeted against and then overruns, and the consultant who
presented it wears that. So this module errs toward completeness and states
plainly what it still leaves out.

**This is an analysis engine.** Like assessment, confidence and the diagram, it
reads the immutable plan and never changes it (ADR 0007).

Rates are list-price, on-demand, and current as of August 2026 — see
`_STORAGE_USD_PER_GB_MONTH` for per-cloud sources. When the plan was built
with live pricing requested, each line's rate is fetched live instead (AWS,
Azure, GCP — `pricing.live_line_rate`) and falls back to these tables one line
at a time; `rate_sources` records which lines got which. Committed-use discounts
(Reserved Instances, Savings Plans, CUDs) routinely cut compute 30-60% and are
deliberately **not** applied: quoting a discount the customer has not actually
purchased is how an estimate becomes wrong in the customer's favour.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from pydantic import BaseModel, Field

from . import pricing
from .models import MigrationPlan
from .targets import get_target
from .targets.base import CAP_PRICED

# Monthly hours used throughout (matches pricing.HOURS_PER_MONTH).
HOURS_PER_MONTH = 730

# Block storage, USD per GB-month, on-demand list price (August 2026).
#   aws          gp3 general purpose SSD ...................... $0.080
#   azure        Standard SSD managed disk .................... $0.113
#   gcp          pd-balanced .................................. $0.100
#   oci          Block Volume (Balanced) ...................... $0.0255
#   digitalocean Block Storage ................................ $0.100
# AWS/Azure/GCP figures verified against published 2026 pricing; OCI and
# DigitalOcean are their standard published rates and carry the same
# list-price caveat as everything else here.
_STORAGE_USD_PER_GB_MONTH = {
    "aws": 0.080,
    "azure": 0.113,
    "gcp": 0.100,
    "oci": 0.0255,
    "digitalocean": 0.100,
}

# Windows Server license-included uplift, USD per vCPU-hour, charged *on top of*
# the equivalent Linux rate. AWS publishes $0.046/vCPU-hour; Azure and GCP price
# Windows comparably per-core. DigitalOcean offers no Windows images at all
# (ADR 0023), so the uplift there is zero by construction.
_WINDOWS_USD_PER_VCPU_HOUR = {
    "aws": 0.046,
    "azure": 0.046,
    "gcp": 0.046,
    "oci": 0.046,
    "digitalocean": 0.0,
}

# Load balancer, USD per month, per balancer, excluding capacity units.
#   aws   ALB  $0.0225/hr  -> ~$16.43
#   azure Standard LB      -> ~$18.25
#   gcp   forwarding rule  -> ~$18.25
#   oci   flexible LB (10Mbps shape)
#   digitalocean LB
_LOAD_BALANCER_USD_PER_MONTH = {
    "aws": 16.43,
    "azure": 18.25,
    "gcp": 18.25,
    "oci": 18.25,
    "digitalocean": 12.00,
}

# Stated on the report so the number is never mistaken for a quote. These are
# genuinely not derivable from an inventory export — nothing in an RVTools file
# says how much data an application egresses.
EXCLUSIONS = [
    "Data transfer / egress — depends on traffic patterns the inventory doesn't record",
    "Backup, snapshots and disaster recovery",
    "Support plans (typically 3-10% of spend)",
    "Managed services adopted after migration (RDS, Azure SQL, etc.)",
    "Migration project cost — tooling, labour, dual-running during cutover",
]


_BASIS = "on-demand list price, no committed-use discount applied"

_LINE_LABELS = {
    pricing.RATE_STORAGE: "storage",
    pricing.RATE_WINDOWS: "Windows licensing",
    pricing.RATE_LB: "load balancers",
}

_STATIC_RATES = {
    pricing.RATE_STORAGE: (_STORAGE_USD_PER_GB_MONTH, 0.10),
    pricing.RATE_WINDOWS: (_WINDOWS_USD_PER_VCPU_HOUR, 0.046),
    pricing.RATE_LB: (_LOAD_BALANCER_USD_PER_MONTH, 18.25),
}


def _line_rates(plan: MigrationPlan, cloud: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Rate and source for each non-compute line, live where asked and available."""
    rates: Dict[str, float] = {}
    sources: Dict[str, str] = {}
    for kind, (table, default) in _STATIC_RATES.items():
        live = (
            pricing.live_line_rate(cloud, kind, plan.region)
            if plan.live_pricing_requested else None
        )
        rates[kind] = live if live is not None else table.get(cloud, default)
        sources[kind] = "live" if live is not None else "static"
    return rates, sources


def _basis(plan: MigrationPlan, sources: Dict[str, str]) -> str:
    """`pricing_basis`, naming which lines are live when live was asked for.

    The silent case is the one to avoid: a report that says "live pricing" in
    its header while storage quietly came from a table dated months earlier.
    """
    if not plan.live_pricing_requested:
        return _BASIS
    live = [_LINE_LABELS[k] for k, s in sources.items() if s == "live"]
    static = [_LINE_LABELS[k] for k, s in sources.items() if s != "live"]
    if not live:
        return (f"{_BASIS}; live rates were requested but unavailable, so "
                f"{', '.join(static)} use catalog rates (August 2026)")
    basis = f"{_BASIS}; live rates for {', '.join(live)}"
    if static:
        basis += f"; catalog rates (August 2026) for {', '.join(static)}"
    return basis


class CostBreakdown(BaseModel):
    """Estimated monthly cost, itemized. All figures USD, on-demand list price."""

    schema_version: int = 1
    compute: float = Field(description="Instance cost, Linux-equivalent rate")
    storage: float = Field(description="Block storage for every attached disk")
    windows_licensing: float = Field(description="Windows Server license-included uplift")
    load_balancers: float = Field(description="Load balancers the plan provisions")
    total: float

    windows_workloads: int = 0
    total_storage_gib: float = 0.0
    load_balancer_count: int = 0
    pricing_basis: str = _BASIS
    #: Where each non-compute line's rate came from: 'live' or 'static'.
    #: Compute's own source is per instance, on the plan (`pricing_source`).
    rate_sources: Dict[str, str] = Field(
        default_factory=lambda: {kind: "static" for kind in pricing.LINE_RATES}
    )
    excludes: List[str] = Field(default_factory=lambda: list(EXCLUSIONS))
    #: False for a target with no published price — an on-premises hypervisor.
    #: Every figure above is then zero, and zero means "not computed", never
    #: "free". Consumers must render the absence, not the number.
    priced: bool = True

    @property
    def compute_share_pct(self) -> float:
        """How much of the bill is compute — the share a compute-only estimate
        would have captured."""
        return round(self.compute / self.total * 100, 1) if self.total else 0.0


def estimate_costs(plan: MigrationPlan) -> CostBreakdown:
    """Itemize the monthly cost of a plan. Read-only; never mutates the plan."""
    cloud = plan.target.lower()

    # A target with no published price gets no number at all. The rate tables
    # below carry defaults for unknown clouds, and those defaults would have
    # quietly priced an AHV cluster's storage, Windows licensing and load
    # balancers at public-cloud rates — a fabricated $1,883/month appeared
    # for the first on-premises target the moment it rendered. Nothing on
    # premises is billed per GB-month or per LB-hour; the customer already
    # owns the licences and the disks.
    if CAP_PRICED not in get_target(cloud).capabilities:
        return CostBreakdown(
            compute=0.0, storage=0.0, windows_licensing=0.0, load_balancers=0.0, total=0.0,
            windows_workloads=sum(1 for c in plan.compute if c.image_key.startswith("windows")),
            total_storage_gib=sum(c.root_volume_gib + sum(c.extra_volumes_gib) for c in plan.compute),
            load_balancer_count=len(plan.network.load_balancers),
            pricing_basis=(
                "on-premises cost is hardware, licensing and facilities, "
                "which an inventory cannot supply"
            ),
            priced=False,
        )

    rates, sources = _line_rates(plan, cloud)
    storage_rate = rates[pricing.RATE_STORAGE]
    windows_rate = rates[pricing.RATE_WINDOWS]
    lb_rate = rates[pricing.RATE_LB]

    compute = sum(c.estimated_monthly_cost_usd for c in plan.compute)

    # Every attached disk, not just the root volume.
    total_gib = sum(c.root_volume_gib + sum(c.extra_volumes_gib) for c in plan.compute)
    storage = total_gib * storage_rate

    # Windows is billed per vCPU of the *provisioned* instance, not the source
    # VM — a right-sized machine pays for the vCPUs it actually gets.
    windows = [c for c in plan.compute if c.image_key.startswith("windows")]
    licensing = sum(c.vcpu * windows_rate * HOURS_PER_MONTH for c in windows)

    lb_count = len(plan.network.load_balancers)
    load_balancers = lb_count * lb_rate

    total = compute + storage + licensing + load_balancers
    return CostBreakdown(
        compute=round(compute, 2),
        storage=round(storage, 2),
        windows_licensing=round(licensing, 2),
        load_balancers=round(load_balancers, 2),
        total=round(total, 2),
        windows_workloads=len(windows),
        total_storage_gib=round(total_gib, 1),
        load_balancer_count=lb_count,
        pricing_basis=_basis(plan, sources),
        rate_sources=sources,
    )
