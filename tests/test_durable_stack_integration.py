"""Everything durable, switched on at once, across a process restart.

Each piece — the project store, the job queue, project grants, encryption at
rest, multi-tenant sessions, idempotency — has its own tests, and each passes in
isolation. That is exactly the condition under which integration bugs survive:
every part is correct and the combination is not.

The scenario is the one a real deployment lives through. An owner creates a
project, uploads an inventory that is encrypted on disk, and shares it with a
colleague. The process is then replaced — a deploy, a crash, an OOM kill — and
everything has to still be true afterwards: the project exists, the grant holds,
the ciphertext still decrypts, the queued work still runs, and the audit trail
still records who did what.
"""
import importlib
import time

import pytest
from fastapi.testclient import TestClient

from iactranslate.crypto import generate_key, is_encrypted

V1 = "/v1"
PASSWORD = "correct-horse-9!"


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """The full durable configuration, as an operator would set it."""
    key = generate_key()
    env = {
        "IACTRANSLATE_STORE": "sqlite",                 # projects, jobs, grants
        "IACTRANSLATE_DB_PATH": str(tmp_path / "stack.db"),
        "IACTRANSLATE_WORKSPACE_ROOT": str(tmp_path / "work"),
        "IACTRANSLATE_AUTH": "session",                 # multi-tenant
        "IACTRANSLATE_COOKIE_SECURE": "0",              # TestClient speaks http
        "IACTRANSLATE_ENCRYPTION_KEY": key,             # encryption at rest
        "IACTRANSLATE_RATE_AUTH": "0",                  # not what this exercises
        "IACTRANSLATE_RATE_WRITE": "0",
        "IACTRANSLATE_RATE_READ": "0",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)

    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    try:
        yield api_main
    finally:
        monkeypatch.undo()
        importlib.reload(api_main)


def restart(module):
    """Replace the process, keeping the same database and workspace."""
    importlib.reload(module)
    return module


