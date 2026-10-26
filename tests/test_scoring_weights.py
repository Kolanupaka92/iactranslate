"""The recommendation's weights are the caller's to set, and a custom ranking says so.

A regulated estate that is barely cost-sensitive asks a different question from
the default 0.45 / 0.30 / 0.25 weighting. Letting it ask is useful; letting the
answer pass as the tool's neutral recommendation would not be.
"""
import pytest
from fastapi.testclient import TestClient

from iactranslate.api.main import app
from iactranslate.cli import main
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.recommend import W_COST, W_FIT, W_OS, ScoringWeights, recommend


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))


# --- the model -----------------------------------------------------------------


def test_defaults_are_the_published_weights():
    w = ScoringWeights()
    assert (w.cost, w.fit, w.os) == (W_COST, W_FIT, W_OS)
    assert w.is_default


def test_weights_are_scaled_to_sum_to_one():
    w = ScoringWeights(cost=1, fit=2, os=2)
    assert (w.cost, w.fit, w.os) == (0.2, 0.4, 0.4)


def test_negative_or_all_zero_weights_are_rejected():
    with pytest.raises(ValueError):
        ScoringWeights(cost=-0.1)
    with pytest.raises(ValueError, match="greater than zero"):
        ScoringWeights(cost=0, fit=0, os=0)


def test_parse_reads_a_cli_spec_and_keeps_unnamed_defaults():
    assert ScoringWeights.parse("cost=0.2,fit=0.4,os=0.4").cost == 0.2
    w = ScoringWeights.parse("cost=0")
    assert w.cost == 0.0 and round(w.fit + w.os, 4) == 1.0
    assert w.fit > w.os  # 0.30 : 0.25 preserved


@pytest.mark.parametrize("spec,fragment", [
    ("price=1", "expected cost="),
    ("cost=cheap", "must be a number"),
    ("cost=-1", "cost weight"),
    ("cost=0,fit=0,os=0", "greater than zero"),
])
def test_parse_errors_are_one_readable_line(spec, fragment):
    with pytest.raises(ValueError) as e:
        ScoringWeights.parse(spec)
    assert fragment in str(e.value)
    assert "\n" not in str(e.value)


# --- the ranking ----------------------------------------------------------------


def test_scores_use_the_supplied_weights(vms):
    rec = recommend(vms, weights=ScoringWeights(cost=1, fit=0, os=0))
    for s in rec.ranked:
        if s.eligible:
            assert s.weighted_score == s.cost_score


def test_cost_only_weights_recommend_the_cheapest_eligible_cloud(vms):
    rec = recommend(vms, weights=ScoringWeights(cost=1, fit=0, os=0))
    eligible = [s for s in rec.ranked if s.eligible]
    assert rec.recommended == min(eligible, key=lambda s: s.total_monthly_cost_usd).cloud


def test_os_only_weights_follow_the_estate_os_mix(vms):
    # The realistic estate has Windows; OS affinity alone favours Azure for it.
    rec = recommend(vms, weights=ScoringWeights(cost=0, fit=0, os=1))
    assert rec.recommended == max(
        (s for s in rec.ranked if s.eligible), key=lambda s: s.os_score
    ).cloud


def test_eligibility_still_dominates_any_weighting(vms):
    rec = recommend(vms, weights=ScoringWeights(cost=1, fit=0, os=0))
    ineligible = [s for s in rec.ranked if not s.eligible]
    assert all(s.weighted_score == 0.0 for s in ineligible)
    assert rec.ranked[0].eligible


def test_a_custom_ranking_is_labelled_as_one(vms):
    rec = recommend(vms, weights=ScoringWeights(cost=0.1, fit=0.45, os=0.45))
    assert rec.custom_weights
    assert rec.notes[0].startswith("Ranked under custom weights")
    assert "Custom weights" in rec.summary
    assert (rec.weights.cost, rec.weights.fit, rec.weights.os) == (0.1, 0.45, 0.45)


def test_the_default_ranking_is_not_labelled_custom(vms):
    for rec in (recommend(vms), recommend(vms, weights=ScoringWeights(cost=0.45, fit=0.3, os=0.25))):
        assert not rec.custom_weights
        assert not any("custom weights" in n for n in rec.notes)


# --- surfaces ------------------------------------------------------------------


def test_cli_recommend_accepts_weights(capsys):
    assert main(["recommend", "tests/fixtures/rvtools_realistic.xlsx",
                 "--weights", "cost=0,fit=0,os=1"]) == 0
    out = capsys.readouterr().out
    assert "Ranked under custom weights" in out


def test_cli_rejects_bad_weights_as_a_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        main(["recommend", "tests/fixtures/rvtools_realistic.xlsx", "--weights", "cost=-1"])
    assert e.value.code == 2
    assert "cost weight" in capsys.readouterr().err


def _project_with_upload(client):
    pid = client.post("/projects", json={"name": "weights", "target": "aws"}).json()["id"]
    with open("tests/fixtures/rvtools_realistic.xlsx", "rb") as f:
        client.post(f"/projects/{pid}/upload", files={"file": ("rv.xlsx", f)})
    return pid


def test_api_without_a_body_uses_the_defaults():
    client = TestClient(app)
    r = client.post(f"/projects/{_project_with_upload(client)}/recommend")
    assert r.status_code == 200
    assert r.json()["custom_weights"] is False
    assert r.json()["weights"] == {"cost": W_COST, "fit": W_FIT, "os": W_OS}


def test_api_applies_supplied_weights():
    client = TestClient(app)
    r = client.post(f"/projects/{_project_with_upload(client)}/recommend",
                    json={"cost": 1, "fit": 2, "os": 2})
    assert r.status_code == 200
    assert r.json()["custom_weights"] is True
    assert r.json()["weights"] == {"cost": 0.2, "fit": 0.4, "os": 0.4}


def test_api_rejects_invalid_weights():
    client = TestClient(app)
    r = client.post(f"/projects/{_project_with_upload(client)}/recommend",
                    json={"cost": 0, "fit": 0, "os": 0})
    assert r.status_code == 422
