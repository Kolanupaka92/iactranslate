"""Volume encryption in the infrastructure we generate.

Distinct from `test_crypto.py`, which covers the inventory *we* store. This is
about the estate the customer ends up running, and it was the sharpest gap in
the product's security story: root and data volumes were emitted with no
encryption on any cloud.

The clouds are not equivalent here, and the tests encode the difference rather
than asserting the same string everywhere. AWS does not encrypt EBS by default
unless the account opts in per region, so the flag has to be set and its absence
is a real CIS finding. Azure, GCP, OCI and DigitalOcean encrypt at rest
unconditionally and cannot be told not to, so emitting an "encrypt = true" there
would be noise that implies a control the provider does not expose.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.generator import build_files
from iactranslate.landing_zone import LandingZone, LandingZoneError
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.policy import evaluate
from iactranslate.targets import get_target

KEY = "arn:aws:kms:us-east-1:111122223333:key/1234abcd"


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_sample.xlsx"))


def _files(vms, cloud, zone=None):
    target = get_target(cloud)
    plan = build_migration_plan(vms, "enc", target, zone=zone)
    return build_files(plan, target, None, zone), plan


# --- AWS: the actual hole ----------------------------------------------------

def test_aws_root_volumes_are_encrypted(vms):
    """EBS is not encrypted by default. An unencrypted root volume is the
    finding that ends a security review (CIS AWS Foundations 2.2.1)."""
    files, _ = _files(vms, "aws")
    assert "encrypted   = true" in files["compute.tf"]


def test_aws_data_volumes_are_encrypted(vms):
    """The extra disks are where the data actually lives."""
    windows_heavy = normalize(parse("tests/fixtures/rvtools_sample.xlsx"))
    files, plan = _files(windows_heavy, "aws")
    if "storage.tf" in files:
        assert "encrypted         = true" in files["storage.tf"]


def test_encryption_is_not_optional_on_aws(vms):
    """No configuration turns it off. A knob here would only ever be used to
    create the exposure this exists to close."""
    files, _ = _files(vms, "aws", LandingZone())
    assert "encrypted   = true" in files["compute.tf"]


# --- whose key ---------------------------------------------------------------

def test_no_customer_key_means_no_kms_argument(vms):
    """Omitting the key uses the provider's managed key — still encrypted.
    Emitting an empty `kms_key_id` would fail to apply."""
    files, _ = _files(vms, "aws")
    assert "kms_key_id" not in files["compute.tf"]


def test_a_customer_key_reaches_aws_volumes(vms):
    files, _ = _files(vms, "aws", LandingZone(kms_key_id=KEY))
    assert KEY in files["compute.tf"]


def test_a_customer_key_reaches_azure_as_a_disk_encryption_set(vms):
    """Azure expresses a customer key as a disk encryption set, not a key id."""
    files, _ = _files(vms, "azure", LandingZone(kms_key_id="/subscriptions/x/des/y"))
    assert "disk_encryption_set_id" in files["compute.tf"]


def test_a_customer_key_reaches_gcp_as_a_kms_self_link(vms):
    files, _ = _files(vms, "gcp", LandingZone(kms_key_id="projects/p/locations/l/keyRings/r/cryptoKeys/k"))
    assert "kms_key_self_link" in files["compute.tf"]


def test_the_key_is_recorded_on_the_plan(vms):
    """Auditors ask whose key protected the data, and the generated code alone
    does not make that obvious once it is applied."""
    _, plan = _files(vms, "aws", LandingZone(kms_key_id=KEY))
    assert plan.kms_key_id == KEY


def test_a_blank_key_is_rejected_rather_than_silently_ignored(vms):
    """An empty string would render `kms_key_id = ""` and fail on apply, long
    after the review passed."""
    with pytest.raises(LandingZoneError):
        LandingZone(kms_key_id="   ")


# --- every cloud still renders ------------------------------------------------

@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci", "digitalocean"])
def test_every_target_still_renders_with_and_without_a_key(vms, cloud):
    plain, _ = _files(vms, cloud)
    assert plain["compute.tf"].strip()
    keyed, _ = _files(vms, cloud, LandingZone(kms_key_id=KEY))
    assert keyed["compute.tf"].strip()


@pytest.mark.parametrize("cloud", ["oci", "digitalocean"])
def test_clouds_that_always_encrypt_emit_no_encryption_config(vms, cloud):
    """These encrypt at rest unconditionally and expose no toggle. Emitting one
    would imply a control the provider does not have."""
    files, _ = _files(vms, cloud)
    assert "encrypted   = true" not in files["compute.tf"]


# --- the policy ---------------------------------------------------------------

def test_the_policy_fails_without_a_customer_key(vms):
    target = get_target("aws")
    plan = build_migration_plan(vms, "enc", target)
    result = evaluate(plan, target, {"require_customer_managed_key": {}})
    assert not result.ok
    assert "provider-managed key" in result.denials[0].message


def test_the_policy_passes_with_one(vms):
    target = get_target("aws")
    zone = LandingZone(kms_key_id=KEY)
    plan = build_migration_plan(vms, "enc", target, zone=zone)
    assert evaluate(plan, target, {"require_customer_managed_key": {}}).ok


def test_the_policy_does_not_assert_encryption_itself(vms):
    """A policy that can never fail is theatre. Encryption at rest is
    unconditional, so the rule checks the thing that actually varies."""
    from iactranslate.policy import list_policies

    assert "require_customer_managed_key" in list_policies()
    assert "require_encryption" not in list_policies()
