"""Migration wave planning — deterministic sequencing, not discovery.

Enterprises don't migrate an estate in one shot; they migrate in *waves* with
an order that respects real dependencies (a web tier is useless without its
app tier; an app tier is useless without its database) and risk (validate the
pattern in lower environments before touching production).

**What this module does NOT do, on purpose:** discover real application
dependencies (service X calls service Y over port Z). That requires live
network flow data or an installed agent — this tool is explicitly offline,
file-in only (see architecture.md's scope boundary), so it has no such signal
to work from, and fabricating a dependency it can't observe would be worse
than not having one. What it *can* derive honestly, from data the plan
already carries:

  1. **Tier order within an application.** `database`/`cache` -> `app` ->
     `web` is a standard, defensible ordering: the layers something depends on
     get migrated (and validated) before the layer that depends on them.
  2. **Environment promotion order.** development/test -> staging ->
     production is the standard "prove it in a lower environment first"
     practice.

Cross-application dependencies (app A calling app B) are real and this module
cannot see them — the report says so explicitly (`WaveReport.notes`), the same
honesty pattern `replatform.py` uses for its own out-of-scope boundary.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from pydantic import BaseModel, Field

from .display import plural
from .models import ComputePlan, Environment, MigrationPlan, Tier

# Lower number = migrated (and validated) earlier. Foundational/stateful
# layers first, so what a later tier depends on already exists.
_TIER_DEPTH: Dict[Tier, int] = {
    Tier.DATABASE: 0,
    Tier.CACHE: 0,
    Tier.OTHER: 0,
    Tier.APP: 1,
    Tier.WEB: 2,
}
_TIER_DEPTH_LABEL = {0: "foundational (data/cache) tier", 1: "application tier", 2: "presentation (web) tier"}

# Lower number = migrated earlier — prove the pattern in a non-production
# environment before production. UNKNOWN is treated as risk-equivalent to
# production: we don't know it's safe to touch first, so don't assume it is.
_ENV_RANK: Dict[Environment, int] = {
    Environment.DEVELOPMENT: 0,
    Environment.TEST: 0,
    Environment.STAGING: 1,
    Environment.UNKNOWN: 2,
    Environment.PRODUCTION: 2,
}

_VALIDATION_CHECKS: Dict[int, List[str]] = {
    0: ["Data integrity spot-check against the source", "Backup/snapshot verified before cutover",
        "Connectivity reachable from the application tier's subnet/security group"],
    1: ["Service starts and passes its health check", "Confirmed reachable to its database/cache tier",
        "API smoke test against a representative endpoint"],
    2: ["HTTP(S) health endpoint returns 200", "Load balancer reports healthy targets",
        "TLS certificate valid for the expected hostname"],
}

_ROLLBACK_STRATEGY: Dict[int, str] = {
    0: "Restore from the pre-cutover backup/snapshot; repoint the application tier back to the source database.",
    1: "Redeploy the prior instance/image; scale the new instances to zero without deleting them.",
    2: "Shift the load balancer / DNS target back to the source web tier; keep new instances warm for retry.",
}


class MigrationWave(BaseModel):
    id: str
    sequence: int = Field(description="Migration order — lower migrates first")
    name: str
    environment: Environment
    tier_depth: int = Field(description="0=data/cache/other, 1=app, 2=web")
    workloads: List[str] = Field(default_factory=list, description="vm_name of each workload in this wave")
    depends_on: List[str] = Field(default_factory=list, description="Wave ids that must complete first")
    rollback_strategy: str
    validation_checks: List[str] = Field(default_factory=list)
    estimated_downtime_minutes: int = Field(
        description="Rough planning estimate, not a guarantee — 0 when every workload in the "
        "wave is fronted by a load balancer (rolling migration, no hard cutover window needed)."
    )


class WaveReport(BaseModel):
    schema_version: int = 1
    waves: List[MigrationWave] = Field(default_factory=list)
    summary: str
    notes: List[str] = Field(default_factory=list)


def _wave_key(c: ComputePlan) -> Tuple[int, int]:
    return (_ENV_RANK.get(c.environment, 2), _TIER_DEPTH.get(c.tier, 0))


def plan_waves(plan: MigrationPlan) -> WaveReport:
    """Group the plan's compute into ordered migration waves.

    Grouped by (environment promotion rank, tier depth) — every workload in a
    wave shares both, so a wave is always a single environment's single
    dependency layer. Deterministic; no AI.
    """
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}

    groups: Dict[Tuple[int, int], List[ComputePlan]] = defaultdict(list)
    for c in plan.compute:
        groups[_wave_key(c)].append(c)

    ordered_keys = sorted(groups.keys())
    wave_id_by_key: Dict[Tuple[int, int], str] = {}
    waves: List[MigrationWave] = []

    for sequence, key in enumerate(ordered_keys):
        env_rank, tier_depth = key
        members = sorted(groups[key], key=lambda c: c.vm_name)
        env_name = members[0].environment.value
        wave_id = f"wave-{sequence}-{env_name}-{_TIER_DEPTH_LABEL[tier_depth].split()[0]}"
        wave_id_by_key[key] = wave_id

        # Depends on the same environment's previous (lower) tier-depth wave,
        # if one exists — e.g. an app-tier wave depends on that environment's
        # data-tier wave. Cross-environment waves never depend on each other:
        # environments are independent estates, safe to run in parallel.
        depends_on: List[str] = []
        for prior_depth in range(tier_depth):
            prior_key = (env_rank, prior_depth)
            if prior_key in wave_id_by_key:
                depends_on.append(wave_id_by_key[prior_key])

        all_fronted = bool(members) and all(c.vm_name in fronted for c in members)
        downtime = 0 if all_fronted else (30 if tier_depth == 0 else 10)

        waves.append(MigrationWave(
            id=wave_id,
            sequence=sequence,
            name=f"{env_name.capitalize()} — {_TIER_DEPTH_LABEL[tier_depth]}",
            environment=members[0].environment,
            tier_depth=tier_depth,
            workloads=[c.vm_name for c in members],
            depends_on=depends_on,
            rollback_strategy=_ROLLBACK_STRATEGY[tier_depth],
            validation_checks=_VALIDATION_CHECKS[tier_depth],
            estimated_downtime_minutes=downtime,
        ))

    if waves:
        total_downtime = sum(w.estimated_downtime_minutes for w in waves)
        summary = (
            f"{plural(len(waves), 'migration wave')} across {plural(plan.vm_count, 'workload')}, sequenced by "
            f"environment (lower environments first) and tier dependency (data/cache before app "
            f"before web). Estimated {plural(total_downtime, 'minute')} of cumulative planning-level "
            f"downtime if waves run sequentially — waves in different environments can run in "
            f"parallel."
        )
    else:
        summary = "No compute workloads to sequence."

    notes = [
        "Ordering is derived from tier semantics (database/cache -> app -> web) and environment "
        "promotion order (dev/test -> staging -> production) — the two dependency signals this "
        "tool can actually observe from an inventory export.",
        "Cross-application dependencies (e.g. one app calling another over the network) are not "
        "discoverable from inventory alone and are not modeled here. If they exist, sequence the "
        "affected waves manually rather than trusting this order blindly.",
        "Downtime estimates are a rough planning input, not a guarantee — they don't account for "
        "data volume, replication lag, or application-specific cutover procedures.",
    ]
    return WaveReport(waves=waves, summary=summary, notes=notes)
