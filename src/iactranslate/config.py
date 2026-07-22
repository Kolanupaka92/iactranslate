"""Runtime configuration and resource limits (env-overridable).

Enterprise infrastructure data is sensitive and uploads are attacker-influenced,
so the API enforces hard limits to bound memory, disk, and CPU per request.
"""
from __future__ import annotations

import os
from typing import List

# Reject uploads larger than this (protects against memory-exhaustion DoS).
MAX_UPLOAD_BYTES: int = int(os.getenv("IACTRANSLATE_MAX_UPLOAD_MB", "25")) * 1024 * 1024

# Reject inventories with more VMs than this (bounds plan/output size + CPU).
MAX_VMS: int = int(os.getenv("IACTRANSLATE_MAX_VMS", "5000"))

# Cap the in-memory project store; oldest projects are evicted (their temp
# workspaces are deleted) beyond this to bound disk usage.
MAX_PROJECTS: int = int(os.getenv("IACTRANSLATE_MAX_PROJECTS", "200"))


def _target_utilization() -> float:
    """Desired utilization of the *target* instance (0 < u <= 1).

    When source utilization data is present, workloads are sized so the chosen
    instance runs at ~this utilization (e.g. 0.65 → size to demand / 0.65,
    leaving ~35% headroom). Default 0.65 is a common right-sizing target.
    """
    try:
        u = float(os.getenv("IACTRANSLATE_TARGET_UTILIZATION", "0.65"))
    except ValueError:
        return 0.65
    return u if 0.1 <= u <= 1.0 else 0.65


TARGET_UTILIZATION: float = _target_utilization()


def cors_origins() -> List[str]:
    """Allowed CORS origins from IACTRANSLATE_CORS_ORIGINS (comma-separated).

    Empty (default) means no cross-origin access. Use "*" to allow all (dev only).
    """
    raw = os.getenv("IACTRANSLATE_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]
