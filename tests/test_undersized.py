"""A machine no catalog size can hold is never shrunk in silence (ADR 0086)."""
from __future__ import annotations

import json

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.agents.base import RightsizeSuggestion
from iactranslate.agents.providers.rule_engine import RuleEngineProvider
from iactranslate.backlog import build_backlog
from iactranslate.cli import main as cli
from iactranslate.confidence import score_plan
from iactranslate.models import NormalizedVM
from iactranslate.packager import migration_summary
from iactranslate.recommend import recommend
from iactranslate.renderers import list_renderers, render, supports
from iactranslate.targets import get_target, list_targets
from iactranslate.targets.base import InstanceSpec, is_undersized, smallest_fit
from iactranslate.waves import plan_waves

TARGETS = list_targets()
HUGE = (400, 9000.0)  # past every catalog


def _vm(cpu, mem, name="big", os="Ubuntu 22.04", **kw):
    return NormalizedVM(vm_name=name, resource_name=name.replace("-", "_"), cpu=cpu, memory_gib=mem,
                        disks_gib=[50], os=os, **kw)


def _plan(target, *vms, **kw):
    return build_migration_plan(list(vms), "acme", get_target(target), **kw)


# --- choosing --------------------------------------------------------------------------


@pytest.mark.parametrize("target,fits", [
    ("aws", (128, 4096)), ("azure", (128, 3892)), ("gcp", (160, 3844)), ("oci", (126, 1024)),
    ("digitalocean", (32, 256)), ("nutanix", (128, 2048)), ("proxmox", (128, 2048))])
def test_the_largest_size_is_used_whole_and_one_more_is_too_much(target, fits):
    for (cpu, mem), undersized in ((fits, False), ((fits[0], fits[1] + 1), True)):
        c = _plan(target, _vm(cpu, mem)).compute[0]
        assert c.undersized is undersized
        assert ("DOES NOT FIT" in c.reason) is undersized
        assert (c.vcpu >= cpu and c.memory_gib >= mem) is not undersized


@pytest.mark.parametrize("target", ["aws", "azure", "gcp", "oci"])
def test_64_vcpu_512_gib_fits_the_big_clouds(target):  # was shrunk to as little as 32/128
    c = _plan(target, _vm(64, 512)).compute[0]
    assert (c.vcpu, c.memory_gib) >= (64, 512) and c.memory_gib >= 512 and not c.undersized


@pytest.mark.parametrize("target", TARGETS)
def test_the_flag_matches_the_numbers_for_every_shape(target):
    for cpu in (1, 3, 8, 17, 40, 64, 100, 150, 300):
        for ratio in (1, 2, 4, 8, 16, 30):
            c = _plan(target, _vm(cpu, float(cpu * ratio))).compute[0]
            assert c.undersized is (c.vcpu < cpu or c.memory_gib < cpu * ratio)
            if c.undersized:
                catalog = [get_target(target).spec_of(n) for n in get_target(target).instance_names()]
                assert all(is_undersized(s, cpu, cpu * ratio) for s in catalog)


@pytest.mark.parametrize("target", TARGETS)
def test_catalogs_have_unique_names_and_a_price_where_priced(target):
    t = get_target(target)
    specs = [t.spec_of(n) for n in t.instance_names()]
    assert len({s.name for s in specs}) == len(specs)
    assert all((s.on_demand_usd_hr > 0) is ("priced" in t.capabilities) for s in specs)


def test_missing_headroom_is_said_and_not_claimed():  # measured 32 / 256, the most there is
    c = _plan("digitalocean", _vm(32, 256, cpu_util_pct=100, mem_util_pct=100)).compute[0]
    assert not c.undersized and "without the usual headroom" in c.reason
    assert not any("% headroom" in a for a in c.decision.assumptions)


def test_the_closest_size_covers_the_most_not_the_most_vcpu():
    catalog = [InstanceSpec("cpu", 96, 100, "f", 1), InstanceSpec("mem", 64, 1000, "f", 9)]
    assert smallest_fit(catalog, 64, 2000).name == "mem"


def test_utilization_decides_whether_it_fits():  # allocated past the catalog, using little
    c = _plan("digitalocean", _vm(128, 1024, cpu_util_pct=10, mem_util_pct=10)).compute[0]
    assert not c.undersized


class _Model(RuleEngineProvider):
    def __init__(self, target, type_):
        super().__init__(target)
        self._type = type_

    def rightsize(self, vm, tier, environment):
        return RightsizeSuggestion(instance_type=self._type, image_key="ubuntu-22.04")


def test_a_models_too_small_choice_is_replaced():
    target = get_target("aws")
    c = _plan("aws", _vm(8, 32), provider=_Model(target, "t3.micro")).compute[0]
    assert c.vcpu >= 8 and not c.undersized
    assert "'t3.micro' is smaller than the workload needs" in c.reason
    assert c.decision.alternatives == ["t3.micro"]


