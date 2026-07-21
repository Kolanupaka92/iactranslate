"""RVTools .xlsx parser.

RVTools exports one workbook with many sheets. We read:
  - vInfo    : one row per VM (name, cpu, memory, os, ip, cluster, ...)
  - vDisk    : one row per virtual disk (capacity) — summed per VM
  - vNetwork : one row per NIC (network / port group, IP) — first per VM

Memory and disk capacity in RVTools are reported in MiB; we keep the raw MiB
values here and let normalize.py convert to GiB.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import pandas as pd

from .._columns import cell, find_column

RawVM = Dict[str, object]


def _read_sheets(path: str) -> Dict[str, pd.DataFrame]:
    xls = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    # Normalize sheet-name lookup to lower-case.
    return {str(name).strip().lower(): df for name, df in xls.items()}


def _disks_by_vm(sheets: Dict[str, pd.DataFrame]) -> Dict[str, List[float]]:
    df = sheets.get("vdisk")
    out: Dict[str, List[float]] = defaultdict(list)
    if df is None or df.empty:
        return out
    name_col = find_column(df, ["VM"])
    cap_col = find_column(df, ["Capacity MiB", "Capacity", "Capacity MB"])
    if name_col is None or cap_col is None:
        return out
    for _, row in df.iterrows():
        vm = cell(row, name_col)
        cap = cell(row, cap_col)
        if vm is None or cap is None:
            continue
        try:
            out[str(vm).strip()].append(float(cap))
        except (TypeError, ValueError):
            continue
    return out


def _network_by_vm(sheets: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, object]]:
    df = sheets.get("vnetwork")
    out: Dict[str, Dict[str, object]] = {}
    if df is None or df.empty:
        return out
    name_col = find_column(df, ["VM"])
    net_col = find_column(df, ["Network", "Port Group", "Portgroup"])
    ip_col = find_column(df, ["IP Address", "IPv4 Address", "IP"])
    if name_col is None:
        return out
    for _, row in df.iterrows():
        vm = cell(row, name_col)
        if vm is None:
            continue
        key = str(vm).strip()
        if key in out:
            continue  # keep the first NIC per VM
        entry: Dict[str, object] = {}
        net = cell(row, net_col)
        ip = cell(row, ip_col)
        if net is not None:
            entry["network"] = str(net).strip()
        if ip is not None:
            entry["ip"] = str(ip).strip()
        out[key] = entry
    return out


def parse(path: str) -> List[RawVM]:
    sheets = _read_sheets(path)
    vinfo = sheets.get("vinfo")
    if vinfo is None:
        # Some exports name the primary sheet differently; fall back to the first.
        if not sheets:
            return []
        vinfo = next(iter(sheets.values()))

    name_col = find_column(vinfo, ["VM", "VM Name", "Name"])
    cpu_col = find_column(vinfo, ["CPUs", "CPU", "vCPU", "Num CPU"])
    mem_col = find_column(vinfo, ["Memory", "Memory MiB", "RAM"])
    os_col = find_column(
        vinfo, ["OS according to the configuration file", "OS", "Guest OS", "OS according to the VMware Tools"]
    )
    power_col = find_column(vinfo, ["Powerstate", "Power State", "Power"])
    dns_col = find_column(vinfo, ["DNS Name", "Hostname", "DNS"])
    ip_col = find_column(vinfo, ["Primary IP Address", "IP Address", "IP"])
    cluster_col = find_column(vinfo, ["Cluster"])
    dc_col = find_column(vinfo, ["Datacenter", "Data Center"])
    provisioned_col = find_column(vinfo, ["Provisioned MiB", "Provisioned MB", "Provisioned"])

    disks = _disks_by_vm(sheets)
    networks = _network_by_vm(sheets)

    records: List[RawVM] = []
    for _, row in vinfo.iterrows():
        name = cell(row, name_col)
        if name is None:
            continue
        key = str(name).strip()
        if not key:
            continue

        rec: RawVM = {
            "name": key,
            "cpus": cell(row, cpu_col),
            "memory_mib": cell(row, mem_col),
            "os": cell(row, os_col),
            "powerstate": cell(row, power_col),
            "dns_name": cell(row, dns_col),
            "ip": cell(row, ip_col),
            "cluster": cell(row, cluster_col),
            "datacenter": cell(row, dc_col),
        }

        # Disks: prefer per-disk data from vDisk; else fall back to provisioned total.
        vm_disks = disks.get(key)
        if vm_disks:
            rec["disks_mib"] = vm_disks
        else:
            prov = cell(row, provisioned_col)
            if prov is not None:
                rec["disks_mib"] = [prov]

        net = networks.get(key)
        if net:
            if "network" in net:
                rec["network"] = net["network"]
            if "ip" in net and not rec.get("ip"):
                rec["ip"] = net["ip"]

        records.append(rec)

    return records
