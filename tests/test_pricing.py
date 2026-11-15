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


# --- circuit breaker ---------------------------------------------------------
#
# The per-call fallback was already correct. What it was not is *cheap*: a
# corporate network that blackholes the billing endpoints made every distinct
# instance type pay the full socket timeout, every run.

import pytest  # noqa: E402

from iactranslate.agents import build_migration_plan  # noqa: E402
from iactranslate.normalize import normalize  # noqa: E402
from iactranslate.parsers import parse  # noqa: E402
from iactranslate.targets import get_target  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_breakers():
    """Endpoint health is process-wide state, so it must not leak between tests."""
    pricing.reset_breakers()
    yield
    pricing.reset_breakers()


class _CountingEndpoint:
    """A fetcher that records how often it was actually called."""

    def __init__(self, price=None):
        self.calls = 0
        self._price = price

    def __call__(self, instance_type, region):
        self.calls += 1
        return self._price


def _price_many(cloud, endpoint, count, monkeypatch, tmp_path):
    monkeypatch.setattr(pricing, "_FETCHERS", {cloud: endpoint})
    monkeypatch.setenv("IACTRANSLATE_PRICE_CACHE", str(tmp_path / "cache.json"))
    for i in range(count):
        pricing.live_hourly(cloud, f"type-{i}", "us-east-1")


def test_a_dead_endpoint_is_called_a_bounded_number_of_times(monkeypatch, tmp_path):
    """The defect this exists for: 20 instance types meant 20 socket timeouts."""
    endpoint = _CountingEndpoint(price=None)
    _price_many("aws", endpoint, 20, monkeypatch, tmp_path)
    assert endpoint.calls == pricing.BREAKER_THRESHOLD


def test_a_healthy_endpoint_is_never_short_circuited(monkeypatch, tmp_path):
    endpoint = _CountingEndpoint(price=0.10)
    _price_many("aws", endpoint, 20, monkeypatch, tmp_path)
    assert endpoint.calls == 20
    assert not pricing.breaker_open("aws")


def test_one_success_clears_a_partial_failure_streak(monkeypatch, tmp_path):
    """A blip must not spend the estate's real prices."""
    monkeypatch.setenv("IACTRANSLATE_PRICE_CACHE", str(tmp_path / "c.json"))
    prices = [None, None, 0.10, None, None]
    monkeypatch.setattr(pricing, "_FETCHERS", {"aws": lambda t, r: prices.pop(0)})
    for i in range(5):
        pricing.live_hourly("aws", f"type-{i}", "us-east-1")
    assert not pricing.breaker_open("aws"), "two failures either side of a success"


def test_the_breaker_is_per_cloud(monkeypatch, tmp_path):
    """Azure being blocked says nothing about AWS."""
    dead, alive = _CountingEndpoint(None), _CountingEndpoint(0.10)
    monkeypatch.setenv("IACTRANSLATE_PRICE_CACHE", str(tmp_path / "c.json"))
    monkeypatch.setattr(pricing, "_FETCHERS", {"azure": dead, "aws": alive})
    for i in range(10):
        pricing.live_hourly("azure", f"t{i}", "eastus")
        pricing.live_hourly("aws", f"t{i}", "us-east-1")
    assert pricing.breaker_open("azure")
    assert not pricing.breaker_open("aws")
    assert alive.calls == 10


def test_an_open_breaker_lets_one_probe_through_after_the_reset(monkeypatch, tmp_path):
    endpoint = _CountingEndpoint(price=None)
    _price_many("aws", endpoint, 10, monkeypatch, tmp_path)
    assert pricing.breaker_open("aws")
    monkeypatch.setattr(pricing, "BREAKER_RESET_S", 0.0)
    assert not pricing.breaker_open("aws"), "half-open after the window"


def test_a_cached_price_is_served_even_with_the_breaker_open(monkeypatch, tmp_path):
    """A price already on disk is just as good whether or not the endpoint is up."""
    cache = tmp_path / "c.json"
    monkeypatch.setenv("IACTRANSLATE_PRICE_CACHE", str(cache))
    monkeypatch.setattr(pricing, "_FETCHERS", {"aws": _CountingEndpoint(0.10)})
    assert pricing.live_hourly("aws", "m5.large", "us-east-1") == 0.10
    monkeypatch.setattr(pricing, "_FETCHERS", {"aws": _CountingEndpoint(None)})
    for i in range(10):
        pricing.live_hourly("aws", f"other-{i}", "us-east-1")
    assert pricing.breaker_open("aws")
    assert pricing.live_hourly("aws", "m5.large", "us-east-1") == 0.10


def test_falling_back_still_produces_catalog_prices(monkeypatch, tmp_path):
    """Degrading must never mean failing."""
    _price_many("aws", _CountingEndpoint(None), 5, monkeypatch, tmp_path)
    usd, source = pricing.monthly_cost("aws", "m5.large", "us-east-1", 70.08, live=True)
    assert (usd, source) == (70.08, "static")


# --- honest reporting of degradation -----------------------------------------


def _plan(monkeypatch, sources):
    """A plan whose workloads carry the given price_source values."""
    vms = normalize(parse("tests/fixtures/rvtools_sample.xlsx"))
    calls = {"n": 0}

    def _cost(cloud, itype, region, static, live):
        i = calls["n"]
        calls["n"] += 1
        source = sources[i % len(sources)]
        return (99.0, "live") if source == "live" else (static, "static")

    import iactranslate.agents.rightsizing as rs
    monkeypatch.setattr(rs, "monthly_cost", _cost)
    return build_migration_plan(vms, "p", get_target("aws"), live_pricing=True)


def test_one_live_price_no_longer_reports_the_estate_as_live_priced(monkeypatch):
    """The old property returned 'live' if *any* instance got a live price, so an
    estate that fell back on all but one was presented as market-priced — and a
    customer builds a business case on that number."""
    plan = _plan(monkeypatch, ["live"] + ["static"] * 20)
    assert plan.pricing_source == "degraded"
    assert plan.pricing_source_degraded


def test_a_fully_live_estate_still_reports_live(monkeypatch):
    plan = _plan(monkeypatch, ["live"])
    assert plan.pricing_source == "live"
    assert not plan.pricing_source_degraded


def test_a_total_blackout_is_still_flagged(monkeypatch):
    """Every workload falls back, so the plan looks exactly like one that never
    wanted live prices. Only the requested mode tells them apart."""
    plan = _plan(monkeypatch, ["static"])
    assert plan.pricing_source == "static"
    assert plan.pricing_source_degraded


def test_static_by_choice_is_not_degraded(rvtools_path, tmp_path, monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_PRICING", raising=False)
    r = run_pipeline(input_path=rvtools_path, project_name="s2",
                     out_dir=str(tmp_path / "s2"), target="azure")
    assert not r.plan.pricing_source_degraded
