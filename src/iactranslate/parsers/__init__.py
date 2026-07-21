"""Source-format parsers.

Each parser reads a discovery export and returns a list of *raw VM records*
(plain dicts with best-effort canonical keys). Normalization/units/dedup is the
job of `normalize.py`, not the parser.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import rvtools, vmware_csv

RawVM = Dict[str, object]


class UnsupportedFormatError(ValueError):
    pass


def detect_format(path: str) -> str:
    """Return 'rvtools' for .xlsx/.xls, 'vmware_csv' for .csv."""
    suffix = Path(path).suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return "rvtools"
    if suffix == ".csv":
        return "vmware_csv"
    raise UnsupportedFormatError(
        f"Unsupported input '{path}': expected an RVTools .xlsx or a VMware .csv"
    )


def parse(path: str) -> List[RawVM]:
    """Detect the format and parse `path` into raw VM records."""
    fmt = detect_format(path)
    if fmt == "rvtools":
        return rvtools.parse(path)
    return vmware_csv.parse(path)
