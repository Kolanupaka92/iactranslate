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

The same three sources also supply the bill's non-compute line rates —
storage, Windows licensing, load balancers — via ``live_line_rate`` (ADR 0069).

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


def _azure_items(flt: str) -> Optional[list]:
    """Items matching an OData filter on the Retail Prices API. None on failure."""
    url = "https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode({"$filter": flt})
    try:
        # Scheme and host are literal `https://prices.azure.com`; only query
        # parameters vary and they are urlencoded. No file:/ or custom scheme
        # is reachable. (nosec: B310 cannot see the scheme is constant.)
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310  # nosec B310
            data = json.load(resp)
    except Exception:  # noqa: BLE001
        return None
    return data.get("Items", [])


def _azure_vm_hourly(instance_type: str, region: str, windows: bool) -> Optional[float]:
    items = _azure_items(
        "serviceName eq 'Virtual Machines' "
        f"and armRegionName eq '{region}' "
        f"and armSkuName eq '{instance_type}' "
        "and priceType eq 'Consumption'"
    )
    if items is None:
        return None
    prices = [
        item["retailPrice"]
        for item in items
        if item.get("unitOfMeasure") == "1 Hour"
        and ("Windows" in item.get("productName", "")) == windows
        and "Spot" not in item.get("skuName", "")
        and "Low Priority" not in item.get("skuName", "")
        and item.get("retailPrice")
    ]
    return min(prices) if prices else None


def _azure_hourly(instance_type: str, region: str) -> Optional[float]:
    return _azure_vm_hourly(instance_type, region, windows=False)


def _aws_first_usd(
    service: str, filters: Dict[str, str], unit: Optional[str] = None, max_results: int = 1
) -> Optional[float]:
    """First positive on-demand USD price among matching products, or None.

    `unit` restricts the price dimension (an ALB product carries both an `Hrs`
    and an `LCU-Hrs` dimension, and only the first is the per-balancer charge).
    """
    try:
        import boto3  # optional dependency; graceful if absent

        client = boto3.client("pricing", region_name="us-east-1")
        resp = client.get_products(
            ServiceCode=service,
            Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in filters.items()],
            MaxResults=max_results,
        )
        for blob in resp.get("PriceList", []):
            product = json.loads(blob)
            on_demand = product["terms"]["OnDemand"]
            for term in on_demand.values():
                for dim in term["priceDimensions"].values():
                    if unit is not None and dim.get("unit") != unit:
                        continue
                    usd = float(dim["pricePerUnit"]["USD"])
                    if usd > 0:
                        return usd
    except Exception:  # noqa: BLE001 — no boto3 / no creds / API error -> static
        return None
    return None


def _aws_ec2_hourly(instance_type: str, location: str, windows: bool) -> Optional[float]:
    filters = {
        "instanceType": instance_type,
        "location": location,
        "operatingSystem": "Windows" if windows else "Linux",
        "tenancy": "Shared",
        "preInstalledSw": "NA",
        "capacitystatus": "Used",
    }
    if windows:
        # AWS's term for license-included Windows ("Bring your own license"
        # is the BYOL variant, which carries no licence uplift at all).
        filters["licenseModel"] = "No License required"
    return _aws_first_usd("AmazonEC2", filters)


def _aws_hourly(instance_type: str, region: str) -> Optional[float]:
    location = _AWS_LOCATION.get(region)
    if not location:
        return None
    return _aws_ec2_hourly(instance_type, location, windows=False)


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


def _gcp_find_price(
    skus: list, region: str, phrase: str, resource_family: str = "Compute"
) -> Optional[float]:
    """First on-demand SKU in `region` whose description contains `phrase`.

    A SKU served from `global` counts as served from every region — licence
    fees are published that way, not per region.
    """
    for sku in skus:
        cat = sku.get("category", {})
        if cat.get("usageType") != "OnDemand" or cat.get("resourceFamily") != resource_family:
            continue
        regions = sku.get("serviceRegions", [])
        if region not in regions and "global" not in regions:
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
# Live line rates — the bill's non-compute lines (see costing.py)
#
# Each fetcher returns the rate in the unit costing multiplies by, or None:
#   storage            USD per GB-month of block storage
#   windows_licensing  USD per vCPU-hour, licence-included Windows over Linux
#   load_balancers     USD per balancer-month, excluding capacity units
#
# Windows is published as a *VM price*, not a licence price, on AWS and Azure.
# The uplift is derived from one reference instance — Windows rate minus Linux
# rate, divided by its vCPUs — which is exactly how the catalog figure of
# $0.046/vCPU-hour was arrived at, and it is linear across a family.
# --------------------------------------------------------------------------- #

RATE_STORAGE = "storage"
RATE_WINDOWS = "windows_licensing"
RATE_LB = "load_balancers"
LINE_RATES = (RATE_STORAGE, RATE_WINDOWS, RATE_LB)

