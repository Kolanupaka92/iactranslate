"""Landing-zone conformance: deploying into an account someone already governs.

Output that violates a customer's guardrails is worse than no output — it fails
on apply, after the review has already passed.
"""
import ipaddress

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.landing_zone import (
    DEFAULT_CIDR,
    LandingZone,
    LandingZoneError,
    carve_subnets,
    merge_tags,
)
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target, list_targets

IPAM_RANGE = "172.20.8.0/21"


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_sample.xlsx"))


# --- configuration validation ------------------------------------------------

def test_default_zone_matches_the_historical_behaviour():
    assert LandingZone().cidr == DEFAULT_CIDR
    assert LandingZone().is_default


def test_invalid_cidr_is_rejected_at_configuration_time():
    with pytest.raises(LandingZoneError):
        LandingZone(cidr="not-a-cidr")


def test_a_cidr_too_small_to_subnet_is_rejected_with_the_fix():
    """Better here than as a provider error thousands of resources later."""
    with pytest.raises(LandingZoneError) as e:
        LandingZone(cidr="192.168.1.0/25")
    assert "/23 or larger" in str(e.value)


def test_host_bits_set_is_rejected():
    """`10.0.0.5/16` is a common typo for a network range."""
    with pytest.raises(LandingZoneError):
        LandingZone(cidr="10.0.0.5/16")


def test_ipv6_is_rejected_rather_than_half_supported():
    with pytest.raises(LandingZoneError):
        LandingZone(cidr="2001:db8::/32")


def test_unportable_tag_keys_are_rejected():
    with pytest.raises(LandingZoneError):
        LandingZone(tags={"cost centre!": "x"})


def test_overlong_tag_values_are_rejected():
    with pytest.raises(LandingZoneError):
        LandingZone(tags={"Owner": "x" * 257})


def test_invalid_name_prefix_is_rejected():
    with pytest.raises(LandingZoneError):
        LandingZone(name_prefix="1-starts-with-a-digit")


# --- subnet carving ----------------------------------------------------------

def test_subnets_are_carved_from_the_supplied_range():
    public, private = carve_subnets(IPAM_RANGE, 2)
    parent = ipaddress.ip_network(IPAM_RANGE)
    for cidr in public + private:
        assert ipaddress.ip_network(cidr).subnet_of(parent)


def test_public_and_private_subnets_never_overlap():
    public, private = carve_subnets(IPAM_RANGE, 2)
    nets = [ipaddress.ip_network(c) for c in public + private]
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            assert not a.overlaps(b)


def test_carving_a_range_that_cannot_fit_the_tiers_raises():
    with pytest.raises(LandingZoneError):
        carve_subnets("10.0.0.0/24", 2)


# --- the bug this feature would otherwise have exposed -----------------------

def test_subnets_stay_inside_the_vpc(vms):
    """Subnets were hardcoded to 10.0.{az}.0/24 while the VPC took its CIDR from
    the target — so any other range put every subnet outside its own VPC. Latent
    only because every target happened to use 10.0.0.0/16."""
    plan = build_migration_plan(
        vms, "lz", get_target("aws"), zone=LandingZone(cidr=IPAM_RANGE)
    )
    vpc = ipaddress.ip_network(plan.network.vpc_cidr)
    assert str(vpc) == IPAM_RANGE
    for subnet in plan.network.subnets:
        assert ipaddress.ip_network(subnet.cidr).subnet_of(vpc), (
            f"{subnet.name} ({subnet.cidr}) falls outside the VPC {vpc}"
        )


def test_internal_ingress_follows_the_vpc_range(vms):
    """Otherwise the rules permit a range the VPC does not use, and deny the one
    it does — over-permissive and broken at the same time."""
    plan = build_migration_plan(
        vms, "lz", get_target("aws"), zone=LandingZone(cidr=IPAM_RANGE)
    )
    for sg in plan.network.security_groups:
        for rule in sg.ingress:
            for block in rule.cidr_blocks:
                assert block != DEFAULT_CIDR, (
                    f"{sg.name}/{rule.description} still points at the default range"
                )


def test_public_ingress_is_not_rewritten(vms):
    """0.0.0.0/0 on a public listener is deliberate, not a stale default."""
    plan = build_migration_plan(
        vms, "lz", get_target("aws"), zone=LandingZone(cidr=IPAM_RANGE)
    )
    web = [sg for sg in plan.network.security_groups if sg.name == "web-sg"]
    assert web, "fixture should exercise a web tier"
    assert any("0.0.0.0/0" in r.cidr_blocks for r in web[0].ingress)


def test_every_target_honours_a_custom_range(vms):
    for cloud in list_targets():
        plan = build_migration_plan(
            vms, "lz", get_target(cloud), zone=LandingZone(cidr=IPAM_RANGE)
        )
        vpc = ipaddress.ip_network(plan.network.vpc_cidr)
        assert str(vpc) == IPAM_RANGE, f"{cloud} ignored the landing-zone CIDR"
        for subnet in plan.network.subnets:
            assert ipaddress.ip_network(subnet.cidr).subnet_of(vpc), f"{cloud}: {subnet.cidr}"


def test_no_zone_reproduces_the_previous_output(vms):
    """The default path must be unchanged for anyone not using this feature."""
    a = build_migration_plan(vms, "lz", get_target("aws"))
    b = build_migration_plan(vms, "lz", get_target("aws"), zone=LandingZone())
    assert a.network.model_dump() == b.network.model_dump()


# --- tags --------------------------------------------------------------------

def test_mandated_tags_are_merged_with_our_own():
    tags = merge_tags(LandingZone(tags={"CostCenter": "CC-4417"}), "acme")
    assert tags["CostCenter"] == "CC-4417"
    assert tags["Project"] == "acme"
    assert tags["ManagedBy"] == "IaCTranslate"


def test_customer_tags_win_on_conflict():
    """Their policy is the one that rejects the deployment."""
    tags = merge_tags(LandingZone(tags={"ManagedBy": "PlatformTeam"}), "acme")
    assert tags["ManagedBy"] == "PlatformTeam"


def test_digitalocean_tags_are_encoded_as_strings_not_dropped():
    """DO tags are a flat list; silently dropping mandated tags on one cloud is
    exactly the quiet divergence this tool exists to avoid."""
    zone = LandingZone(tags={"CostCenter": "CC-4417", "Owner": "platform team"})
    assert zone.do_tags() == ["costcenter:cc-4417", "owner:platform-team"]


def test_digitalocean_tags_obey_the_api_charset():
    """`tofu validate` rejects anything outside [a-z0-9:_-] — caught in review
    on `Owner:platform@acme.com`."""
    import re

    zone = LandingZone(tags={"Owner": "platform@acme.com", "Cost.Center": "CC/4417"})
    for tag in zone.do_tags():
        assert re.fullmatch(r"[a-z0-9:_-]+", tag), tag
        assert len(tag) <= 255
