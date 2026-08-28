"""Workload identity per tier, with no permissions attached.

Generated instances used to land with no identity at all, so the first thing a
customer wrote by hand was the security-sensitive part. The tests that matter
here are the ones asserting what is *not* generated: a migration tool that
guesses permissions is a migration tool that creates the breach.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.generator import build_files
from iactranslate.identity import (
    SSM_MANAGED_POLICY,
    IdentityError,
    IdentityMode,
    parse_mode,
    roles_for,
)
from iactranslate.identity import supported as identity_supported
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target, list_targets


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_sample.xlsx"))


def _files(vms, cloud, mode=IdentityMode.BASIC):
    target = get_target(cloud)
    plan = build_migration_plan(vms, "identity-test", target)
    return build_files(plan, target, None, None, mode), plan


# --- modes -------------------------------------------------------------------

def test_basic_is_the_default():
    assert parse_mode(None) is IdentityMode.BASIC


def test_an_unknown_mode_names_the_alternatives():
    with pytest.raises(IdentityError) as e:
        parse_mode("wizard")
    assert "none, basic, ssm" in str(e.value)


def test_none_generates_nothing(vms):
    """The historical output, unchanged, for anyone who wants it."""
    files, _ = _files(vms, "aws", IdentityMode.NONE)
    assert "identity.tf" not in files
    assert "iam_instance_profile" not in files["compute.tf"]


# --- one identity per tier, not per instance ---------------------------------

def test_roles_are_per_tier_not_per_instance(vms):
    """Per-instance roles are theoretically tighter and practically unusable: a
    5,000-VM estate would emit 5,000 roles, exceed the IAM quota, and give an
    operator 5,000 places to attach the same policy."""
    _, plan = _files(vms, "aws")
    roles = roles_for(plan.compute, IdentityMode.BASIC)
    assert roles == sorted(set(roles)), "no duplicates"
    assert len(roles) < len(plan.compute)
    assert set(roles) == {c.tier.value for c in plan.compute}


# --- what is generated, and what deliberately is not -------------------------

def test_no_permissions_are_attached_by_default(vms):
    """The central design decision. An inventory cannot say what a workload may
    legitimately reach, and a guess is either too narrow to work — widened to
    `*` under time pressure — or too broad to be safe."""
    files, _ = _files(vms, "aws")
    identity = files["identity.tf"]
    assert "aws_iam_role" in identity
    assert "aws_iam_instance_profile" in identity

    # Assert on the *resources*, not the file: the header carries a commented
    # example of attaching a policy, which is documentation and should stay.
    code = "\n".join(
        line for line in identity.splitlines() if not line.lstrip().startswith("#")
    )
    assert "aws_iam_policy" not in code
    assert "aws_iam_role_policy_attachment" not in code
    assert '"*"' not in code.replace('Action    = "sts:AssumeRole"', "")


def test_the_trust_policy_names_only_ec2(vms):
    """A trust policy naming the wrong principal produces a role nothing can
    assume, and the failure shows up at runtime rather than at apply."""
    files, _ = _files(vms, "aws")
    assert "ec2.amazonaws.com" in files["identity.tf"]
    assert "sts:AssumeRole" in files["identity.tf"]


def test_instances_carry_their_tier_identity(vms):
    files, plan = _files(vms, "aws")
    for c in plan.compute:
        assert f"aws_iam_instance_profile.{c.tier.value}.name" in files["compute.tf"]


def test_ssm_is_opt_in_and_uses_the_managed_policy(vms):
    """Offered because it removes risk — keyless, auditable access with no SSH
    port, bastion or key pair — but a capability all the same, so opt-in."""
    default, _ = _files(vms, "aws")
    assert SSM_MANAGED_POLICY not in default["identity.tf"]

    opted, _ = _files(vms, "aws", IdentityMode.SSM)
    assert SSM_MANAGED_POLICY in opted["identity.tf"]
    assert "aws_iam_role_policy_attachment" in opted["identity.tf"]


# --- per-cloud behaviour -----------------------------------------------------

@pytest.mark.parametrize("cloud,resource", [
    ("aws", "aws_iam_role"),
    ("azure", "azurerm_user_assigned_identity"),
    ("gcp", "google_service_account"),
])
def test_each_supported_cloud_uses_its_native_identity(vms, cloud, resource):
    files, _ = _files(vms, cloud)
    assert resource in files["identity.tf"]


@pytest.mark.parametrize("cloud", ["oci", "digitalocean"])
def test_unsupported_clouds_say_why_rather_than_going_quiet(vms, cloud):
    """Silence would read as 'your instances have identities'. They do not."""
    files, _ = _files(vms, cloud)
    assert "identity.tf" not in files, "nothing to emit for this target"
    assert "## Workload identity" in files["README.md"]
    readme = files["README.md"]
    assert ("dynamic groups" in readme) or ("no per-instance identity" in readme)


def test_every_target_still_produces_a_readable_plan(vms):
    for cloud in list_targets():
        files, _ = _files(vms, cloud)
        assert files["compute.tf"].strip()
        if identity_supported(cloud):
            assert "identity.tf" in files


def test_the_readme_states_that_permissions_are_the_operators_job(vms):
    files, _ = _files(vms, "aws")
    assert "no permissions attached" in files["README.md"]
    assert "Attach your policies" in files["README.md"]
