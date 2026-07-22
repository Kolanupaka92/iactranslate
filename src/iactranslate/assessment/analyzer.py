"""Deterministic pre-migration assessment of a normalized inventory.

`assess(vms)` runs a fixed battery of checks over the estate and returns an
`InfrastructureAssessment`: portfolio stats, categorized findings, and a
readiness score. Every finding is reproducible from the input — no AI, no
network — so a client can audit each conclusion.
"""
from __future__ import annotations

import re
from typing import Callable, List, Tuple

from ..models import NormalizedVM
from .models import (
    SEVERITY_WEIGHT,
    Finding,
    InfrastructureAssessment,
    ReadinessScore,
    Severity,
)

# Idle / oversized thresholds (utilization %). A workload reporting sustained
# low CPU *and* memory usage is a right-sizing opportunity.
_IDLE_CPU_PCT = 5.0
_IDLE_MEM_PCT = 15.0
# "Large" workloads that may need memory-optimized / dedicated instance families.
_LARGE_VCPU = 32
_LARGE_MEM_GIB = 128.0
# Per-category penalty cap so one noisy check can't dominate the score.
_CATEGORY_PENALTY_CAP = 30.0

# End-of-life / legacy OS signatures (substring, case-insensitive) -> label.
_LEGACY_OS: List[Tuple[str, str]] = [
    ("windows server 2003", "Windows Server 2003"),
    ("windows server 2008", "Windows Server 2008/R2"),
    ("2008 r2", "Windows Server 2008 R2"),
    ("windows server 2012", "Windows Server 2012/R2"),
    ("windows 7", "Windows 7"),
    ("centos", "CentOS (EOL)"),
    ("rhel 5", "RHEL 5"),
    ("rhel 6", "RHEL 6"),
    ("red hat enterprise linux 5", "RHEL 5"),
    ("red hat enterprise linux 6", "RHEL 6"),
    ("ubuntu 14", "Ubuntu 14.04"),
    ("ubuntu 16", "Ubuntu 16.04"),
    ("ubuntu 18", "Ubuntu 18.04"),
    ("sles 11", "SLES 11"),
    ("solaris", "Solaris"),
    ("aix", "AIX"),
]

# Cost-sensitive database engine signatures -> label (licensing considerations).
_DB_HINTS: List[Tuple[str, str]] = [
    ("sql server", "SQL Server"),
    ("oracle", "Oracle Database"),
]


def _is_powered_off(vm: NormalizedVM) -> bool:
    return bool(vm.power_state and vm.power_state.strip().lower() not in {"poweredon", "on", "running", "up"})


def _is_windows(vm: NormalizedVM) -> bool:
    return bool(vm.os and "windows" in vm.os.lower())


def _os_or_blank(vm: NormalizedVM) -> str:
    return (vm.os or "").strip().lower()


def _storage_gib(vm: NormalizedVM) -> float:
    return float(sum(vm.disks_gib))


# --- individual checks: each returns a list of Findings ------------------------

def _check_powered_off(vms: List[NormalizedVM]) -> List[Finding]:
    off = [v.vm_name for v in vms if _is_powered_off(v)]
    if not off:
        return []
    return [Finding(
        id="powered-off",
        category="cost",
        severity=Severity.MEDIUM,
        title="Powered-off workloads in scope",
        detail=(
            f"{len(off)} of {len(vms)} workloads are powered off. Migrating them provisions "
            "paid cloud capacity for machines that may be decommissioned."
        ),
        recommendation="Confirm each is still needed; exclude retired VMs before migrating.",
        affected=off,
    )]


def _check_missing_os(vms: List[NormalizedVM]) -> List[Finding]:
    missing = [v.vm_name for v in vms if not _os_or_blank(v)]
    if not missing:
        return []
    return [Finding(
        id="missing-os",
        category="data-quality",
        severity=Severity.HIGH,
        title="Workloads with no operating system recorded",
        detail=(
            f"{len(missing)} workloads have no OS in the inventory. OS drives image selection and "
            "licensing; without it the tool falls back to a Linux default."
        ),
        recommendation="Populate the OS column in the export, or verify the defaulted image per VM.",
        affected=missing,
    )]


