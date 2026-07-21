"""Back-compat shim. The parsing layer now lives under `iactranslate.sources`;
this module preserves the original `parse` / `detect_format` API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..sources import parse as _source_parse

RawVM = Dict[str, object]


class UnsupportedFormatError(ValueError):
    pass


def detect_format(path: str) -> str:
    """Legacy format label ('rvtools' for .xlsx, 'vmware_csv' for .csv)."""
    suffix = Path(path).suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return "rvtools"
    if suffix == ".csv":
        return "vmware_csv"
    raise UnsupportedFormatError(
        f"Unsupported input '{path}': expected an .xlsx or .csv inventory export"
    )


def parse(path: str) -> List[RawVM]:
    """Auto-detect the source and parse into raw VM/host records."""
    return _source_parse(path)
