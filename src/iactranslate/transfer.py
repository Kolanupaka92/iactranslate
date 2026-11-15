"""How long the data actually takes to move.

The plan says what to build. It never said how long it takes to *fill*, and that
is the number a migration lead plans around: a wave either fits the cutover
weekend or it does not, and finding out during the weekend is the expensive way
to learn.

Every input is already in the plan — disk sizes come from the inventory — except
one the inventory cannot know: the bandwidth of the link between the datacenter
and the cloud. That is asked for rather than assumed.

**This is an analysis engine.** Like assessment, confidence, costing and the
diagram, it reads the immutable plan and never changes it (ADR 0007).

**It is deliberately pessimistic**, for the same reason the cost model is
(ADR 0039): an optimistic transfer estimate gets written into a cutover plan and
discovered at 3am on a Sunday. Three haircuts are applied to nominal link speed:

  * **Protocol and WAN efficiency.** A "1 Gbps" link does not move 1 Gbps of
    payload. TCP overhead, latency and windowing put sustained throughput far
    below line rate on a long-haul path; 70% is a conventional planning figure
    and optimistic for a busy link.
  * **Provisioned, not allocated, capacity.** Migration traffic shares the link
    with production. Only a fraction is usable, and assuming otherwise is how a
    migration takes down the business it was meant to move.
  * **Allocated disk, not used disk.** RVTools reports provisioned capacity, and
    a 500 GiB disk holding 40 GiB of data transfers 40 GiB. Without used-capacity
    data the estimate has to assume the disk is full, which overstates — the
    honest direction, and stated in the output rather than hidden.

Where a wave cannot fit its window, the remedy is named: offline transfer
appliances (AWS Snowball, Azure Data Box, GCP Transfer Appliance) or continuous
replication with a short delta cutover, which is how large estates are actually
moved.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .models import MigrationPlan

#: Sustained payload throughput as a fraction of nominal line rate.
WAN_EFFICIENCY = 0.70

#: Share of the link a migration may use without starving production.
DEFAULT_LINK_SHARE = 0.50

#: Above this, online transfer stops being the sensible mechanism regardless of
#: arithmetic — the wire time approaches the shipping time of an appliance.
OFFLINE_THRESHOLD_TIB = 20.0

GIB_IN_TIB = 1024.0
#: 1 GiB = 8,589,934,592 bits; Mbps is 10^6 bits/s.
_GIB_TO_MEGABITS = 8589.934592


class WorkloadTransfer(BaseModel):
    vm_name: str
    gib: float
    hours: float


class WaveTransfer(BaseModel):
    wave_id: str
    sequence: int
    name: str
    workload_count: int
    gib: float
    hours: float
    fits_window: bool = True
    workloads: List[WorkloadTransfer] = Field(default_factory=list)


class TransferEstimate(BaseModel):
    """How long the estate takes to move, and whether that fits."""

    schema_version: int = 1
    total_gib: float
    total_hours: float
    effective_mbps: float
    link_mbps: float
    link_share: float
    cutover_window_hours: Optional[float] = None
    waves: List[WaveTransfer] = Field(default_factory=list)
    recommend_offline_transfer: bool = False
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @property
    def total_tib(self) -> float:
        return round(self.total_gib / GIB_IN_TIB, 2)

    @property
    def total_days(self) -> float:
        return round(self.total_hours / 24, 1)


def effective_throughput_mbps(link_mbps: float, link_share: float = DEFAULT_LINK_SHARE) -> float:
    """Payload throughput after WAN efficiency and the share left for production."""
    return link_mbps * WAN_EFFICIENCY * link_share


def transfer_hours(gib: float, mbps: float) -> float:
    """Hours to move `gib` at `mbps` of sustained payload throughput."""
    if mbps <= 0:
        return float("inf")
    return (gib * _GIB_TO_MEGABITS) / mbps / 3600


def estimate_transfer(
    plan: MigrationPlan,
    link_mbps: float = 1000.0,
    link_share: float = DEFAULT_LINK_SHARE,
    cutover_window_hours: Optional[float] = None,
    waves: Optional[object] = None,
) -> TransferEstimate:
    """Estimate data-transfer time for the plan, optionally per wave.

    `waves` accepts a `WaveReport` so the estimate lines up with the migration
    sequence the tool already produces — a total for the estate is interesting,
    but "wave 3 does not fit your window" is the finding someone can act on.
    """
    effective = effective_throughput_mbps(link_mbps, link_share)

    per_vm: Dict[str, float] = {
        c.vm_name: float(c.root_volume_gib) + float(sum(c.extra_volumes_gib))
        for c in plan.compute
    }
    total_gib = sum(per_vm.values())
    total_hours = transfer_hours(total_gib, effective)

    wave_transfers: List[WaveTransfer] = []
    if waves is not None:
        for wave in getattr(waves, "waves", []):
            members = [
                WorkloadTransfer(
                    vm_name=name,
                    gib=round(per_vm.get(name, 0.0), 1),
                    hours=round(transfer_hours(per_vm.get(name, 0.0), effective), 2),
                )
                for name in wave.workloads
            ]
            wave_gib = sum(m.gib for m in members)
            wave_hours = transfer_hours(wave_gib, effective)
            wave_transfers.append(
                WaveTransfer(
                    wave_id=wave.id,
                    sequence=wave.sequence,
                    name=wave.name,
                    workload_count=len(members),
                    gib=round(wave_gib, 1),
                    hours=round(wave_hours, 2),
                    fits_window=(
                        cutover_window_hours is None or wave_hours <= cutover_window_hours
                    ),
                    workloads=members,
                )
            )

    assumptions = [
        f"Link: {link_mbps:,.0f} Mbps nominal, {link_share:.0%} available to the migration, "
        f"{WAN_EFFICIENCY:.0%} sustained efficiency — {effective:,.0f} Mbps effective.",
        "Disk sizes are allocated capacity from the inventory, not used capacity. "
        "A 500 GiB disk holding 40 GiB transfers 40 GiB, so this estimate is an "
        "upper bound wherever used capacity is unknown.",
        "Assumes a single sequential stream. Parallel transfers across workloads "
        "reduce elapsed time but not the total bytes, and contend for the same link.",
        "Excludes the delta re-sync at cutover, application quiesce time, and any "
        "validation window after the copy completes.",
    ]

    warnings: List[str] = []
    total_tib = total_gib / GIB_IN_TIB
    recommend_offline = total_tib >= OFFLINE_THRESHOLD_TIB
    if recommend_offline:
        warnings.append(
            f"{total_tib:,.1f} TiB over this link takes {total_hours / 24:,.1f} days of "
            f"continuous transfer. At this volume, offline transfer appliances "
            f"(AWS Snowball, Azure Data Box, GCP Transfer Appliance) or continuous "
            f"replication with a short delta cutover are the usual mechanisms."
        )
    if cutover_window_hours is not None:
        over = [w for w in wave_transfers if not w.fits_window]
        if over:
            warnings.append(
                f"{len(over)} of {len(wave_transfers)} waves exceed the "
                f"{cutover_window_hours:g}h cutover window: "
                + ", ".join(f"{w.name} ({w.hours:,.1f}h)" for w in over[:5])
                + ". Either split them, pre-seed the data with replication, or widen "
                "the window."
            )
    if link_share >= 0.9:
        warnings.append(
            "Nearly the whole link is allocated to the migration. Production "
            "traffic shares it — this will be felt."
        )

    return TransferEstimate(
        total_gib=round(total_gib, 1),
        total_hours=round(total_hours, 2),
        effective_mbps=round(effective, 1),
        link_mbps=link_mbps,
        link_share=link_share,
        cutover_window_hours=cutover_window_hours,
        waves=wave_transfers,
        recommend_offline_transfer=recommend_offline,
        assumptions=assumptions,
        warnings=warnings,
    )
