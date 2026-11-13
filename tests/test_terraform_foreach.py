"""The opt-in `for_each` layout: same infrastructure, one resource per kind.

The real-provider check (`tofu validate`, migration file included) lives in
test_e2e.py. These pin the shape, and above all that switching layouts is safe.
"""
import re

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.generator.renderer import RendererNotSupportedError
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.renderers import render, renderers_for
from iactranslate.targets import get_target, list_targets

_LABEL = re.compile(r'^resource "([a-z0-9_]+)" "([A-Za-z0-9_-]+)"', re.MULTILINE)
_MOVED = re.compile(r"from = (\S+)\n  to   = (\S+)")


@pytest.fixture(scope="module")
def plan():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    return build_migration_plan(vms, "foreach", get_target("aws"))


@pytest.fixture(scope="module")
def classic(plan):
    return render("terraform", plan, get_target("aws"))


@pytest.fixture(scope="module")
def foreach(plan):
    return render("terraform-foreach", plan, get_target("aws"))


def _addresses(files, rtype):
    return {label for name, body in files.items() if name.endswith(".tf")
            for t, label in _LABEL.findall(body) if t == rtype}


SUPPORTED = ("aws", "azure", "gcp")


def test_it_is_offered_where_templates_exist():
    for t in list_targets():
        assert ("terraform-foreach" in renderers_for(t)) == (t in SUPPORTED), t


def test_other_clouds_refuse_rather_than_render_classic():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    oci = build_migration_plan(vms, "x", get_target("oci"))
    with pytest.raises(RendererNotSupportedError):
        render("terraform-foreach", oci, get_target("oci"))


# --- every supported cloud ------------------------------------------------------


def _both(cloud):
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    plan = build_migration_plan(vms, "fe", get_target(cloud))
    return plan, render("terraform", plan, get_target(cloud)), \
        render("terraform-foreach", plan, get_target(cloud))


@pytest.mark.parametrize("cloud", SUPPORTED)
def test_every_per_machine_resource_is_migrated(cloud):
    """Each classic per-machine or per-volume address moves to its map entry,
    for every resource type, and nothing else moves."""
    plan, classic, foreach = _both(cloud)
    machines = {c.resource_name for c in plan.compute}
    moves = dict(_MOVED.findall(foreach["migrate-from-classic.tf"]))
    expected = {}
    for body in classic.values():
        for rtype, label in _LABEL.findall(body):
            if label in machines:
                expected[f"{rtype}.{label}"] = f'{rtype}.vm["{label}"]'
            elif "_data_" in label and label.rsplit("_data_", 1)[0] in machines:
                expected[f"{rtype}.{label}"] = f'{rtype}.data["{label}"]'
    assert moves == expected
    # And every destination is a declared map in the for_each render.
    declared = {(t, n) for b in foreach.values() for t, n in _LABEL.findall(b)}
    for dest in moves.values():
        rtype, rest = dest.split(".", 1)
        assert (rtype, rest.split("[")[0]) in declared, dest


@pytest.mark.parametrize("cloud", SUPPORTED)
def test_only_compute_storage_and_references_differ(cloud):
    _, classic, foreach = _both(cloud)
    for name, body in classic.items():
        if name.startswith(("compute", "storage", "loadbalancer", "outputs", "imports")):
            continue
        assert foreach.get(name) == body, f"{cloud}/{name} differs between layouts"


def test_azure_splits_windows_and_linux_and_filters_optional_resources():
    plan, _, foreach = _both("azure")
    compute = foreach["compute.tf"]
    for resource, source in (("azurerm_windows_virtual_machine", "local.windows_vms"),
                             ("azurerm_linux_virtual_machine", "local.linux_vms"),
                             ("azurerm_public_ip", "local.public_vms"),
                             ("azurerm_network_interface_backend_address_pool_association",
                              "local.pooled_vms")):
        assert re.search(rf'resource "{resource}" "vm" {{\n  for_each = {source}\n', compute), resource
    # Every row carries the same attributes — a for_each map needs one type.
    rows = re.findall(r'^    "[a-z0-9_]+" = \{\n(.*?)\n    \}', compute, re.MULTILINE | re.DOTALL)
    shapes = {tuple(re.findall(r"^\s+(\w+)\s+=", r, re.MULTILINE)) for r in rows}
    assert len(rows) == len(plan.compute) and len(shapes) == 1
    # Attachments resolve through one merged lookup, never a two-branch conditional.
    assert "virtual_machine_id = local.vm_ids[each.value.vm]" in foreach["storage.tf"]


