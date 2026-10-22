"""Live rates for the bill's non-compute lines: storage, Windows licensing, LBs.

Every endpoint is mocked; the suite never touches the network. What these pin is
the parsing of each provider's real response shape, the per-line fallback to
catalog rates, and that the estimate says which lines are live.
"""
import json
import sys
import types

import pytest

import iactranslate.pricing as pricing
from iactranslate.agents import build_migration_plan
from iactranslate.costing import _STORAGE_USD_PER_GB_MONTH, estimate_costs
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("IACTRANSLATE_PRICE_CACHE", str(tmp_path / "cache.json"))
    pricing.reset_breakers()
    yield
    pricing.reset_breakers()


@pytest.fixture(scope="module")
def realistic_vms():
    return normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))


def _plan(vms, cloud, live_requested):
    plan = build_migration_plan(vms, "line-rates", get_target(cloud))
    return plan.model_copy(update={"live_pricing_requested": live_requested})


# --- Azure: response shapes taken from the live Retail Prices API ------------


def test_azure_storage_is_the_disk_tier_price_over_its_size(monkeypatch):
    # The same SKU also bills a monthly mount fee; taking the cheapest
    # "1/Month" item would price storage at roughly a tenth of its real rate.
    monkeypatch.setattr(pricing, "_azure_items", lambda flt: [
        {"meterName": "E10 LRS Disk Mount", "unitOfMeasure": "1/Month", "retailPrice": 0.91},
        {"meterName": "E10 LRS Disk", "unitOfMeasure": "1/Month", "retailPrice": 9.6},
        {"meterName": "E10 LRS Disk Operations", "unitOfMeasure": "10K", "retailPrice": 0.002},
    ])
    assert pricing._azure_line_rate(pricing.RATE_STORAGE, "eastus") == 0.075


def test_azure_windows_uplift_is_windows_minus_linux_per_vcpu(monkeypatch):
    rates = {True: 0.188, False: 0.096}  # Standard_D2s_v5, eastus
    monkeypatch.setattr(pricing, "_azure_vm_hourly", lambda sku, region, windows: rates[windows])
    assert pricing._azure_line_rate(pricing.RATE_WINDOWS, "eastus") == 0.046


def test_azure_load_balancer_is_the_hourly_rule_charge_per_month(monkeypatch):
    monkeypatch.setattr(pricing, "_azure_items", lambda flt: [
        {"unitOfMeasure": "1 Hour", "retailPrice": 0.025},
    ])
    assert pricing._azure_line_rate(pricing.RATE_LB, "eastus") == 18.25


def test_an_unreachable_azure_api_yields_no_rate(monkeypatch):
    monkeypatch.setattr(pricing, "_azure_items", lambda flt: None)
    monkeypatch.setattr(pricing, "_azure_vm_hourly", lambda sku, region, windows: None)
    for kind in pricing.LINE_RATES:
        assert pricing._azure_line_rate(kind, "eastus") is None


def test_an_uplift_that_is_not_positive_is_not_a_rate():
    # Windows cheaper than Linux means the lookup matched the wrong products.
    assert pricing._uplift(0.09, 0.096, 2) is None
    assert pricing._uplift(None, 0.096, 2) is None


# --- AWS --------------------------------------------------------------------


def _fake_boto3(monkeypatch, products):
    client = types.SimpleNamespace(
        get_products=lambda **kw: {"PriceList": [json.dumps(p) for p in products]}
    )
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: client))


def _aws_product(*dims):
    return {"terms": {"OnDemand": {"t": {"priceDimensions": {
        str(i): {"unit": unit, "pricePerUnit": {"USD": str(usd)}} for i, (unit, usd) in enumerate(dims)
    }}}}}


def test_aws_load_balancer_takes_the_hourly_charge_not_capacity_units(monkeypatch):
    # An ALB product lists its LCU charge alongside the per-hour one; pricing a
    # balancer at the LCU rate would be wrong by a factor that varies by region.
    _fake_boto3(monkeypatch, [_aws_product(("LCU-Hrs", 0.008), ("Hrs", 0.0225))])
    assert pricing._aws_line_rate(pricing.RATE_LB, "us-east-1") == round(0.0225 * 730, 2)


def test_aws_storage_is_the_gp3_gb_month_price(monkeypatch):
    _fake_boto3(monkeypatch, [_aws_product(("GB-Mo", 0.08))])
    assert pricing._aws_line_rate(pricing.RATE_STORAGE, "us-east-1") == 0.08


def test_aws_without_a_known_location_yields_no_rate():
    assert pricing._aws_line_rate(pricing.RATE_STORAGE, "mars-north-1") is None


# --- GCP --------------------------------------------------------------------


def _gcp_sku(desc, family, regions, nanos):
    return {"description": desc,
            "category": {"usageType": "OnDemand", "resourceFamily": family},
            "serviceRegions": regions,
            "pricingInfo": [{"pricingExpression": {"tieredRates": [
                {"unitPrice": {"units": "0", "nanos": nanos}}]}}]}


