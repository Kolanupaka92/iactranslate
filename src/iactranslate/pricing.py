"""Pricing provider — static (default) or live market pricing.

Mirrors the LLM-provider pattern so the product stays offline, deterministic, and
credential-free by default:

  - ``static`` (default): use the curated per-cloud catalog rates. No network.
  - ``live``: fetch real prices, cache them on disk, and **fall back to static on
    any failure** (no network, no credentials, API error, unknown SKU).

Live coverage:
  * Azure — public Retail Prices API, **no credentials**. Works out of the box.
  * AWS   — Price List Query API via boto3, when boto3 + AWS credentials exist.
  * GCP   — not yet wired (Cloud Billing Catalog needs an API key); falls back
            to static.

Enable with ``IACTRANSLATE_PRICING=live``.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

HOURS_PER_MONTH = 730
_CACHE_TTL_S = 24 * 3600
_HTTP_TIMEOUT = 8

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
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 — fixed host
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


def _gcp_hourly(instance_type: str, region: str) -> Optional[float]:
    # Cloud Billing Catalog requires an API key and complex SKU matching; not
    # yet wired. Falls back to static. (Documented as the next pricing target.)
    return None


_FETCHERS = {"azure": _azure_hourly, "aws": _aws_hourly, "gcp": _gcp_hourly}


def live_hourly(cloud: str, instance_type: str, region: str) -> Optional[float]:
    """Cached live USD/hour, or None if unavailable."""
    key = f"{cloud}:{region}:{instance_type}"
    cache = _cache_load()
    cached = _cache_get(cache, key)
    if cached is not None:
        return cached
    fetch = _FETCHERS.get(cloud)
    price = fetch(instance_type, region) if fetch else None
    if price is not None:
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