def signup(module, email):
    client = TestClient(module.app)
    r = client.post(f"{V1}/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return client


def login(module, email):
    client = TestClient(module.app)
    r = client.post(f"{V1}/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def upload(client, pid):
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as handle:
        return client.post(
            f"{V1}/projects/{pid}/upload",
            files={"file": ("i.xlsx", handle, "application/vnd.openxmlformats-"
                                              "officedocument.spreadsheetml.sheet")},
        )


def test_the_whole_stack_survives_a_restart(stack, tmp_path):
    owner = signup(stack, "owner@acme.test")
    signup(stack, "colleague@acme.test")

    pid = owner.post(f"{V1}/projects", json={"name": "estate", "target": "aws"}).json()["id"]
    assert upload(owner, pid).status_code == 200
    assert owner.post(
        f"{V1}/projects/{pid}/members",
        json={"email": "colleague@acme.test", "role": "editor"},
    ).status_code == 201

    stored = list((tmp_path / "work").rglob("upload.xlsx"))
    assert stored, "the upload should have been persisted"
    assert is_encrypted(stored[0].read_bytes()), "and encrypted at rest"

    # --- the process is replaced ---------------------------------------------
    module = restart(stack)

    colleague = login(module, "colleague@acme.test")

    # the project survived
    assert colleague.get(f"{V1}/projects/{pid}").status_code == 200

    # the grant survived, with its role
    listing = colleague.get(f"{V1}/projects").json()
    assert [p["id"] for p in listing] == [pid]
    assert listing[0]["role"] == "editor"

    # the encrypted upload is still readable — the key outlived the process, and
    # a parser can still get at the plaintext
    run = colleague.post(f"{V1}/projects/{pid}/run")
    assert run.status_code == 200, run.text
    assert run.json()["result"]["vm_count"] == 7

    # no decrypted copy was left behind by the run
    assert not list((tmp_path / "work").rglob(".plain-*"))


def test_a_queued_job_survives_the_restart_and_still_runs(stack, tmp_path):
    """The durable queue and the durable store have to agree about the project:
    the worker re-resolves it from the store on whatever process picks it up."""
    owner = signup(stack, "owner2@acme.test")
    pid = owner.post(f"{V1}/projects", json={"name": "queued", "target": "aws"}).json()["id"]
    assert upload(owner, pid).status_code == 200

    job_id = owner.post(f"{V1}/projects/{pid}/jobs").json()["id"]

    module = restart(stack)
    client = login(module, "owner2@acme.test")

    deadline = time.time() + 60
    state = None
    while time.time() < deadline:
        state = client.get(f"{V1}/jobs/{job_id}").json()["status"]
        if state in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert state == "completed", client.get(f"{V1}/jobs/{job_id}").json()


def test_a_revoked_grant_does_not_come_back(stack):
    """The dangerous direction. Access returning after a restart is worse than
    access disappearing, because nobody goes looking for it."""
    owner = signup(stack, "owner3@acme.test")
    other = signup(stack, "gone@acme.test")
    pid = owner.post(f"{V1}/projects", json={"name": "revoked", "target": "aws"}).json()["id"]
    owner.post(f"{V1}/projects/{pid}/members",
               json={"email": "gone@acme.test", "role": "viewer"})
    uid = other.get(f"{V1}/auth/me").json()["id"]
    assert owner.delete(f"{V1}/projects/{pid}/members/{uid}").status_code == 200

    module = restart(stack)
    assert login(module, "gone@acme.test").get(f"{V1}/projects/{pid}").status_code == 404


def test_tenants_stay_separate_across_a_restart(stack):
    """The security boundary, re-checked after everything has been reloaded from
    disk rather than held in memory."""
    a = signup(stack, "a@acme.test")
    signup(stack, "b@other.test")
    pid = a.post(f"{V1}/projects", json={"name": "private", "target": "aws"}).json()["id"]

    module = restart(stack)
    stranger = login(module, "b@other.test")
    assert stranger.get(f"{V1}/projects/{pid}").status_code == 404
    assert stranger.get(f"{V1}/projects").json() == []


def test_the_audit_trail_outlives_the_process(stack):
    owner = signup(stack, "owner4@acme.test")
    pid = owner.post(f"{V1}/projects", json={"name": "audited", "target": "aws"}).json()["id"]
    upload(owner, pid)

    module = restart(stack)
    client = login(module, "owner4@acme.test")
    events = client.get(f"{V1}/audit?project_id={pid}").json()
    assert events, "the trail should have survived"
    assert any("upload" in e["action"] for e in events)


def test_encryption_and_multi_tenancy_do_not_interfere(stack, tmp_path):
    """Two tenants' uploads are both encrypted and neither can read the other's."""
    a = signup(stack, "t1@acme.test")
    b = signup(stack, "t2@acme.test")
    pid_a = a.post(f"{V1}/projects", json={"name": "one", "target": "aws"}).json()["id"]
    pid_b = b.post(f"{V1}/projects", json={"name": "two", "target": "aws"}).json()["id"]
    upload(a, pid_a)
    upload(b, pid_b)

    uploads = list((tmp_path / "work").rglob("upload.xlsx"))
    assert len(uploads) == 2
    assert all(is_encrypted(p.read_bytes()) for p in uploads)
    assert a.get(f"{V1}/projects/{pid_b}").status_code == 404
    assert b.get(f"{V1}/projects/{pid_a}").status_code == 404


def test_an_idempotent_retry_still_replays_within_the_process(stack):
    """Idempotency is in-process by design (ADR 0056); this asserts it works
    where it claims to, so the boundary is a decision and not a surprise."""
    owner = signup(stack, "owner5@acme.test")
    headers = {"Idempotency-Key": "stack-retry-1"}
    first = owner.post(f"{V1}/projects", json={"name": "once", "target": "aws"},
                       headers=headers)
    second = owner.post(f"{V1}/projects", json={"name": "once", "target": "aws"},
                        headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotency-Replayed"] == "true"
