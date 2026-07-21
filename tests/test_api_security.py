"""Security/robustness tests for the API hardening."""
import io

import pytest
from fastapi.testclient import TestClient

import iactranslate.api.main as api
import iactranslate.recommend as rec
from iactranslate.api.main import app
from iactranslate.models import NormalizedVM

client = TestClient(app, raise_server_exceptions=False)


def _new_project(target="aws"):
    return client.post("/projects", json={"name": "sec", "target": target}).json()["id"]


def test_oversized_upload_is_rejected(rvtools_path, monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 16)  # 16 bytes — fixture is larger
    pid = _new_project()
    with open(rvtools_path, "rb") as f:
        r = client.post(f"/projects/{pid}/upload", files={"file": ("rvtools_sample.xlsx", f)})
    assert r.status_code == 413


def test_malformed_upload_returns_400_not_500(monkeypatch):
    pid = _new_project()
    junk = io.BytesIO(b"this is definitely not an excel workbook")
    client.post(f"/projects/{pid}/upload", files={"file": ("bad.xlsx", junk)})
    r = client.post(f"/projects/{pid}/recommend")
    assert r.status_code == 400
    # No traceback / internal detail leaked.
    assert "Traceback" not in r.text


def test_unsupported_extension_rejected():
    pid = _new_project()
    r = client.post(f"/projects/{pid}/upload", files={"file": ("evil.exe", io.BytesIO(b"x"))})
    assert r.status_code == 400


def test_invalid_project_name_rejected():
    r = client.post("/projects", json={"name": "../etc/passwd", "target": "aws"})
    assert r.status_code == 422


def test_delete_project_lifecycle(rvtools_path):
    pid = _new_project()
    assert client.delete(f"/projects/{pid}").status_code == 204
    assert client.get(f"/projects/{pid}").status_code == 404
    assert client.delete(f"/projects/{pid}").status_code == 404  # already gone


def test_vm_count_limit_enforced(monkeypatch):
    monkeypatch.setattr(rec, "MAX_VMS", 2)
    vms = [NormalizedVM(vm_name=f"vm-{i}", cpu=2, memory_gib=4) for i in range(5)]
    with pytest.raises(ValueError, match="exceeding the limit"):
        rec.recommend(vms)


def test_store_evicts_beyond_capacity():
    from iactranslate.api.store import ProjectStore

    store = ProjectStore(max_projects=3)
    ids = [store.create(name=f"p{i}").id for i in range(5)]
    # Oldest two evicted; newest three remain.
    assert store.get(ids[0]) is None
    assert store.get(ids[1]) is None
    assert all(store.get(i) is not None for i in ids[2:])
