"""VMware source — RVTools .xlsx workbooks and flat VMware .csv exports."""
from __future__ import annotations

from typing import Dict, List, Optional

from ..base import (
    RawRecord,
    any_header_contains,
    headers,
    is_csv,
    is_xlsx,
    sheet_names,
)
from . import rvtools, vmware_csv


class VmwareSource:
    name = "vmware"
    label = "VMware (RVTools / vSphere export)"
    source_platform = "vmware"

    def detect(self, path: str) -> float:
        if is_xlsx(path):
            sheets = sheet_names(path)
            if "vinfo" in sheets:
                return 0.98
            if any_header_contains(headers(path), ["cpus", "provisioned mib"]):
                return 0.6
            return 0.4  # some .xlsx inventory — plausible VMware
        if is_csv(path):
            hdrs = headers(path)
            if any_header_contains(hdrs, ["powerstate", "provisioned", "datacenter", "vlan"]):
                return 0.6
            return 0.2
        return 0.0

    def parse(self, path: str, column_map: Optional[Dict[str, str]] = None) -> List[RawRecord]:
        if is_xlsx(path):
            return rvtools.parse(path)
        return vmware_csv.parse(path)
