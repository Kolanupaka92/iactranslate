"""AI (Anthropic) provider wiring — CLI, API, and the honest provider_used record.

The provider itself (classify/rightsize via Claude) is exercised in
test_agents.py-style unit tests against RuleEngineProvider; these tests cover
the *reachability* gap: CLI flag, API field, and MigrationPlan.provider_used
correctly reporting what actually ran (never silently claiming AI when it
fell back — see agents/providers/__init__.py::get_provider).
"""
import subprocess
import sys

from fastapi.testclient import TestClient

from iactranslate.agents import build_migration_plan
from iactranslate.agents.providers import get_provider
from iactranslate.agents.providers.rule_engine import RuleEngineProvider
from iactranslate.api.main import app
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target

client = TestClient(app, raise_server_exceptions=False)


def _plan(provider=None, target="aws"):
    path = "tests/fixtures/rvtools_sample.xlsx"
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "ai", get_target(target), provider=provider)


def test_default_provider_used_is_rule():
    plan = _plan()
    assert plan.provider_used == "rule"


def test_explicit_rule_provider_records_rule(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan = _plan(provider=get_provider(get_target("aws"), name="rule"))
    assert plan.provider_used == "rule"


def test_requesting_anthropic_without_a_key_falls_back_to_rule(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = get_provider(get_target("aws"), name="anthropic")
    assert isinstance(provider, RuleEngineProvider)  # get_provider's own honest fallback
    plan = _plan(provider=provider)
    assert plan.provider_used == "rule"


def test_cli_provider_flag_reports_fallback_honestly(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "iactranslate.cli", "translate",
         "tests/fixtures/rvtools_sample.xlsx", "--target", "aws",
         "--out", str(out), "--provider", "anthropic"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "rule engine (deterministic)" in result.stdout
    assert "fell back" in result.stdout


def test_cli_rejects_unknown_provider(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "iactranslate.cli", "translate",
         "tests/fixtures/rvtools_sample.xlsx", "--target", "aws",
         "--out", str(tmp_path / "out"), "--provider", "openai"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0


def test_api_create_project_accepts_provider_field():
    r = client.post("/projects", json={"name": "ai-test", "target": "aws", "provider": "anthropic"})
    assert r.status_code == 201
    assert r.json()["provider"] == "anthropic"


def test_api_create_project_rejects_unknown_provider():
    r = client.post("/projects", json={"name": "ai-test-bad", "target": "aws", "provider": "openai"})
    assert r.status_code == 422


def test_api_default_provider_is_rule():
    r = client.post("/projects", json={"name": "ai-default", "target": "aws"})
    assert r.json()["provider"] == "rule"


def test_api_run_reports_honest_provider_used(rvtools_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pid = client.post(
        "/projects", json={"name": "ai-run", "target": "aws", "provider": "anthropic"}
    ).json()["id"]
    with open(rvtools_path, "rb") as f:
        client.post(f"/projects/{pid}/upload", files={"file": ("rvtools_sample.xlsx", f)})
    r = client.post(f"/projects/{pid}/run")
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["provider_requested"] == "anthropic"
    assert result["provider_used"] == "rule"  # honest fallback, no API key in this env
