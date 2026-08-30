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
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from .sql import Database, create_database

# OWASP's recommended floor for PBKDF2-HMAC-SHA256. Stored per-hash so the
# count can be raised later without invalidating existing passwords.
_PBKDF2_ITERATIONS = 600_000
_SESSION_TTL_SECONDS = 14 * 24 * 3600  # 14 days
# Short on purpose: a reset link sits in an inbox, which is a less trustworthy
# place than a cookie jar. Long enough to act on, short enough to age out.
_RESET_TTL_SECONDS = 3600  # 1 hour
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
    """Users and sessions, in whichever engine the deployment uses (ADR 0063)."""

    _SCHEMA_USERS = """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at {REAL} NOT NULL
        )
    """
    _SCHEMA_SESSIONS = """
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at {REAL} NOT NULL,
            expires_at {REAL} NOT NULL
        )
    """
    # Reset tokens are hashed exactly like sessions: whoever can read this table
    # must not be able to take over an account with what they find there.
    _SCHEMA_RESETS = """
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at {REAL} NOT NULL,
            expires_at {REAL} NOT NULL
        )
    """

    def __init__(self, db: "Database | str") -> None:
        # A path string opens SQLite, which is how this was always constructed.
        self._db = db if isinstance(db, Database) else Database("sqlite", db)
        for ddl in (self._SCHEMA_USERS, self._SCHEMA_SESSIONS, self._SCHEMA_RESETS):
            self._db.execute(self._db.schema(ddl))
        self._db.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id)")

    # -- users ---------------------------------------------------------------

    def create_user(self, email: str, password: str) -> User:
        email = validate_email(email)
        validate_password(password)
        user = User(id=uuid.uuid4().hex[:12], email=email, created_at=time.time())
        try:
            self._db.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user.id, email, hash_password(password), user.created_at),
            )
        except self._db.integrity_errors as exc:
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
        row = self._db.query_one(
            "SELECT id, email, password_hash, created_at FROM users WHERE email = ?", (email,)
        )
        if row is None:
            # Burn equivalent work against a dummy hash before failing.
            verify_password(password, hash_password("no-such-user-timing-equalizer"))
            raise InvalidCredentials()
        if not verify_password(password, row[2]):
            raise InvalidCredentials()
        return User(id=row[0], email=row[1], created_at=row[3])

    def get_user(self, user_id: str) -> Optional[User]:
        row = self._db.query_one(
            "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
        )
        return User(id=row[0], email=row[1], created_at=row[2]) if row else None

    # -- sessions ------------------------------------------------------------

    def create_session(self, user_id: str, ttl_seconds: int = _SESSION_TTL_SECONDS) -> str:
        """Return the plaintext session token — stored only as a hash."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._db.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), user_id, now, now + ttl_seconds),
        )
        return token

    def user_for_session(self, token: str) -> Optional[User]:
        """Resolve a session cookie to its user, or None if invalid/expired."""
        if not token:
            return None
        row = self._db.query_one(
            "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
            (_hash_token(token),),
        )
        if row is None:
            return None
        if row[1] < time.time():
            self.delete_session(token)
            return None
        return self.get_user(row[0])

    def delete_session(self, token: str) -> None:
        self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    def purge_expired_sessions(self) -> int:
        return self._db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    def delete_sessions_for_user(self, user_id: str) -> int:
        """Sign a user out everywhere.

        This is what makes a password change *mean* something: if an attacker
        already holds a stolen session cookie, changing the password without
        this leaves them logged in indefinitely.
        """
        return self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # -- passwords -----------------------------------------------------------

    def get_user_by_email(self, email: str) -> Optional[User]:
        try:
            email = validate_email(email)
        except ValueError:
            return None
        row = self._db.query_one(
            "SELECT id, email, created_at FROM users WHERE email = ?", (email,)
        )
        return User(id=row[0], email=row[1], created_at=row[2]) if row else None

    def set_password(self, user_id: str, new_password: str) -> None:
        validate_password(new_password)
        encoded = hash_password(new_password)
        self._db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (encoded, user_id))

    def create_reset_token(self, user_id: str, ttl_seconds: int = _RESET_TTL_SECONDS) -> str:
        """Issue a single-use reset token. Returns the plaintext; only the hash
        is stored. Any previously issued token for this user is invalidated, so
        a stale link in an old email can't be used after a newer request."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        # One transaction: between the delete and the insert this user has no
        # valid token at all, and a concurrent reset must not observe that gap.
        with self._db.transaction() as tx:
            tx.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
            tx.execute(
                "INSERT INTO password_resets (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (_hash_token(token), user_id, now, now + ttl_seconds),
            )
        return token

    def consume_reset_token(self, token: str) -> Optional[str]:
        """Redeem a reset token, returning its user id — or None if it is
        unknown or expired. **Single use**: the row is deleted whether or not
        it had expired, so a token can never be replayed."""
        if not token:
            return None
        token_hash = _hash_token(token)
        # Read and delete atomically. Single use is a security property, not a
        # tidiness one: split across two statements, two concurrent redemptions
        # of the same link could both read the row before either deleted it and
        # both succeed — which is exactly the replay this is meant to prevent.
        with self._db.transaction() as tx:
            row = tx.query_one(
                "SELECT user_id, expires_at FROM password_resets WHERE token_hash = ?",
                (token_hash,),
            )
            if row is not None:
                tx.execute("DELETE FROM password_resets WHERE token_hash = ?", (token_hash,))
        if row is None or row[1] < time.time():
            return None
        return row[0]


def auth_enabled() -> bool:
    """Multi-tenant mode. Off by default so the CLI and single-user/self-host
    deployments are unchanged; required for any shared deployment."""
    return os.getenv("IACTRANSLATE_AUTH", "none").strip().lower() == "session"


def create_account_store() -> Optional[AccountStore]:
    """Build the account store when `IACTRANSLATE_AUTH=session`, else None."""
    if not auth_enabled():
        return None
    return AccountStore(create_database())
