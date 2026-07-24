"""Discovery-source abstraction — the input-side twin of `targets/`.

A `Source` reads *any* company's inventory export (RVTools, Hyper-V, a CMDB
dump, a cloud instance list, a hand-rolled spreadsheet) and emits the raw-record
contract that `normalize.py` already understands. Sources are cloud-agnostic;
they only describe *where the estate came from*, never where it's going.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable

import pandas as pd

RawRecord = Dict[str, object]

XLSX_SUFFIXES = {".xlsx", ".xls", ".xlsm"}


@runtime_checkable
class Source(Protocol):
    name: str
    label: str

    def detect(self, path: str) -> float:
        """Confidence in [0, 1] that this source can parse `path`."""
        ...

    def parse(self, path: str, column_map: Optional[Dict[str, str]] = None) -> List[RawRecord]:
        """Read `path` into raw VM/host records (canonical raw-record contract)."""
        ...


# --------------------------------------------------------------------------- #
# Shared detection helpers (read just enough to score confidence)
# --------------------------------------------------------------------------- #


def suffix(path: str) -> str:
    return Path(path).suffix.lower()


def is_xlsx(path: str) -> bool:
    return suffix(path) in XLSX_SUFFIXES


def is_csv(path: str) -> bool:
    return suffix(path) == ".csv"


def is_json(path: str) -> bool:
    return suffix(path) == ".json"


def load_json(path: str) -> object:
    """Parse a JSON file (returns None on any failure — detection must never raise)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def sheet_names(path: str) -> List[str]:
    """Lower-cased sheet names of an .xlsx (empty list on any failure)."""
    try:
        xls = pd.ExcelFile(path, engine="openpyxl")
        return [str(s).strip().lower() for s in xls.sheet_names]
    except Exception:  # noqa: BLE001 — detection must never raise
        return []


def headers(path: str) -> List[str]:
    """Lower-cased column headers of a CSV or the first sheet of an .xlsx."""
    try:
        if is_csv(path):
            df = pd.read_csv(path, nrows=0)
        elif is_xlsx(path):
            df = pd.read_excel(path, nrows=0, engine="openpyxl")
        else:
            return []
        return [str(c).strip().lower() for c in df.columns]
    except Exception:  # noqa: BLE001
        return []


def any_header_contains(hdrs: List[str], needles: List[str]) -> bool:
    return any(any(n in h for n in needles) for h in hdrs)
