"""Encryption at rest for uploaded inventory.

An RVTools export is hostnames, IP addresses, OS and patch levels, cluster and
datacenter topology for an entire enterprise estate. For an attacker that is a
reconnaissance map — plausibly the most valuable thing this service will ever
hold, and more useful than most databases it might touch. It was stored as
plaintext files on disk, so one stolen backup or snapshot leaked every
customer's attack surface at once.

**Envelope encryption, AES-256-GCM.** Each file gets a random 16-byte salt; the
file key is derived from the master key with HKDF-SHA256 over that salt, so no
two files share a key and compromising one file's key reveals nothing about the
others. GCM authenticates, so tampering is detected rather than parsed. The
header is bound as additional authenticated data, so an attacker cannot swap
salts between files to force key reuse.

    IACTE1 | salt (16) | nonce (12) | ciphertext+tag

**Optional by design.** `cryptography` is a compiled dependency, and this tool
sells partly on installing and running anywhere with no services. Encryption is
therefore an extra (`pip install iactranslate[encryption]`) and is enabled by
setting a key.

**It fails closed.** If a key is configured but the library is missing, or the
key is malformed, startup raises rather than falling back — silently writing
plaintext when the operator asked for encryption is the one outcome worse than
having no encryption at all, because they would believe they were covered.

**What this does and does not protect.** It protects data at rest: stolen disks,
backups, snapshots, a misconfigured volume — cases where the attacker gets the
bytes but not the process environment holding the key. It does not protect
against an attacker who can read the running process's memory or environment,
and it is not a substitute for a KMS: the master key lives in an environment
variable, so key rotation is manual and there is no per-tenant key separation
yet. Both are tracked on the roadmap.
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from typing import Optional

MAGIC = b"IACTE1"
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32  # AES-256
HEADER_LEN = len(MAGIC) + SALT_LEN + NONCE_LEN

ENV_KEY = "IACTRANSLATE_ENCRYPTION_KEY"

_HKDF_INFO = b"iactranslate:upload:v1"


class EncryptionUnavailable(RuntimeError):
    """A key is configured but encryption cannot be performed."""


class DecryptionError(ValueError):
    """Ciphertext is corrupt, truncated, tampered with, or from another key."""


def _aesgcm():
    """Import lazily so the dependency is only needed when encryption is on."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise EncryptionUnavailable(
            f"{ENV_KEY} is set but the 'cryptography' package is not installed. "
            "Install it with `pip install iactranslate[encryption]`, or unset the "
            "key. Refusing to store inventory in plaintext when encryption was "
            "requested."
        ) from exc
    return AESGCM


def generate_key() -> str:
    """A fresh base64 master key, for `IACTRANSLATE_ENCRYPTION_KEY`."""
    return base64.urlsafe_b64encode(secrets.token_bytes(KEY_LEN)).decode()


def master_key() -> Optional[bytes]:
    """The configured master key, or None when encryption is off.

    A malformed key raises rather than disabling encryption: a typo in a
    deployment secret must not quietly downgrade every customer's data to
    plaintext.
    """
    raw = os.getenv(ENV_KEY, "").strip()
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(_pad(raw))
    except Exception as exc:
        raise EncryptionUnavailable(
            f"{ENV_KEY} is not valid base64. Generate one with "
            "`python -c \"from iactranslate.crypto import generate_key; print(generate_key())\"`."
        ) from exc
    if len(key) != KEY_LEN:
        raise EncryptionUnavailable(
            f"{ENV_KEY} must decode to {KEY_LEN} bytes (got {len(key)})."
        )
    return key


def _pad(value: str) -> str:
    """base64 without padding is common in env vars; accept both."""
    return value + "=" * (-len(value) % 4)


def encryption_enabled() -> bool:
    return master_key() is not None


def _derive(key: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(
        algorithm=hashes.SHA256(), length=KEY_LEN, salt=salt, info=_HKDF_INFO
    ).derive(key)


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt with a fresh per-file key. Raises if encryption is not configured."""
    key = master_key()
    if key is None:
        raise EncryptionUnavailable(f"{ENV_KEY} is not set")
    AESGCM = _aesgcm()
    salt = secrets.token_bytes(SALT_LEN)
    nonce = secrets.token_bytes(NONCE_LEN)
    header = MAGIC + salt + nonce
    # The header is authenticated, so salt and nonce cannot be swapped between
    # files to coerce key or nonce reuse.
    ciphertext = AESGCM(_derive(key, salt)).encrypt(nonce, data, header)
    return header + ciphertext


def decrypt_bytes(blob: bytes) -> bytes:
    key = master_key()
    if key is None:
        raise EncryptionUnavailable(f"{ENV_KEY} is not set")
    if not is_encrypted(blob):
        raise DecryptionError("not an IaCTranslate encrypted file")
    if len(blob) < HEADER_LEN:
        raise DecryptionError("encrypted file is truncated")
    AESGCM = _aesgcm()
    header, body = blob[:HEADER_LEN], blob[HEADER_LEN:]
    salt = header[len(MAGIC):len(MAGIC) + SALT_LEN]
    nonce = header[len(MAGIC) + SALT_LEN:]
    try:
        return AESGCM(_derive(key, salt)).decrypt(nonce, body, header)
    except Exception as exc:
        raise DecryptionError(
            "could not decrypt — the file is corrupt, was tampered with, or was "
            "written with a different key"
        ) from exc


def is_encrypted(blob: bytes) -> bool:
    """Cheap check so a workspace can hold both, across an encryption rollout."""
    return blob[:len(MAGIC)] == MAGIC


def write_file(path: Path, data: bytes) -> None:
    """Write `data`, encrypted when a key is configured.

    Permissions are set to 0600 *before* the bytes land, not after: a file
    created world-readable and chmod-ed afterwards is readable for the window in
    between, which is exactly when it is being filled with the sensitive part.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, encrypt_bytes(data) if encryption_enabled() else data)
    finally:
        os.close(fd)


def read_file(path: Path) -> bytes:
    """Read `path`, decrypting when it carries the encrypted-file header.

    Keyed off the header rather than off configuration so a workspace written
    before encryption was enabled still reads. The reverse — a key removed while
    encrypted files remain — raises, rather than handing a parser ciphertext and
    letting it fail somewhere confusing.
    """
    blob = path.read_bytes()
    if not is_encrypted(blob):
        return blob
    if not encryption_enabled():
        raise EncryptionUnavailable(
            f"{path.name} is encrypted but {ENV_KEY} is not set — the key that "
            "wrote it is required to read it."
        )
    return decrypt_bytes(blob)
