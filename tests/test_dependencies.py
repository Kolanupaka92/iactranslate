"""Application dependencies from observed network flows.

The review's sharpest product criticism was that you cannot correctly group
applications from a static inventory. These tests are mostly about what the
analysis *refuses* to conclude — a dependency map that says everything depends
on everything is worse than none, because it looks like an answer.
"""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.dependencies import (
    INFRASTRUCTURE_PORTS,
    DependencyError,
    Flow,
    analyze_dependencies,
    parse_flows,
)
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target
from iactranslate.waves import plan_waves

FLOWS = "tests/fixtures/flows_realistic.csv"


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))


@pytest.fixture(scope="module")
def waves(vms):
    return plan_waves(build_migration_plan(vms, "deps", get_target("aws")))


def _vm(name, ip):
    return NormalizedVM(vm_name=name, resource_name=name.replace("-", "_"),
                        cpu=2, memory_gib=4.0, ip_addresses=[ip])


# --- parsing -----------------------------------------------------------------

def test_the_fixture_export_parses(vms):
    flows = parse_flows(FLOWS)
    assert flows
    assert all(f.dst_port > 0 for f in flows)


@pytest.mark.parametrize("header", [
    "source_ip,destination_ip,destination_port",
    "src,dst,port",
    "Source Address,Destination Address,Destination Port",
    "src_ip,dst_ip,dst_port",
])
def test_column_names_from_different_tools_are_accepted(tmp_path, header):
    """vRNI, Tetration and NetFlow collectors all name these differently and
    none of them agree; making the customer reshape the export first is a
    needless reason for them to stop."""
    path = tmp_path / "f.csv"
    path.write_text(f"{header}\n10.0.0.1,10.0.0.2,5432\n")
    flows = parse_flows(path)
    assert flows[0].dst_port == 5432


def test_an_export_without_the_needed_columns_says_what_it_wanted(tmp_path):
    path = tmp_path / "f.csv"
    path.write_text("who,whom,why\na,b,c\n")
    with pytest.raises(DependencyError) as e:
        parse_flows(path)
    assert "src_ip" in str(e.value)


def test_malformed_rows_are_skipped_not_fatal(tmp_path):
    """A 300,000-row export with three bad lines should still be usable."""
    path = tmp_path / "f.csv"
    path.write_text(
        "src_ip,dst_ip,dst_port\n"
        "10.0.0.1,10.0.0.2,5432\n"
        "10.0.0.1,10.0.0.2,not-a-port\n"
        ",10.0.0.2,5432\n"
        "10.0.0.3,10.0.0.4,8080\n"
    )
    assert len(parse_flows(path)) == 2


# --- what counts as a dependency ---------------------------------------------

def test_direction_comes_from_the_destination_port():
    """The side being connected *to* is the one that must already be running.
    That asymmetry is what makes this a dependency and not an association."""
    inv = [_vm("web", "10.0.0.1"), _vm("db", "10.0.0.2")]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.2", 5432, 100)], inv)
    assert [(d.source, d.target) for d in report.dependencies] == [("web", "db")]


def test_infrastructure_ports_are_filtered():
    """Without this every workload depends on the DNS server and the graph says
    nothing — the most common way a dependency map becomes useless."""
    inv = [_vm("app", "10.0.0.1"), _vm("dns", "10.0.0.2")]
    flows = [Flow("10.0.0.1", "10.0.0.2", port, 5000) for port in sorted(INFRASTRUCTURE_PORTS)]
    report = analyze_dependencies(flows, inv)
    assert report.dependencies == []
    assert report.filtered_flows == len(flows)


def test_a_single_stray_connection_is_not_a_dependency():
    """One probe or health check is noise, not architecture."""
    inv = [_vm("a", "10.0.0.1"), _vm("b", "10.0.0.2")]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.2", 9999, 1)], inv)
    assert report.dependencies == []


def test_a_workload_talking_to_itself_is_not_a_dependency():
    inv = [_vm("a", "10.0.0.1")]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.1", 8080, 500)], inv)
    assert report.dependencies == []


def test_ports_are_aggregated_per_pair():
    inv = [_vm("app", "10.0.0.1"), _vm("db", "10.0.0.2")]
    report = analyze_dependencies([
        Flow("10.0.0.1", "10.0.0.2", 5432, 100),
        Flow("10.0.0.1", "10.0.0.2", 1433, 50),
    ], inv)
    assert len(report.dependencies) == 1
    assert report.dependencies[0].ports == [1433, 5432]
    assert report.dependencies[0].connections == 150


# --- the findings that save trouble ------------------------------------------

