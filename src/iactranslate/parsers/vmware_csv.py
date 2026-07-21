"""Flat VMware CSV parser.

A single-table export with one row per VM. Memory and disk may be reported in
either MiB/MB or GiB/GB depending on the tool; we detect the unit from the
column header and record the value plus its unit so normalize.py converts it.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from ._columns import cell, find_column

RawVM = Dict[str, object]


def _value_and_unit(row: pd.Series, col, default_unit: str):
    """Return (value, unit) inferring the unit from the column header."""
    if col is None:
        return None, default_unit
    raw = cell(row, col)
    if raw is None:
        return None, default_unit
    header = str(col).lower()
    if "gib" in header or "gb" in header:
        unit = "gib"
    elif "mib" in header or "mb" in header:
        unit = "mib"
    else:
        unit = default_unit
    return raw, unit


def parse(path: str) -> List[RawVM]:
    df = pd.read_csv(path)
    if df.empty:
        return []

    name_col = find_column(df, ["VM", "VM Name", "Name", "vm_name"])
    cpu_col = find_column(df, ["CPUs", "CPU", "vCPU", "cpu"])
    mem_col = find_column(df, ["Memory GiB", "Memory MiB", "Memory", "RAM", "memory"])
    disk_col = find_column(
        df, ["Disk GiB", "Disk MiB", "Provisioned MiB", "Storage", "Disk", "disk"]
    )
    os_col = find_column(df, ["OS", "Guest OS", "Operating System", "os"])
    power_col = find_column(df, ["Powerstate", "Power State", "power_state"])
    dns_col = find_column(df, ["DNS Name", "Hostname", "hostname"])
    ip_col = find_column(df, ["Primary IP Address", "IP Address", "IP", "ip"])
    net_col = find_column(df, ["Network", "VLAN", "Port Group", "network"])
    cluster_col = find_column(df, ["Cluster", "cluster"])
    dc_col = find_column(df, ["Datacenter", "Data Center", "datacenter"])

    records: List[RawVM] = []
    for _, row in df.iterrows():
        name = cell(row, name_col)
        if name is None or not str(name).strip():
            continue

        mem_val, mem_unit = _value_and_unit(row, mem_col, default_unit="gib")
        disk_val, disk_unit = _value_and_unit(row, disk_col, default_unit="gib")

        rec: RawVM = {
            "name": str(name).strip(),
            "cpus": cell(row, cpu_col),
            "memory_value": mem_val,
            "memory_unit": mem_unit,
            "os": cell(row, os_col),
            "powerstate": cell(row, power_col),
            "dns_name": cell(row, dns_col),
            "ip": cell(row, ip_col),
            "network": cell(row, net_col),
            "cluster": cell(row, cluster_col),
            "datacenter": cell(row, dc_col),
        }
        if disk_val is not None:
            rec["disk_value"] = disk_val
            rec["disk_unit"] = disk_unit
        records.append(rec)

    return records
