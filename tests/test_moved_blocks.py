"""A machine renamed at the source is moved in Terraform state, not recreated.

The real-provider check — `tofu validate` with the generated blocks in place —
lives in test_e2e.py; these pin the pairing logic, which needs no provider.
"""
import json

import pandas as pd
import pytest

from iactranslate.cli import main
from iactranslate.models import NormalizedVM
from iactranslate.moves import _relabel, moved_blocks
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import list_targets

U1 = "421a0001-aaaa-bbbb-cccc-000000000017"
U2 = "421a0002-aaaa-bbbb-cccc-000000000042"


@pytest.fixture(scope="module")
def estate():
    return normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))


def _rename(vms, old, new):
    return [v.model_copy(update={"vm_name": new}) if v.vm_name == old else v for v in vms]


# --- label substitution ----------------------------------------------------------


@pytest.mark.parametrize("label,expected", [
    ("app01", "db01"),                                  # the machine itself
    ("app01_data_2", "db01_data_2"),                    # its disks
    ("prod_web_lb_443_app01", "prod_web_lb_443_db01"),  # its LB membership
    ("x_app01_y", "x_db01_y"),
])
def test_relabel_replaces_whole_components(label, expected):
    assert _relabel(label, "app01", "db01") == expected


@pytest.mark.parametrize("label", ["app011", "myapp01", "app01x_data_1"])
def test_relabel_ignores_partial_matches(label):
    assert _relabel(label, "app01", "db01") is None


# --- pairing ------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["aws", "azure", "gcp", "oci", "digitalocean"])
def test_a_rename_moves_every_resource_of_the_machine(estate, target):
    after = _rename(estate, "prod-web-01", "prod-web-renamed")
    r = moved_blocks(estate, after, target=target)
    assert r.moves, "a renamed machine must produce moved blocks"
    assert not r.unmatched_removed and not r.unmatched_added
    for m in r.moves:
        assert "prod_web_01" in m.from_address and "prod_web_renamed" in m.to_address
        assert m.from_address.split(".")[0] == m.to_address.split(".")[0]  # same type
        assert m.vm_name == "prod-web-renamed"
    # The instance itself is always among them.
    assert any(m.to_address.endswith(".prod_web_renamed") for m in r.moves)


@pytest.mark.parametrize("target", [t for t in list_targets() if t in ("nutanix", "proxmox")])
def test_on_premises_targets_pair_cleanly_too(estate, target):
    r = moved_blocks(estate, _rename(estate, "prod-web-01", "prod-web-renamed"), target=target)
    assert r.moves and not r.unmatched_removed and not r.unmatched_added


def test_no_rename_means_no_blocks(estate):
    r = moved_blocks(estate, estate, target="aws")
    assert r.moves == [] and r.to_hcl() == ""


def test_without_uuids_nothing_is_guessed():
    before = [NormalizedVM(vm_name="old-name", cpu=2, memory_gib=4, disks_gib=[20])]
    after = [NormalizedVM(vm_name="new-name", cpu=2, memory_gib=4, disks_gib=[20])]
    r = moved_blocks(before, after, target="aws")
    # Same shape, different name, no identity: could be a replacement machine.
    assert r.moves == []
    assert "aws_instance.old_name" in r.unmatched_removed
    assert "aws_instance.new_name" in r.unmatched_added


def test_a_shifted_collision_suffix_is_a_conflict_with_state_commands():
    # `web-01` and `web.01` both slug to `web_01`; the second gets `web_01_2`.
    # Renaming the first hands the second the bare label, though it was never
    # renamed itself. Terraform chains `moved` blocks (web_01_2 -> web_01 ->
    # api_01), which would route the wrong object, so this must not be emitted
    # as blocks — it comes back as a state-mv sequence via temporary addresses.
    a = NormalizedVM(vm_name="web-01", cpu=2, memory_gib=4, disks_gib=[20], source_uuid=U1)
    b = NormalizedVM(vm_name="web.01", cpu=2, memory_gib=4, disks_gib=[20], source_uuid=U2)
    before = [a, b]
    after = [a.model_copy(update={"vm_name": "api-01"}), b]
    r = moved_blocks(before, after, target="aws")

    blocks = {(m.from_address, m.to_address) for m in r.moves}
    assert not any("aws_instance" in f for f, _ in blocks)  # none for the instances
    instance_conflict = next(c for c in r.conflicts if "aws_instance.api_01" in c.addresses)
    cmds = instance_conflict.state_mv
    assert cmds == [
        "terraform state mv 'aws_instance.web_01' 'aws_instance.web_01__iactranslate_tmp'",
        "terraform state mv 'aws_instance.web_01_2' 'aws_instance.web_01_2__iactranslate_tmp'",
        "terraform state mv 'aws_instance.web_01__iactranslate_tmp' 'aws_instance.api_01'",
        "terraform state mv 'aws_instance.web_01_2__iactranslate_tmp' 'aws_instance.web_01'",
    ]
    assert instance_conflict.addresses == [
        "aws_instance.api_01", "aws_instance.web_01", "aws_instance.web_01_2"]
    # `api-01` classifies as an app server, so the web LB goes and app-tier
    # networking arrives. Real changes, reported — never folded into a move.
    assert not any(a.startswith("aws_instance.") for a in r.unmatched_removed + r.unmatched_added)


