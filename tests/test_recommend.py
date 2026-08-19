import pytest
from fastapi.testclient import TestClient

from iactranslate.api.main import app
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.recommend import recommend
from iactranslate.targets import list_targets


def test_recommend_scores_all_clouds(rvtools_path):
    vms = normalize(parse(rvtools_path))
    rec = recommend(vms)
    assert {s.cloud for s in rec.ranked} == set(list_targets())
    # Ranked best-first; the winner matches `recommended`.
    assert rec.ranked[0].cloud == rec.recommended
    assert all(
        rec.ranked[i].weighted_score >= rec.ranked[i + 1].weighted_score
        for i in range(len(rec.ranked) - 1)
    )
    # Every cloud has a rationale and a real cost.
    for s in rec.ranked:
        assert s.reasons
        assert s.total_monthly_cost_usd > 0
        assert 0.0 <= s.cost_score <= 1.0


def test_cheapest_cloud_gets_cost_score_one(rvtools_path):
    vms = normalize(parse(rvtools_path))
    rec = recommend(vms)
    cheapest = min(rec.ranked, key=lambda s: s.total_monthly_cost_usd)
    assert cheapest.cost_score == 1.0


def test_windows_heavy_estate_favors_azure_os_score():
    # An all-Windows estate should give Azure the top OS-affinity score.
    vms = [
        NormalizedVM(vm_name=f"win-{i}", cpu=4, memory_gib=16, os="Windows Server 2022")
        for i in range(4)
    ]
    rec = recommend(vms)
    by_cloud = {s.cloud: s for s in rec.ranked}
    assert by_cloud["azure"].os_score > by_cloud["gcp"].os_score
    assert by_cloud["azure"].windows_vms == 4


def test_recommendation_2_0_fields(rvtools_path):
    vms = normalize(parse(rvtools_path))
    rec = recommend(vms)
    # Decisiveness reflects the margin band.
    assert rec.decisiveness in {"clear", "moderate", "close"}
    assert rec.margin >= 0.0
    if len(rec.ranked) > 1:
        expected = round(rec.ranked[0].weighted_score - rec.ranked[1].weighted_score, 4)
        assert abs(rec.margin - expected) < 1e-6
    # Annualized cost = 12x monthly, per cloud.
    for s in rec.ranked:
        assert abs(s.annual_cost_usd - s.total_monthly_cost_usd * 12) < 0.01
    # At least the cost-spread note is always present.
    assert rec.notes and any("Annual spend" in n for n in rec.notes)


def test_api_recommend_flow(rvtools_path):
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "rec", "target": "aws"}).json()["id"]
    with open(rvtools_path, "rb") as f:
        client.post(f"/projects/{pid}/upload", files={"file": ("rvtools_sample.xlsx", f)})
    r = client.post(f"/projects/{pid}/recommend")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recommended"] in list_targets()
    assert len(body["ranked"]) == len(list_targets())


# -- the weighting must be inspectable (ADR 0037) ---------------------------


def test_weights_are_returned_so_the_ranking_can_be_checked_by_hand(rvtools_path):
    """"Weights are explicit and inspectable" is a stated design principle and
    the main reason to trust this over a cloud vendor's own tool — but they
    lived only as module constants, so the one question an architect asks
    ("how are you weighting this?") needed a source dive."""
    from iactranslate.normalize import normalize
    from iactranslate.recommend import W_COST, W_FIT, W_OS, recommend
    from iactranslate.sources import resolve_source

    vms = normalize(resolve_source(rvtools_path, "auto").parse(rvtools_path))
    rec = recommend(vms)

    assert (rec.weights.cost, rec.weights.fit, rec.weights.os) == (W_COST, W_FIT, W_OS)
    assert round(rec.weights.cost + rec.weights.fit + rec.weights.os, 6) == 1.0

    # Every reported score must be reproducible from the published weights.
    for s in rec.ranked:
        recomputed = (
            rec.weights.cost * s.cost_score
            + rec.weights.fit * s.fit_score
            + rec.weights.os * s.os_score
        )
        assert round(recomputed, 4) == pytest.approx(s.weighted_score, abs=1e-4)


def test_margin_names_the_cloud_it_is_measured_against(rvtools_path):
    """"margin 0.06" alone says nothing — not against whom."""
    from iactranslate.normalize import normalize
    from iactranslate.recommend import recommend
    from iactranslate.sources import resolve_source

    vms = normalize(resolve_source(rvtools_path, "auto").parse(rvtools_path))
    rec = recommend(vms)

    assert rec.runner_up == rec.ranked[1].cloud
    assert rec.runner_up != rec.recommended
    assert rec.margin == pytest.approx(
        rec.ranked[0].weighted_score - rec.ranked[1].weighted_score, abs=1e-4
    )
