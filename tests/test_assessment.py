"""Pre-migration assessment: deterministic findings + readiness scoring."""
from iactranslate.assessment import Severity, assess, to_html, to_json
from iactranslate.assessment.models import InfrastructureAssessment
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source


def _ids(a: InfrastructureAssessment) -> set:
    return {f.id for f in a.findings}


def test_clean_estate_scores_high():
    vms = [
        NormalizedVM(
            vm_name=f"web-{i}", cpu=4, memory_gib=16, disks_gib=[80],
            os="Ubuntu 22.04", power_state="poweredOn",
            cpu_util_pct=55, mem_util_pct=60,
        )
        for i in range(4)
    ]
    a = assess(vms, project_name="clean", source_platform="vmware")
    assert a.readiness.score >= 85
    assert a.readiness.band == "ready"
    # No risk/cost/data-quality findings for a clean, utilized, modern estate.
    assert "legacy-os" not in " ".join(_ids(a))
    assert "low-utilization-coverage" not in _ids(a)
    assert "powered-off" not in _ids(a)


def test_legacy_os_flagged_high():
    vms = [
        NormalizedVM(vm_name="old-1", cpu=2, memory_gib=8, disks_gib=[40],
                     os="Windows Server 2008 R2", power_state="poweredOn"),
        NormalizedVM(vm_name="old-2", cpu=2, memory_gib=8, disks_gib=[40],
                     os="CentOS 7", power_state="poweredOn"),
    ]
    a = assess(vms)
    legacy = [f for f in a.findings if f.category == "risk" and "legacy-os" in f.id]
    assert legacy, "expected end-of-life OS findings"
    assert all(f.severity == Severity.HIGH for f in legacy)
    assert any("old-1" in f.affected for f in legacy)


def test_powered_off_and_idle_flagged():
    vms = [
        NormalizedVM(vm_name="off-1", cpu=4, memory_gib=16, disks_gib=[80],
                     os="Ubuntu 22.04", power_state="poweredOff"),
        NormalizedVM(vm_name="idle-1", cpu=8, memory_gib=32, disks_gib=[80],
                     os="Ubuntu 22.04", power_state="poweredOn",
                     cpu_util_pct=2, mem_util_pct=5),
    ]
    a = assess(vms)
    ids = _ids(a)
    assert "powered-off" in ids
    assert "idle-oversized" in ids
    # The powered-off VM is excluded from the idle finding.
    idle = next(f for f in a.findings if f.id == "idle-oversized")
    assert idle.affected == ["idle-1"]


def test_large_workload_and_no_storage_flagged():
    vms = [
        NormalizedVM(vm_name="big", cpu=48, memory_gib=256, disks_gib=[500],
                     os="Ubuntu 22.04", power_state="poweredOn"),
        NormalizedVM(vm_name="nodisk", cpu=2, memory_gib=8, disks_gib=[],
                     os="Ubuntu 22.04", power_state="poweredOn"),
    ]
    a = assess(vms)
    ids = _ids(a)
    assert "large-workloads" in ids
    assert "no-storage" in ids


def test_missing_os_and_low_coverage():
    vms = [
        NormalizedVM(vm_name="mystery", cpu=2, memory_gib=8, disks_gib=[40],
                     power_state="poweredOn"),
    ]
    a = assess(vms)
    ids = _ids(a)
    assert "missing-os" in ids
    assert "low-utilization-coverage" in ids
    assert a.unknown_os_workloads == 1


def test_database_licensing_flagged():
    vms = [
        NormalizedVM(vm_name="sql-1", cpu=8, memory_gib=64, disks_gib=[200],
                     os="Windows Server 2022 + SQL Server", power_state="poweredOn",
                     cpu_util_pct=40, mem_util_pct=50),
    ]
    a = assess(vms)
    db = [f for f in a.findings if f.id.startswith("db-licensing")]
    assert db and db[0].severity == Severity.MEDIUM


def test_readiness_bands_monotonic():
    """More/worse findings never raise the score."""
    good = assess([
        NormalizedVM(vm_name="g", cpu=4, memory_gib=16, disks_gib=[80],
                     os="Ubuntu 22.04", power_state="poweredOn",
                     cpu_util_pct=50, mem_util_pct=55),
    ])
    bad = assess([
        NormalizedVM(vm_name="b", cpu=2, memory_gib=8, disks_gib=[],
                     os="Windows Server 2008", power_state="poweredOff"),
    ])
    assert bad.readiness.score < good.readiness.score
    assert 0 <= bad.readiness.score <= 100


def test_findings_sorted_by_severity():
    vms = [
        NormalizedVM(vm_name="x", cpu=2, memory_gib=8, disks_gib=[],
                     os="CentOS 7", power_state="poweredOff"),
    ]
    a = assess(vms)
    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    idxs = [order.index(f.severity) for f in a.findings]
    assert idxs == sorted(idxs)


def test_serialization_roundtrip():
    vms = [NormalizedVM(vm_name="x", cpu=2, memory_gib=8, disks_gib=[40],
                        os="Ubuntu 22.04", power_state="poweredOn")]
    a = assess(vms)
    js = to_json(a)
    assert '"readiness"' in js and '"findings"' in js
    html = to_html(a)
    assert "<html" in html.lower() and "Migration Readiness Assessment" in html
    # HTML must not leak template braces / be empty.
    assert "{" not in html.split("<style>")[0]


def test_assess_on_real_fixture(rvtools_path):
    vms = normalize(resolve_source(rvtools_path).parse(rvtools_path))
    a = assess(vms, project_name="rv", source_platform="vmware")
    assert a.total_workloads == len(vms)
    assert a.windows_workloads + a.linux_workloads + a.unknown_os_workloads == a.total_workloads
    assert 0 <= a.readiness.score <= 100
