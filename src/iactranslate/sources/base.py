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

from ..config import MAX_VMS

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


class InventoryTooLarge(ValueError):
    """More rows than MAX_VMS. Raised by the readers, before anything is built."""


#: One more than the cap. If a bounded read returns this many rows, the file
#: has more than MAX_VMS and is rejected without reading the rest — so a hostile
#: upload costs O(MAX_VMS) memory whatever its size. The alternative, checking
#: after normalisation, was measured: a 24 MB CSV of 547,000 unique rows peaked
#: at 1,327 MB RSS before the cap fired, on a 1,024 MB instance. One request
#: from any signed-up user would have OOM-killed the API.
_VM_ROW_LIMIT = MAX_VMS + 1

#: Auxiliary sheets (disks, NICs) legitimately outnumber VMs. 16 per VM is
#: generous for a real estate and still bounds a hostile one.
_AUX_ROW_LIMIT = MAX_VMS * 16


def _check_rows(df: pd.DataFrame, limit: int, what: str) -> pd.DataFrame:
    if len(df) >= limit:
        raise InventoryTooLarge(
            f"{what} has more than {limit - 1:,} rows, exceeding the supported "
            f"limit of {MAX_VMS:,} workloads. Split the estate or raise "
            "IACTRANSLATE_MAX_VMS on a deployment sized for it."
        )
    return df


def read_csv_bounded(path: str, **kwargs) -> pd.DataFrame:
    """`pd.read_csv` that refuses files over MAX_VMS rows without loading them."""
    return _check_rows(pd.read_csv(path, nrows=_VM_ROW_LIMIT, **kwargs), _VM_ROW_LIMIT, "The inventory")


def read_excel_bounded(path_or_xls, *, aux: bool = False, **kwargs):
    """`pd.read_excel` bounded per sheet. `aux=True` for disk/NIC sheets, which
    may have several rows per VM and get a proportionally larger limit."""
    limit = _AUX_ROW_LIMIT if aux else _VM_ROW_LIMIT
    result = pd.read_excel(path_or_xls, nrows=limit, engine="openpyxl", **kwargs)
    if isinstance(result, dict):
        return {name: _check_rows(df, limit, f"Sheet '{name}'") for name, df in result.items()}
    return _check_rows(result, limit, "The inventory sheet")


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
