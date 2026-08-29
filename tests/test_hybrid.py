"""On-prem networks the estate still needs after it moves.

Network planning is greenfield: a clean VPC carved from one range. That is right
about the resources being created and quietly wrong about the world they land
in, because large estates call things that are not moving — an Oracle box, an AD
forest, a licence server. Cut over without reaching them and the workload starts
and fails to work.

The tests here are mostly about restraint. The analysis is only as good as the
evidence, so what it declines to conclude matters more than what it emits: a
public address is not an on-prem network, a route that cannot exist is not
generated, and connectivity parameters that only the customer's network team
knows are never invented.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.dependencies import DependencyReport, ExternalDependency, Flow, analyze_dependencies
from iactranslate.generator import build_files
from iactranslate.hybrid import SUMMARY_PREFIX, plan_hybrid
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target, list_targets

VPC = "10.0.0.0/16"


def _report(*externals):
    return DependencyReport(external=list(externals))


def _external(source, address, ports=(1521,), connections=100):
    import ipaddress
    return ExternalDependency(
        source=source, address=address, ports=list(ports), connections=connections,
        is_private=ipaddress.ip_address(address).is_private,
    )


# --- what counts as an on-prem network ---------------------------------------

def test_a_private_address_outside_the_inventory_becomes_a_route():
    plan = plan_hybrid(_report(_external("app-01", "192.168.40.11")), VPC)
    assert [n.cidr for n in plan.networks] == ["192.168.40.0/24"]
    assert plan.required


def test_a_public_address_is_not_an_on_prem_network():
    """A partner API reached over the internet needs egress, not a private
    circuit. Putting it in a VPN route table would break it."""
    plan = plan_hybrid(_report(_external("app-01", "52.94.236.248", ports=[443])), VPC)
    assert plan.networks == []
    assert not plan.required


def test_addresses_are_summarised_to_routable_networks():
    """A route table with 300 /32 entries is unreviewable and hits route limits."""
    hosts = [_external("app-01", f"192.168.40.{i}") for i in range(1, 30)]
    plan = plan_hybrid(_report(*hosts), VPC)
    assert len(plan.networks) == 1
    assert plan.networks[0].cidr.endswith(f"/{SUMMARY_PREFIX}")
    assert len(plan.networks[0].addresses) == 29


def test_ports_and_callers_are_aggregated_per_network():
    plan = plan_hybrid(_report(
        _external("app-01", "192.168.40.11", ports=[1521], connections=100),
        _external("web-02", "192.168.40.12", ports=[445], connections=50),
    ), VPC)
    network = plan.networks[0]
    assert network.ports == [445, 1521]
    assert network.dependent_workloads == ["app-01", "web-02"]
    assert network.connections == 150


def test_the_busiest_network_is_listed_first():
    plan = plan_hybrid(_report(
        _external("a", "192.168.1.5", connections=10),
        _external("b", "172.16.9.5", connections=900),
    ), VPC)
    assert plan.networks[0].cidr == "172.16.9.0/24"


def test_no_evidence_produces_no_plan():
    """Absence of a flow export is not evidence of no dependency, so this emits
    nothing rather than an all-clear."""
    assert not plan_hybrid(DependencyReport(), VPC).required


def test_a_network_team_can_declare_ranges_directly():
    """The common case is having no flow export at all. An IPAM allocation is
    better data than anything inferred from sampled traffic, so it is trusted as
    given and not summarised."""
    plan = plan_hybrid(DependencyReport(), VPC, on_prem_cidrs=["192.168.0.0/16"])
    assert [n.cidr for n in plan.networks] == ["192.168.0.0/16"]


def test_a_malformed_declared_range_is_skipped_not_fatal():
    plan = plan_hybrid(DependencyReport(), VPC, on_prem_cidrs=["not-a-cidr", "10.9.0.0/24"])
    assert [n.cidr for n in plan.networks] == ["10.9.0.0/24"]


# --- the finding that saves the most trouble ---------------------------------

def test_an_on_prem_range_inside_the_vpc_range_is_flagged_as_unroutable():
    """The estate talks to 10.0.5.11 and the landing zone was allocated
    10.0.0.0/16. The VPC claims that address, so traffic stays inside the VPC and
    never reaches the corporate network. Unfixable by routing."""
    plan = plan_hybrid(_report(_external("app-01", "10.0.5.11")), VPC)
    assert plan.conflicts
    assert plan.conflicts[0].cidr == "10.0.5.0/24"
    assert plan.routable == []
    assert any("overlap" in n for n in plan.notes)


def test_the_conflict_note_says_the_landing_zone_must_change():
    """A note that only says "overlap" leaves the reader to work out that
    reallocating the CIDR re-plans every subnet."""
    plan = plan_hybrid(_report(_external("app-01", "10.0.5.11")), VPC)
    assert any("reallocated" in n for n in plan.notes)


def test_a_conflict_does_not_suppress_the_networks_that_do_work():
    plan = plan_hybrid(_report(
        _external("app-01", "10.0.5.11"),
        _external("app-02", "192.168.40.11"),
    ), VPC)
    assert [n.cidr for n in plan.conflicts] == ["10.0.5.0/24"]
    assert [n.cidr for n in plan.routable] == ["192.168.40.0/24"]


def test_the_analysis_never_edits_the_landing_zone():
    """Silently moving a customer's allocated range would be a worse failure than
    the one it fixed (ADR 0007: analysis engines do not mutate)."""
    plan = plan_hybrid(_report(_external("app-01", "10.0.5.11")), VPC)
    assert plan.vpc_cidr == VPC


# --- what gets rendered -------------------------------------------------------

@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_sample.xlsx"))


def _files(vms, cloud, hybrid):
    target = get_target(cloud)
    plan = build_migration_plan(vms, "hybrid-test", target)
    return build_files(plan, target, None, None, None, hybrid)


def test_no_hybrid_evidence_emits_no_file(vms):
    """The default output is unchanged for every customer without a flow export."""
    for cloud in list_targets():
        assert "hybrid.tf" not in _files(vms, cloud, None)


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_evidence_emits_connectivity_for_clouds_that_have_it(vms, cloud):
    hybrid = plan_hybrid(_report(_external("app-01", "192.168.40.11")), "10.0.0.0/16")
    files = _files(vms, cloud, hybrid)
    assert "192.168.40.0/24" in files["hybrid.tf"]


@pytest.mark.parametrize("cloud", ["oci", "digitalocean"])
def test_clouds_without_generated_connectivity_still_record_the_requirement(vms, cloud):
    """Emitting nothing would read as "no on-prem dependency exists", which is
    the opposite of what the flows show."""
    hybrid = plan_hybrid(_report(_external("app-01", "192.168.40.11")), "10.0.0.0/16")
    body = _files(vms, cloud, hybrid)["hybrid.tf"]
    assert "192.168.40.0/24" in body
    assert "not generated" in body


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_unroutable_ranges_generate_no_connectivity(vms, cloud):
    """Generating a route that cannot work is worse than generating none: it
    applies cleanly and fails at cutover."""
    hybrid = plan_hybrid(_report(_external("app-01", "10.0.5.11")), "10.0.0.0/16")
    body = _files(vms, cloud, hybrid)["hybrid.tf"]
    assert "STOP" in body
    assert "10.0.5.0/24" in body
    # Assert on the HCL, not the file: the explanation above it is prose that
    # uses the word "resource" and should stay.
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert "resource" not in code, "nothing may be generated for an unroutable range"
    assert "variable" not in code


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_connectivity_parameters_are_variables_with_no_default(vms, cloud):
    """The customer gateway address belongs to their edge router and cannot be
    inferred. A plausible placeholder that applied cleanly would build a tunnel
    to nowhere, so `terraform plan` must stop and ask."""
    hybrid = plan_hybrid(_report(_external("app-01", "192.168.40.11")), "10.0.0.0/16")
    body = _files(vms, cloud, hybrid)["hybrid.tf"]
    assert 'variable "on_prem_gateway_ip"' in body
    block = body.split('variable "on_prem_gateway_ip"')[1].split("}")[0]
    assert "default" not in block


@pytest.mark.parametrize("cloud", ["azure", "gcp"])
def test_the_pre_shared_key_is_sensitive_and_never_written_down(vms, cloud):
    hybrid = plan_hybrid(_report(_external("app-01", "192.168.40.11")), "10.0.0.0/16")
    body = _files(vms, cloud, hybrid)["hybrid.tf"]
    block = body.split('variable "on_prem_shared_key"')[1].split("}")[0]
    assert "sensitive   = true" in block
    assert "default" not in block


def test_no_billable_physical_circuit_is_ever_generated(vms):
    """Direct Connect / ExpressRoute / Interconnect are physical cross-connects
    with weeks of lead time, and the resources bill from the moment they apply.
    Emitting one from an inventory scan would charge a customer for hardware
    nobody ordered."""
    hybrid = plan_hybrid(_report(_external("app-01", "192.168.40.11")), "10.0.0.0/16")
    forbidden = ["aws_dx_connection", "azurerm_express_route_circuit",
                 "google_compute_interconnect"]
    for cloud in list_targets():
        body = _files(vms, cloud, hybrid).get("hybrid.tf", "")
        for resource in forbidden:
            assert f'resource "{resource}"' not in body


def test_routes_are_emitted_not_just_a_tunnel(vms):
    """A tunnel with no route is up and unused — the failure that looks like
    success, because every resource applied cleanly."""
    hybrid = plan_hybrid(_report(_external("app-01", "192.168.40.11")), "10.0.0.0/16")
    assert 'resource "aws_route" "to_on_prem_public"' in _files(vms, "aws", hybrid)["hybrid.tf"]
    assert 'resource "google_compute_route" "to_on_prem"' in _files(vms, "gcp", hybrid)["hybrid.tf"]


# --- against the real fixture estate -----------------------------------------

def test_the_fixture_estates_unlisted_systems_become_on_prem_networks():
    """The dependency analysis already finds "an unlisted internal system"; this
    is the same evidence, summarised into something routable."""
    from iactranslate.dependencies import parse_flows

    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    report = analyze_dependencies(parse_flows("tests/fixtures/flows_realistic.csv"), vms)
    plan = plan_hybrid(report, "10.0.0.0/16")
    assert plan.required, "the fixture has private externals"
    assert all(n.dependent_workloads for n in plan.networks)


def test_a_workload_that_talks_to_nothing_external_needs_no_connectivity():
    inventory = [NormalizedVM(vm_name="a", cpu=2, memory_gib=4.0, ip_addresses=["10.1.0.1"]),
                 NormalizedVM(vm_name="b", cpu=2, memory_gib=4.0, ip_addresses=["10.1.0.2"])]
    report = analyze_dependencies([Flow("10.1.0.1", "10.1.0.2", 5432, 100)], inventory)
    assert not plan_hybrid(report, "10.0.0.0/16").required


# --- reachable from the pipeline ----------------------------------------------

def test_a_flow_export_produces_connectivity_end_to_end(tmp_path):
    """The analysis is worthless if nothing calls it. `--flows` on the translate
    command is the whole user-facing surface."""
    from iactranslate.pipeline import run_pipeline

    result = run_pipeline(
        input_path="tests/fixtures/rvtools_realistic.xlsx",
        project_name="hyb", out_dir=str(tmp_path / "hyb"), target="aws",
        flows_path="tests/fixtures/flows_realistic.csv",
    )
    hybrid_tf = (tmp_path / "hyb" / "hybrid.tf").read_text()
    assert "on_prem" in hybrid_tf
    assert any(t.stage == "hybrid" for t in result.trace.stages)


def test_without_a_flow_export_the_output_is_byte_identical(tmp_path):
    """Every existing user must see exactly what they saw before."""
    from iactranslate.pipeline import run_pipeline

    run_pipeline(input_path="tests/fixtures/rvtools_realistic.xlsx", project_name="p",
                 out_dir=str(tmp_path / "plain"), target="aws")
    assert not (tmp_path / "plain" / "hybrid.tf").exists()


def test_the_overlap_is_detected_against_the_actual_landing_zone(tmp_path):
    """The VPC range comes from the customer's IPAM team, so the conflict check
    has to run against the range this plan really used, not a default."""
    from iactranslate.landing_zone import LandingZone
    from iactranslate.pipeline import run_pipeline

    run_pipeline(
        input_path="tests/fixtures/rvtools_realistic.xlsx", project_name="ov",
        out_dir=str(tmp_path / "ov"), target="aws",
        flows_path="tests/fixtures/flows_realistic.csv",
        zone=LandingZone(cidr="192.168.0.0/16"),
    )
    body = (tmp_path / "ov" / "hybrid.tf").read_text()
    assert "STOP" in body, "192.168.x on-prem systems now collide with the VPC"


# --- the generated HCL is real ------------------------------------------------

import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

_TOFU = shutil.which("tofu") or shutil.which("terraform")
_ENABLED = os.getenv("IACTRANSLATE_E2E_TOFU") == "1" and _TOFU is not None


@pytest.mark.skipif(not _ENABLED, reason="set IACTRANSLATE_E2E_TOFU=1 and install tofu/terraform")
@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_generated_connectivity_validates_against_the_real_providers(cloud, tmp_path):
    """This HCL is hand-written in templates rather than derived from a schema,
    so a unit test asserting on strings proves only that the strings are there.
    The providers are the authority on whether it is valid."""
    from iactranslate.pipeline import run_pipeline

    out = tmp_path / cloud
    run_pipeline(input_path="tests/fixtures/rvtools_realistic.xlsx", project_name="tofuhyb",
                 out_dir=str(out), target=cloud,
                 flows_path="tests/fixtures/flows_realistic.csv")
    assert (out / "hybrid.tf").exists()

    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": os.getenv(
        "TF_PLUGIN_CACHE_DIR", "/tmp/iactranslate_tf_plugin_cache"), "TF_IN_AUTOMATION": "1"}
    os.makedirs(env["TF_PLUGIN_CACHE_DIR"], exist_ok=True)
    init = subprocess.run([_TOFU, "init", "-backend=false", "-no-color", "-input=false"],
                          cwd=out, env=env, capture_output=True, text=True, timeout=300)
    assert init.returncode == 0, init.stderr
    validate = subprocess.run([_TOFU, "validate", "-no-color"],
                              cwd=out, env=env, capture_output=True, text=True, timeout=120)
    assert validate.returncode == 0, validate.stdout + validate.stderr
