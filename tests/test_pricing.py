"""Pricing provider: static default (offline/deterministic) + live with fallback.

Live fetchers are mocked so the suite never hits the network.
"""
import iactranslate.pricing as pricing
from iactranslate.pipeline import run_pipeline


def test_static_is_the_default(rvtools_path, tmp_path, monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_PRICING", raising=False)
    r = run_pipeline(input_path=rvtools_path, project_name="s", out_dir=str(tmp_path / "s"), target="azure")
    assert r.plan.pricing_source == "static"
    assert r.plan.total_estimated_monthly_cost_usd == 3004.68  # unchanged catalog rates


def test_monthly_cost_static_ignores_live_flag():
    usd, source = pricing.monthly_cost("aws", "t3.large", "us-east-1", 60.0, live=False)
    assert (usd, source) == (60.0, "static")


def test_live_overrides_static_when_available(monkeypatch):
    monkeypatch.setattr(pricing, "live_hourly", lambda cloud, itype, region: 0.10)
    usd, source = pricing.monthly_cost("azure", "Standard_D4as_v5", "eastus", 999.0, live=True)
    assert source == "live"
    assert usd == round(0.10 * pricing.HOURS_PER_MONTH, 2)  # 73.0, not the 999 static


def test_live_falls_back_to_static_on_failure(monkeypatch):
    # Simulate no network / unknown SKU -> None -> static.
    monkeypatch.setattr(pricing, "live_hourly", lambda cloud, itype, region: None)
    usd, source = pricing.monthly_cost("gcp", "e2-standard-4", "us-central1", 97.82, live=True)
    assert (usd, source) == (97.82, "static")


def test_live_pipeline_uses_mocked_prices(rvtools_path, tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_PRICING", "live")
    monkeypatch.setattr(pricing, "live_hourly", lambda cloud, itype, region: 0.05)
    r = run_pipeline(input_path=rvtools_path, project_name="l", out_dir=str(tmp_path / "l"), target="azure")
    assert r.plan.pricing_source == "live"
    assert all(c.price_source == "live" for c in r.plan.compute)
    # every instance priced at 0.05/hr -> 7 * 36.5
    assert r.plan.total_estimated_monthly_cost_usd == round(7 * 0.05 * pricing.HOURS_PER_MONTH, 2)


def test_gcp_hourly_needs_api_key(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_GCP_BILLING_API_KEY", raising=False)
    assert pricing._gcp_hourly("e2-standard-4", "us-central1") is None


def test_gcp_family_parsing():
    assert pricing._gcp_family("e2-standard-4") == "E2"
    assert pricing._gcp_family("n2d-highmem-8") == "N2D"
    assert pricing._gcp_family("n2-standard-16") == "N2"


def test_gcp_sku_unit_price_math():
    sku = {"pricingInfo": [{"pricingExpression": {"tieredRates": [
        {"unitPrice": {"units": "0", "nanos": 21810000}},
    ]}}]}
    assert abs(pricing._gcp_sku_unit_price(sku) - 0.02181) < 1e-9


def test_gcp_find_price_matches_region_and_family():
    skus = [
        {"description": "N2 Instance Core running in Mumbai",
         "category": {"usageType": "OnDemand", "resourceFamily": "Compute"},
         "serviceRegions": ["asia-south1"],
         "pricingInfo": [{"pricingExpression": {"tieredRates": [
             {"unitPrice": {"units": "0", "nanos": 31000000}}]}}]},
        {"description": "N2 Instance Ram running in Mumbai",
         "category": {"usageType": "OnDemand", "resourceFamily": "Compute"},
         "serviceRegions": ["asia-south1"],
         "pricingInfo": [{"pricingExpression": {"tieredRates": [
             {"unitPrice": {"units": "0", "nanos": 4000000}}]}}]},
    ]
    core = pricing._gcp_find_price(skus, "asia-south1", "N2 Instance Core running")
    ram = pricing._gcp_find_price(skus, "asia-south1", "N2 Instance Ram running")
    assert core == 0.031 and ram == 0.004
    # Wrong region → no match.
    assert pricing._gcp_find_price(skus, "us-central1", "N2 Instance Core running") is None


def test_gcp_hourly_sums_core_and_ram(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_GCP_BILLING_API_KEY", "fake-key")
    # 4 vCPU / 16 GiB e2-standard-4 → 4*core + 16*ram.
    monkeypatch.setattr(pricing, "_gcp_fetch_skus", lambda key: ["sentinel"])
    prices = iter([0.021811, 0.002923])  # core, ram
    monkeypatch.setattr(pricing, "_gcp_find_price", lambda skus, region, phrase: next(prices))
    hourly = pricing._gcp_hourly("e2-standard-4", "us-central1")
    assert hourly is not None
    assert abs(hourly - (4 * 0.021811 + 16 * 0.002923)) < 1e-6


def test_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("IACTRANSLATE_PRICE_CACHE", str(tmp_path / "px.json"))
    calls = {"n": 0}

    def fake_azure(itype, region):
        calls["n"] += 1
        return 0.2

    monkeypatch.setitem(pricing._FETCHERS, "azure", fake_azure)
    a = pricing.live_hourly("azure", "Standard_D4as_v5", "eastus")
    b = pricing.live_hourly("azure", "Standard_D4as_v5", "eastus")  # served from cache
    assert a == b == 0.2
    assert calls["n"] == 1  # fetched once, cached thereafter
