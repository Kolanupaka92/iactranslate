"""Raw VM records -> validated `NormalizedVM` list.

Handles unit coercion (MiB -> GiB), IP parsing, and de-duplication by VM name.
Accepts the raw dicts produced by either parser (RVTools or flat VMware CSV).
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

from .models import NormalizedVM

MIB_PER_GIB = 1024.0

# Floor for a workload reporting zero or missing memory. Mirrors the
# `max(1, ...)` clamp already applied to vCPU: bad data must not lose the
# machine, and `NormalizedVM.memory_gib` is constrained `> 0`.
MIN_MEMORY_GIB = 1.0


def _to_int(value: object, default: int = 1) -> int:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    # A spreadsheet cell reading "Inf"/"NaN" parses as a float but cannot be
    # made an int: `int(round(inf))` raises OverflowError, which used to escape
    # `normalize()` and fail the whole upload over one bad row. pandas also
    # produces NaN for blank numeric cells, so this is ordinary input.
    if not math.isfinite(number):
        return default
    try:
        return max(1, int(round(number)))
    except (OverflowError, ValueError):
        return default


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # NaN/inf would propagate into sizing and cost arithmetic and surface to a
    # customer as "nan" in a report.
    return number if math.isfinite(number) else None


def _util_pct(value: object) -> Optional[float]:
    """Parse a utilization percent (accepts '22', '22%', 0.22 as fraction)."""
    if value is None:
        return None
    text = str(value).strip().rstrip("%").strip()
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    if 0 < v <= 1:  # a fraction like 0.22 -> 22%
        v *= 100
    if 0 <= v <= 100:
        return round(v, 2)
    return None


def _mib_to_gib(mib: Optional[float]) -> Optional[float]:
    if mib is None:
        return None
    return round(mib / MIB_PER_GIB, 2)


def _memory_gib(rec: Dict[str, object]) -> float:
    # RVTools path: memory reported in MiB.
    if "memory_mib" in rec:
        gib = _mib_to_gib(_to_float(rec.get("memory_mib")))
        if gib:
            return gib
    # CSV path: value + unit.
    val = _to_float(rec.get("memory_value"))
    if val is not None:
        unit = str(rec.get("memory_unit", "gib")).lower()
        gib = round(val / MIB_PER_GIB, 2) if unit == "mib" else round(val, 2)
        # A row reporting *zero* memory is the same data-quality problem as a
        # row reporting none — templates, powered-off shells, and half-filled
        # CMDB rows all produce it. The floor applies to both: `memory_gib` is
        # constrained `> 0`, so returning 0.0 raised a ValidationError out of
        # `normalize()` and one bad row failed the entire upload.
        return gib if gib > 0 else MIN_MEMORY_GIB
    return MIN_MEMORY_GIB


def _disks_gib(rec: Dict[str, object]) -> List[float]:
    # RVTools path: list of MiB values.
    if "disks_mib" in rec:
        out = []
        for d in rec.get("disks_mib", []) or []:
            gib = _mib_to_gib(_to_float(d))
            if gib and gib > 0:
                out.append(gib)
        if out:
            return out
    # CSV path: single value + unit.
    val = _to_float(rec.get("disk_value"))
    if val is not None and val > 0:
        unit = str(rec.get("disk_unit", "gib")).lower()
        gib = round(val / MIB_PER_GIB, 2) if unit == "mib" else round(val, 2)
        return [gib]
    return []


def _parse_ips(value: object) -> List[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[,\s;]+", text)
    ips = []
    for p in parts:
        p = p.strip()
        # keep only IPv4-looking tokens
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", p):
            ips.append(p)
    return ips


def _clean_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Characters that are harmless in a hostname but dangerous once the value is
# written into generated code. Braces are stripped outright rather than just
# the `${`/`%{` pairs: dropping only the pairs leaves stray braces that make
# generated code confusing to read and review, and no real hostname has them.
# Quotes and backslashes break out of string literals in HCL, Python
# (Pulumi/CDK), JSON (CloudFormation) and YAML (Kubernetes); backticks and
# `$(` are shell substitution; angle brackets matter in HTML reports.
_UNSAFE_IN_GENERATED_CODE = re.compile(r"""[\x00-\x1f\x7f"'\\`<>{}]|\$\(""")


def sanitize_identifier(value: str) -> str:
    """Strip characters that would let inventory data inject into generated code.

    This is the single choke point protecting **every** renderer. The tool's
    whole job is turning an untrusted file into code someone runs, so a name
    like `x-${file("/etc/passwd")}` must never reach a template: Terraform
    evaluates `${...}` *inside* string literals, so the value doesn't even need
    to escape its quotes to be executed at plan time.

    Fixing this in `normalize` rather than in six template languages means one
    correct implementation instead of six chances to get HCL, Python, JSON,
    YAML, and Bicep escaping right — consistent with `NormalizedVM` being the
    narrow waist every renderer reads from (ADR 0002).

    Legitimate hostnames pass through untouched; RFC-valid hostnames contain
    none of these characters.
    """
    cleaned = _UNSAFE_IN_GENERATED_CODE.sub("-", value)
    # Normalize *all* whitespace — including Unicode forms like U+0085 (NEL)
    # and U+00A0 (NBSP) that the control-character class above doesn't cover —
    # before trimming. Without this the function was not idempotent: `.strip()`
    # removed those characters while the regex didn't, so a second pass could
    # shorten the result again. That matters because a name that changes on
    # re-run changes the Terraform resource label, and Terraform treats a
    # renamed resource as destroy-and-recreate.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Collapse the runs of dashes a substitution can leave behind, then trim
    # dashes and spaces together so neither can strand the other.
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("- ")
    return cleaned or "unnamed"


def _safe_str(value: object) -> Optional[str]:
    """`_clean_str` plus injection sanitizing, for values that reach templates."""
    text = _clean_str(value)
    return sanitize_identifier(text) if text is not None else None


def normalize(records: List[Dict[str, object]]) -> List[NormalizedVM]:
    seen: Dict[str, NormalizedVM] = {}
    for rec in records:
        name = _safe_str(rec.get("name"))
        if not name:
            continue

        # Every free-text field below is attacker-influenced (it came out of an
        # uploaded file) and ends up inside generated code or a report, so all
        # of them go through the same sanitizer — not just the name.
        vm = NormalizedVM(
            vm_name=name,
            cpu=_to_int(rec.get("cpus")),
            memory_gib=_memory_gib(rec),
            disks_gib=_disks_gib(rec),
            cpu_util_pct=_util_pct(rec.get("cpu_util_pct")),
            mem_util_pct=_util_pct(rec.get("mem_util_pct")),
            network=_safe_str(rec.get("network")),
            os=_safe_str(rec.get("os")),
            power_state=_safe_str(rec.get("powerstate")),
            ip_addresses=_parse_ips(rec.get("ip")),
            hostname=_safe_str(rec.get("dns_name")),
            cluster=_safe_str(rec.get("cluster")),
            datacenter=_safe_str(rec.get("datacenter")),
            external_id=_safe_str(rec.get("external_id")),
        )
        # De-dupe by name; prefer the record with more disk detail.
        existing = seen.get(vm.vm_name)
        if existing is None or vm.total_disk_gib > existing.total_disk_gib:
            seen[vm.vm_name] = vm

    return list(seen.values())
