"""Data-transfer and cutover-window estimation.

The plan said what to build and never how long it takes to fill. These tests
mostly guard the direction of error: an optimistic transfer estimate gets written
into a cutover plan and discovered at 3am on a Sunday.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target
from iactranslate.transfer import (
    DEFAULT_LINK_SHARE,
    OFFLINE_THRESHOLD_TIB,
    WAN_EFFICIENCY,
    effective_throughput_mbps,
    estimate_transfer,
    transfer_hours,
)
from iactranslate.waves import plan_waves


@pytest.fixture(scope="module")
def plan():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    return build_migration_plan(vms, "transfer-test", get_target("aws"))


@pytest.fixture(scope="module")
def waves(plan):
    return plan_waves(plan)


# --- the arithmetic ----------------------------------------------------------

def test_throughput_is_derated_not_taken_at_line_rate():
    """A '1 Gbps' link does not move 1 Gbps of payload across a WAN."""
    assert effective_throughput_mbps(1000) == pytest.approx(1000 * WAN_EFFICIENCY * DEFAULT_LINK_SHARE)
    assert effective_throughput_mbps(1000) < 1000


def test_transfer_time_matches_a_hand_calculation():
    """1024 GiB at 1000 Mbps of *payload* is ~2.44 hours."""
    hours = transfer_hours(1024, 1000)
    assert hours == pytest.approx(1024 * 8589.934592 / 1000 / 3600, rel=1e-6)
    assert 2.4 < hours < 2.5


def test_a_dead_link_does_not_divide_by_zero():
    assert transfer_hours(100, 0) == float("inf")


def test_more_bandwidth_is_proportionally_faster(plan):
    slow = estimate_transfer(plan, link_mbps=1000)
    fast = estimate_transfer(plan, link_mbps=10000)
    # Reported hours are rounded to 2dp for presentation, so compare within that
    # rather than exactly: 74.04/10 is 7.404, stored as 7.4.
    assert fast.total_hours == pytest.approx(slow.total_hours / 10, abs=0.01)


# --- what it counts ----------------------------------------------------------

def test_every_attached_disk_counts_not_just_the_root(plan):
    est = estimate_transfer(plan, link_mbps=1000)
    root_only = sum(c.root_volume_gib for c in plan.compute)
    assert est.total_gib > root_only


def test_wave_totals_reconcile_with_the_estate_total(plan, waves):
    """Waves partition the estate; if they did not sum, one of them is wrong."""
    est = estimate_transfer(plan, link_mbps=1000, waves=waves)
    assert sum(w.gib for w in est.waves) == pytest.approx(est.total_gib, abs=1.0)


def test_no_waves_still_gives_an_estate_estimate(plan):
    est = estimate_transfer(plan, link_mbps=1000)
    assert est.waves == []
    assert est.total_hours > 0


# --- the findings someone acts on -------------------------------------------

def test_waves_that_exceed_the_cutover_window_are_flagged(plan, waves):
    est = estimate_transfer(plan, link_mbps=200, cutover_window_hours=48, waves=waves)
    over = [w for w in est.waves if not w.fits_window]
    assert over, "a 200 Mbps link should not move this estate in 48h per wave"
    assert any("exceed the 48h cutover window" in w for w in est.warnings)
    assert all(w.hours > 48 for w in over)


def test_a_fat_link_fits_the_window(plan, waves):
    est = estimate_transfer(plan, link_mbps=10000, cutover_window_hours=48, waves=waves)
    assert all(w.fits_window for w in est.waves)
    assert not any("cutover window" in w for w in est.warnings)


def test_no_window_means_no_window_warnings(plan, waves):
    est = estimate_transfer(plan, link_mbps=200, waves=waves)
    assert all(w.fits_window for w in est.waves), "no window means nothing to exceed"


def test_very_large_estates_are_told_to_ship_disks(plan, monkeypatch):
    """Past a point the wire time approaches the shipping time of an appliance."""
    big = plan.model_copy(deep=True)
    for c in big.compute:
        c.root_volume_gib = 4096
        c.extra_volumes_gib = [4096, 4096]
    est = estimate_transfer(big, link_mbps=1000)
    assert est.total_tib >= OFFLINE_THRESHOLD_TIB
    assert est.recommend_offline_transfer
    assert any("Snowball" in w or "Data Box" in w for w in est.warnings)


def test_saturating_the_link_is_called_out(plan):
    est = estimate_transfer(plan, link_mbps=1000, link_share=0.95)
    assert any("whole link" in w for w in est.warnings)


# --- honesty about its own inputs -------------------------------------------

def test_the_estimate_states_its_assumptions(plan):
    est = estimate_transfer(plan, link_mbps=1000)
    joined = " ".join(est.assumptions).lower()
    assert "allocated capacity" in joined, "must say it counts provisioned, not used, disk"
    assert "delta" in joined, "must say the cutover re-sync is excluded"
    assert "efficiency" in joined


def test_it_errs_long_not_short(plan):
    """Sanity: the effective rate is always below the nominal link, so the
    estimate can only be pessimistic relative to a naive line-rate calculation."""
    est = estimate_transfer(plan, link_mbps=1000)
    naive = transfer_hours(est.total_gib, 1000)
    assert est.total_hours > naive


def test_transfer_never_mutates_the_plan(plan, waves):
    """An analysis engine reads the plan and changes nothing (ADR 0007)."""
    before = plan.model_dump_json()
    estimate_transfer(plan, link_mbps=1000, cutover_window_hours=12, waves=waves)
    assert plan.model_dump_json() == before
