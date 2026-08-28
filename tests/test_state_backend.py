"""Remote state backends in the generated Terraform.

The defect these cover: generated projects declared no backend at all, so
Terraform silently defaulted to local state — unshareable, unlockable, and lost
with the laptop. Silence was the bug, so most of these assert that the output
*says something* about state in every mode.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.state import (
    BACKEND_BY_CLOUD,
    UnknownBackendError,
    resolve_backend,
)
from iactranslate.targets import get_target, list_targets

FULL_S3 = {"bucket": "acme-tfstate", "key": "prod/migration.tfstate", "region": "us-east-1"}


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_sample.xlsx"))


def _files(vms, cloud, backend=None):
    target = get_target(cloud)
    plan = build_migration_plan(vms, "state-test", target)
    return build_files(plan, target, backend)


# --- resolution --------------------------------------------------------------

def test_omitting_a_backend_means_local_not_a_guess():
    """Inventing a remote backend nobody asked for would be worse than local."""
    assert resolve_backend("aws").is_local


def test_auto_picks_each_clouds_native_backend():
    for cloud, expected in BACKEND_BY_CLOUD.items():
        assert resolve_backend(cloud, "auto").kind == expected


def test_unknown_backend_is_rejected_loudly():
    with pytest.raises(UnknownBackendError):
        resolve_backend("aws", "cassandra")


def test_partial_config_is_reported_as_incomplete():
    b = resolve_backend("aws", "auto", {"bucket": "only-a-bucket"})
    assert not b.is_complete
    assert b.missing_keys == ["key", "region"]


# --- rendering ---------------------------------------------------------------

def test_local_state_is_stated_not_silent(vms):
    """The whole defect: local state used to be the undocumented default."""
    files = _files(vms, "aws")
    assert "STATE IS LOCAL" in files["versions.tf"]
    assert "## Terraform state" in files["README.md"]
    # No backend stanza — local really is local; the banner is the only mention.
    assert 'backend "' not in files["versions.tf"]


def test_full_config_renders_a_complete_block(vms):
    tf = _files(vms, "aws", resolve_backend("aws", "auto", FULL_S3))["versions.tf"]
    assert 'backend "s3" {' in tf
    assert '"acme-tfstate"' in tf
    assert '"prod/migration.tfstate"' in tf
    assert "STATE IS LOCAL" not in tf


def test_partial_config_fails_init_rather_than_falling_back(vms):
    """`terraform init` must fail until configured — silence is how this broke."""
    files = _files(vms, "aws", resolve_backend("aws", "auto"))
    assert 'backend "s3" {}' in files["versions.tf"]
    assert "backend.hcl" in files["versions.tf"]
    assert "backend.hcl.example" in files
    example = files["backend.hcl.example"]
    for key in ("bucket", "key", "region"):
        assert key in example


def test_no_example_file_when_the_backend_is_complete(vms):
    files = _files(vms, "aws", resolve_backend("aws", "auto", FULL_S3))
    assert "backend.hcl.example" not in files


def test_no_example_file_for_local_state(vms):
    assert "backend.hcl.example" not in _files(vms, "aws")


def test_every_target_renders_a_backend(vms):
    """A cloud whose templates forgot the hook would silently keep local state."""
    for cloud in list_targets():
        backend = resolve_backend(cloud, "auto")
        tf = _files(vms, cloud, backend)["versions.tf"]
        assert f'backend "{backend.kind}"' in tf, f"{cloud} lost its backend block"


def test_s3_compatible_clouds_get_the_flags_that_make_init_work(vms):
    """OCI Object Storage and DO Spaces need the AWS preflight checks disabled.

    Without these `terraform init` fails against a non-AWS endpoint with an
    AWS-specific error the operator has no reason to expect.
    """
    for cloud in ("oci", "digitalocean"):
        backend = resolve_backend(cloud, "auto", FULL_S3)
        tf = _files(vms, cloud, backend)["versions.tf"]
        assert "skip_credentials_validation = true" in tf
        assert "use_path_style" in tf


def test_aws_does_not_get_the_compatibility_flags(vms):
    """They are for non-AWS endpoints; on real S3 they are noise."""
    tf = _files(vms, "aws", resolve_backend("aws", "auto", FULL_S3))["versions.tf"]
    assert "skip_credentials_validation" not in tf


def test_readme_explains_each_mode(vms):
    local = _files(vms, "aws")["README.md"]
    assert "State is local" in local

    partial = _files(vms, "aws", resolve_backend("aws", "auto"))["README.md"]
    assert "backend.hcl" in partial

    full = _files(vms, "aws", resolve_backend("aws", "auto", FULL_S3))["README.md"]
    assert "versioning enabled" in full, "a bad apply against unversioned state is unrecoverable"


def test_backend_is_terraform_only(vms):
    """Pulumi keeps its own state; CFN/Bicep/CDK have no client-side state.

    Emitting a Terraform backend into them would imply a control they lack.
    """
    from iactranslate.renderers import render

    target = get_target("aws")
    plan = build_migration_plan(vms, "state-test", target)
    files = render("cloudformation", plan, target, resolve_backend("aws", "auto", FULL_S3))
    assert "backend.hcl.example" not in files
