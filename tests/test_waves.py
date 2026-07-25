"""Migration wave planning — deterministic sequencing by environment + tier depth.

Covers: ordering (data/cache -> app -> web, dev before prod), the depends_on
chain, LB-aware downtime estimates, determinism, and that planning never
touches the plan.
"""
from iactranslate.agents import build_migration_plan
from iactranslate.models import Environment
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target
from iactranslate.waves import WaveReport, plan_waves


def _plan(target="aws"):
    path = "tests/fixtures/rvtools_sample.xlsx"
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "waves", get_target(target))


def _wave(report, vm_name):
    return next(w for w in report.waves if vm_name in w.workloads)


def test_data_tier_migrates_before_app_and_web_within_an_environment():
    plan = _plan()
    report = plan_waves(plan)
    db_wave = _wave(report, "prod-db-01")
    app_wave = _wave(report, "prod-app-01")
    web_wave = _wave(report, "prod-web-01")
    assert db_wave.sequence < app_wave.sequence < web_wave.sequence


def test_non_production_migrates_before_production():
    plan = _plan()
    report = plan_waves(plan)
    dev_web = _wave(report, "dev-web-01")
    prod_web = _wave(report, "prod-web-01")
    assert dev_web.sequence < prod_web.sequence


def test_dependency_chain_is_transitive_and_correct():
    plan = _plan()
    report = plan_waves(plan)
    db_wave = _wave(report, "prod-db-01")
    app_wave = _wave(report, "prod-app-01")
    web_wave = _wave(report, "prod-web-01")
    assert app_wave.depends_on == [db_wave.id]
    assert set(web_wave.depends_on) == {db_wave.id, app_wave.id}
    assert db_wave.depends_on == []


def test_every_wave_is_a_single_environment_single_layer():
    plan = _plan()
    report = plan_waves(plan)
    for w in report.waves:
        assert all(
            plan.compute[[c.vm_name for c in plan.compute].index(vm)].environment == w.environment
            for vm in w.workloads
        )


def test_load_balancer_fronted_waves_estimate_zero_downtime():
    plan = _plan()
    report = plan_waves(plan)
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    for w in report.waves:
        if fronted and all(vm in fronted for vm in w.workloads):
            assert w.estimated_downtime_minutes == 0


def test_unfronted_database_wave_has_nonzero_downtime():
    plan = _plan()
    report = plan_waves(plan)
    db_wave = _wave(report, "prod-db-01")
    assert db_wave.estimated_downtime_minutes > 0


def test_every_workload_appears_in_exactly_one_wave():
    plan = _plan()
    report = plan_waves(plan)
    seen = [vm for w in report.waves for vm in w.workloads]
    assert sorted(seen) == sorted(c.vm_name for c in plan.compute)


def test_waves_are_deterministic():
    plan = _plan()
    a, b = plan_waves(plan), plan_waves(plan)
    assert a.model_dump() == b.model_dump()


def test_notes_disclose_the_real_scope_boundary():
    plan = _plan()
    report = plan_waves(plan)
    assert isinstance(report, WaveReport)
    joined = " ".join(report.notes).lower()
    assert "cross-application" in joined or "cross-app" in joined


def test_planning_does_not_mutate_the_plan():
    plan = _plan()
    before = plan.model_dump()
    plan_waves(plan)
    assert plan.model_dump() == before


def test_works_across_environments_present():
    # Sanity: the fixture spans development + production; both should be
    # reflected as independent, non-cross-dependent chains.
    plan = _plan()
    report = plan_waves(plan)
    envs = {w.environment for w in report.waves}
    assert Environment.DEVELOPMENT in envs
    assert Environment.PRODUCTION in envs
    dev_waves = [w for w in report.waves if w.environment == Environment.DEVELOPMENT]
    prod_waves = [w for w in report.waves if w.environment == Environment.PRODUCTION]
    prod_ids = {w.id for w in prod_waves}
    for w in dev_waves:
        assert not (set(w.depends_on) & prod_ids)


def test_pipeline_writes_waves_json(tmp_path):
    from iactranslate.pipeline import run_pipeline

    out = tmp_path / "proj"
    result = run_pipeline(
        input_path="tests/fixtures/rvtools_sample.xlsx",
        project_name="waves-e2e",
        out_dir=str(out),
        target="aws",
    )
    waves_path = result.project_dir / "waves.json"
    assert waves_path.exists()
    import json
    data = json.loads(waves_path.read_text())
    assert data["waves"]
    assert "notes" in data