def test_gcp_external_ip_is_a_dynamic_block():
    _, classic, foreach = _both("gcp")
    public = len(re.findall(r"access_config \{", "".join(classic.values())))
    assert public and 'dynamic "access_config"' in foreach["compute.tf"]
    assert foreach["compute.tf"].count("external_ip  = true") == public


def test_one_instance_resource_with_a_row_per_machine(plan, foreach):
    compute = foreach["compute.tf"]
    assert _addresses(foreach, "aws_instance") == {"vm"}
    assert "for_each = local.vms" in compute
    keys = re.findall(r'^    "([a-z0-9_]+)" = \{', compute, re.MULTILINE)
    # Keys are exactly the classic labels, so addresses map one-to-one.
    assert sorted(keys) == sorted(c.resource_name for c in plan.compute)


def test_data_volumes_are_keyed_like_classic_labels(classic, foreach):
    classic_vols = _addresses(classic, "aws_ebs_volume")
    keys = set(re.findall(r'^    "([a-z0-9_]+)" = \{ vm =', foreach["storage.tf"], re.MULTILINE))
    assert classic_vols and keys == classic_vols
    assert _addresses(foreach, "aws_ebs_volume") == {"data"}
    assert _addresses(foreach, "aws_volume_attachment") == {"data"}


def test_everything_else_is_unchanged(classic, foreach):
    for name, body in classic.items():
        if name.startswith(("compute", "storage", "loadbalancer", "outputs", "imports")):
            continue
        assert foreach.get(name) == body, f"{name} differs between layouts"


def test_references_follow_the_layout(foreach):
    lb = foreach["loadbalancer.tf"]
    assert re.search(r'target_id\s+= aws_instance\.vm\["[a-z0-9_]+"\]\.id', lb)
    assert 'aws_instance.vm["' in foreach["outputs.tf"]
    # No classic instance address survives anywhere except the migration file,
    # whose whole job is to name them.
    code = "\n".join(line for n, b in foreach.items() if n != "migrate-from-classic.tf"
                     for line in b.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"aws_instance\.(?!vm\[)[a-z]", code)


def test_the_migration_file_moves_every_classic_machine_and_disk(classic, foreach):
    moves = dict(_MOVED.findall(foreach["migrate-from-classic.tf"]))
    for rtype, name in (("aws_instance", "vm"), ("aws_ebs_volume", "data"),
                        ("aws_volume_attachment", "data")):
        for label in _addresses(classic, rtype):
            assert moves[f"{rtype}.{label}"] == f'{rtype}.{name}["{label}"]'
    # Nothing else is moved: only these resources changed address.
    assert len(moves) == sum(len(_addresses(classic, t)) for t in
                             ("aws_instance", "aws_ebs_volume", "aws_volume_attachment"))


def test_brownfield_imports_target_the_map_entry():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    vms = [v.model_copy(update={"external_id": "i-0123456789abcdef0"}) if i == 0 else v
           for i, v in enumerate(vms)]
    files = render("terraform-foreach", build_migration_plan(vms, "x", get_target("aws")),
                   get_target("aws"))
    assert re.search(r'to = aws_instance\.vm\["[a-z0-9_]+"\]\n  id = "i-0123456789abcdef0"',
                     files["imports.tf"])


def test_a_large_estate_is_still_one_compute_file(monkeypatch):
    # The classic layout splits compute per environment/tier above 50 machines;
    # a single resource over a map has nothing to split.
    monkeypatch.setenv("IACTRANSLATE_SPLIT_COMPUTE_ABOVE", "5")
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    plan = build_migration_plan(vms, "x", get_target("aws"))
    assert sum(n.startswith("compute") for n in render("terraform", plan, get_target("aws"))) > 1
    assert [n for n in render("terraform-foreach", plan, get_target("aws"))
            if n.startswith("compute")] == ["compute.tf"]


def test_the_layout_is_much_smaller_where_it_applies(classic, foreach):
    def lines(files, prefixes):
        return sum(b.count("\n") for n, b in files.items() if n.startswith(prefixes))
    assert lines(foreach, ("compute", "storage")) < 0.5 * lines(classic, ("compute", "storage"))
