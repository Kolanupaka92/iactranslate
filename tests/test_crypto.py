"""Encryption at rest for uploaded inventory.

The data being protected is a reconnaissance map of a customer's estate —
hostnames, IPs, OS and patch levels. Most of these tests are about the failure
modes rather than the happy path, because the dangerous outcome is not "cannot
decrypt", it is "silently wrote plaintext while the operator believed otherwise".
"""
import base64

import pytest
from fastapi.testclient import TestClient

from iactranslate import crypto
from iactranslate.crypto import (
    ENV_KEY,
    DecryptionError,
    EncryptionUnavailable,
    decrypt_bytes,
    encrypt_bytes,
    encryption_enabled,
    generate_key,
    is_encrypted,
    read_file,
    write_file,
)

SECRET = b"hostname,ip,os\nprod-db-01,10.0.5.9,Windows Server 2019\n"


@pytest.fixture
def key(monkeypatch):
    k = generate_key()
    monkeypatch.setenv(ENV_KEY, k)
    return k


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)


# --- key handling ------------------------------------------------------------

def test_encryption_is_off_without_a_key(no_key):
    assert not encryption_enabled()


def test_a_malformed_key_raises_rather_than_disabling_encryption(monkeypatch):
    """A typo in a deployment secret must not quietly downgrade to plaintext."""
    monkeypatch.setenv(ENV_KEY, "not-base64-!!!")
    with pytest.raises(EncryptionUnavailable):
        encryption_enabled()


def test_a_wrong_length_key_is_rejected(monkeypatch):
    monkeypatch.setenv(ENV_KEY, base64.urlsafe_b64encode(b"tooshort").decode())
    with pytest.raises(EncryptionUnavailable) as e:
        encryption_enabled()
    assert "32 bytes" in str(e.value)


def test_unpadded_base64_is_accepted(monkeypatch):
    """Secret managers commonly strip '=' padding."""
    monkeypatch.setenv(ENV_KEY, generate_key().rstrip("="))
    assert encryption_enabled()


def test_generated_keys_are_unique():
    assert len({generate_key() for _ in range(200)}) == 200


# --- the cryptography itself -------------------------------------------------

def test_roundtrip(key):
    assert decrypt_bytes(encrypt_bytes(SECRET)) == SECRET


def test_ciphertext_does_not_contain_the_plaintext(key):
    blob = encrypt_bytes(SECRET)
    assert b"prod-db-01" not in blob
    assert b"10.0.5.9" not in blob


def test_same_plaintext_encrypts_differently_every_time(key):
    """Per-file salt and nonce: identical uploads must not be correlatable."""
    assert encrypt_bytes(SECRET) != encrypt_bytes(SECRET)


def test_tampering_is_detected_not_parsed(key):
    """GCM authenticates. A flipped byte must fail loudly, not yield garbage."""
    blob = bytearray(encrypt_bytes(SECRET))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        decrypt_bytes(bytes(blob))


def test_swapping_the_salt_between_files_is_detected(key):
    """The header is authenticated, so salts cannot be moved to force key reuse."""
    a, b = bytearray(encrypt_bytes(SECRET)), encrypt_bytes(b"other")
    a[6:22] = b[6:22]  # splice in the other file's salt
    with pytest.raises(DecryptionError):
        decrypt_bytes(bytes(a))


def test_truncated_ciphertext_is_rejected(key):
    with pytest.raises(DecryptionError):
        decrypt_bytes(encrypt_bytes(SECRET)[:20])


def test_another_key_cannot_decrypt(key, monkeypatch):
    blob = encrypt_bytes(SECRET)
    monkeypatch.setenv(ENV_KEY, generate_key())
    with pytest.raises(DecryptionError):
        decrypt_bytes(blob)


def test_plaintext_is_not_mistaken_for_ciphertext():
    assert not is_encrypted(b"hostname,ip\nweb-01,10.0.0.1")


# --- file helpers ------------------------------------------------------------

