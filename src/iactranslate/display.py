"""Human-readable names and grammar for customer-facing output.

The internal identifiers are lower-case slugs — `vmware`, `aws`, `digitalocean`
— because that is what a registry key should be. They are not what a company
calls itself, and a report that a consultant puts in front of their client
should not read "vmware → aws".

Likewise `workload(s)`: the parenthetical plural is a shortcut that says the
document was written by a program. A report that says "1 workloads" is worse,
so the fix is to inflect properly rather than to drop the marker.
"""
from __future__ import annotations

from typing import Optional

_CLOUD_NAMES = {
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "Google Cloud",
    "oci": "Oracle Cloud (OCI)",
    "digitalocean": "DigitalOcean",
}

_SOURCE_NAMES = {
    "vmware": "VMware",
    "hyperv": "Microsoft Hyper-V",
    "kubernetes": "Kubernetes",
    "cloud": "existing cloud fleet",
    "cmdb": "CMDB export",
    "generic": "CMDB / spreadsheet export",
    "unknown": "inventory export",
}


def display_cloud(name: Optional[str]) -> str:
    """`aws` -> `AWS`, `gcp` -> `Google Cloud`."""
    if not name:
        return "the target cloud"
    return _CLOUD_NAMES.get(name.strip().lower(), name.upper())


def display_source(name: Optional[str]) -> str:
    """`vmware` -> `VMware`, `hyperv` -> `Microsoft Hyper-V`."""
    if not name:
        return "inventory export"
    key = name.strip().lower()
    return _SOURCE_NAMES.get(key, name.replace("_", " ").title())


def plural(count: int, noun: str, plural_form: Optional[str] = None) -> str:
    """`3, "workload"` -> `"3 workloads"`; `1, "workload"` -> `"1 workload"`."""
    word = noun if count == 1 else (plural_form or f"{noun}s")
    return f"{count:,} {word}"
