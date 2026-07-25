"""Bearer-token auth for the API — a real, testable stopgap, not OIDC/SSO.

The API ships with **no** authentication by default (matches every prior
behavior — nothing changes for local/dev use unless you opt in), which is
fine for a laptop but not for exposing this beyond localhost. Setting
`IACTRANSLATE_API_KEY` requires every request to carry a matching
`Authorization: Bearer <key>` header.

This is deliberately **not** presented as enterprise SSO. Real OIDC/SAML/RBAC
needs an actual identity provider to integrate against and verify — this repo
has no such provider available to build and test against honestly. A single
shared bearer token is a real, immediately useful improvement over "anyone
with network access can read/modify/delete any project" (zero auth), and is
fully testable without external services — but it is a stopgap, not a
substitute for real multi-user identity when that becomes buildable.
"""
from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request


def api_key_configured() -> bool:
    return bool(os.getenv("IACTRANSLATE_API_KEY", "").strip())


def require_api_key(request: Request) -> None:
    """FastAPI dependency: no-op unless IACTRANSLATE_API_KEY is set."""
    expected = os.getenv("IACTRANSLATE_API_KEY", "").strip()
    if not expected:
        return  # auth disabled — identical to today's behavior

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "missing or invalid bearer token")
