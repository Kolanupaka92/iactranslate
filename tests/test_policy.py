"""Policy engine — enforce org rules on the plan, read-only, before rendering."""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import MigrationPlan
from iactranslate.normalize import normalize
from iactranslate.pipeline import run_pipeline
from iactranslate.policy import (
    PolicyViolationError,
    Severity,
    UnknownPolicyError,
    evaluate,
    list_policies,
)
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(path="tests/fixtures/rvtools_sample.xlsx", target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "pol", get_target(target)), vms


def test_no_config_means_no_violations():
    plan, _ = _plan()
    r = evaluate(plan, get_target("aws"), None)
    assert r.ok and r.violations == []


def test_max_vcpu_denies_oversized():
    plan, _ = _plan()
    r = evaluate(plan, get_target("aws"), {"max_vcpu": {"max": 8}})
    assert not r.ok
    assert all(v.severity == Severity.DENY for v in r.denials)
    assert any("vCPU" in v.message for v in r.denials)


def test_no_public_subnets_flags_web_tier():
    plan, _ = _plan()
    r = evaluate(plan, get_target("aws"), {"no_public_subnets": {}})
    assert not r.ok
    assert all(v.policy == "no_public_subnets" for v in r.denials)


def test_severity_override_downgrades_to_warn():
    plan, _ = _plan()
    r = evaluate(plan, get_target("aws"), {"max_vcpu": {"max": 8, "severity": "warn"}})
    assert r.ok  # no denials — all downgraded to warnings
    assert r.warnings and all(v.severity == Severity.WARN for v in r.warnings)


def test_allowed_instance_families():
    plan, _ = _plan()
    # Allow only 'c5' (not used in the sample) → everything violates.
    r = evaluate(plan, get_target("aws"), {"allowed_instance_families": {"families": ["c5"]}})
    assert not r.ok
    # Allow every family actually used → clean.
    used = sorted({c.instance_type.split(".")[0] for c in plan.compute})
    r2 = evaluate(plan, get_target("aws"), {"allowed_instance_families": {"families": used}})
    assert r2.ok


def test_max_monthly_cost_budget():
    plan, _ = _plan()
    over = evaluate(plan, get_target("aws"), {"max_monthly_cost": {"budget_usd": 1.0}})
    assert not over.ok
    under = evaluate(plan, get_target("aws"), {"max_monthly_cost": {"budget_usd": 1_000_000}})
    assert under.ok


def test_unknown_policy_raises():
    plan, _ = _plan()
    with pytest.raises(UnknownPolicyError):
        evaluate(plan, get_target("aws"), {"encrypt_everything": {}})


def test_list_policies_nonempty():
    names = list_policies()
    assert "no_public_subnets" in names and "max_vcpu" in names
    assert all(isinstance(desc, str) and desc for desc in names.values())


def test_policy_does_not_mutate_plan():
    plan, _ = _plan()
    before = plan.model_dump()
    evaluate(plan, get_target("aws"), {"max_vcpu": {"max": 1}, "no_public_subnets": {}})
    assert plan.model_dump() == before  # read-only guarantee


def test_pipeline_denies_and_aborts(tmp_path):
    with pytest.raises(PolicyViolationError):
        run_pipeline(
            input_path="tests/fixtures/rvtools_sample.xlsx",
            project_name="p", out_dir=str(tmp_path / "p"), target="aws",
            policy_config={"max_vcpu": {"max": 8}},
        )
    # Nothing was rendered.
    assert not (tmp_path / "p" / "compute.tf").exists()


def test_pipeline_ships_warnings(tmp_path):
    r = run_pipeline(
        input_path="tests/fixtures/rvtools_sample.xlsx",
        project_name="p", out_dir=str(tmp_path / "p"), target="aws",
        policy_config={"naming_prefix": {"prefix": "acme_"}},
    )
    assert r.policy is not None and r.policy.warnings
    assert (r.project_dir / "policy-report.json").exists()
    assert (r.project_dir / "compute.tf").exists()  # still rendered


def test_valid_plan_type(tmp_path):
    # Sanity: evaluate returns for any MigrationPlan without needing vms.
    plan, _ = _plan()
    assert isinstance(plan, MigrationPlan)
