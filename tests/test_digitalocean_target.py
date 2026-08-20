import zipfile

from fastapi.testclient import TestClient

from iactranslate.agents import build_migration_plan
from iactranslate.api.main import app
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.pipeline import run_pipeline
from iactranslate.targets import get_target
from iactranslate.targets.base import CAP_GITOPS, CAP_LIVE_PRICING, CAP_PULUMI, CAP_TERRAFORM

DO = get_target("digitalocean")


def _files(rvtools_path):
    vms = normalize(parse(rvtools_path))
    plan = build_migration_plan(vms, project_name="do-gen", target=DO)
    return build_files(plan, DO), plan


def test_digitalocean_capabilities_are_honest():
    caps = DO.capabilities
    assert CAP_TERRAFORM in caps
    assert CAP_GITOPS in caps
    assert CAP_PULUMI not in caps
    assert CAP_LIVE_PRICING not in caps


def test_digitalocean_catalog_uses_real_stable_slugs():
    names = DO.instance_names()
    assert len(names) == len(set(names))
    assert all(n.startswith(("s-", "m-")) for n in names)


def test_digitalocean_renders_droplet_and_vpc_resources(rvtools_path):
    files, plan = _files(rvtools_path)
    assert 'provider "digitalocean"' in files["provider.tf"]
    assert 'resource "digitalocean_vpc" "main"' in files["networking.tf"]
    # No subnet resource — DigitalOcean has none.
    assert "digitalocean_subnet" not in files["networking.tf"]
    for c in plan.compute:
        assert f'resource "digitalocean_droplet" "{c.resource_name}"' in files["compute.tf"]


def test_digitalocean_windows_source_vms_flagged_in_readme(rvtools_path):
    files, plan = _files(rvtools_path)
    windows = [c for c in plan.compute if c.os_family_changed]
    assert windows, "fixture should exercise at least one Windows source VM"
    assert "Windows source VMs" in files["README.md"]
    for c in windows:
        assert c.vm_name in files["README.md"]
    # Rendered as Ubuntu, not a fabricated Windows slug.
    assert 'image    = "ubuntu-22-04-x64"' in files["compute.tf"]


def test_digitalocean_never_claims_a_windows_image_it_does_not_have(rvtools_path):
    """The plan must not say `windows-*` when the Droplet will boot Ubuntu.

    Every `windows-*` key in DigitalOcean's catalog resolves to
    `ubuntu-22-04-x64`, so publishing one made `image_key` a false statement:
    the plan claimed Windows, the infrastructure ran Linux, and the
    OS-substitution check compared source-2019 against key-2019 and stayed
    silent about the single most consequential substitution the tool can make.
    """
    _, plan = _files(rvtools_path)
    windows_sources = [c for c in plan.compute if "windows" in (c.source_os or "").lower()]
    assert windows_sources, "fixture should exercise at least one Windows source VM"
    for c in windows_sources:
        assert not c.image_key.startswith("windows"), (
            f"{c.vm_name}: image_key {c.image_key!r} claims Windows, but DigitalOcean "
            f"has no Windows image and will provision Ubuntu"
        )
        assert c.os_family_changed
        assert "OS FAMILY CHANGED" in (c.reason or "")


def test_digitalocean_is_not_recommended_for_a_windows_estate(rvtools_path):
    """A cloud that cannot run part of the estate must not win on cost.

    DigitalOcean looked cheapest on a Windows-heavy estate precisely *because*
    it skipped Windows licensing for machines it cannot host — an argument that
    only holds if you ignore that the migration would fail.
    """
    from iactranslate.recommend import recommend

    vms = normalize(parse(rvtools_path))
    rec = recommend(vms)
    do = next(s for s in rec.ranked if s.cloud == "digitalocean")
    assert not do.eligible
    assert do.unsupported_workloads > 0
    assert rec.recommended != "digitalocean"
    assert any("DIGITALOCEAN was excluded" in n for n in rec.notes)


def test_digitalocean_firewalls_use_tags_not_subnets(rvtools_path):
    files, plan = _files(rvtools_path)
    for sg in plan.network.security_groups:
        assert f'resource "digitalocean_tag" "{sg.resource_name}"' in files["security.tf"]
        assert f'resource "digitalocean_firewall" "{sg.resource_name}"' in files["security.tf"]


def test_digitalocean_load_balancers_use_dedicated_tags(rvtools_path):
    files, plan = _files(rvtools_path)
    assert plan.network.load_balancers, "fixture should exercise load balancers"
    lb_tf = files["loadbalancer.tf"]
    for lb in plan.network.load_balancers:
        assert f'resource "digitalocean_loadbalancer" "{lb.resource_name}"' in lb_tf
        assert f"{lb.resource_name}_lb" in files["compute.tf"]  # fronted instances carry the LB tag


def test_digitalocean_pipeline_e2e(rvtools_path, tmp_path):
    out = tmp_path / "do"
    result = run_pipeline(
        input_path=rvtools_path,
        project_name="do-e2e",
        out_dir=str(out),
        target="digitalocean",
        make_zip=True,
    )
    assert result.plan.target == "digitalocean"
    assert result.plan.region == "nyc3"
    for c in result.plan.compute:
        assert c.instance_type.startswith(("s-", "m-"))
    with zipfile.ZipFile(result.zip_path) as zf:
        assert "compute.tf" in zf.namelist()


def test_api_digitalocean_flow(rvtools_path):
    client = TestClient(app)
    r = client.post("/projects", json={"name": "api-do", "target": "digitalocean"})
    assert r.status_code == 201
    pid = r.json()["id"]
    with open(rvtools_path, "rb") as f:
        assert client.post(f"/projects/{pid}/upload",
                           files={"file": ("rvtools_sample.xlsx", f)}).status_code == 200
    r = client.post(f"/projects/{pid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["vm_count"] == 7
    assert client.get(f"/projects/{pid}/download").status_code == 200
