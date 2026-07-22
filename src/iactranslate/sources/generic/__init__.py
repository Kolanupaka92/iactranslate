"""Generic "bring-your-own-inventory" source — the universal ingest path.

Reads *any* company's CSV/XLSX (ServiceNow/Device42/Lansweeper CMDB export,
physical-server list, hand-rolled spreadsheet). With no configuration it
auto-detects the canonical fields from a broad synonym table; with an explicit
`column_map` it maps arbitrary headers to canonical fields. This is what lets
IaCTranslate cover every company's estate, not just VMware shops.

Canonical fields a column_map may set:
    name, cpu, memory_gib | memory_mib, disk_gib | disk_mib, os, network, ip, cluster
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .._columns import cell, find_column
from ..base import RawRecord, headers, is_csv, is_xlsx

# Synonyms per canonical field, ordered most- to least-specific.
_SYNONYMS: Dict[str, List[str]] = {
    "name": ["hostname", "host name", "vm name", "server name", "ci name", "device name",
             "name", "host", "vm", "server", "device", "asset", "system"],
    "cpu": ["vcpu", "cpu cores", "core count", "processorcount", "num cpu", "cpus",
            "cores", "cpu", "processors", "sockets"],
    "memory_gib": ["memory gib", "memory gb", "ram gb", "ram (gb)", "memory (gb)",
                   "memorygb", "ram", "memory", "mem"],
    "memory_mib": ["memory mib", "memory mb", "ram mib", "ram mb"],
    # Utilization (matched after allocation so a "Memory %" column can't be
    # mistaken for allocated memory). Kept specific: % / util / usage / avg.
    "cpu_util_pct": ["cpu utilization", "cpu util", "avg cpu", "average cpu",
                     "cpu usage", "cpu percent", "cpu %", "%cpu", "peak cpu"],
    "mem_util_pct": ["memory utilization", "mem util", "avg memory", "average memory",
                     "memory usage", "memory percent", "memory %", "mem %", "ram %",
                     "peak memory"],
    "mem_used_gib": ["memory used", "used memory", "mem used", "used ram", "ram used"],
    "disk_gib": ["disk gib", "disk gb", "storage gb", "capacity gb", "provisioned gb",
                 "total storage", "storage", "capacity", "disk", "hdd"],
    "disk_mib": ["disk mib", "disk mb", "storage mib"],
    "os": ["operating system", "os name", "guest os", "os family", "platform", "os"],
    "network": ["network", "vlan", "port group", "subnet"],
    "ip": ["ip address", "ipv4", "primary ip", "ip"],
    "cluster": ["cluster", "datacenter", "location", "site"],
}


class GenericSource:
    name = "generic"
    label = "Generic CMDB / spreadsheet (bring your own inventory)"
    source_platform = "cmdb"

    def detect(self, path: str) -> float:
        if not (is_csv(path) or is_xlsx(path)):
            return 0.0
        hdrs = headers(path)
        if not hdrs:
            return 0.0
        # Floor source: eligible whenever we can find a name-ish column plus a
        # cpu- or memory-ish column. Never outbids a specific source.
        found = self._auto_map(hdrs)
        if "name" in found and ("cpu" in found or "memory_gib" in found or "memory_mib" in found):
            return 0.35
        if "name" in found:
            return 0.2
        return 0.1

    def _auto_map(self, hdrs: List[str]) -> Dict[str, str]:
        """Best-effort canonical -> actual-header map from headers alone.

        A header is claimed by at most one field (first field wins), and fields
        are tried in _SYNONYMS order — allocation before utilization — so a
        "Memory %" column can't be swallowed by allocated `memory_gib`.
        """
        mapping: Dict[str, str] = {}
        claimed: set = set()
        for canon, syns in _SYNONYMS.items():
            for syn in syns:
                match = next((h for h in hdrs if h == syn and h not in claimed), None) or \
                        next((h for h in hdrs if syn in h and h not in claimed), None)
                if match:
                    mapping[canon] = match
                    claimed.add(match)
                    break
        return mapping

    def parse(self, path: str, column_map: Optional[Dict[str, str]] = None) -> List[RawRecord]:
        df = pd.read_csv(path) if is_csv(path) else pd.read_excel(path, engine="openpyxl")
        if df.empty:
            return []

        # Resolve each canonical field to a real column: explicit map wins, else auto.
        lower_to_real = {str(c).strip().lower(): c for c in df.columns}
        resolved: Dict[str, object] = {}
        auto = self._auto_map(list(lower_to_real.keys()))
        for canon in _SYNONYMS:
            if column_map and canon in column_map:
                col = find_column(df, [column_map[canon]])
            elif canon in auto:
                col = lower_to_real.get(auto[canon])
            else:
                col = None
            resolved[canon] = col

        records: List[RawRecord] = []
        for _, row in df.iterrows():
            name = cell(row, resolved["name"])
            if name is None or not str(name).strip():
                continue
            rec: RawRecord = {
                "name": str(name).strip(),
                "cpus": cell(row, resolved["cpu"]),
                "os": cell(row, resolved["os"]),
                "network": cell(row, resolved["network"]),
                "ip": cell(row, resolved["ip"]),
                "cluster": cell(row, resolved["cluster"]),
            }
            mib = cell(row, resolved["memory_mib"])
            if mib is not None:
                rec["memory_mib"] = mib
            else:
                gib = cell(row, resolved["memory_gib"])
                if gib is not None:
                    rec["memory_value"] = gib
                    rec["memory_unit"] = "gib"
            dmib = cell(row, resolved["disk_mib"])
            if dmib is not None:
                rec["disk_value"] = dmib
                rec["disk_unit"] = "mib"
            else:
                dgib = cell(row, resolved["disk_gib"])
                if dgib is not None:
                    rec["disk_value"] = dgib
                    rec["disk_unit"] = "gib"

            # Utilization — enables right-sizing to actual demand.
            cpu_u = cell(row, resolved["cpu_util_pct"])
            if cpu_u is not None:
                rec["cpu_util_pct"] = cpu_u
            mem_u = cell(row, resolved["mem_util_pct"])
            if mem_u is not None:
                rec["mem_util_pct"] = mem_u
            else:
                # Derive a memory-utilization % from "used GB" vs allocation.
                used = _to_float(cell(row, resolved["mem_used_gib"]))
                alloc = _to_float(rec.get("memory_value"))
                if used is not None and alloc:
                    rec["mem_util_pct"] = round(100.0 * used / alloc, 2)

            records.append(rec)
        return records


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
