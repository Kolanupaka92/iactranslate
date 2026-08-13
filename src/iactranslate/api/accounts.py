"""User accounts and session auth — the multi-tenant security boundary.

This is the layer that makes a shared deployment safe: every project belongs to
exactly one user, and a request can only ever see its own. It replaces the
single shared bearer token from ADR 0025, which had no notion of *who* was
calling and therefore could not separate one customer from another.

**Why sessions rather than bearer tokens.** The web UI exposes the generated
Terraform and the executive report as ordinary links (`<a href>`, a new tab) —
navigations the browser makes on its own. A bearer token cannot ride on those:
there is no fetch call to attach a header to. A cookie can, because the browser
sends it automatically. That is an architectural constraint, not a preference,
and it is why the bearer-token scheme could never have secured the whole
product.

**Passwords** are stored as PBKDF2-HMAC-SHA256 with a per-user random salt at
OWASP's recommended iteration count — stdlib `hashlib`, no new dependency.
**Session tokens** are stored *hashed*, so a database leak yields no usable
session; the plaintext token exists only in the user's cookie.

Honest boundary: this is username/password with server-side sessions, not
OIDC/SSO, and it has no org/team sharing — one user, one tenant. Both are real
next steps; neither is claimed here.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# OWASP's recommended floor for PBKDF2-HMAC-SHA256. Stored per-hash so the
# count can be raised later without invalidating existing passwords.
_PBKDF2_ITERATIONS = 600_000
_SESSION_TTL_SECONDS = 14 * 24 * 3600  # 14 days
SESSION_COOKIE = "iactranslate_session"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 12


@dataclass
class User:
    id: str
    email: str
    created_at: float


class InvalidCredentials(Exception):
    """Login failed. Deliberately does not distinguish unknown-user from
    wrong-password — that difference tells an attacker which emails exist."""


class EmailTaken(Exception):
    """Registration hit an existing account."""


def hash_password(password: str) -> str:
    """`pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` — self-describing, so
    the iteration count can change without breaking stored hashes."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification against a stored hash."""
    try:
        algorithm, iterations, salt_hex, hash_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), hash_hex)


def validate_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise ValueError("enter a valid email address")
    return email


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 1024:
        # Long inputs are a PBKDF2 CPU-exhaustion vector, not a strength gain.
        raise ValueError("password must be at most 1024 characters")
    return password


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccountStore:
    """Users and sessions in SQLite — the same file the project store uses."""

    _SCHEMA_USERS = """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """
    _SCHEMA_SESSIONS = """
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """

    def __init__(self, db_path: str) -> None:
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(self._SCHEMA_USERS)
        self._conn.execute(self._SCHEMA_SESSIONS)
        self._conn.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id)")
        self._conn.commit()

    # -- users ---------------------------------------------------------------

    def create_user(self, email: str, password: str) -> User:
        email = validate_email(email)
        validate_password(password)
        user = User(id=uuid.uuid4().hex[:12], email=email, created_at=time.time())
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user.id, email, hash_password(password), user.created_at),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise EmailTaken(email) from exc
        return user

    def authenticate(self, email: str, password: str) -> User:
        """Verify credentials, or raise `InvalidCredentials`.

        Runs the KDF even when the email is unknown, so response time does not
        reveal whether an account exists.
        """
        try:
            email = validate_email(email)
        except ValueError:
            email = ""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = ?", (email,)
            ).fetchone()
        if row is None:
            # Burn equivalent work against a dummy hash before failing.
            verify_password(password, hash_password("no-such-user-timing-equalizer"))
            raise InvalidCredentials()
        if not verify_password(password, row[2]):
            raise InvalidCredentials()
        return User(id=row[0], email=row[1], created_at=row[3])

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return User(id=row[0], email=row[1], created_at=row[2]) if row else None

    # -- sessions ------------------------------------------------------------

    def create_session(self, user_id: str, ttl_seconds: int = _SESSION_TTL_SECONDS) -> str:
        """Return the plaintext session token — stored only as a hash."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(token), user_id, now, now + ttl_seconds),
            )
            self._conn.commit()
        return token

    def user_for_session(self, token: str) -> Optional[User]:
        """Resolve a session cookie to its user, or None if invalid/expired."""
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
                (_hash_token(token),),
            ).fetchone()
        if row is None:
            return None
        if row[1] < time.time():
            self.delete_session(token)
            return None
        return self.get_user(row[0])

    def delete_session(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
            self._conn.commit()

    def purge_expired_sessions(self) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            self._conn.commit()
            return cursor.rowcount


def auth_enabled() -> bool:
    """Multi-tenant mode. Off by default so the CLI and single-user/self-host
    deployments are unchanged; required for any shared deployment."""
    return os.getenv("IACTRANSLATE_AUTH", "none").strip().lower() == "session"


def create_account_store() -> Optional[AccountStore]:
    """Build the account store when `IACTRANSLATE_AUTH=session`, else None."""
    if not auth_enabled():
        return None
    return AccountStore(os.getenv("IACTRANSLATE_DB_PATH", "./iactranslate.db"))
