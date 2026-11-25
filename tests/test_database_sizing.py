"""A database is sized by what it has, not by being a database (ADR 0085).

Every database and cache used to be forced into the memory-optimized family, so
a 2 GiB database landed on a 16 GiB machine.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import Tier
from iactranslate.normalize import normalize
from iactranslate.targets import get_target, list_targets
from iactranslate.targets.base import InstanceSpec, smallest_fit

PRICED = ["aws", "azure", "gcp", "oci", "digitalocean"]


def _size(target, cpus, gib, name="prod-db-01"):
    vms = normalize([{"name": name, "cpus": cpus, "memory_mib": gib * 1024, "disks_mib": [20480],
                      "os": "Ubuntu Linux (64-bit)"}])
    plan = build_migration_plan(vms, "x", get_target(target)).compute[0]
    return plan, get_target(target).spec_of(plan.instance_type)


@pytest.mark.parametrize("target,expected", [
    ("aws", "m5.large"), ("azure", "Standard_D2as_v5"), ("gcp", "e2-standard-2")])
def test_a_small_database_gets_a_small_machine(target, expected):
    plan, spec = _size(target, 1, 2)
    assert plan.tier == Tier.DATABASE
    assert plan.instance_type == expected and spec.memory_gib <= 8


@pytest.mark.parametrize("target,expected", [
    ("aws", "r5.xlarge"), ("azure", "Standard_E4as_v5"), ("gcp", "n2-highmem-4")])
def test_a_memory_heavy_database_still_gets_a_memory_heavy_machine(target, expected):
    assert _size(target, 3, 24)[0].instance_type == expected


@pytest.mark.parametrize("name", ["prod-db-01", "prod-redis-01"])
@pytest.mark.parametrize("target", list_targets())
@pytest.mark.parametrize("cpus,gib", [(1, 1), (1, 2), (2, 4), (2, 12), (4, 16), (6, 48)])
def test_databases_and_caches_fit_and_never_share_cpu(target, cpus, gib, name):
    plan, spec = _size(target, cpus, gib, name)
    assert plan.tier in (Tier.DATABASE, Tier.CACHE)
    assert not spec.shared_cpu
    assert spec.vcpu >= cpus and spec.memory_gib >= gib


@pytest.mark.parametrize("target", PRICED)
@pytest.mark.parametrize("cpus,gib", [(1, 2), (2, 4), (2, 12), (4, 16), (4, 32), (6, 48)])
def test_nothing_steady_that_fits_is_cheaper(target, cpus, gib):
    plan, spec = _size(target, cpus, gib)
    t = get_target(target)
    others = [t.spec_of(n) for n in t.instance_names()]
    assert spec.on_demand_usd_hr == min(
        o.on_demand_usd_hr for o in others
        if not o.shared_cpu and o.vcpu >= cpus and o.memory_gib >= gib)


def test_a_web_server_may_still_share_cpu():
    assert _size("aws", 1, 1, "prod-web-01")[1].shared_cpu


def test_shared_cpu_is_used_when_nothing_steady_fits():
    catalog = [InstanceSpec("big-shared", 8, 64, "s", 1.0, shared_cpu=True), InstanceSpec("small", 2, 8, "m", 0.5)]
    assert smallest_fit(catalog, 4, 32, steady_cpu=True).name == "big-shared"