def test_a_wave_that_moves_a_workload_before_its_dependency_is_flagged():
    """The finding this exists for: a cutover that fails."""
    class _Wave:
        def __init__(self, seq, names):
            self.sequence, self.workloads = seq, names

    class _Waves:
        waves = [_Wave(0, ["web"]), _Wave(5, ["db"])]

    inv = [_vm("web", "10.0.0.1"), _vm("db", "10.0.0.2")]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.2", 5432, 200)], inv, waves=_Waves())
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert (violation.source, violation.source_wave) == ("web", 0)
    assert (violation.target, violation.target_wave) == ("db", 5)
    assert any("before a dependency" in n for n in report.notes)


def test_moving_together_in_one_wave_is_not_a_violation():
    class _Wave:
        sequence, workloads = 2, ["web", "db"]

    class _Waves:
        waves = [_Wave()]

    inv = [_vm("web", "10.0.0.1"), _vm("db", "10.0.0.2")]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.2", 5432, 200)], inv, waves=_Waves())
    assert report.violations == []


def test_the_planned_waves_for_the_fixture_estate_are_sound(vms, waves):
    """A real check, not a decorative one: the existing wave planner orders data
    tiers before the things that call them, and this proves it on real flows."""
    report = analyze_dependencies(parse_flows(FLOWS), vms, waves=waves)
    assert report.dependencies, "the fixture should produce dependencies"
    assert report.violations == [], [v.model_dump() for v in report.violations]


def test_addresses_outside_the_inventory_are_reported_not_dropped():
    """A mainframe or licence server nobody listed is exactly what an
    inventory-only analysis cannot see."""
    inv = [_vm("app", "10.0.0.1")]
    report = analyze_dependencies([
        Flow("10.0.0.1", "192.168.240.11", 1414, 300),
        Flow("10.0.0.1", "52.94.236.248", 443, 120),
    ], inv)
    assert len(report.external) == 2
    private = [e for e in report.external if e.is_private]
    assert [e.address for e in private] == ["192.168.240.11"]


def test_the_fixture_estate_has_the_dependencies_nobody_listed(vms):
    report = analyze_dependencies(parse_flows(FLOWS), vms)
    assert any(e.is_private for e in report.external), "an unlisted internal system"
    assert any(not e.is_private for e in report.external), "a third-party service"


def test_mutually_calling_workloads_must_move_together():
    """Whichever moves first leaves the other calling a machine that is gone, so
    a wave plan that splits them is wrong in either order."""
    inv = [_vm("a", "10.0.0.1"), _vm("b", "10.0.0.2")]
    report = analyze_dependencies([
        Flow("10.0.0.1", "10.0.0.2", 8080, 100),
        Flow("10.0.0.2", "10.0.0.1", 9090, 100),
    ], inv)
    assert report.must_move_together == [["a", "b"]]


def test_a_one_way_call_does_not_force_moving_together():
    inv = [_vm("a", "10.0.0.1"), _vm("b", "10.0.0.2")]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.2", 8080, 100)], inv)
    assert report.must_move_together == []


# --- honesty about the inputs ------------------------------------------------

def test_flows_from_unknown_callers_are_counted_not_guessed():
    inv = [_vm("known", "10.0.0.1")]
    report = analyze_dependencies([Flow("10.9.9.9", "10.0.0.1", 5432, 100)], inv)
    assert report.unmapped_flows == 1
    assert report.dependencies == []


def test_poor_coverage_is_called_out():
    """The two exports probably describe different estates or time windows."""
    inv = [_vm("known", "10.0.0.1")]
    flows = [Flow(f"10.9.9.{i}", "10.0.0.1", 5432, 50) for i in range(20)]
    flows.append(Flow("10.0.0.1", "10.0.0.1", 5432, 50))
    report = analyze_dependencies(flows, inv)
    assert report.coverage_pct < 60
    assert any("matched a workload" in n for n in report.notes)


def test_an_inventory_with_no_ips_says_so_rather_than_returning_nothing():
    """Silence here looks like 'no dependencies found', which is a wrong answer
    rather than a missing one."""
    inv = [NormalizedVM(vm_name="a", resource_name="a", cpu=1, memory_gib=1.0)]
    report = analyze_dependencies([Flow("10.0.0.1", "10.0.0.2", 5432, 100)], inv)
    assert any("has an IP address" in n for n in report.notes)


def test_analysis_does_not_mutate_the_inventory(vms):
    """An analysis engine reads and changes nothing (ADR 0007)."""
    before = [v.model_dump_json() for v in vms]
    analyze_dependencies(parse_flows(FLOWS), vms)
    assert [v.model_dump_json() for v in vms] == before
