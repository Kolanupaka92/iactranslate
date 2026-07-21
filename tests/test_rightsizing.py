from iactranslate.agents.providers.rule_engine import RuleEngineProvider
from iactranslate.agents.rightsizing import build_compute_plans
from iactranslate.models import Environment, NormalizedVM, Tier
from iactranslate.targets import get_target

AWS = get_target("aws")


def test_smallest_fit_respects_requirements():
    inst = AWS.smallest_fit(vcpu=4, memory_gib=16, headroom=1.0)
    assert inst.vcpu >= 4 and inst.memory_gib >= 16


def test_smallest_fit_prefers_family():
    inst = AWS.smallest_fit(vcpu=2, memory_gib=16, headroom=1.0, prefer_family="r5")
    assert inst.family == "r5"


def test_rule_engine_rightsize_uses_catalog():
    provider = RuleEngineProvider(AWS)
    vm = NormalizedVM(vm_name="db-01", cpu=4, memory_gib=16, os="Windows Server 2022")
    s = provider.rightsize(vm, Tier.DATABASE, Environment.PRODUCTION)
    assert AWS.instance_exists(s.instance_type)
    assert s.image_key == "windows-2022"


def test_build_compute_plans_all_valid():
    provider = RuleEngineProvider(AWS)
    vms = [
        NormalizedVM(vm_name="prod-web-01", cpu=4, memory_gib=16, disks_gib=[200], os="Windows Server 2022"),
        NormalizedVM(vm_name="prod-db-01", cpu=8, memory_gib=64, disks_gib=[100, 500], os="Ubuntu Linux"),
    ]
    tier_env = {
        "prod-web-01": (Tier.WEB, Environment.PRODUCTION),
        "prod-db-01": (Tier.DATABASE, Environment.PRODUCTION),
    }
    plans = build_compute_plans(vms, provider, tier_env, AWS)
    assert len(plans) == 2
    for p in plans:
        assert AWS.instance_exists(p.instance_type)
        assert p.estimated_monthly_cost_usd > 0
    db = next(p for p in plans if p.vm_name == "prod-db-01")
    assert db.extra_volumes_gib == [500]
    assert db.root_volume_gib == 100