def _check_legacy_os(vms: List[NormalizedVM]) -> List[Finding]:
    findings: List[Finding] = []
    for label_match, label in _LEGACY_OS:
        hit = [v.vm_name for v in vms if label_match in _os_or_blank(v)]
        if hit:
            findings.append(Finding(
                id=f"legacy-os-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}",
                category="risk",
                severity=Severity.HIGH,
                title=f"End-of-life OS: {label}",
                detail=(
                    f"{len(hit)} workload(s) run {label}, which is past or near end of support. "
                    "No security patches; some clouds restrict or surcharge these images."
                ),
                recommendation="Plan an in-place or post-migration OS upgrade; check extended-support options.",
                affected=hit,
            ))
    return findings


def _check_idle(vms: List[NormalizedVM]) -> List[Finding]:
    idle = [
        v.vm_name for v in vms
        if v.has_utilization
        and (v.cpu_util_pct is not None and v.cpu_util_pct <= _IDLE_CPU_PCT)
        and (v.mem_util_pct is not None and v.mem_util_pct <= _IDLE_MEM_PCT)
        and not _is_powered_off(v)
    ]
    if not idle:
        return []
    return [Finding(
        id="idle-oversized",
        category="cost",
        severity=Severity.MEDIUM,
        title="Idle / heavily over-provisioned workloads",
        detail=(
            f"{len(idle)} running workloads report CPU <= {_IDLE_CPU_PCT:.0f}% and memory "
            f"<= {_IDLE_MEM_PCT:.0f}% utilization. These are strong right-sizing or consolidation candidates."
        ),
        recommendation="Let utilization-based right-sizing shrink them, or consolidate before migrating.",
        affected=idle,
    )]


def _check_large(vms: List[NormalizedVM]) -> List[Finding]:
    large = [v.vm_name for v in vms if v.cpu >= _LARGE_VCPU or v.memory_gib >= _LARGE_MEM_GIB]
    if not large:
        return []
    return [Finding(
        id="large-workloads",
        category="capacity",
        severity=Severity.MEDIUM,
        title="Large workloads needing special instance families",
        detail=(
            f"{len(large)} workloads exceed {_LARGE_VCPU} vCPU or {_LARGE_MEM_GIB:.0f} GiB RAM. "
            "These map to memory-optimized or dedicated instance families and dominate cost."
        ),
        recommendation="Validate the selected instance type and quota limits for these workloads.",
        affected=large,
    )]


def _check_no_disks(vms: List[NormalizedVM]) -> List[Finding]:
    nodisk = [v.vm_name for v in vms if not v.disks_gib or _storage_gib(v) <= 0]
    if not nodisk:
        return []
    return [Finding(
        id="no-storage",
        category="data-quality",
        severity=Severity.MEDIUM,
        title="Workloads with no storage recorded",
        detail=(
            f"{len(nodisk)} workloads have no disk data. Root volumes will fall back to a "
            "default size, which may under- or over-provision storage."
        ),
        recommendation="Include disk/capacity columns in the export to size volumes accurately.",
        affected=nodisk,
    )]


def _check_licensing(vms: List[NormalizedVM]) -> List[Finding]:
    findings: List[Finding] = []
    win = [v.vm_name for v in vms if _is_windows(v)]
    if win:
        findings.append(Finding(
            id="windows-licensing",
            category="risk",
            severity=Severity.LOW,
            title="Windows licensing to reconcile",
            detail=(
                f"{len(win)} Windows workloads. Cloud Windows pricing includes license cost unless "
                "you bring your own (Azure Hybrid Benefit / AWS/GCP BYOL)."
            ),
            recommendation="Decide license-included vs BYOL; it materially shifts the cost comparison.",
            affected=win,
        ))
    for match, label in _DB_HINTS:
        hit = [v.vm_name for v in vms if match in _os_or_blank(v)]
        if hit:
            findings.append(Finding(
                id=f"db-licensing-{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}",
                category="risk",
                severity=Severity.MEDIUM,
                title=f"{label} licensing to reconcile",
                detail=(
                    f"{len(hit)} workload(s) appear to run {label}. Commercial database licensing is "
                    "often the largest single migration cost and may favor a managed service."
                ),
                recommendation=f"Evaluate a managed database vs self-hosted {label}, and BYOL options.",
                affected=hit,
            ))
    return findings


