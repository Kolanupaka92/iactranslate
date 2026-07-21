"""Agent provider interface.

An LLM provider makes *structured decisions* only (grouping, instance choice).
It never emits Terraform. Two decision points are pluggable:

  - classify(vms)            -> List[AppGroup]
  - rightsize(vm, tier, env) -> RightsizeSuggestion

Network CIDR planning is deterministic (see agents/network.py) and is never
delegated to an LLM. Every provider decision is re-checked by the validation
layer, so a bad LLM answer degrades gracefully rather than corrupting output.
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from pydantic import BaseModel

from ..models import AppGroup, Environment, NormalizedVM, Tier


class RightsizeSuggestion(BaseModel):
    """A provider's compute recommendation for a single VM."""

    instance_type: str
    image_key: str


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def classify(self, vms: List[NormalizedVM]) -> List[AppGroup]:
        ...

    def rightsize(
        self, vm: NormalizedVM, tier: Tier, environment: Environment
    ) -> RightsizeSuggestion:
        ...
