"""Capability claims, checked against what the project actually does.

Both statements here are the kind that drift: someone adds a cloud, or removes
a CI step, and a sentence somewhere keeps claiming the old thing. These read the
real sources — the CI workflow, the target registry — so drift fails a test
rather than surviving into a sales conversation.
"""
import pathlib
import re

import pytest

from iactranslate.maturity import (
    BOUNDARY_STATEMENT,
    HIGHEST_REACHED,
    ProviderMaturity,
    ValidationLevel,
    maturity_of,
    maturity_table,
)
from iactranslate.targets import list_targets

WORKFLOW = pathlib.Path(".github/workflows/ci.yml")


# --- the validation ladder ----------------------------------------------------

def test_the_product_does_not_claim_deployment_or_application_validation():
    """The expensive misunderstanding this exists to prevent: hearing
    "validated" and understanding "it will work in production"."""
    assert HIGHEST_REACHED == ValidationLevel.POLICY
    assert HIGHEST_REACHED < ValidationLevel.DEPLOYMENT
    assert HIGHEST_REACHED < ValidationLevel.APPLICATION


def test_the_boundary_is_stated_in_words_not_only_in_a_number():
    """A level number means nothing to a CTO. The sentence has to say what is
    and is not proven."""
    text = BOUNDARY_STATEMENT.lower()
    assert "does not apply infrastructure" in text
    assert "not a replacement" in text


def test_every_level_explains_itself():
    for level in ValidationLevel:
        assert level.title and level.means
        assert len(level.means) > 40, f"{level.name} needs a real explanation"


def test_levels_are_ordered_so_comparisons_mean_something():
    assert ValidationLevel.SYNTAX < ValidationLevel.POLICY < ValidationLevel.APPLICATION


# --- provider maturity, against CI --------------------------------------------

@pytest.mark.skipif(not WORKFLOW.exists(), reason="CI workflow not present")
def test_provider_validated_matches_what_ci_actually_validates():
    """The claim that AWS/Azure/GCP are provider-validated is only true while CI
    validates them. If that job changes, this must fail rather than let a stale
    maturity claim reach a customer.
    """
    workflow = WORKFLOW.read_text()
    job = workflow[workflow.index("terraform-validate:"):]
    job = job[: job.index("\n  web:")] if "\n  web:" in job else job

    claimed = {n for n in list_targets()
               if maturity_of(n) is ProviderMaturity.PROVIDER_VALIDATED}
    # The job names the clouds it validates in its step name, e.g.
    # "Validate generated Terraform (aws/azure/gcp)".
    named = set(re.findall(r"aws|azure|gcp|oci|digitalocean", job.lower()))
    assert claimed <= named, (
        f"claimed provider-validated {sorted(claimed)} but the CI job only "
        f"mentions {sorted(named)}"
    )


def test_every_target_has_a_maturity():
    table = maturity_table()
    assert set(table) == set(list_targets())
    assert all(table.values())


def test_the_two_tiers_are_distinguishable_to_a_reader():
    """"Supported" flattens a real difference; the labels must not."""
    a = ProviderMaturity.PROVIDER_VALIDATED
    b = ProviderMaturity.RENDERED_AND_TESTED
    assert a.label != b.label
    assert a > b
    assert "not checked against the provider" in b.means


def test_oci_and_digitalocean_are_not_overclaimed():
    """They render and pass tests; no provider binary ever sees the output."""
    for name in ("oci", "digitalocean"):
        assert maturity_of(name) is ProviderMaturity.RENDERED_AND_TESTED
