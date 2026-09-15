"""Nutanix AHV and Proxmox VE as destinations.

The first targets that are not public clouds, and the reason they exist is
strategic: most of the VMware exodus is going to another hypervisor, not to a
hyperscaler, and no hyperscaler's tooling will ever say "stay on-prem". These
tests are about the two ways an on-premises target could quietly lie —
pretending to have a price, and pretending Windows needs substituting.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.costing import estimate_costs
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.recommend import recommend
from iactranslate.targets import get_target
from iactranslate.targets.base import CAP_PRICED

ONPREM = ["nutanix", "proxmox"]


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_sample.xlsx"))


def _files(vms, cloud):
    target = get_target(cloud)
    plan = build_migration_plan(vms, "onprem", target)
    return build_files(plan, target), plan


# --- no price, stated as such ------------------------------------------------

@pytest.mark.parametrize("cloud", ONPREM)
def test_on_premises_targets_carry_no_price(cloud):
    assert CAP_PRICED not in get_target(cloud).capabilities


@pytest.mark.parametrize("cloud", ONPREM)
def test_zero_is_not_presented_as_free(vms, cloud):
    """The README says why there is no number. A bare $0 next to five priced
    clouds is the most flattering lie the tool could tell."""
    files, _ = _files(vms, cloud)
    readme = files["README.md"]
    cost_line = next(line for line in readme.splitlines() if line.startswith("- **Estimated cost:**"))
    assert "not estimated" in cost_line
    assert "$" not in cost_line
    assert "will not invent it" in readme


@pytest.mark.parametrize("cloud", ONPREM)
def test_the_cost_engine_does_not_apply_default_cloud_rates(vms, cloud):
    """The regression: storage, Windows licensing and load balancers were
    priced at public-cloud defaults for a hypervisor where none is billed."""
    _, plan = _files(vms, cloud)
    costs = estimate_costs(plan)
    assert not costs.priced
    assert costs.total == 0


def test_the_recommender_reports_them_without_ranking_them(vms):
    """Absent from the table must never read as "not an option"."""
    rec = recommend(vms)
    ranked = {s.cloud for s in rec.ranked}
    not_ranked = {n.target for n in rec.not_ranked}
    assert not ranked & set(ONPREM)
    assert set(ONPREM) <= not_ranked
    # And their presence did not distort the real comparison: the cheapest
    # priced cloud still scores 1.0 on cost, not something crushed by a zero.
    assert max(s.cost_score for s in rec.ranked) == 1.0


# --- Windows stays Windows ---------------------------------------------------

@pytest.mark.parametrize("cloud", ONPREM)
def test_windows_source_vms_are_not_substituted(vms, cloud):
    """A hypervisor runs whatever the operator uploads. Unlike DigitalOcean,
    there is nothing to substitute and the OS-family check must stay quiet."""
    _, plan = _files(vms, cloud)
    windows = [c for c in plan.compute if (c.source_os or "").lower().startswith("microsoft")]
    assert windows, "fixture should contain Windows workloads"
    for c in windows:
        assert c.image_key.startswith("windows"), c.vm_name
        assert not c.os_family_changed, c.vm_name


# --- the generated artefact --------------------------------------------------

@pytest.mark.parametrize("cloud", ONPREM)
def test_images_are_variables_the_operator_fills(vms, cloud):
    """There is no public image catalog on a cluster; inventing a name would
    fail at apply. Every image is a variable pointing at their upload."""
    files, _ = _files(vms, cloud)
    assert "REPLACE_ME" in files["variables.tf"] or "9000" in files["variables.tf"]
    assert "image_" in files["variables.tf"] or "template_" in files["variables.tf"]


def test_nutanix_records_firewall_intent_rather_than_guessing_flow_rules(vms):
    """A wrong-but-valid Flow rule passes `terraform validate` and leaves a
    port open. Categories are generated; the rules are stated for a human."""
    files, _ = _files(vms, "nutanix")
    sec = files["security.tf"]
    assert "nutanix_category_value" in sec
    assert "Intended ingress" in sec
    assert "nutanix_network_security_rule" not in sec


def test_proxmox_generates_real_firewall_groups(vms):
    """Proxmox's firewall has a named, attachable rule set — the exact shape
    the plan carries — so here the rules are real, not recorded."""
    files, _ = _files(vms, "proxmox")
    assert "proxmox_virtual_environment_cluster_firewall_security_group" in files["security.tf"]
    assert "proxmox_virtual_environment_firewall_rules" in files["compute.tf"]
    assert "Firewall: **yes**" in files["README.md"], "the datacenter firewall being off makes every group inert"


@pytest.mark.parametrize("cloud", ONPREM)
def test_sizes_come_from_the_standard_grid(vms, cloud):
    """Carrying the source allocation across exactly would reproduce the
    over-provisioning the migration exists to leave behind."""
    _, plan = _files(vms, cloud)
    names = get_target(cloud).instance_names()
    for c in plan.compute:
        assert c.instance_type in names
