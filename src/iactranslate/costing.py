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
`_STORAGE_USD_PER_GB_MONTH` for per-cloud sources. Committed-use discounts
(Reserved Instances, Savings Plans, CUDs) routinely cut compute 30-60% and are
deliberately **not** applied: quoting a discount the customer has not actually
purchased is how an estimate becomes wrong in the customer's favour.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from .models import MigrationPlan

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
    pricing_basis: str = "on-demand list price, no committed-use discount applied"
    excludes: List[str] = Field(default_factory=lambda: list(EXCLUSIONS))

    @property
    def compute_share_pct(self) -> float:
        """How much of the bill is compute — the share a compute-only estimate
        would have captured."""
        return round(self.compute / self.total * 100, 1) if self.total else 0.0


def estimate_costs(plan: MigrationPlan) -> CostBreakdown:
    """Itemize the monthly cost of a plan. Read-only; never mutates the plan."""
    cloud = plan.target.lower()
    storage_rate = _STORAGE_USD_PER_GB_MONTH.get(cloud, 0.10)
    windows_rate = _WINDOWS_USD_PER_VCPU_HOUR.get(cloud, 0.046)
    lb_rate = _LOAD_BALANCER_USD_PER_MONTH.get(cloud, 18.25)

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
    )
