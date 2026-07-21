"""Generate deterministic sample discovery exports for tests and demos.

Produces:
  tests/fixtures/rvtools_sample.xlsx   (RVTools-style multi-sheet workbook)
  tests/fixtures/vmware_sample.csv     (flat VMware CSV export)

Run:  python scripts/make_fixtures.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# (vm, cpu, mem_gib, disk_gib, network, os, power, dns, ip, cluster, dc)
VMS = [
    ("prod-web-01", 4, 16, 200, "VLAN20", "Microsoft Windows Server 2022 (64-bit)", "poweredOn", "prod-web-01.corp.local", "10.10.20.11", "PROD-CLUSTER", "DC1"),
    ("prod-web-02", 4, 16, 200, "VLAN20", "Microsoft Windows Server 2022 (64-bit)", "poweredOn", "prod-web-02.corp.local", "10.10.20.12", "PROD-CLUSTER", "DC1"),
    ("prod-app-01", 8, 32, 300, "VLAN30", "Red Hat Enterprise Linux 9 (64-bit)", "poweredOn", "prod-app-01.corp.local", "10.10.30.21", "PROD-CLUSTER", "DC1"),
    ("prod-app-02", 8, 32, 300, "VLAN30", "Red Hat Enterprise Linux 9 (64-bit)", "poweredOn", "prod-app-02.corp.local", "10.10.30.22", "PROD-CLUSTER", "DC1"),
    ("prod-db-01", 16, 64, 1000, "VLAN40", "Microsoft Windows Server 2019 (64-bit)", "poweredOn", "prod-db-01.corp.local", "10.10.40.31", "PROD-CLUSTER", "DC1"),
    ("dev-web-01", 2, 8, 100, "VLAN120", "Ubuntu Linux (64-bit)", "poweredOn", "dev-web-01.corp.local", "10.20.20.11", "DEV-CLUSTER", "DC1"),
    ("dev-db-01", 4, 16, 250, "VLAN140", "Ubuntu Linux (64-bit)", "poweredOff", "dev-db-01.corp.local", "10.20.40.31", "DEV-CLUSTER", "DC1"),
]

MIB = 1024


def build_rvtools(path: Path) -> None:
    vinfo_rows = []
    vdisk_rows = []
    vnet_rows = []
    for (vm, cpu, mem, disk, net, os_, power, dns, ip, cluster, dc) in VMS:
        vinfo_rows.append(
            {
                "VM": vm,
                "Powerstate": power,
                "CPUs": cpu,
                "Memory": mem * MIB,  # RVTools reports memory in MiB
                "OS according to the configuration file": os_,
                "DNS Name": dns,
                "Primary IP Address": ip,
                "Cluster": cluster,
                "Datacenter": dc,
                "Provisioned MiB": disk * MIB,
            }
        )
        # Split larger disks into an OS disk + a data disk to exercise multi-disk.
        if disk > 200:
            vdisk_rows.append({"VM": vm, "Disk": "Hard disk 1", "Capacity MiB": 100 * MIB})
            vdisk_rows.append({"VM": vm, "Disk": "Hard disk 2", "Capacity MiB": (disk - 100) * MIB})
        else:
            vdisk_rows.append({"VM": vm, "Disk": "Hard disk 1", "Capacity MiB": disk * MIB})
        vnet_rows.append({"VM": vm, "Network": net, "IP Address": ip})

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(vinfo_rows).to_excel(writer, sheet_name="vInfo", index=False)
        pd.DataFrame(vdisk_rows).to_excel(writer, sheet_name="vDisk", index=False)
        pd.DataFrame(vnet_rows).to_excel(writer, sheet_name="vNetwork", index=False)


def build_csv(path: Path) -> None:
    rows = []
    for (vm, cpu, mem, disk, net, os_, power, dns, ip, cluster, dc) in VMS:
        rows.append(
            {
                "VM": vm,
                "CPUs": cpu,
                "Memory GiB": mem,
                "Disk GiB": disk,
                "Network": net,
                "OS": os_,
                "Powerstate": power,
                "DNS Name": dns,
                "Primary IP Address": ip,
                "Cluster": cluster,
                "Datacenter": dc,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


GIB_BYTES = 1024 ** 3

# AWS instance type per VM (specs match the estate; cloud source recovers vCPU/mem).
_CLOUD_TYPE = {
    ("dev-web-01"): "m5.large",       # 2/8
    ("prod-web-01"): "m5.xlarge",     # 4/16
    ("prod-web-02"): "m5.xlarge",
    ("dev-db-01"): "m5.xlarge",
    ("prod-app-01"): "m5.2xlarge",    # 8/32
    ("prod-app-02"): "m5.2xlarge",
    ("prod-db-01"): "m5.4xlarge",     # 16/64
}


def build_hyperv(path: Path) -> None:
    rows = []
    for (vm, cpu, mem, disk, net, os_, power, dns, ip, cluster, dc) in VMS:
        rows.append(
            {
                "VMName": vm,
                "State": "Running" if power == "poweredOn" else "Off",
                "ProcessorCount": cpu,
                "MemoryStartup": mem * GIB_BYTES,   # PowerShell reports bytes
                "OperatingSystem": os_,
                "DiskSizeGB": disk,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def build_cmdb(path: Path) -> None:
    # Deliberately non-VMware headers — proves the generic source auto-detects.
    rows = []
    for (vm, cpu, mem, disk, net, os_, power, dns, ip, cluster, dc) in VMS:
        rows.append(
            {
                "Host Name": vm,
                "CPU Cores": cpu,
                "RAM (GB)": mem,
                "Storage (GB)": disk,
                "Operating System": os_,
                "Location": dc,
                "IP Address": ip,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def build_cloud(path: Path) -> None:
    rows = []
    for (vm, cpu, mem, disk, net, os_, power, dns, ip, cluster, dc) in VMS:
        rows.append(
            {
                "Instance Name": vm,
                "InstanceType": _CLOUD_TYPE[vm],
                "Platform": "windows" if "Windows" in os_ else "linux",
                "VolumeSize": disk,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def main() -> None:
    builders = {
        "rvtools_sample.xlsx": build_rvtools,
        "vmware_sample.csv": build_csv,
        "hyperv_sample.csv": build_hyperv,
        "cmdb_sample.csv": build_cmdb,
        "cloud_sample.csv": build_cloud,
    }
    for name, builder in builders.items():
        builder(FIXTURES / name)
        print(f"Wrote {FIXTURES / name}")


if __name__ == "__main__":
    main()
