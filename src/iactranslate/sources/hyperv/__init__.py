"""Hyper-V source — `Get-VM | Export-Csv` style exports.

Typical columns: Name/VMName, ProcessorCount, MemoryAssigned/MemoryStartup
(bytes) or a *GB variant, State, and optionally a disk-size column. Memory is
reported in bytes by PowerShell, so we convert to MiB for the raw contract.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .._columns import cell, find_column
from ..base import RawRecord, any_header_contains, headers, is_csv

_BYTES_PER_MIB = 1024 * 1024


class HypervSource:
    name = "hyperv"
    label = "Microsoft Hyper-V (Get-VM export)"
    source_platform = "hyper-v"

    def detect(self, path: str) -> float:
        if not is_csv(path):
            return 0.0
        hdrs = headers(path)
        if any_header_contains(hdrs, ["processorcount"]):
            return 0.9
        if any_header_contains(hdrs, ["memoryassigned", "memorystartup"]):
            return 0.85
        return 0.0

    def parse(self, path: str, column_map: Optional[Dict[str, str]] = None) -> List[RawRecord]:
        df = pd.read_csv(path)
        if df.empty:
            return []

        name_col = find_column(df, ["VMName", "Name"])
        cpu_col = find_column(df, ["ProcessorCount", "CPUCount", "CPUs"])
        mem_bytes_col = find_column(df, ["MemoryAssigned", "MemoryStartup", "MemoryMaximum"])
        mem_gb_col = find_column(df, ["MemoryAssignedGB", "MemoryStartupGB", "MemoryGB", "Memory GB"])
        state_col = find_column(df, ["State", "Status"])
        os_col = find_column(df, ["OperatingSystem", "Guest OS", "OS", "OSName"])
        disk_col = find_column(df, ["DiskSizeGB", "VHDSizeGB", "TotalDiskGB", "Disk GB"])

        records: List[RawRecord] = []
        for _, row in df.iterrows():
            name = cell(row, name_col)
            if name is None or not str(name).strip():
                continue
            rec: RawRecord = {
                "name": str(name).strip(),
                "cpus": cell(row, cpu_col),
                "powerstate": cell(row, state_col),
                "os": cell(row, os_col),
            }
            gb = cell(row, mem_gb_col)
            if gb is not None:
                rec["memory_value"] = gb
                rec["memory_unit"] = "gib"
            else:
                raw = cell(row, mem_bytes_col)
                if raw is not None:
                    try:
                        rec["memory_mib"] = float(raw) / _BYTES_PER_MIB
                    except (TypeError, ValueError):
                        pass
            disk = cell(row, disk_col)
            if disk is not None:
                rec["disk_value"] = disk
                rec["disk_unit"] = "gib"
            records.append(rec)
        return records
