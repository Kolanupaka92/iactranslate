"""Deterministic multi-cloud recommender.

Given a normalized VMware inventory, builds a migration plan for every registered
target and scores each cloud on three transparent, auditable dimensions:

  - cost      : projected monthly on-demand spend (cheaper is better)
  - fit       : how tightly the chosen instances match the source vCPU/memory
                (less over-provisioning is better)
  - os_affinity: alignment with the estate's OS mix (Windows -> Azure,
                Linux -> GCP, broad/mixed -> AWS)

The AI is not in this loop — the recommendation is a weighted score the client
can inspect and defend. Weights are explicit and tunable below.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .agents import build_migration_plan
from .config import MAX_VMS
from .models import NormalizedVM
from .sizing import effective_demand
from .targets import get_target, list_targets

# Scoring weights (sum to 1.0). Cost dominates but sizing fit and OS affinity
# meaningfully shift the recommendation for Windows- or Linux-heavy estates.
W_COST = 0.45
W_FIT = 0.30
W_OS = 0.25

# Baseline OS-affinity score for AWS (neutral, broad ecosystem).
_AWS_OS_BASELINE = 0.6


class CloudScore(BaseModel):
    cloud: str
    total_monthly_cost_usd: float
    annual_cost_usd: float = 0.0
    windows_vms: int
    linux_vms: int
    cost_score: float = Field(description="0-1, higher is cheaper relative to the cheapest cloud")
    fit_score: float = Field(description="0-1, higher means less over-provisioning")
    os_score: float = Field(description="0-1, higher means better OS-mix alignment")
    weighted_score: float
    unsupported_workloads: int = Field(
        default=0,
        description="VMs whose operating system this cloud publishes no image for",
    )
    eligible: bool = Field(
        default=True,
        description="False when the cloud cannot host part of the estate at all",
    )
    reasons: List[str] = Field(default_factory=list)


class ScoringWeights(BaseModel):
    """The weights behind `weighted_score`, carried in the response.

    "Weights are explicit and inspectable; no vendor gets a thumb on the scale"
    is a design principle and the main reason to trust this over a cloud
    vendor's own tool — but the weights lived only as module constants, so the
    one question an architect actually asks ("how are you weighting this?")
    could only be answered by reading the source. Shipping them makes the
    ranking checkable by hand.
    """

    cost: float = Field(description="Weight applied to cost_score")
    fit: float = Field(description="Weight applied to fit_score")
    os: float = Field(description="Weight applied to os_score")


class Recommendation(BaseModel):
    recommended: str
    summary: str
    ranked: List[CloudScore]
    # 2.0: how decisive the call is, the score gap to the runner-up, and
    # estate-level observations a reviewer should weigh.
    decisiveness: str = Field(default="clear", description="'clear' | 'moderate' | 'close'")
    margin: float = Field(default=0.0, description="Winner's weighted-score lead over #2")
    runner_up: Optional[str] = Field(
        default=None, description="Cloud ranked #2 — what `margin` is measured against"
    )
    weights: ScoringWeights = Field(
        default_factory=lambda: ScoringWeights(cost=W_COST, fit=W_FIT, os=W_OS)
    )
    notes: List[str] = Field(default_factory=list)


def _os_family(text: str) -> str:
    """`windows` vs `linux` — see `agents.rightsizing._os_family`."""
    return "windows" if "windows" in text.lower() else "linux"


def _is_windows(vm: NormalizedVM) -> bool:
    return bool(vm.os and "windows" in vm.os.lower())


def _fit(vms: List[NormalizedVM], compute) -> float:
    """Ratio of real demand to provisioned capacity (1.0 = perfect, lower = waste).

    Demand is utilization-based where available, so fit reflects true tightness
    rather than translated over-provisioning.
    """
    demands = [effective_demand(v) for v in vms]
    req_cpu = sum(d.vcpu for d in demands) or 1.0
    req_mem = sum(d.memory_gib for d in demands) or 1.0
    got_cpu = sum(c.vcpu for c in compute) or req_cpu
    got_mem = sum(c.memory_gib for c in compute) or req_mem
    cpu_fit = min(1.0, req_cpu / got_cpu)
    mem_fit = min(1.0, req_mem / got_mem)
    return round((cpu_fit + mem_fit) / 2, 4)


def _os_affinity(cloud: str, windows_fraction: float) -> float:
    linux_fraction = 1.0 - windows_fraction
    if cloud == "azure":
        return round(0.5 + 0.5 * windows_fraction, 4)
    if cloud == "gcp":
        return round(0.5 + 0.5 * linux_fraction, 4)
    # aws, oci, digitalocean: neutral, no first-party OS licensing bias either way
    return _AWS_OS_BASELINE


def _unsupported_count(target, vms: List[NormalizedVM]) -> int:
    """How many workloads this cloud publishes no usable image for.

    Asked of the target's own catalog rather than hardcoded per cloud: if a
    target maps a Windows source OS to a Linux image key, it has no Windows
    image, and any plan it produces would boot the wrong operating system.
    DigitalOcean is the live example (ADR 0023) but nothing here names it, so a
    future catalog change is caught the same way.
    """
    count = 0
    for vm in vms:
        if not vm.os:
            continue
        if _os_family(vm.os) != _os_family(target.image_key(vm.os)):
            count += 1
    return count


def recommend(vms: List[NormalizedVM], targets: Optional[List[str]] = None) -> Recommendation:
    if len(vms) > MAX_VMS:
        raise ValueError(f"Inventory has {len(vms)} VMs, exceeding the limit of {MAX_VMS}")
    names = targets or list_targets()
    total = len(vms)
    windows = sum(1 for v in vms if _is_windows(v))
    linux = total - windows
    windows_fraction = (windows / total) if total else 0.0

    # Build a plan per cloud and collect raw metrics.
    raw: Dict[str, dict] = {}
    for name in names:
        target = get_target(name)
        plan = build_migration_plan(vms, project_name="recommendation", target=target)
        raw[name] = {
            "cost": plan.total_estimated_monthly_cost_usd,
            "fit": _fit(vms, plan.compute),
            "os": _os_affinity(name, windows_fraction),
            "unsupported": _unsupported_count(target, vms),
        }

    # The cost baseline every other cloud is measured against must come from a
    # cloud that could actually take the estate. An ineligible cloud is cheapest
    # by construction — it silently drops the workloads it cannot host — so
    # anchoring "$X more than the cheapest option" to it would overstate the
    # premium on every real candidate.
    priced = {n: r for n, r in raw.items() if not r["unsupported"]} or raw
    cheapest_cost = min(r["cost"] for r in priced.values()) or 1.0
    best_fit = max(r["fit"] for r in raw.values())
    lowest_cost_cloud = min(priced, key=lambda n: priced[n]["cost"])
    # Only claim "tightest fit" when one cloud strictly wins (not a tie).
    fit_winners = [n for n, r in raw.items() if r["fit"] == best_fit]
    tightest_fit_cloud = fit_winners[0] if len(fit_winners) == 1 else None

    scores: List[CloudScore] = []
    for name, r in raw.items():
        cost_score = round(min(cheapest_cost / r["cost"], 1.0), 4) if r["cost"] else 1.0
        fit_score = r["fit"]
        os_score = r["os"]
        unsupported = r["unsupported"]
        eligible = unsupported == 0
        weighted = round(W_COST * cost_score + W_FIT * fit_score + W_OS * os_score, 4)
        if not eligible:
            # A disqualified cloud scores zero, not "well but excluded". Leaving
            # it a competitive number produced a table whose ranking contradicted
            # its own scores — DigitalOcean sitting last with the highest figure
            # on the page. The component scores below are untouched, so what it
            # *would* have scored is still fully visible and checkable.
            weighted = 0.0

        reasons: List[str] = []
        if not eligible:
            # Stated first, and stated bluntly. This cloud's cost advantage is
            # partly an artifact of not running workloads it cannot run, so
            # presenting it as a cheaper peer would be actively misleading.
            reasons.append(
                f"NOT ELIGIBLE: {unsupported} of {total} workloads run an operating "
                f"system {name.upper()} publishes no image for. Its cost figure "
                f"excludes licensing for machines it cannot host, so it is not "
                f"comparable to the others."
            )
        if eligible:
            if name == lowest_cost_cloud:
                reasons.append(f"Lowest projected cost (${r['cost']:.2f}/mo).")
            else:
                delta = r["cost"] - cheapest_cost
                reasons.append(f"${delta:.2f}/mo more than the cheapest option.")
            if name == tightest_fit_cloud:
                reasons.append("Tightest instance sizing — least over-provisioning.")
        else:
            # No selling points for a disqualified cloud. "Cheapest" and
            # "tightest sizing" are arguments *for* choosing it, and both are
            # artifacts of the workloads it silently drops.
            reasons.append(
                f"Priced at ${r['cost']:.2f}/mo for the "
                f"{total - unsupported} workloads it can host, shown for reference only."
            )
        if name == "azure" and windows > 0:
            reasons.append(
                f"Strong fit for a Windows-heavy estate ({windows} of {total} VMs run Windows): "
                "Azure Hybrid Benefit and native AD/SQL Server affinity."
            )
        if name == "gcp" and linux > 0:
            reasons.append(
                f"Strong fit for a Linux-heavy estate ({linux} of {total} VMs run Linux): "
                "competitive per-second billing and container/Kubernetes strengths."
            )
        if name == "aws":
            reasons.append("Broadest service ecosystem and the largest VMware-migration tooling footprint.")

        scores.append(
            CloudScore(
                cloud=name,
                total_monthly_cost_usd=r["cost"],
                annual_cost_usd=round(r["cost"] * 12, 2),
                windows_vms=windows,
                linux_vms=linux,
                cost_score=cost_score,
                fit_score=fit_score,
                os_score=os_score,
                weighted_score=weighted,
                unsupported_workloads=unsupported,
                eligible=eligible,
                reasons=reasons,
            )
        )

    # Eligibility dominates score. A cloud that cannot run part of the estate is
    # not a cheaper option, it is not an option — and it would otherwise *win* on
    # cost precisely because it skips the workloads it cannot host.
    scores.sort(key=lambda s: (s.eligible, s.weighted_score), reverse=True)
    winner = scores[0]
    eligible_scores = [s for s in scores if s.eligible]

    # 2.0: decisiveness from the score gap to the runner-up.
    # Measured against the next genuine alternative: a margin over a cloud that
    # cannot host the estate says nothing about how decisive the choice is.
    contenders = eligible_scores or scores
    margin = (
        round(winner.weighted_score - contenders[1].weighted_score, 4)
        if len(contenders) > 1
        else winner.weighted_score
    )
    if margin >= 0.10:
        decisiveness = "clear"
    elif margin >= 0.03:
        decisiveness = "moderate"
    else:
        decisiveness = "close"

    # 2.0: estate-level notes a reviewer should weigh.
    # Quote the spread across clouds that could actually take the estate. An
    # ineligible cloud always looks cheapest — it is not running everything —
    # and headlining its number would reintroduce the comparison the
    # eligibility check exists to prevent.
    priciest = max(contenders, key=lambda s: s.total_monthly_cost_usd)
    annual_spread = round((priciest.total_monthly_cost_usd - cheapest_cost) * 12, 2)
    notes: List[str] = [
        f"Annual spend ranges ${cheapest_cost * 12:,.0f} ({lowest_cost_cloud.upper()}) to "
        f"${priciest.total_monthly_cost_usd * 12:,.0f} ({priciest.cloud.upper()}) — "
        f"a ${annual_spread:,.0f}/yr difference across clouds.",
    ]
    if windows and linux:
        notes.append(
            f"Mixed estate: {windows} Windows / {linux} Linux. Windows licensing "
            "(Azure Hybrid Benefit vs BYOL) can shift the cost comparison."
        )
    for s_ in scores:
        if not s_.eligible:
            notes.append(
                f"{s_.cloud.upper()} was excluded from the recommendation: it cannot "
                f"host {s_.unsupported_workloads} of {total} workloads. Migrating those "
                f"would need a different cloud or a custom image."
            )
    if decisiveness == "close":
        notes.append(
            "Close call — the top two clouds are within the scoring noise; "
            "adjusting the cost/fit/OS weights could flip the recommendation."
        )

    os_note = (
        f"{windows}/{total} Windows"
        if windows >= linux
        else f"{linux}/{total} Linux"
    )
    summary = (
        f"Recommended: {winner.cloud.upper()} (score {winner.weighted_score:.2f}, "
        f"{decisiveness} lead of {margin:.2f} over #{2 if len(scores) > 1 else 1}). "
        f"Estate is {os_note}; cheapest option is {lowest_cost_cloud.upper()} at "
        f"${cheapest_cost:.2f}/mo (${cheapest_cost * 12:,.0f}/yr). "
        f"Weights — cost {W_COST}, fit {W_FIT}, OS {W_OS}."
    )
    return Recommendation(
        recommended=winner.cloud, summary=summary, ranked=scores,
        decisiveness=decisiveness, margin=margin,
        runner_up=contenders[1].cloud if len(contenders) > 1 else None,
        weights=ScoringWeights(cost=W_COST, fit=W_FIT, os=W_OS),
        notes=notes,
    )
