"""Deterministic network planning: subnets, security groups, load balancers."""
from iactranslate.agents import build_migration_plan
from iactranslate.models import SubnetTier
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(path="tests/fixtures/rvtools_sample.xlsx", target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "n", get_target(target))


def test_multi_instance_tiers_get_a_load_balancer():
    plan = _plan()
    by_name = {lb.name: lb for lb in plan.network.load_balancers}
    assert "production-web-lb" in by_name
    assert "production-app-lb" in by_name
    web = by_name["production-web-lb"]
    assert web.internet_facing
    assert web.subnet_tier == SubnetTier.PUBLIC
    assert sorted(web.targets) == ["prod-web-01", "prod-web-02"]


def test_load_balancer_listeners_come_from_the_security_groups_ingress():
    plan = _plan()
    web = next(lb for lb in plan.network.load_balancers if lb.name == "production-web-lb")
    ports = sorted(item.listener_port for item in web.listeners)
    sg = next(sg for sg in plan.network.security_groups if sg.name == web.security_group)
    assert ports == sorted({r.from_port for r in sg.ingress})


def test_https_listener_protocol_is_derived_from_port_443():
    plan = _plan()
    web = next(lb for lb in plan.network.load_balancers if lb.name == "production-web-lb")
    protocol_by_port = {item.listener_port: item.protocol for item in web.listeners}
    assert protocol_by_port[443] == "HTTPS"
    assert protocol_by_port[80] == "HTTP"


def test_single_instance_groups_get_no_load_balancer():
    plan = _plan()
    # dev-web-01 and dev-db-01 are the only members of their (tier, env) groups.
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    assert "dev-web-01" not in fronted
    assert "dev-db-01" not in fronted


def test_load_balancers_are_deterministic():
    a, b = _plan(), _plan()
    assert [lb.model_dump() for lb in a.network.load_balancers] == [
        lb.model_dump() for lb in b.network.load_balancers
    ]


def test_load_balancers_work_for_every_target():
    for cloud in ("aws", "azure", "gcp", "oci"):
        plan = _plan(target=cloud)
        assert plan.network.load_balancers, f"expected load balancers for {cloud}"