#: Azure prices Standard SSD per disk tier, not per GB; every tier works out to
#: the same per-GiB rate, so one tier divided by its size gives it.
_AZURE_DISK_TIER = ("E10 LRS", 128)
_AZURE_WINDOWS_REF = ("Standard_D2s_v5", 2)
_AWS_WINDOWS_REF = ("m5.large", 2)


def _uplift(windows_hr: Optional[float], linux_hr: Optional[float], vcpu: int) -> Optional[float]:
    if windows_hr is None or linux_hr is None or windows_hr <= linux_hr:
        return None
    return round((windows_hr - linux_hr) / vcpu, 6)


def _azure_line_rate(kind: str, region: str) -> Optional[float]:
    if kind == RATE_STORAGE:
        sku, size_gib = _AZURE_DISK_TIER
        items = _azure_items(
            "serviceName eq 'Storage' "
            f"and armRegionName eq '{region}' "
            f"and skuName eq '{sku}' and meterName eq '{sku} Disk' "
            "and priceType eq 'Consumption'"
        )
        # Checked here as well as in the filter: the same SKU also carries a
        # per-month "Disk Mount" meter, roughly a tenth of the capacity price.
        prices = [i["retailPrice"] for i in items or []
                  if i.get("meterName") == f"{sku} Disk"
                  and i.get("unitOfMeasure") == "1/Month" and i.get("retailPrice")]
        return round(min(prices) / size_gib, 6) if prices else None
    if kind == RATE_WINDOWS:
        ref, vcpu = _AZURE_WINDOWS_REF
        return _uplift(_azure_vm_hourly(ref, region, windows=True),
                       _azure_vm_hourly(ref, region, windows=False), vcpu)
    if kind == RATE_LB:
        # Standard Load Balancer is billed from the `Global` region, not the
        # region the balancer lives in.
        items = _azure_items(
            "serviceName eq 'Load Balancer' and armRegionName eq 'Global' "
            "and meterName eq 'Standard Included LB Rules and Outbound Rules' "
            "and priceType eq 'Consumption'"
        )
        prices = [i["retailPrice"] for i in items or []
                  if i.get("unitOfMeasure") == "1 Hour" and i.get("retailPrice")]
        return round(min(prices) * HOURS_PER_MONTH, 2) if prices else None
    return None


def _aws_line_rate(kind: str, region: str) -> Optional[float]:
    location = _AWS_LOCATION.get(region)
    if not location:
        return None
    if kind == RATE_STORAGE:
        return _aws_first_usd(
            "AmazonEC2", {"productFamily": "Storage", "volumeApiName": "gp3", "location": location}
        )
    if kind == RATE_WINDOWS:
        ref, vcpu = _AWS_WINDOWS_REF
        return _uplift(_aws_ec2_hourly(ref, location, windows=True),
                       _aws_ec2_hourly(ref, location, windows=False), vcpu)
    if kind == RATE_LB:
        hourly = _aws_first_usd(
            "AWSELB", {"productFamily": "Load Balancer-Application", "location": location},
            unit="Hrs", max_results=10,
        )
        return round(hourly * HOURS_PER_MONTH, 2) if hourly is not None else None
    return None


def _gcp_line_rate(kind: str, region: str) -> Optional[float]:
    api_key = os.getenv("IACTRANSLATE_GCP_BILLING_API_KEY")
    if not api_key or kind not in LINE_RATES:
        return None
    skus = _gcp_fetch_skus(api_key)
    if not skus:
        return None
    if kind == RATE_STORAGE:
        return _gcp_find_price(skus, region, "Balanced PD Capacity", "Storage")
    if kind == RATE_WINDOWS:
        return _gcp_find_price(skus, region, "Datacenter Edition (CPU cost)", "License")
    hourly = _gcp_find_price(skus, region, "Forwarding Rule Minimum Service Charge", "Network")
    return round(hourly * HOURS_PER_MONTH, 2) if hourly is not None else None


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


_RATE_FETCHERS = {"azure": _azure_line_rate, "aws": _aws_line_rate, "gcp": _gcp_line_rate}


def live_line_rate(cloud: str, kind: str, region: str) -> Optional[float]:
    """Cached live rate for one non-compute line (see `LINE_RATES`), or None.

    Same cache and same breaker behaviour as `live_hourly`, but a breaker of its
    own: these lookups hit different products than the instance prices, and a
    line rate that keeps failing must not push the next run's compute prices
    onto catalog rates too.
    """
    key = f"{cloud}:{region}:rate:{kind}"
    cache = _cache_load()
    cached = _cache_get(cache, key)
    if cached is not None:
        return cached
    fetch = _RATE_FETCHERS.get(cloud)
    breaker = f"{cloud}:rates"
    if fetch is None or kind not in LINE_RATES or breaker_open(breaker):
        return None
    rate = fetch(kind, region)
    if rate is None:
        _record_failure(breaker)
        return None
    _record_success(breaker)
    _cache_put(cache, key, rate)
    return rate


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
