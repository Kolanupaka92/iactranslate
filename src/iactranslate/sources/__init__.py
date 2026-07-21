"""Discovery-source registry — pick a parser by name or auto-detect from the file."""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import RawRecord, Source  # noqa: F401
from .cloud import CloudSource
from .generic import GenericSource
from .hyperv import HypervSource
from .vmware import VmwareSource

# Order matters only for stable tie-breaks; detection uses confidence scores.
_REGISTRY: Dict[str, Source] = {
    VmwareSource.name: VmwareSource(),
    HypervSource.name: HypervSource(),
    CloudSource.name: CloudSource(),
    GenericSource.name: GenericSource(),  # always-eligible floor
}


class UnknownSourceError(ValueError):
    pass


def get_source(name: str) -> Source:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownSourceError(
            f"unknown source '{name}' (available: {', '.join(list_sources())})"
        ) from None


def list_sources() -> List[str]:
    return list(_REGISTRY)


def detect_source(path: str) -> Source:
    """Return the highest-confidence source for `path` (generic is the floor)."""
    scored = sorted(
        _REGISTRY.values(), key=lambda s: s.detect(path), reverse=True
    )
    best = scored[0]
    if best.detect(path) <= 0.0:
        # Nothing recognized it — fall back to the generic reader.
        return _REGISTRY[GenericSource.name]
    return best


def resolve_source(path: str, name: Optional[str] = None) -> Source:
    """Resolve an explicit source name, or auto-detect when name is None/'auto'."""
    if not name or name == "auto":
        return detect_source(path)
    return get_source(name)


def parse(
    path: str,
    source: Optional[str] = None,
    column_map: Optional[Dict[str, str]] = None,
) -> List[RawRecord]:
    return resolve_source(path, source).parse(path, column_map=column_map)
