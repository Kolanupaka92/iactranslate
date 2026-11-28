"""What four real RVTools exports showed (ADR 0102).

Author: Sai Kolanupaka

The exports themselves are not in the repository. Each test builds the smallest
workbook that reproduces what one of them did.
"""
from __future__ import annotations

import openpyxl
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.assessment.analyzer import assess
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.renderers import render
from iactranslate.sources.vmware import rvtools
from iactranslate.targets import get_target

HEAD = ["VM", "Powerstate", "Template", "SRM Placeholder", "CPUs", "Memory",
        "OS according to the configuration file", "VM UUID"]


def _workbook(tmp_path, rows, disks=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "vInfo"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    vd = wb.create_sheet("vDisk")
    vd.append(["VM", "VM UUID", "Capacity MiB"])
    for d in disks:
        vd.append(d)
    path = tmp_path / "rvtools.xlsx"
    wb.save(path)
    return str(path)


def _row(name, uuid, template="False", placeholder="False", os="Red Hat Enterprise Linux 9 (64-bit)"):
    return [name, "poweredOn", template, placeholder, 2, 4096, os, uuid]


def test_templates_placeholders_and_nameless_rows_are_left_out_and_named(tmp_path):
    path = _workbook(tmp_path, [
        _row("app01", "u1"),
        _row("gold-image", "u2", template="True"),
        _row("dr-copy", "u3", placeholder="True"),
        _row(None, "u4"),
    ])
    vms = normalize(rvtools.parse(path))
    assert [v.vm_name for v in vms] == ["app01"]
    assert vms.excluded == {"template": ["gold-image"], "SRM placeholder": ["dr-copy"], "no name": ["u4"]}
    found = {f.id: f for f in assess(vms).findings}
    assert found["excluded-template"].affected == ["gold-image"]
    assert found["excluded-srm-placeholder"].affected == ["dr-copy"]
    assert found["excluded-no-name"].title == "Left out of the estate: 1 VM with no name"


def test_a_machine_gets_one_end_of_life_label():
    vms = [NormalizedVM(vm_name=f"w{i}", cpu=2, memory_gib=4, os=os)
           for i, os in enumerate(["Microsoft Windows Server 2008 R2 (64-bit)",
                                   "Microsoft Windows Server 2008 (32-bit)"])]
    legacy = {f.title: f.affected for f in assess(vms).findings if f.id.startswith("legacy-os")}
    assert legacy == {"End-of-life OS: Windows Server 2008 R2": ["w0"],
                      "End-of-life OS: Windows Server 2008": ["w1"]}


def _big(boot_gib, *data_gib):
    return NormalizedVM(vm_name="big-01", cpu=4, memory_gib=16, os="Ubuntu 22.04",
                        disks_gib=[boot_gib, *data_gib])


@pytest.mark.parametrize("target,boot,data,want_boot,want_data", [
    ("azure", 5120, 30000, 4095, 30000),    # OS disk limit; data disk fits
    ("aws", 100, 20000, 100, 16384),        # EBS limit
    ("gcp", 5120, 20000, 5120, 20000),      # both fit
])
def test_disks_over_the_cloud_limit_are_planned_at_it_and_flagged(target, boot, data, want_boot, want_data):
    t = get_target(target)
    c = build_migration_plan([_big(boot, data)], "acme", t).compute[0]
    assert (c.root_volume_gib, c.extra_volumes_gib) == (want_boot, [want_data])
    assert c.disk_too_large == ((want_boot, want_data) != (boot, data))
    if c.disk_too_large:
        assert "Too large for" in c.reason
        readme = render("terraform", build_migration_plan([_big(boot, data)], "acme", t), t)["README.md"]
        assert "with a disk larger than" in readme


def test_a_cloud_that_cannot_take_a_disk_is_not_eligible():
    from iactranslate.recommend import recommend
    rec = recommend([_big(100, 20000)])
    aws = next(s for s in rec.ranked if s.cloud == "aws")
    assert not aws.eligible
    assert any("disk larger than AWS accepts" in r for r in aws.reasons)
