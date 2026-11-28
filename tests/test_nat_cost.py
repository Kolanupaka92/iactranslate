"""The cost estimate prices the NAT gateway the output creates (KI-090, ADR 0101)."""
from __future__ import annotations

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.costing import estimate_costs
from iactranslate.models import NormalizedVM
from iactranslate.targets import get_target


def _vm(name, cpu=2):
    return NormalizedVM(vm_name=name, resource_name=name.replace("-", "_"), cpu=cpu, memory_gib=8,
                        disks_gib=[30], os="Ubuntu 22.04")


def _costs(target, *names):
    return estimate_costs(build_migration_plan([_vm(n) for n in names], "acme", get_target(target)))


PRIVATE = ("prod-app-01", "prod-app-02")


@pytest.mark.parametrize("target", ["aws", "azure"])
def test_one_gateway_and_its_address_on_aws_and_azure(target):
    costs = _costs(target, *PRIVATE)
    assert costs.nat == round((0.045 + 0.005) * 730, 2) == 36.5
    assert costs.nat_machines == 2


def test_google_charges_per_machine_using_the_gateway():
    costs = _costs("gcp", *PRIVATE)
    assert costs.nat == round((2 * 0.0014 + 0.005) * 730, 2)


def test_google_caps_the_gateway_at_32_machines():
    costs = _costs("gcp", *(f"prod-app-{i:02d}" for i in range(40)))
    assert costs.nat_machines == 40
    assert costs.nat == round((0.044 + 0.005) * 730, 2)


@pytest.mark.parametrize("target", ["aws", "azure", "gcp"])
def test_nat_is_in_the_total_and_its_traffic_is_named(target):
    costs = _costs(target, *PRIVATE)
    parts = costs.compute + costs.storage + costs.windows_licensing + costs.load_balancers + costs.nat
    assert costs.total == pytest.approx(parts, abs=0.02)
    assert any("NAT data processing" in x for x in costs.excludes)


@pytest.mark.parametrize("target", ["azure", "gcp"])
def test_no_nat_line_when_no_machine_is_private(target):
    plan = build_migration_plan([_vm("prod-web-01"), _vm("prod-web-02")], "acme", get_target(target))
    assert not plan.network.nat_gateway
    costs = estimate_costs(plan)
    assert costs.nat == 0
    assert not any("NAT" in x for x in costs.excludes)


def test_live_pricing_says_nat_used_the_catalog(monkeypatch):
    from iactranslate import pricing
    monkeypatch.setattr(pricing, "live_line_rate", lambda cloud, kind, region: 0.05)
    plan = build_migration_plan([_vm(n) for n in PRIVATE], "acme", get_target("azure"))
    plan = plan.model_copy(update={"live_pricing_requested": True})
    assert "catalog rates (November 2026) for NAT" in estimate_costs(plan).pricing_basis


def test_oracle_nat_is_named_as_not_priced_rather_than_free():
    costs = _costs("oci", *PRIVATE)
    assert costs.nat == 0
    assert any("NAT gateway" in x and "no rate" in x for x in costs.excludes)


def test_digitalocean_has_no_nat_gateway_so_no_nat_line_or_note():
    costs = _costs("digitalocean", *PRIVATE)
    assert costs.nat == 0 and costs.nat_machines == 0
    assert not any("NAT" in x for x in costs.excludes)