def _check_utilization_coverage(vms: List[NormalizedVM]) -> List[Finding]:
    with_util = sum(1 for v in vms if v.has_utilization)
    if not vms:
        return []
    coverage = with_util / len(vms)
    if coverage >= 0.5:
        return []
    sev = Severity.MEDIUM if coverage > 0 else Severity.HIGH
    return [Finding(
        id="low-utilization-coverage",
        category="data-quality",
        severity=sev,
        title="Little or no utilization data",
        detail=(
            f"Only {with_util} of {len(vms)} workloads ({coverage * 100:.0f}%) carry utilization data. "
            "Without it, sizing uses allocation (over-provisioned) rather than real demand, inflating cost."
        ),
        recommendation=(
            "Export CPU/memory utilization (e.g. RVTools vCPU/vMem %, monitoring data) "
            "for accurate right-sizing."
        ),
        affected=[],
    )]


def _check_duplicate_names(vms: List[NormalizedVM]) -> List[Finding]:
    seen: dict[str, int] = {}
    for v in vms:
        key = v.vm_name.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    dupes = sorted(name for name, n in seen.items() if n > 1)
    if not dupes:
        return []
    return [Finding(
        id="duplicate-names",
        category="data-quality",
        severity=Severity.LOW,
        title="Duplicate workload names",
        detail=(
            f"{len(dupes)} name(s) appear on more than one workload. Terraform resource names are "
            "de-duplicated automatically, but duplicates often signal inventory errors."
        ),
        recommendation="Confirm these are distinct machines and not double-counted rows.",
        affected=dupes,
    )]


_CHECKS: List[Callable[[List[NormalizedVM]], List[Finding]]] = [
    _check_powered_off,
    _check_missing_os,
    _check_legacy_os,
    _check_idle,
    _check_large,
    _check_no_disks,
    _check_licensing,
    _check_utilization_coverage,
    _check_duplicate_names,
]

_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


def _readiness(findings: List[Finding]) -> ReadinessScore:
    """Score 100 minus severity-weighted penalties, capped per category."""
    per_category: dict[str, float] = {}
    for f in findings:
        per_category[f.category] = per_category.get(f.category, 0.0) + SEVERITY_WEIGHT[f.severity]
    penalty = sum(min(p, _CATEGORY_PENALTY_CAP) for p in per_category.values())
    score = int(max(0.0, min(100.0, 100.0 - penalty)))

    if score >= 85:
        band, why = "ready", "Estate is clean and migration-ready with only minor items."
    elif score >= 65:
        band, why = "minor-gaps", "Migration-ready after addressing a few flagged items."
    elif score >= 40:
        band, why = "needs-work", "Several risks or data gaps should be resolved before migrating."
    else:
        band, why = "blocked", "Significant risks or missing data must be resolved first."
    return ReadinessScore(score=score, band=band, rationale=why)


def assess(
    vms: List[NormalizedVM],
    project_name: str = "assessment",
    source_platform: str = "unknown",
) -> InfrastructureAssessment:
    """Run every check and assemble the assessment artifact."""
    findings: List[Finding] = []
    for check in _CHECKS:
        findings.extend(check(vms))
    # Stable, severity-first ordering for reproducible output.
    findings.sort(key=lambda f: (_SEVERITY_ORDER.index(f.severity), f.id))

    total = len(vms)
    powered_off = sum(1 for v in vms if _is_powered_off(v))
    windows = sum(1 for v in vms if _is_windows(v))
    unknown_os = sum(1 for v in vms if not _os_or_blank(v))
    linux = total - windows - unknown_os
    with_util = sum(1 for v in vms if v.has_utilization)

    return InfrastructureAssessment(
        project_name=project_name,
        source_platform=source_platform,
        total_workloads=total,
        powered_on=total - powered_off,
        powered_off=powered_off,
        total_vcpu=sum(v.cpu for v in vms),
        total_memory_gib=round(sum(v.memory_gib for v in vms), 2),
        total_storage_gib=round(sum(_storage_gib(v) for v in vms), 2),
        windows_workloads=windows,
        linux_workloads=linux,
        unknown_os_workloads=unknown_os,
        utilization_coverage_pct=round((with_util / total * 100) if total else 0.0, 1),
        readiness=_readiness(findings),
        findings=findings,
    )