def test_written_files_are_encrypted_on_disk(key, tmp_path):
    path = tmp_path / "upload.csv"
    write_file(path, SECRET)
    assert b"prod-db-01" not in path.read_bytes()
    assert read_file(path) == SECRET


def test_files_are_written_owner_only(key, tmp_path):
    """0600 is set at open() time, not chmod-ed after the sensitive bytes land."""
    path = tmp_path / "upload.csv"
    write_file(path, SECRET)
    assert path.stat().st_mode & 0o077 == 0


def test_without_a_key_files_are_written_plainly_and_still_readable(no_key, tmp_path):
    path = tmp_path / "upload.csv"
    write_file(path, SECRET)
    assert path.read_bytes() == SECRET
    assert read_file(path) == SECRET


def test_plaintext_written_before_encryption_was_enabled_still_reads(key, tmp_path):
    """Keyed off the file header, so enabling encryption is not a flag day."""
    path = tmp_path / "legacy.csv"
    path.write_bytes(SECRET)
    assert read_file(path) == SECRET


def test_removing_the_key_does_not_silently_hand_back_ciphertext(key, tmp_path, monkeypatch):
    """Better a clear error than a parser failing somewhere confusing."""
    path = tmp_path / "upload.csv"
    write_file(path, SECRET)
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(EncryptionUnavailable) as e:
        read_file(path)
    assert ENV_KEY in str(e.value)


def test_missing_library_fails_closed_rather_than_writing_plaintext(key, monkeypatch):
    """The one outcome worse than no encryption is believing you have it."""
    def _boom():
        raise EncryptionUnavailable("cryptography is not installed")

    monkeypatch.setattr(crypto, "_aesgcm", _boom)
    with pytest.raises(EncryptionUnavailable):
        encrypt_bytes(SECRET)


# --- end to end through the API ---------------------------------------------

def test_uploaded_inventory_is_encrypted_at_rest_and_still_usable(key, tmp_path, monkeypatch):
    """The point of the feature: unreadable on disk, unchanged in behaviour."""
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    import importlib

    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    try:
        client = TestClient(api_main.app)
        r = client.post("/projects", json={"name": "enc", "target": "aws"})
        pid = r.json()["id"]
        with open("tests/fixtures/rvtools_realistic.xlsx", "rb") as f:
            assert client.post(
                f"/projects/{pid}/upload",
                files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                             "officedocument.spreadsheetml.sheet")},
            ).status_code == 200

        stored = list(tmp_path.rglob("upload.xlsx"))
        assert stored, "upload should have been persisted"
        raw = stored[0].read_bytes()
        assert is_encrypted(raw), "upload must not be plaintext on disk"
        # An xlsx is a zip; its magic must not survive if encryption worked.
        assert not raw.startswith(b"PK")

        # And the pipeline still runs against it.
        run = client.post(f"/projects/{pid}/run")
        assert run.status_code == 200, run.text
        assert run.json()["result"]["vm_count"] == 25

        # No decrypted copy is left behind.
        assert not list(tmp_path.rglob(".plain-*")), "plaintext temp file leaked"
    finally:
        monkeypatch.undo()
        importlib.reload(api_main)


def test_the_upload_handler_does_not_encrypt_on_the_event_loop():
    """`upload` is the one `async def` route handler — correctly, since it awaits
    `file.read()`. Encrypting and writing 25 MB inside it stalled every other
    request for 14-70 ms, measured at the MAX_UPLOAD_BYTES ceiling.

    Non-async handlers are already run in a threadpool by FastAPI, which is why
    the rest of the API does not have this problem — and why sync handlers are
    the right choice for the CPU-bound pipeline work, not the defect an earlier
    review called them.
    """
    import inspect

    from iactranslate.api import main as api_main

    source = inspect.getsource(api_main.upload)
    assert "run_in_threadpool(write_file" in source, (
        "the encrypt-and-write must stay off the event loop"
    )
