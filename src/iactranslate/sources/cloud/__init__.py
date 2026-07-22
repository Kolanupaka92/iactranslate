"""Cloud-inventory source — an existing AWS/Azure fleet export (re-platform / cross-cloud).

Cloud exports usually list an instance *type* (e.g. m5.xlarge, Standard_D4as_v5)
rather than vCPU/memory. We recover the specs by looking the type up in the
existing target catalogs (`targets/aws`, `targets/azure`) — so a company already
in one cloud can be re-sized and translated for any other, unbiased.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .._columns import cell, find_column
from ..base import RawRecord, any_header_contains, headers, is_csv, is_xlsx


class CloudSource:
    name = "cloud"
    label = "Cloud inventory (existing AWS / Azure fleet)"
    source_platform = "cloud"

    def detect(self, path: str) -> float:
        if not (is_csv(path) or is_xlsx(path)):
            return 0.0
        hdrs = headers(path)
        if any_header_contains(hdrs, ["instancetype", "instance type", "vmsize", "vm size"]):
            return 0.8
        return 0.0

    def parse(self, path: str, column_map: Optional[Dict[str, str]] = None) -> List[RawRecord]:
        # Imported lazily to avoid any import-time coupling to the target registry.
        from ...targets import get_target

        aws = get_target("aws")
        azure = get_target("azure")

        df = pd.read_csv(path) if is_csv(path) else pd.read_excel(path, engine="openpyxl")
        if df.empty:
            return []

        name_col = find_column(df, ["Name", "Instance Name", "InstanceId", "VM Name", "Hostname"])
        type_col = find_column(df, ["InstanceType", "Instance Type", "VMSize", "VM Size", "Size"])
        os_col = find_column(df, ["Platform", "OperatingSystem", "OS", "Image"])
        cpu_col = find_column(df, ["vCPUs", "vCPU", "CPUs", "Cores"])
        mem_col = find_column(df, ["Memory GiB", "MemoryGB", "RAM GB", "Memory"])
        disk_col = find_column(df, ["VolumeSize", "Disk GB", "Storage GB", "RootVolumeGB"])
        # Brownfield: an existing resource id lets us emit Terraform import blocks.
        id_col = find_column(df, ["InstanceId", "Instance ID", "ResourceId", "Resource ID", "VM ID"])

        records: List[RawRecord] = []
        for _, row in df.iterrows():
            name = cell(row, name_col)
            if name is None or not str(name).strip():
                continue
            rec: RawRecord = {"name": str(name).strip(), "os": cell(row, os_col)}
            ext_id = cell(row, id_col)
            if ext_id is not None and str(ext_id).strip():
                rec["external_id"] = str(ext_id).strip()

            spec = None
            itype = cell(row, type_col)
            if itype is not None:
                itype = str(itype).strip()
                spec = aws.spec_of(itype) or azure.spec_of(itype)

            if spec is not None:
                rec["cpus"] = spec.vcpu
                rec["memory_value"] = spec.memory_gib
                rec["memory_unit"] = "gib"
            else:
                # Fall back to explicit vCPU/memory columns if the type is unknown.
                rec["cpus"] = cell(row, cpu_col)
                gib = cell(row, mem_col)
                if gib is not None:
                    rec["memory_value"] = gib
                    rec["memory_unit"] = "gib"

            disk = cell(row, disk_col)
            if disk is not None:
                rec["disk_value"] = disk
                rec["disk_unit"] = "gib"
            records.append(rec)
        return records