def test_a_coincidental_match_onto_an_occupied_address_is_dropped():
    # Renaming machine `web` to `app` would turn an address containing `_web_`
    # into one containing `_app_`. If that address already holds something
    # else, it is not this machine's resource, and nothing may move onto it.
    a = NormalizedVM(vm_name="web", cpu=2, memory_gib=4, disks_gib=[20], source_uuid=U1)
    b = NormalizedVM(vm_name="app", cpu=2, memory_gib=4, disks_gib=[20], source_uuid=U2)
    before = [a, b]
    after = [a.model_copy(update={"vm_name": "app"}), b.model_copy(update={"vm_name": "web"})]
    r = moved_blocks(before, after, target="aws")
    # A genuine swap: both machines' addresses are reused, so state mv, not blocks.
    assert not r.moves
    assert r.conflicts


def test_a_resize_alongside_a_rename_leaves_new_disks_unmatched(estate):
    after = [
        v.model_copy(update={"vm_name": "prod-web-renamed", "disks_gib": v.disks_gib + [100.0]})
        if v.vm_name == "prod-web-01" else v
        for v in estate
    ]
    r = moved_blocks(estate, after, target="aws")
    # The existing disks move; the new one is a genuine creation, stated as such.
    assert any(m.to_address == "aws_instance.prod_web_renamed" for m in r.moves)
    assert r.unmatched_added and all("prod_web_renamed_data" in a for a in r.unmatched_added)
    assert not r.unmatched_removed


def test_hcl_is_one_moved_block_per_address(estate):
    hcl = moved_blocks(estate, _rename(estate, "prod-web-01", "x-01"), target="aws").to_hcl()
    assert hcl.count("moved {") == hcl.count("from = ") == hcl.count("to   = ")
    assert "from = aws_instance.prod_web_01\n  to   = aws_instance.x_01" in hcl


# --- the CLI -------------------------------------------------------------------------


def _workbook(path, name):
    pd.DataFrame({"VM": [name, "db-01"], "CPUs": [2, 4], "Memory": [4096, 8192],
                  "VM UUID": [U1, U2]}).to_excel(path, sheet_name="vInfo", index=False)
    return str(path)


def test_cli_writes_moved_blocks(tmp_path, capsys):
    before = _workbook(tmp_path / "before.xlsx", "app-01")
    after = _workbook(tmp_path / "after.xlsx", "app-01-renamed")
    out = tmp_path / "moved.tf"
    assert main(["diff", before, after, "--moved-tf", str(out), "--target", "aws"]) == 0
    assert "aws_instance.app_01_renamed" in out.read_text()
    assert "moved` blocks for aws" in capsys.readouterr().out


def test_cli_json_carries_the_moves(tmp_path, capsys):
    before = _workbook(tmp_path / "before.xlsx", "app-01")
    after = _workbook(tmp_path / "after.xlsx", "app-01-renamed")
    assert main(["diff", before, after, "--json", "--moved-tf", str(tmp_path / "m.tf")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["renamed"] == [{"before": "app-01", "after": "app-01-renamed"}]
    assert payload["moved"]["moves"][0]["to_address"] == "aws_instance.app_01_renamed"


def test_cli_writes_no_file_when_nothing_moved(tmp_path, capsys):
    snap = _workbook(tmp_path / "same.xlsx", "app-01")
    out = tmp_path / "moved.tf"
    assert main(["diff", snap, snap, "--moved-tf", str(out)]) == 0
    assert not out.exists()
    assert "no `moved` blocks needed" in capsys.readouterr().out


def test_cli_rejects_an_unknown_target(tmp_path):
    snap = _workbook(tmp_path / "same.xlsx", "app-01")
    assert main(["diff", snap, snap, "--moved-tf", str(tmp_path / "m.tf"), "--target", "mars"]) == 2
