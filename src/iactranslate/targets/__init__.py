"""Cloud-target registry."""
from __future__ import annotations

from typing import Dict, List

from .aws import AwsTarget
from .base import InstanceSpec, Target, smallest_fit  # noqa: F401

from .azure import AzureTarget
from .gcp import GcpTarget

_REGISTRY: Dict[str, Target] = {
    AwsTarget.name: AwsTarget(),
    AzureTarget.name: AzureTarget(),
    GcpTarget.name: GcpTarget(),
}


class UnknownTargetError(ValueError):
    pass


def get_target(name: str) -> Target:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownTargetError(
            f"unknown target '{name}' (available: {', '.join(sorted(_REGISTRY))})"
        )


def list_targets() -> List[str]:
    return sorted(_REGISTRY)
