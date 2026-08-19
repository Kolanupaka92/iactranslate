"""Managed-database re-platforming advisor.

Lift-and-shift puts a database on a plain VM. That works, but for a database
tier the cloud-native move is often a *managed* service (Amazon RDS, Azure SQL,
Cloud SQL) — the provider runs backups, patching, HA, and failover instead of
your team. This module flags database-tier workloads as re-platforming
*candidates* and names the managed service each cloud offers for the detected
engine.

Deliberately **advisory-only**: it does NOT change the migration plan. The
database still gets a lift-and-shift instance in the generated IaC — because
re-platforming a database is a data-migration project (schema conversion,
replication cutover, connection-string changes, downtime planning) that this
tool explicitly does not perform. The report tells the operator *where* managed
services would help and *what* to weigh, and stops there. No AI, deterministic.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .display import plural
from .models import MigrationPlan, NormalizedVM, Tier

# Engine keyword → canonical engine id. Order matters (check specific before
# generic): "postgres" before "sql", etc.
_ENGINE_KEYWORDS: List[tuple] = [
    ("postgresql", ("postgres", "postgre", "pgsql", "pg-")),
    ("mysql", ("mysql",)),
    ("mariadb", ("mariadb",)),
    ("sqlserver", ("sqlserver", "mssql", "sql-server", "sql server")),
    ("oracle", ("oracle", "orcl")),
    ("mongodb", ("mongo",)),
    ("redis", ("redis",)),
]

# engine → managed service name, per target cloud. "unknown" is the fallback
# when the workload name doesn't reveal the engine.
_MANAGED_SERVICE: Dict[str, Dict[str, str]] = {
    "aws": {
        "postgresql": "Amazon RDS for PostgreSQL",
        "mysql": "Amazon RDS for MySQL",
        "mariadb": "Amazon RDS for MariaDB",
        "sqlserver": "Amazon RDS for SQL Server",
        "oracle": "Amazon RDS for Oracle",
        "mongodb": "Amazon DocumentDB (MongoDB-compatible)",
        "redis": "Amazon ElastiCache for Redis",
        "unknown": "Amazon RDS",
    },
    "azure": {
        "postgresql": "Azure Database for PostgreSQL Flexible Server",
        "mysql": "Azure Database for MySQL Flexible Server",
        "mariadb": "Azure Database for MySQL (MariaDB-compatible workloads)",
        "sqlserver": "Azure SQL Managed Instance",
        "oracle": "Oracle Database@Azure",
        "mongodb": "Azure Cosmos DB for MongoDB",
        "redis": "Azure Cache for Redis",
        "unknown": "Azure SQL / Azure Database",
    },
    "gcp": {
        "postgresql": "Cloud SQL for PostgreSQL (or AlloyDB)",
        "mysql": "Cloud SQL for MySQL",
        "mariadb": "Cloud SQL for MySQL",
        "sqlserver": "Cloud SQL for SQL Server",
        "oracle": "Bare Metal Solution / partner offering (no native managed Oracle)",
        "mongodb": "MongoDB Atlas on GCP (partner)",
        "redis": "Memorystore for Redis",
        "unknown": "Cloud SQL",
    },
}

# Engines with no fully-managed equivalent on a given cloud — surfaced as a caveat.
_NO_NATIVE_MANAGED: Dict[str, set] = {
    "gcp": {"oracle", "mongodb"},
}


class ReplatformCandidate(BaseModel):
    vm_name: str
    engine: str = Field(description="Detected DB engine, or 'unknown'")
    current_instance_type: str = Field(description="The lift-and-shift instance the plan chose")
    managed_service: str
    suggested_vcpu: int
    suggested_memory_gib: float
    storage_gib: int
    rationale: str
    caveats: List[str] = Field(default_factory=list)


class ReplatformReport(BaseModel):
    schema_version: int = 1
    target: str
    candidates: List[ReplatformCandidate] = Field(default_factory=list)
    summary: str
    notes: List[str] = Field(default_factory=list)


def _haystack(vm_name: str, vm: Optional[NormalizedVM]) -> str:
    parts = [vm_name]
    if vm is not None:
        parts += [vm.hostname or "", vm.os or ""]
    return " ".join(parts).lower()


def detect_engine(vm_name: str, vm: Optional[NormalizedVM] = None) -> str:
    hay = _haystack(vm_name, vm)
    for engine, needles in _ENGINE_KEYWORDS:
        if any(n in hay for n in needles):
            return engine
    return "unknown"


def analyze_replatforming(
    plan: MigrationPlan, vms: Optional[List[NormalizedVM]] = None
) -> ReplatformReport:
    """Flag database-tier workloads as managed-DB re-platforming candidates."""
    target = plan.target
    services = _MANAGED_SERVICE.get(target, _MANAGED_SERVICE["aws"])
    no_native = _NO_NATIVE_MANAGED.get(target, set())
    vm_by_name = {v.vm_name: v for v in (vms or [])}

    candidates: List[ReplatformCandidate] = []
    for c in plan.compute:
        if c.tier != Tier.DATABASE:
            continue
        engine = detect_engine(c.vm_name, vm_by_name.get(c.vm_name))
        storage = c.root_volume_gib + sum(c.extra_volumes_gib)

        caveats = [
            "Data migration (schema conversion, replication cutover, downtime) is "
            "out of scope — this is a candidate flag, not an automated migration.",
        ]
        if engine == "unknown":
            caveats.append(
                "Engine could not be identified from the workload name — confirm the "
                "actual database before choosing a managed service."
            )
        if engine == "sqlserver":
            caveats.append("SQL Server licensing (BYOL vs. license-included) changes the cost comparison.")
        if engine in no_native:
            caveats.append(
                f"{target.upper()} has no fully-managed {engine} service — the named option is a "
                "partner/self-managed path; keeping the lift-and-shift VM may be simpler."
            )

        rationale = (
            f"Database-tier workload on a lift-and-shift {c.instance_type}. A managed "
            f"{services.get(engine, services['unknown'])} would offload backups, patching, "
            "and HA/failover to the provider."
        )
        candidates.append(
            ReplatformCandidate(
                vm_name=c.vm_name,
                engine=engine,
                current_instance_type=c.instance_type,
                managed_service=services.get(engine, services["unknown"]),
                suggested_vcpu=c.vcpu,
                suggested_memory_gib=c.memory_gib,
                storage_gib=storage,
                rationale=rationale,
                caveats=caveats,
            )
        )

    if candidates:
        summary = (
            f"{plural(len(candidates), 'database-tier workload')} are candidates for managed-DB "
            f"re-platforming on {target.upper()}. The generated IaC still lift-and-shifts them; "
            "review each against the caveats before committing to a managed service."
        )
    else:
        summary = "No database-tier workloads detected — nothing to re-platform."

    notes = [
        "Managed databases trade operational control for provider-run backups, patching, "
        "and HA — factor in engine-version support, extension/feature parity, and egress.",
        "Cost is not directly comparable: a managed instance bundles storage, backups, and "
        "HA that a bare VM does not — compare total cost of ownership, not sticker price.",
    ]
    return ReplatformReport(target=target, candidates=candidates, summary=summary, notes=notes)
