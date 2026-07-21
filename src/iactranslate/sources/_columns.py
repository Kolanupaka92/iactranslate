"""Helpers for tolerant column matching across export variants."""
from __future__ import annotations

from typing import List, Optional

import pandas as pd


def _canon(name: object) -> str:
    return str(name).strip().lower().replace("_", " ")


def find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Return the real column name matching any candidate (case/space-insensitive).

    Tries exact canonical match first, then 'startswith' (RVTools appends units,
    e.g. 'Memory' -> 'Memory' but disks like 'Capacity MiB').
    """
    canon_map = {_canon(c): c for c in df.columns}
    for cand in candidates:
        key = _canon(cand)
        if key in canon_map:
            return canon_map[key]
    for cand in candidates:
        key = _canon(cand)
        for canon_col, real in canon_map.items():
            if canon_col.startswith(key):
                return real
    return None


def cell(row: pd.Series, col: Optional[str]) -> Optional[object]:
    """Safely read a cell; return None for missing columns or NaN values."""
    if col is None or col not in row:
        return None
    value = row[col]
    if pd.isna(value):
        return None
    return value
