"""Target capability flags + model schema versioning."""
from fastapi.testclient import TestClient

from iactranslate.api.main import app
from iactranslate.models import MigrationPlan, NormalizedVM
from iactranslate.targets import get_target, list_targets
from iactranslate.targets.base import (
    CAP_BROWNFIELD_IMPORT,
    CAP_GITOPS,
    CAP_LIVE_PRICING,
    CAP_PULUMI,
    CAP_TERRAFORM,
)


def test_every_target_advertises_core_capabilities():
    for name in list_targets():
        caps = get_target(name).capabilities
        assert {CAP_TERRAFORM, CAP_PULUMI, CAP_GITOPS, CAP_LIVE_PRICING} <= caps


def test_only_aws_supports_brownfield_import_today():
    assert CAP_BROWNFIELD_IMPORT in get_target("aws").capabilities
    assert CAP_BROWNFIELD_IMPORT not in get_target("azure").capabilities
    assert CAP_BROWNFIELD_IMPORT not in get_target("gcp").capabilities


def test_targets_endpoint_exposes_capabilities():
    client = TestClient(app)
    r = client.get("/targets")
    assert r.status_code == 200
    by_name = {t["name"]: set(t["capabilities"]) for t in r.json()}
    assert set(by_name) == set(list_targets())
    assert CAP_BROWNFIELD_IMPORT in by_name["aws"]


def test_models_carry_schema_version():
    vm = NormalizedVM(vm_name="x", cpu=2, memory_gib=8)
    assert vm.schema_version == 1
    assert "schema_version" in vm.model_dump()

    from iactranslate.agents import build_migration_plan

    plan = build_migration_plan([vm], "x", get_target("aws"))
    assert isinstance(plan, MigrationPlan)
    assert plan.schema_version == 1
    assert "schema_version" in plan.model_dump()
