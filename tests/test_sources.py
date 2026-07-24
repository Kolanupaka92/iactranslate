"""Tests for the discovery-source abstraction (any inventory -> canonical model)."""
import pandas as pd
import pytest

from iactranslate.normalize import normalize
from iactranslate.pipeline import run_pipeline
from iactranslate.sources import (
    UnknownSourceError,
    detect_source,
    get_source,
    list_sources,
    resolve_source,
)


def test_registry_lists_all_sources():
    assert set(list_sources()) == {"vmware", "hyperv", "kubernetes", "cloud", "generic"}


def test_unknown_source_raises():
    with pytest.raises(UnknownSourceError):
        get_source("mainframe")


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("rvtools_path", "vmware"),
        ("vmware_csv_path", "vmware"),
        ("hyperv_path", "hyperv"),
        ("cmdb_path", "generic"),
        ("cloud_path", "cloud"),
        ("k8s_path", "kubernetes"),
    ],
)
def test_auto_detection(request, fixture, expected):
    path = request.getfixturevalue(fixture)
    assert detect_source(path).name == expected


def test_kubernetes_source_reads_container_requests(k8s_path):
    vms = {v.vm_name: v for v in normalize(resolve_source(k8s_path).parse(k8s_path))}
    # namespace-qualified names, cpu/mem from resources.requests (millicores + Gi).
    assert vms["prod/prod-web-01"].cpu == 4
    assert vms["prod/prod-web-01"].memory_gib == 16.0
    # StatefulSet volumeClaimTemplates storage becomes the workload's disk.
    assert vms["prod/prod-db-01"].disks_gib == [1000.0]


def test_kubernetes_workloads_classify_by_name(k8s_path, tmp_path):
    result = run_pipeline(
        input_path=k8s_path, project_name="k8s", out_dir=str(tmp_path / "k8s"), target="aws",
    )
    assert result.plan.source_platform == "kubernetes"
    by_name = {c.vm_name: c for c in result.plan.compute}
    assert by_name["prod/prod-db-01"].tier.value == "database"
    assert by_name["prod/prod-web-01"].tier.value == "web"
    assert by_name["prod/prod-web-01"].environment.value == "production"


@pytest.mark.parametrize("fixture", ["hyperv_path", "cmdb_path", "cloud_path"])
def test_each_source_yields_the_same_estate(request, fixture):
    path = request.getfixturevalue(fixture)
    vms = normalize(resolve_source(path).parse(path))
    assert len(vms) == 7
    by_name = {v.vm_name: v for v in vms}
    web = by_name["prod-web-01"]
    assert web.cpu == 4 and web.memory_gib == 16.0  # units recovered correctly


def test_cloud_source_recovers_specs_from_catalog(cloud_path):
    # cloud_sample lists instance types, not vCPU/mem — the source looks them up.
    vms = {v.vm_name: v for v in normalize(resolve_source(cloud_path).parse(cloud_path))}
    assert vms["prod-db-01"].cpu == 16 and vms["prod-db-01"].memory_gib == 64.0


def test_generic_explicit_column_map(tmp_path):
    # Arbitrary headers a synonym table wouldn't catch — explicit map handles them.
    path = tmp_path / "weird.csv"
    pd.DataFrame(
        [{"Box": "srv-a", "Procs": 8, "Mem_G": 32, "Disk_G": 500, "Plat": "Ubuntu Linux"}]
    ).to_csv(path, index=False)

    src = get_source("generic")
    mapping = {"name": "Box", "cpu": "Procs", "memory_gib": "Mem_G", "disk_gib": "Disk_G", "os": "Plat"}
    vms = normalize(src.parse(str(path), column_map=mapping))
    assert len(vms) == 1
    assert vms[0].vm_name == "srv-a" and vms[0].cpu == 8 and vms[0].memory_gib == 32.0


def test_pipeline_sets_source_platform(hyperv_path, tmp_path):
    result = run_pipeline(
        input_path=hyperv_path,
        project_name="hv",
        out_dir=str(tmp_path / "hv"),
        target="aws",
    )
    assert result.plan.source_platform == "hyper-v"
    assert result.plan.vm_count == 7


def test_explicit_source_overrides_detection(cmdb_path):
    # Force 'generic' even though auto-detect would also pick it — exercises the name path.
    src = resolve_source(cmdb_path, "generic")
    assert src.name == "generic"
