import copy

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import Subnet, SubnetTier
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.validation import PlanValidationError, assert_valid, validate_plan


def _plan(rvtools_path):
    vms = normalize(parse(rvtools_path))
    return build_migration_plan(vms, project_name="test")


def test_valid_plan_has_no_issues(rvtools_path):
    assert validate_plan(_plan(rvtools_path)) == []


def test_bad_instance_type_is_caught(rvtools_path):
    plan = _plan(rvtools_path)
    plan.compute[0].instance_type = "t3.nonexistent"
    issues = validate_plan(plan)
    assert any("not in the AWS catalog" in i for i in issues)


def test_cidr_overlap_is_caught(rvtools_path):
    plan = _plan(rvtools_path)
    # Force two subnets onto the same CIDR.
    plan.network.subnets[1].cidr = plan.network.subnets[0].cidr
    issues = validate_plan(plan)
    assert any("overlap" in i for i in issues)


def test_subnet_outside_vpc_is_caught(rvtools_path):
    plan = _plan(rvtools_path)
    plan.network.subnets[0].cidr = "192.168.5.0/24"
    issues = validate_plan(plan)
    assert any("not within the VPC" in i for i in issues)


def test_undefined_security_group_is_caught(rvtools_path):
    plan = _plan(rvtools_path)
    plan.compute[0].security_group = "ghost-sg"
    issues = validate_plan(plan)
    assert any("undefined security group" in i for i in issues)


def test_assert_valid_raises(rvtools_path):
    plan = _plan(rvtools_path)
    plan.compute[0].instance_type = "bogus"
    with pytest.raises(PlanValidationError):
        assert_valid(plan)
