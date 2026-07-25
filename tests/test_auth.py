"""Bearer-token auth (ADR 0025) — off by default, real when configured.

Not OIDC/SSO; a stopgap that closes "anyone with network access can read or
modify any project" without pretending to be enterprise identity.
"""
from fastapi.testclient import TestClient

from iactranslate.api.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    r = client.post("/projects", json={"name": "no-auth-needed", "target": "aws"})
    assert r.status_code == 201


def test_request_without_token_is_rejected_when_key_is_set(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_API_KEY", "topsecret")
    try:
        r = client.post("/projects", json={"name": "should-fail", "target": "aws"})
        assert r.status_code == 401
    finally:
        monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)


def test_request_with_wrong_token_is_rejected(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_API_KEY", "topsecret")
    try:
        r = client.post(
            "/projects", json={"name": "should-fail", "target": "aws"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401
    finally:
        monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)


def test_request_with_correct_token_succeeds(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_API_KEY", "topsecret")
    try:
        r = client.post(
            "/projects", json={"name": "should-pass", "target": "aws"},
            headers={"Authorization": "Bearer topsecret"},
        )
        assert r.status_code == 201
    finally:
        monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)


def test_health_targets_policies_stay_open_even_with_a_key_set(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_API_KEY", "topsecret")
    try:
        assert client.get("/health").status_code == 200
        assert client.get("/targets").status_code == 200
        assert client.get("/policies").status_code == 200
    finally:
        monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