def test_a_models_choice_that_fits_is_kept():
    c = _plan("aws", _vm(2, 4), provider=_Model(get_target("aws"), "m5.large")).compute[0]
    assert c.instance_type == "m5.large" and "Guardrail" not in c.reason


# --- saying so -------------------------------------------------------------------------


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("renderer", list_renderers())
def test_every_readme_says_so_and_only_then(renderer, target):
    if not supports(renderer, target):
        pytest.skip("not a combination")
    t = get_target(target)
    bad = render(renderer, _plan(target, _vm(*HUGE), _vm(2, 4, "small")), t)["README.md"]
    assert bad.startswith("# ") and "1 workload will not fit" in bad and "- `big`:" in bad
    assert "`small`:" not in bad
    assert "will not fit" not in render(renderer, _plan(target, _vm(2, 4)), t)["README.md"]


def test_summary_backlog_and_confidence_say_so():
    vms = [_vm(*HUGE), _vm(2, 4, "small")]
    plan = _plan("aws", *vms)
    assert "**Will not fit:** 1 workload" in migration_summary(plan, vms)
    by_title = {i.title: " ".join(i.description) for i in build_backlog(plan, plan_waves(plan))}
    assert "too small" in by_title["Migrate big"] and "too small" not in by_title["Migrate small"]
    scores = {w.vm_name: w for w in score_plan(plan, vms).workloads}
    assert next(f for f in scores["big"].factors if f.factor == "sizing").score == 0
    assert scores["big"].overall < scores["small"].overall


def test_the_cli_and_decisions_say_so(tmp_path, capsys):
    source = tmp_path / "in.csv"
    source.write_text("name,cpu,memory_gib,disk_gib,os\nbig,400,9000,50,Ubuntu 22.04\nsmall,2,4,50,Ubuntu 22.04\n")
    assert cli(["translate", str(source), "--target", "aws", "--name", "x", "--out", str(tmp_path / "o")]) == 0
    assert "1 VM(s) do not fit any aws size in the catalog: big." in capsys.readouterr().out
    decisions = next((tmp_path / "o").rglob("decisions.json")).read_text()
    assert "DOES NOT FIT" in json.dumps(json.loads(decisions))


# --- recommending ----------------------------------------------------------------------


def _scores(rec):
    return {s.cloud: s for s in rec.ranked}


def test_a_cloud_that_cannot_hold_a_machine_is_not_recommended():
    rec = recommend([_vm(64, 512), _vm(2, 4, "small")])
    do = _scores(rec)["digitalocean"]
    assert not do.eligible and do.undersized_workloads == 1 and do.weighted_score == 0
    assert do.unsupported_workloads == 0 and not any("operating system" in r for r in do.reasons)
    assert any("need more than the largest DIGITALOCEAN size" in r for r in do.reasons)
    assert rec.recommended != "digitalocean" and rec.runner_up != "digitalocean"
    assert any("DIGITALOCEAN was excluded" in n and "smaller workloads" in n for n in rec.notes)
    assert all(s.eligible for c, s in _scores(rec).items() if c != "digitalocean")


def test_a_machine_blocked_twice_counts_once():
    rec = recommend([_vm(64, 512, os="Windows Server 2022"), _vm(2, 4, "small")])
    do = _scores(rec)["digitalocean"]
    assert (do.unsupported_workloads, do.undersized_workloads) == (1, 1)
    assert any("cannot host 1 of 2 workloads" in n for n in rec.notes)


def test_nothing_is_recommended_outright_when_nothing_fits():
    rec = recommend([_vm(*HUGE)])
    assert not any(s.eligible for s in rec.ranked)
    assert rec.summary.startswith("No compared cloud can take every workload")
    assert rec.notes[0].startswith("No compared cloud")


def test_the_api_reports_it():
    from fastapi.testclient import TestClient

    from iactranslate.api.main import app
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "x", "target": "aws"}).json()["id"]
    csv = "name,cpu,memory_gib,disk_gib,os\nbig,400,9000,50,Ubuntu 22.04\nsmall,2,4,50,Ubuntu 22.04\n"
    client.post(f"/projects/{pid}/upload", files={"file": ("in.csv", csv, "text/csv")})
    client.post(f"/projects/{pid}/run")
    result = client.get(f"/projects/{pid}").json()["result"]
    assert result["undersized_count"] == 1
    assert {i["vm"]: i["undersized"] for i in result["instances"]} == {"big": True, "small": False}


def test_among_ineligible_clouds_the_fewest_blocked_comes_first():
    rec = recommend([_vm(*HUGE), _vm(64, 512, "mid")])
    assert rec.ranked[-1].cloud == "digitalocean" and "digitalocean" not in rec.summary.lower()
