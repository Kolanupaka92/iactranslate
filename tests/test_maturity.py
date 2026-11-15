"""Capability claims, checked against what the project actually does.

Both statements here are the kind that drift: someone adds a cloud, or removes
a CI step, and a sentence somewhere keeps claiming the old thing. These read the
real sources — the CI workflow, the target registry — so drift fails a test
rather than surviving into a sales conversation.
"""
from iactranslate.maturity import (
    BOUNDARY_STATEMENT,
    HIGHEST_REACHED,
    ProviderMaturity,
    ValidationLevel,
    maturity_of,
    maturity_table,
)
from iactranslate.targets import list_targets

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

def test_provider_validated_matches_what_ci_actually_validates():
    """The maturity claim must equal the set the validate test really runs.

    Read from the test's own parameter list, not from the workflow. The first
    version of this read the CI step's *name*, which said "(aws/azure/gcp)"
    while the test underneath validated all five — so the maturity table
    under-claimed OCI and DigitalOcean for a week, and this test passed the
    whole time because it was checking the label rather than the thing.
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "e2e", pathlib.Path(__file__).with_name("test_e2e.py")
    )
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    PROVIDER_VALIDATED_TARGETS = e2e.PROVIDER_VALIDATED_TARGETS

    claimed = {n for n in list_targets()
               if maturity_of(n) is ProviderMaturity.PROVIDER_VALIDATED}
    assert claimed == set(PROVIDER_VALIDATED_TARGETS), (
        f"maturity claims {sorted(claimed)}; the validate test runs "
        f"{sorted(PROVIDER_VALIDATED_TARGETS)}"
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


def test_the_on_premises_targets_are_validated_like_the_clouds():
    """Nutanix and Proxmox meet their real providers in CI — the same bar as
    AWS, not a lower one because they are not hyperscalers."""
    for name in ("nutanix", "proxmox"):
        assert maturity_of(name) is ProviderMaturity.PROVIDER_VALIDATED
