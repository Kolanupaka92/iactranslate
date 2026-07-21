import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import UnknownTargetError, get_target, list_targets
from iactranslate.validation import validate_plan


def test_registry_lists_all_clouds():
    assert set(list_targets()) == {"aws", "azure", "gcp"}


def test_unknown_target_raises():
    with pytest.raises(UnknownTargetError):
        get_target("oracle")


@pytest.mark.parametrize("target_name", ["aws", "azure", "gcp"])
def test_target_produces_valid_plan(rvtools_path, target_name):
    target = get_target(target_name)
    vms = normalize(parse(rvtools_path))
    plan = build_migration_plan(vms, project_name="reg-test", target=target)

    assert plan.target == target_name
    assert plan.region == target.default_region
    assert validate_plan(plan, target) == []
    # Every chosen instance type belongs to that cloud's catalog.
    for c in plan.compute:
        assert target.instance_exists(c.instance_type)


def test_targets_pick_distinct_instance_families(rvtools_path):
    vms = normalize(parse(rvtools_path))
    aws_types = {c.instance_type for c in build_migration_plan(vms, "t", get_target("aws")).compute}
    az_types = {c.instance_type for c in build_migration_plan(vms, "t", get_target("azure")).compute}
    gcp_types = {c.instance_type for c in build_migration_plan(vms, "t", get_target("gcp")).compute}
    assert all(t.startswith(("t3.", "m5.", "r5.")) for t in aws_types)
    assert all(t.startswith("Standard_") for t in az_types)
    assert all(t.startswith(("e2-", "n2-")) for t in gcp_types)
