"""Managed-database re-platforming advisor — advisory-only, never changes the plan."""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import (
    ComputePlan,
    Environment,
    MigrationPlan,
    NetworkPlan,
    Tier,
)
from iactranslate.normalize import normalize
from iactranslate.replatform import analyze_replatforming, detect_engine
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


@pytest.mark.parametrize(
    "name,expected",
    [
        ("prod-postgres-01", "postgresql"),
        ("mysql-primary", "mysql"),
        ("app-mssql-1", "sqlserver"),
        ("oracle-erp", "oracle"),
        ("mongo-shard-a", "mongodb"),
        ("prod-db-01", "unknown"),
        ("web-01", "unknown"),
    ],
)
def test_detect_engine(name, expected):
    assert detect_engine(name) == expected


def _db_plan(target="aws", vm_name="prod-postgres-01"):
    compute = [
        ComputePlan(
            vm_name=vm_name, resource_name=vm_name.replace("-", "_"),
            instance_type="t3.large", image_key="ubuntu-22.04", vcpu=4, memory_gib=16.0,
            root_volume_gib=100, extra_volumes_gib=[500], tier=Tier.DATABASE,
            environment=Environment.PRODUCTION,
        ),
        ComputePlan(
            vm_name="web-01", resource_name="web_01",
            instance_type="t3.medium", image_key="ubuntu-22.04", vcpu=2, memory_gib=8.0,
            root_volume_gib=50, tier=Tier.WEB, environment=Environment.PRODUCTION,
        ),
    ]
    return MigrationPlan(
        project_name="p", target=target, region="us-east-1",
        network=NetworkPlan(), compute=compute,
    )


def test_only_database_tier_workloads_are_candidates():
    report = analyze_replatforming(_db_plan())
    assert len(report.candidates) == 1
    cand = report.candidates[0]
    assert cand.vm_name == "prod-postgres-01"
    assert cand.engine == "postgresql"
    assert cand.managed_service == "Amazon RDS for PostgreSQL"
    assert cand.storage_gib == 600  # root + extra volumes


def test_managed_service_is_cloud_specific():
    assert analyze_replatforming(_db_plan("aws")).candidates[0].managed_service.startswith("Amazon RDS")
    assert "Azure" in analyze_replatforming(_db_plan("azure")).candidates[0].managed_service
    assert "Cloud SQL" in analyze_replatforming(_db_plan("gcp")).candidates[0].managed_service


def test_unknown_engine_gets_a_confirm_caveat():
    report = analyze_replatforming(_db_plan(vm_name="prod-db-01"))
    cand = report.candidates[0]
    assert cand.engine == "unknown"
    assert any("confirm" in c.lower() for c in cand.caveats)


def test_gcp_flags_no_native_oracle():
    report = analyze_replatforming(_db_plan("gcp", vm_name="oracle-erp-01"))
    cand = report.candidates[0]
    assert any("no fully-managed oracle" in c.lower() or "no native managed" in c.lower()
               for c in cand.caveats)


def test_no_databases_yields_no_candidates():
    plan = MigrationPlan(
        project_name="p", target="aws", region="us-east-1", network=NetworkPlan(),
        compute=[ComputePlan(
            vm_name="web-01", resource_name="web_01", instance_type="t3.medium",
            image_key="ubuntu-22.04", vcpu=2, memory_gib=8.0, root_volume_gib=50,
            tier=Tier.WEB, environment=Environment.PRODUCTION,
        )],
    )
    report = analyze_replatforming(plan)
    assert report.candidates == []
    assert "No database-tier" in report.summary


def test_advisory_only_does_not_mutate_the_plan(rvtools_path):
    vms = normalize(resolve_source(rvtools_path).parse(rvtools_path))
    plan = build_migration_plan(vms, "p", get_target("aws"))
    before = plan.model_dump()
    analyze_replatforming(plan, vms)
    assert plan.model_dump() == before  # report is read-only
