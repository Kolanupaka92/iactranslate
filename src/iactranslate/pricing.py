"""Pricing provider — static (default) or live market pricing.

Mirrors the LLM-provider pattern so the product stays offline, deterministic, and
credential-free by default:

  - ``static`` (default): use the curated per-cloud catalog rates. No network.
  - ``live``: fetch real prices, cache them on disk, and **fall back to static on
    any failure** (no network, no credentials, API error, unknown SKU).

Live coverage:
  * Azure — public Retail Prices API, **no credentials**. Works out of the box.
  * AWS   — Price List Query API via boto3, when boto3 + AWS credentials exist.
  * GCP   — Cloud Billing Catalog API, when ``IACTRANSLATE_GCP_BILLING_API_KEY``
            is set (sums the machine family's vCPU-core + RAM-GB SKUs).

Enable with ``IACTRANSLATE_PRICING=live``.

A **circuit breaker** guards the live path. Falling back per call was already
correct, but it was also per call: in a corporate network that blackholes these
endpoints, a 1,500-workload estate with 20 distinct instance types paid the full
socket timeout twenty times over before finishing, every run. After
``BREAKER_THRESHOLD`` consecutive failures a cloud's fetcher is skipped outright
until ``BREAKER_RESET_S`` has passed, so a blocked endpoint costs one timeout,
not one per SKU.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .observability import get_logger

HOURS_PER_MONTH = 730
_CACHE_TTL_S = 24 * 3600
_HTTP_TIMEOUT = 8

#: Consecutive failures before a cloud's live endpoint is considered down.
#: Three rather than one because a single blip should not cost an estate its
#: real prices for the next five minutes.
BREAKER_THRESHOLD = 3

#: How long an open breaker stays open before one probe is allowed through.
BREAKER_RESET_S = 300.0

_log = get_logger(__name__)

# region -> AWS Price List "location" name (extend as needed).
_AWS_LOCATION = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-central-1": "EU (Frankfurt)",
    "ap-south-1": "Asia Pacific (Mumbai)",
}


def pricing_mode() -> str:
    return (os.getenv("IACTRANSLATE_PRICING") or "static").strip().lower()


def live_enabled() -> bool:
    return pricing_mode() == "live"


# --------------------------------------------------------------------------- #
# On-disk cache: { "cloud:region:instance": [usd_per_hour, epoch_seconds] }
# --------------------------------------------------------------------------- #


def _cache_file() -> Path:
    override = os.getenv("IACTRANSLATE_PRICE_CACHE")
    return Path(override) if override else Path(tempfile.gettempdir()) / "iactranslate_pricing.json"


def _cache_load() -> Dict[str, list]:
    try:
        return json.loads(_cache_file().read_text())
    except Exception:  # noqa: BLE001 — a missing/corrupt cache is not an error
        return {}


def _cache_get(cache: Dict[str, list], key: str) -> Optional[float]:
    entry = cache.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL_S:
        return float(entry[0])
    return None


def _cache_put(cache: Dict[str, list], key: str, price: float) -> None:
    cache[key] = [price, time.time()]
    try:
        _cache_file().write_text(json.dumps(cache))
    except Exception:  # noqa: BLE001 — cache write failure must never break sizing
        pass


# --------------------------------------------------------------------------- #
# Live fetchers — each returns USD/hour or None (None -> caller uses static)
# --------------------------------------------------------------------------- #


def _azure_hourly(instance_type: str, region: str) -> Optional[float]:
    flt = (
        "serviceName eq 'Virtual Machines' "
        f"and armRegionName eq '{region}' "
        f"and armSkuName eq '{instance_type}' "
        "and priceType eq 'Consumption'"
    )
    url = "https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode({"$filter": flt})
    try:
        # Scheme and host are literal `https://prices.azure.com`; only query
        # parameters vary and they are urlencoded. No file:/ or custom scheme
        # is reachable. (nosec: B310 cannot see the scheme is constant.)
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310  # nosec B310
            data = json.load(resp)
    except Exception:  # noqa: BLE001
        return None
    prices = [
        item["retailPrice"]
        for item in data.get("Items", [])
        if item.get("unitOfMeasure") == "1 Hour"
        and "Windows" not in item.get("productName", "")
        and "Spot" not in item.get("skuName", "")
        and "Low Priority" not in item.get("skuName", "")
        and item.get("retailPrice")
    ]
    return min(prices) if prices else None


def _aws_hourly(instance_type: str, region: str) -> Optional[float]:
    location = _AWS_LOCATION.get(region)
    if not location:
        return None
    try:
        import boto3  # optional dependency; graceful if absent

        client = boto3.client("pricing", region_name="us-east-1")
        resp = client.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
            MaxResults=1,
        )
        for blob in resp.get("PriceList", []):
            product = json.loads(blob)
            on_demand = product["terms"]["OnDemand"]
            for term in on_demand.values():
                for dim in term["priceDimensions"].values():
                    usd = float(dim["pricePerUnit"]["USD"])
                    if usd > 0:
                        return usd
    except Exception:  # noqa: BLE001 — no boto3 / no creds / API error -> static
        return None
    return None


# Compute Engine service id in the Cloud Billing Catalog.
_GCP_COMPUTE_SERVICE = "6F81-5844-456A"


def _gcp_family(instance_type: str) -> Optional[str]:
    """'e2-standard-4' -> 'E2', 'n2d-highmem-8' -> 'N2D'. None if unrecognized."""
    head = instance_type.split("-", 1)[0].strip()
    return head.upper() if head else None


def _gcp_sku_unit_price(sku: dict) -> Optional[float]:
    """USD per usage unit (per vCPU-hour or per GB-hour) from a SKU entry."""
    try:
        rate = sku["pricingInfo"][0]["pricingExpression"]["tieredRates"][-1]["unitPrice"]
        return int(rate.get("units", 0)) + int(rate.get("nanos", 0)) / 1e9
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _gcp_find_price(skus: list, region: str, phrase: str) -> Optional[float]:
    """First on-demand SKU in `region` whose description contains `phrase`."""
    for sku in skus:
        cat = sku.get("category", {})
        if cat.get("usageType") != "OnDemand" or cat.get("resourceFamily") != "Compute":
            continue
        if region not in sku.get("serviceRegions", []):
            continue
        desc = sku.get("description", "")
        if phrase in desc and "Sole Tenancy" not in desc and "Custom" not in desc:
            price = _gcp_sku_unit_price(sku)
            if price is not None:
                return price
    return None


def _gcp_fetch_skus(api_key: str) -> list:
    """All Compute Engine SKUs (paginated). [] on any failure."""
    skus: list = []
    page_token = ""
    base = f"https://cloudbilling.googleapis.com/v1/services/{_GCP_COMPUTE_SERVICE}/skus"
    for _ in range(20):  # hard page cap
        params = {"key": api_key, "pageSize": "5000"}
        if page_token:
            params["pageToken"] = page_token
        url = base + "?" + urllib.parse.urlencode(params)
        try:
            # Literal `https://cloudbilling.googleapis.com` host; only query
            # parameters vary. See the note in `_azure_hourly`.
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310  # nosec B310
                data = json.load(resp)
        except Exception:  # noqa: BLE001
            return skus
        skus.extend(data.get("skus", []))
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    return skus


def _gcp_hourly(instance_type: str, region: str) -> Optional[float]:
    api_key = os.getenv("IACTRANSLATE_GCP_BILLING_API_KEY")
    if not api_key:
        return None
    family = _gcp_family(instance_type)
    if not family:
        return None
    try:
        from .targets import get_target  # lazy: recover vCPU/RAM for the machine type

        spec = get_target("gcp").spec_of(instance_type)
    except Exception:  # noqa: BLE001
        return None
    if spec is None:
        return None

    skus = _gcp_fetch_skus(api_key)
    if not skus:
        return None
    core = _gcp_find_price(skus, region, f"{family} Instance Core running")
    ram = _gcp_find_price(skus, region, f"{family} Instance Ram running")
    if core is None or ram is None:
        return None
    return round(spec.vcpu * core + spec.memory_gib * ram, 6)


# --------------------------------------------------------------------------- #
# Circuit breaker
#
# Deliberately *not* tripped on latency. A 1.5s threshold was proposed, and it
# would throw away correct answers: the Azure Retail Prices API legitimately
# takes longer than that under load, and a price that arrives slowly is still a
# real price. Errors and timeouts are what indicate an endpoint is unusable, so
# those are what count. The existing socket timeout already bounds the wait.
# --------------------------------------------------------------------------- #

_breaker_lock = threading.Lock()
#: cloud -> [consecutive_failures, opened_at_monotonic]
_breakers: Dict[str, List[float]] = {}


def reset_breakers() -> None:
    """Forget all endpoint health. For tests and long-lived processes."""
    with _breaker_lock:
        _breakers.clear()


def breaker_open(cloud: str) -> bool:
    """True if `cloud`'s live endpoint should be skipped without trying.

    Once the reset window elapses this returns False *without* clearing the
    failure count — that lets exactly one probe through (half-open). If the
    probe fails the window restarts; if it succeeds the count is cleared.
    """
    with _breaker_lock:
        state = _breakers.get(cloud)
        if state is None or state[0] < BREAKER_THRESHOLD:
            return False
        return (time.monotonic() - state[1]) < BREAKER_RESET_S


def _record_failure(cloud: str) -> None:
    with _breaker_lock:
        state = _breakers.setdefault(cloud, [0.0, 0.0])
        state[0] += 1
        if state[0] >= BREAKER_THRESHOLD:
            newly_open = state[1] == 0.0 or (time.monotonic() - state[1]) >= BREAKER_RESET_S
            state[1] = time.monotonic()
            if newly_open:
                _log.warning(
                    "live pricing unavailable, using catalog rates",
                    extra={"cloud": cloud, "consecutive_failures": int(state[0]),
                           "pricing_source_degraded": True},
                )


def _record_success(cloud: str) -> None:
    with _breaker_lock:
        if _breakers.pop(cloud, None) is not None:
            _log.info("live pricing recovered", extra={"cloud": cloud,
                                                       "pricing_source_degraded": False})


_FETCHERS = {"azure": _azure_hourly, "aws": _aws_hourly, "gcp": _gcp_hourly}


def live_hourly(cloud: str, instance_type: str, region: str) -> Optional[float]:
    """Cached live USD/hour, or None if unavailable.

    The cache is consulted before the breaker: a price already on disk is just as
    good whether or not the endpoint is currently reachable.
    """
    key = f"{cloud}:{region}:{instance_type}"
    cache = _cache_load()
    cached = _cache_get(cache, key)
    if cached is not None:
        return cached
    fetch = _FETCHERS.get(cloud)
    if fetch is None or breaker_open(cloud):
        return None
    price = fetch(instance_type, region)
    if price is None:
        # A fetcher returns None both for "the endpoint is down" and for "this
        # SKU is not in the catalog". Conflating them is the safe direction:
        # an unknown SKU on a healthy endpoint costs at most BREAKER_THRESHOLD
        # lookups before the breaker parks the rest of the run on catalog rates,
        # which is the same answer those lookups were going to produce anyway.
        _record_failure(cloud)
        return None
    _record_success(cloud)
    _cache_put(cache, key, price)
    return price


def monthly_cost(
    cloud: str,
    instance_type: str,
    region: str,
    static_monthly: float,
    live: bool,
) -> Tuple[float, str]:
    """Return (monthly_usd, source) where source is 'live' or 'static'."""
    if live:
        hr = live_hourly(cloud, instance_type, region)
        if hr is not None:
            return round(hr * HOURS_PER_MONTH, 2), "live"
    return static_monthly, "static"
