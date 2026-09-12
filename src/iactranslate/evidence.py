"""Why a decision was made, in a shape a reviewer can argue with.

The product's claim is not that it picks instance types — anyone can pick an
instance type. It is that a migration engineer can *interrogate* the choice:
see what it rested on, what was assumed, what was unknown, and what else was
considered. A recommendation nobody can challenge is a recommendation nobody
should trust.

That information already existed and was scattered: a free-text `reason` on
`ComputePlan`, a separate factor score in `confidence.py`, a `right_sized`
boolean, and `price_source`. A human reading the console could piece it
together; nothing else could. This gives it one structure.

**`basis` is the load-bearing field.** It separates a size computed from
*measured* utilization from one inferred from *allocation* — the difference
between "this workload uses 38% of eight cores" and "this workload was given
eight cores by someone, years ago, for reasons nobody recorded". Both produce
an instance type. Only one is evidence. Presenting them identically is the
single most misleading thing a migration tool can do, because over-provisioning
is exactly what the customer is paying to escape.

**No timestamps.** Decisions carry an engine version but not a time: the same
inventory must produce the same plan, and a clock in the output would break
that (ADR 0007). When a decision was made belongs to the run, not the decision.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from . import __version__


class Basis(str, Enum):
    """Where a decision's authority comes from, strongest first."""

    #: Measured from the source system — utilization, flow records, real config.
    OBSERVED = "observed"
    #: Stated by the customer or their governance (a mandated CIDR, a policy).
    DECLARED = "declared"
    #: Derived from what was *allocated* rather than what is used. Legitimate,
    #: and much weaker: allocation records a past decision, not present demand.
    ALLOCATED = "allocated"
    #: No signal at all — a documented default was applied.
    DEFAULTED = "defaulted"

    @property
    def label(self) -> str:
        """Wording for a customer-facing surface.

        Deliberately never says "optimal". An allocation-derived size cannot be
        known to be optimal, and calling it that is the claim this type exists
        to prevent.
        """
        return {
            Basis.OBSERVED: "Measured from observed utilization",
            Basis.DECLARED: "Supplied by your configuration",
            Basis.ALLOCATED: "Allocation-based estimate",
            Basis.DEFAULTED: "Default applied — no signal in the inventory",
        }[self]

    @property
    def is_measured(self) -> bool:
        return self in (Basis.OBSERVED, Basis.DECLARED)


class DecisionKind(str, Enum):
    SIZING = "sizing"
    IMAGE = "image"
    PLACEMENT = "placement"
    CLOUD = "cloud"
    WAVE = "wave"


class Evidence(BaseModel):
    """One fact the decision rested on, and where it came from."""

    field: str = Field(description="What was read, e.g. 'cpu_util_pct'")
    value: str = Field(description="What it said, rendered for a human")
    source: str = Field(description="Where it came from, e.g. 'inventory' | 'target catalog'")


class Decision(BaseModel):
    """A choice, with everything needed to challenge it.

    `alternatives` and `unknowns` are as important as `evidence`. A reviewer's
    first question is "what else did you consider?" and their second is "what
    didn't you know?" — a model that cannot answer those turns every review
    into an argument about trust rather than about the estate.
    """

    schema_version: int = 1
    subject: str = Field(description="What this is about — a vm_name, or 'estate'")
    kind: DecisionKind
    outcome: str = Field(description="What was chosen")
    basis: Basis
    evidence: List[Evidence] = Field(default_factory=list)
    assumptions: List[str] = Field(
        default_factory=list,
        description="What had to be true. Stated so it can be disagreed with.",
    )
    alternatives: List[str] = Field(
        default_factory=list, description="What else was viable, and was not chosen"
    )
    unknowns: List[str] = Field(
        default_factory=list,
        description="What the inventory did not say. Absence stated, never inferred away.",
    )
    engine_version: str = Field(default=__version__)

    @property
    def headline(self) -> str:
        """One line for a table or a card."""
        return f"{self.outcome} — {self.basis.label}"


def sizing_decision(
    vm_name: str,
    chosen: str,
    *,
    source_vcpu: int,
    source_memory_gib: float,
    cpu_util_pct: Optional[float],
    mem_util_pct: Optional[float],
    headroom: float,
    target_vcpu: Optional[int] = None,
    target_memory_gib: Optional[float] = None,
    alternatives: Optional[List[str]] = None,
) -> Decision:
    """Build the evidence for one instance-type choice.

    Utilization decides the basis, because it is the only input that reports
    what the workload actually does. Everything else describes what someone
    once provisioned.
    """
    measured = cpu_util_pct is not None or mem_util_pct is not None
    evidence = [
        Evidence(field="allocated", source="inventory",
                 value=f"{source_vcpu} vCPU / {source_memory_gib:g} GiB"),
    ]
    unknowns: List[str] = []

    if cpu_util_pct is not None:
        evidence.append(Evidence(field="cpu_util_pct", source="inventory",
                                 value=f"{cpu_util_pct:g}%"))
    else:
        unknowns.append("CPU utilization was not in the inventory")
    if mem_util_pct is not None:
        evidence.append(Evidence(field="mem_util_pct", source="inventory",
                                 value=f"{mem_util_pct:g}%"))
    else:
        unknowns.append("Memory utilization was not in the inventory")

    if target_vcpu is not None and target_memory_gib is not None:
        evidence.append(Evidence(field="instance_spec", source="target catalog",
                                 value=f"{target_vcpu} vCPU / {target_memory_gib:g} GiB"))

    assumptions = [f"{round((headroom - 1) * 100)}% headroom above demand"]
    if measured:
        assumptions.append(
            "Utilization in the inventory is representative of normal load"
        )
    else:
        assumptions.append(
            "The source allocation was appropriate — this is carried over, not verified"
        )
        unknowns.append(
            "Whether the workload was over-provisioned; without utilization this "
            "cannot be determined and any saving is unproven"
        )

    return Decision(
        subject=vm_name,
        kind=DecisionKind.SIZING,
        outcome=chosen,
        basis=Basis.OBSERVED if measured else Basis.ALLOCATED,
        evidence=evidence,
        assumptions=assumptions,
        alternatives=alternatives or [],
        unknowns=unknowns,
    )
