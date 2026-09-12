"""What "validated" actually means here, and for which clouds.

Two claims are easy to make carelessly and expensive to make wrongly.

**"The generated infrastructure is validated."** Validation is a ladder, and the
rungs are not close together. Passing a provider's schema check says the
configuration is well-formed; it says nothing about whether the application will
run. A customer who hears "validated" and understands "it will work" has been
misled by a true statement, and will discover it during a cutover.

**"We support five clouds."** Support is not uniform. Generated Terraform for
AWS, Azure and GCP is checked against the real providers on every push; OCI and
DigitalOcean are rendered and unit-tested but never see a provider binary. Both
are genuinely supported. They are not equally proven, and flattening that into
one word is the kind of thing an enterprise reviewer catches and remembers.

Neither of these is a marketing decision. Both are properties of what CI does,
so both are derived from it and tested against it.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Dict, List


class ValidationLevel(IntEnum):
    """How far a generated configuration has actually been proven.

    Ordered, so `reached >= ValidationLevel.POLICY` is a meaningful question.
    """

    SYNTAX = 1
    SEMANTIC = 2
    POLICY = 3
    DEPLOYMENT = 4
    APPLICATION = 5

    @property
    def title(self) -> str:
        return {
            ValidationLevel.SYNTAX: "Syntax and provider schema",
            ValidationLevel.SEMANTIC: "Semantic consistency",
            ValidationLevel.POLICY: "Policy conformance",
            ValidationLevel.DEPLOYMENT: "Deployment against a real cloud",
            ValidationLevel.APPLICATION: "Application correctness",
        }[self]

    @property
    def means(self) -> str:
        return {
            ValidationLevel.SYNTAX:
                "The configuration parses and matches the provider's schema. "
                "Every resource and argument exists and is well-formed.",
            ValidationLevel.SEMANTIC:
                "Resources refer to each other coherently — subnets sit inside "
                "their VPC, security groups exist, instance types are in the "
                "target's catalog, dependencies resolve.",
            ValidationLevel.POLICY:
                "The plan satisfies the organisation's rules: placement, "
                "encryption keys, naming, instance families, budget.",
            ValidationLevel.DEPLOYMENT:
                "The configuration was applied to a real cloud account and the "
                "resources came up.",
            ValidationLevel.APPLICATION:
                "The migrated workload actually runs correctly on the new "
                "infrastructure.",
        }[self]


#: The highest level this product reaches on its own, for any target.
#:
#: Deliberately not 4. Nothing here applies infrastructure to a cloud account,
#: so deployment is unproven by construction — and level 5 is not a software
#: problem at all: whether an application behaves after a move depends on the
#: application, which an inventory cannot describe.
HIGHEST_REACHED = ValidationLevel.POLICY

#: Stated plainly, because this is the sentence that prevents the expensive
#: misunderstanding.
BOUNDARY_STATEMENT = (
    "IaCTranslate validates up to policy conformance (level 3). It does not "
    "apply infrastructure to a cloud account, so deployment is unproven, and it "
    "cannot determine whether a migrated application behaves correctly. Passing "
    "validation here means the plan is sound and reviewable — it is the input to "
    "your normal engineering validation, not a replacement for it."
)


class ProviderMaturity(IntEnum):
    """How thoroughly a target's output is actually exercised."""

    PROVIDER_VALIDATED = 2
    RENDERED_AND_TESTED = 1

    @property
    def label(self) -> str:
        return {
            ProviderMaturity.PROVIDER_VALIDATED: "Provider-validated",
            ProviderMaturity.RENDERED_AND_TESTED: "Rendered and unit-tested",
        }[self]

    @property
    def means(self) -> str:
        return {
            ProviderMaturity.PROVIDER_VALIDATED:
                "Generated configuration is checked against the real provider "
                "on every push to main.",
            ProviderMaturity.RENDERED_AND_TESTED:
                "Generated configuration is exercised by the test suite but is "
                "not checked against the provider binary in CI. Expect it to be "
                "correct; do not treat it as proven to the same depth.",
        }[self]


#: Targets whose output CI checks against the real provider. This is not a
#: judgement about the clouds — it records which providers the
#: `terraform-validate` job actually initialises, and `test_maturity.py` reads
#: the workflow to confirm the two agree.
_PROVIDER_VALIDATED = frozenset({"aws", "azure", "gcp"})


def maturity_of(target_name: str) -> ProviderMaturity:
    return (
        ProviderMaturity.PROVIDER_VALIDATED
        if target_name in _PROVIDER_VALIDATED
        else ProviderMaturity.RENDERED_AND_TESTED
    )


def maturity_table() -> Dict[str, str]:
    """Every target and its maturity label, for a UI or a report."""
    from .targets import list_targets

    return {name: maturity_of(name).label for name in list_targets()}


def levels() -> List[ValidationLevel]:
    return list(ValidationLevel)