def test_gcp_line_rates_need_an_api_key(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_GCP_BILLING_API_KEY", raising=False)
    assert pricing._gcp_line_rate(pricing.RATE_STORAGE, "us-central1") is None


def test_gcp_line_rates_read_storage_licence_and_forwarding_rule_skus(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_GCP_BILLING_API_KEY", "fake-key")
    monkeypatch.setattr(pricing, "_gcp_fetch_skus", lambda key: [
        _gcp_sku("Balanced PD Capacity in Iowa", "Storage", ["us-central1"], 100_000_000),
        # Licence fees are published once, from `global`.
        _gcp_sku("Licensing Fee for Windows Server 2022 Datacenter Edition (CPU cost)",
                 "License", ["global"], 46_000_000),
        _gcp_sku("Network Load Balancing: Forwarding Rule Minimum Service Charge in Iowa",
                 "Network", ["us-central1"], 25_000_000),
    ])
    assert pricing._gcp_line_rate(pricing.RATE_STORAGE, "us-central1") == 0.1
    assert pricing._gcp_line_rate(pricing.RATE_WINDOWS, "us-central1") == 0.046
    assert pricing._gcp_line_rate(pricing.RATE_LB, "us-central1") == 18.25


# --- caching and the breaker ------------------------------------------------


def test_a_live_line_rate_is_fetched_once_then_cached(monkeypatch):
    calls = []
    monkeypatch.setitem(pricing._RATE_FETCHERS, "azure",
                        lambda kind, region: calls.append(kind) or 0.075)
    assert pricing.live_line_rate("azure", pricing.RATE_STORAGE, "eastus") == 0.075
    assert pricing.live_line_rate("azure", pricing.RATE_STORAGE, "eastus") == 0.075
    assert calls == [pricing.RATE_STORAGE]


def test_failing_line_rates_do_not_open_the_compute_breaker(monkeypatch):
    monkeypatch.setitem(pricing._RATE_FETCHERS, "aws", lambda kind, region: None)
    for _ in range(pricing.BREAKER_THRESHOLD + 2):
        pricing.live_line_rate("aws", pricing.RATE_LB, "us-east-1")
    assert pricing.breaker_open("aws:rates")
    assert not pricing.breaker_open("aws")


def test_clouds_without_a_live_source_never_fetch():
    assert pricing.live_line_rate("oci", pricing.RATE_STORAGE, "us-ashburn-1") is None
    assert pricing.live_line_rate("digitalocean", pricing.RATE_LB, "nyc3") is None


# --- the estimate ------------------------------------------------------------


def _boom(*_a, **_k):
    raise AssertionError("live rates fetched when they were not requested")


def test_a_static_plan_never_asks_for_live_rates(realistic_vms, monkeypatch):
    monkeypatch.setattr(pricing, "live_line_rate", _boom)
    costs = estimate_costs(_plan(realistic_vms, "aws", live_requested=False))
    assert set(costs.rate_sources.values()) == {"static"}
    assert costs.pricing_basis == "on-demand list price, no committed-use discount applied"


def test_live_rates_replace_the_catalog_and_say_so(realistic_vms, monkeypatch):
    live = {pricing.RATE_STORAGE: 0.05, pricing.RATE_WINDOWS: 0.05, pricing.RATE_LB: 20.0}
    monkeypatch.setattr(pricing, "live_line_rate", lambda cloud, kind, region: live[kind])
    costs = estimate_costs(_plan(realistic_vms, "azure", live_requested=True))

    assert costs.storage == round(costs.total_storage_gib * 0.05, 2)
    assert costs.load_balancers == round(costs.load_balancer_count * 20.0, 2)
    assert set(costs.rate_sources.values()) == {"live"}
    assert "live rates for storage, Windows licensing, load balancers" in costs.pricing_basis
    assert "no committed-use discount" in costs.pricing_basis
    assert costs.total == round(costs.compute + costs.storage
                                + costs.windows_licensing + costs.load_balancers, 2)


def test_a_missing_live_rate_falls_back_for_that_line_only(realistic_vms, monkeypatch):
    monkeypatch.setattr(pricing, "live_line_rate",
                        lambda cloud, kind, region: 20.0 if kind == pricing.RATE_LB else None)
    costs = estimate_costs(_plan(realistic_vms, "aws", live_requested=True))

    assert costs.rate_sources == {pricing.RATE_STORAGE: "static",
                                  pricing.RATE_WINDOWS: "static",
                                  pricing.RATE_LB: "live"}
    assert costs.storage == round(costs.total_storage_gib * _STORAGE_USD_PER_GB_MONTH["aws"], 2)
    assert "live rates for load balancers" in costs.pricing_basis
    assert "catalog rates (August 2026) for storage, Windows licensing" in costs.pricing_basis


def test_a_total_blackout_is_stated_not_hidden(realistic_vms, monkeypatch):
    monkeypatch.setattr(pricing, "live_line_rate", lambda cloud, kind, region: None)
    costs = estimate_costs(_plan(realistic_vms, "gcp", live_requested=True))
    assert set(costs.rate_sources.values()) == {"static"}
    assert "requested but unavailable" in costs.pricing_basis
