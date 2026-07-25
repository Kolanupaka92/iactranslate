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


def test_every_target_advertises_terraform_and_gitops():
    for name in list_targets():
        caps = get_target(name).capabilities
        assert {CAP_TERRAFORM, CAP_GITOPS} <= caps


def test_aws_azure_gcp_have_the_full_mature_capability_set():
    for name in ("aws", "azure", "gcp"):
        caps = get_target(name).capabilities
        assert {CAP_TERRAFORM, CAP_PULUMI, CAP_GITOPS, CAP_LIVE_PRICING} <= caps


def test_oci_has_no_pulumi_or_live_pricing_yet():
    # Honest, not a gap masked as parity: no Pulumi renderer and no live
    # pricing integration exist for OCI, so it doesn't claim either.
    caps = get_target("oci").capabilities
    assert CAP_PULUMI not in caps
    assert CAP_LIVE_PRICING not in caps


def test_only_aws_supports_brownfield_import_today():
    assert CAP_BROWNFIELD_IMPORT in get_target("aws").capabilities
    assert CAP_BROWNFIELD_IMPORT not in get_target("azure").capabilities
    assert CAP_BROWNFIELD_IMPORT not in get_target("gcp").capabilities
    assert CAP_BROWNFIELD_IMPORT not in get_target("oci").capabilities


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
