"""Raw VM records -> validated `NormalizedVM` list.

Handles unit coercion (MiB -> GiB), IP parsing, and de-duplication by VM name.
Accepts the raw dicts produced by either parser (RVTools or flat VMware CSV).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .models import NormalizedVM

MIB_PER_GIB = 1024.0


def _to_int(value: object, default: int = 1) -> int:
    if value is None:
        return default
    try:
        return max(1, int(round(float(value))))
    except (TypeError, ValueError):
        return default


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _util_pct(value: object) -> Optional[float]:
    """Parse a utilization percent (accepts '22', '22%', 0.22 as fraction)."""
    if value is None:
        return None
    text = str(value).strip().rstrip("%").strip()
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    if 0 < v <= 1:  # a fraction like 0.22 -> 22%
        v *= 100
    if 0 <= v <= 100:
        return round(v, 2)
    return None


def _mib_to_gib(mib: Optional[float]) -> Optional[float]:
    if mib is None:
        return None
    return round(mib / MIB_PER_GIB, 2)


def _memory_gib(rec: Dict[str, object]) -> float:
    # RVTools path: memory reported in MiB.
    if "memory_mib" in rec:
        gib = _mib_to_gib(_to_float(rec.get("memory_mib")))
        if gib:
            return gib
    # CSV path: value + unit.
    val = _to_float(rec.get("memory_value"))
    if val is not None:
        unit = str(rec.get("memory_unit", "gib")).lower()
        return round(val / MIB_PER_GIB, 2) if unit == "mib" else round(val, 2)
    return 1.0  # sane floor; a VM always has some memory


def _disks_gib(rec: Dict[str, object]) -> List[float]:
    # RVTools path: list of MiB values.
    if "disks_mib" in rec:
        out = []
        for d in rec.get("disks_mib", []) or []:
            gib = _mib_to_gib(_to_float(d))
            if gib and gib > 0:
                out.append(gib)
        if out:
            return out
    # CSV path: single value + unit.
    val = _to_float(rec.get("disk_value"))
    if val is not None and val > 0:
        unit = str(rec.get("disk_unit", "gib")).lower()
        gib = round(val / MIB_PER_GIB, 2) if unit == "mib" else round(val, 2)
        return [gib]
    return []


def _parse_ips(value: object) -> List[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[,\s;]+", text)
    ips = []
    for p in parts:
        p = p.strip()
        # keep only IPv4-looking tokens
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", p):
            ips.append(p)
    return ips


def _clean_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize(records: List[Dict[str, object]]) -> List[NormalizedVM]:
    seen: Dict[str, NormalizedVM] = {}
    for rec in records:
        name = _clean_str(rec.get("name"))
        if not name:
            continue

        vm = NormalizedVM(
            vm_name=name,
            cpu=_to_int(rec.get("cpus")),
            memory_gib=_memory_gib(rec),
            disks_gib=_disks_gib(rec),
            cpu_util_pct=_util_pct(rec.get("cpu_util_pct")),
            mem_util_pct=_util_pct(rec.get("mem_util_pct")),
            network=_clean_str(rec.get("network")),
            os=_clean_str(rec.get("os")),
            power_state=_clean_str(rec.get("powerstate")),
            ip_addresses=_parse_ips(rec.get("ip")),
            hostname=_clean_str(rec.get("dns_name")),
            cluster=_clean_str(rec.get("cluster")),
            datacenter=_clean_str(rec.get("datacenter")),
            external_id=_clean_str(rec.get("external_id")),
        )
        # De-dupe by name; prefer the record with more disk detail.
        existing = seen.get(vm.vm_name)
        if existing is None or vm.total_disk_gib > existing.total_disk_gib:
            seen[vm.vm_name] = vm

    return list(seen.values())
