"""Organisations — projects that belong to a team, not to one person.

Before this, a project belonged to its creator and was shared one person at a
time (project roles, ADR 0050). That works for a consultant and two clients. It
fails for a migration team of fifteen: every new teammate needs a grant on
every project, and a project leaves when its creator does.

An organisation has members with one of three roles:

======= ======================================================================
member  Works on the org's projects (editor on each) and creates new ones.
admin   Administers the org's projects, invites people, changes roles.
owner   Everything an admin can do, plus managing owners and deleting the org.
        There is always at least one.
======= ======================================================================

**Access is the higher of two roles.** A project grant still works on an org
project, and an org role can't be lowered by one: an org admin is admin on
every org project.

**Joining is by invitation only.** An admin invites an address. The person
accepts while signed in with that address, either from the emailed link or
in the app. Accepting in the app needs a verified address (ADR 0079). Without
that, anyone who registered the invited address first could accept for its
owner.

**No caching.** Every check reads the database. The API runs on several
instances, and a membership cached in one would outlive its revocation there.

One implementation over `Database`. The in-memory backend is an in-memory
SQLite database, rather than a second copy of this logic that could drift.
"""
from __future__ import annotations

import hashlib
import secrets
import time
import unicodedata
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .roles import Role
from .sql import Database, backend, create_database

_INVITE_TTL_SECONDS = 7 * 24 * 3600
MAX_NAME_LENGTH = 80


class OrgRole(str, Enum):
    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


_RANK: Dict[OrgRole, int] = {OrgRole.MEMBER: 0, OrgRole.ADMIN: 1, OrgRole.OWNER: 2}

#: What an org role grants on each of the org's projects.
PROJECT_ROLE: Dict[OrgRole, Role] = {
    OrgRole.MEMBER: Role.EDITOR,
    OrgRole.ADMIN: Role.ADMIN,
    OrgRole.OWNER: Role.ADMIN,
}


def org_at_least(held: OrgRole, required: OrgRole) -> bool:
    return _RANK[held] >= _RANK[required]


def parse_org_role(value: str) -> OrgRole:
    try:
        return OrgRole(value.strip().lower())
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"unknown role '{value}' — expected one of {', '.join(r.value for r in OrgRole)}"
        ) from exc


def validate_org_name(name: str) -> str:
    """1–80 printable characters. The name appears in email subjects and
    bodies, so no control characters (a newline in a header is an injection)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("an organisation needs a name")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"organisation names are at most {MAX_NAME_LENGTH} characters")
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        raise ValueError("organisation names can't contain control characters")
    return name


class OrgError(ValueError):
    """A request the org rules refuse; the message is safe to show."""


class LastOwner(OrgError):
    def __init__(self) -> None:
        super().__init__("an organisation must keep at least one owner; make someone else owner first")


@dataclass
class Org:
    id: str
    name: str
    created_at: float


@dataclass
class Invitation:
    id: str
    org_id: str
    email: str
    role: OrgRole
    invited_by: str
    created_at: float
    expires_at: float

    def to_dict(self) -> dict:
        return {"id": self.id, "org_id": self.org_id, "email": self.email, "role": self.role.value,
                "invited_by": self.invited_by, "created_at": self.created_at,
                "expires_at": self.expires_at}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class OrgStore:
    _SCHEMAS = (
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at {REAL} NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS org_members (
            org_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            joined_at {REAL} NOT NULL,
            PRIMARY KEY (org_id, user_id)
        )
        """,
        # The token is stored hashed, like reset tokens: whoever reads this
        # table must not be able to accept invitations with what they find.
        """
        CREATE TABLE IF NOT EXISTS org_invitations (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            invited_by TEXT NOT NULL,
            created_at {REAL} NOT NULL,
            expires_at {REAL} NOT NULL
        )
        """,
    )
    _INVITE_COLUMNS = "id, org_id, email, role, invited_by, created_at, expires_at"

    def __init__(self, db: "Database | str") -> None:
        self._db = db if isinstance(db, Database) else Database("sqlite", db)
        for ddl in self._SCHEMAS:
            self._db.execute(self._db.schema(ddl))
        self._db.execute("CREATE INDEX IF NOT EXISTS org_members_user ON org_members (user_id)")
        self._db.execute("CREATE INDEX IF NOT EXISTS org_invitations_email ON org_invitations (email)")

    # -- organisations ---------------------------------------------------------------

    def create_org(self, name: str, owner_id: str) -> Org:
        org = Org(id=uuid.uuid4().hex[:12], name=validate_org_name(name), created_at=time.time())
        with self._db.transaction() as tx:
            tx.execute("INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
                       (org.id, org.name, org.created_at))
            tx.execute("INSERT INTO org_members (org_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                       (org.id, owner_id, OrgRole.OWNER.value, org.created_at))
        return org

    def get(self, org_id: str) -> Optional[Org]:
        row = self._db.query_one("SELECT id, name, created_at FROM organizations WHERE id = ?", (org_id,))
        return Org(*row) if row else None

    def rename(self, org_id: str, name: str) -> None:
        self._db.execute("UPDATE organizations SET name = ? WHERE id = ?", (validate_org_name(name), org_id))

    def delete_org(self, org_id: str) -> None:
        with self._db.transaction() as tx:
            tx.execute("DELETE FROM org_invitations WHERE org_id = ?", (org_id,))
            tx.execute("DELETE FROM org_members WHERE org_id = ?", (org_id,))
            tx.execute("DELETE FROM organizations WHERE id = ?", (org_id,))

    # -- members ------------------------------------------------------------------

    def role_of(self, org_id: str, user_id: str) -> Optional[OrgRole]:
        row = self._db.query_one("SELECT role FROM org_members WHERE org_id = ? AND user_id = ?",
                                 (org_id, user_id))
        if row is None:
            return None
        try:
            return OrgRole(row[0])
        except ValueError:
            return None  # a role from a newer version: no access rather than a crash

    def orgs_for(self, user_id: str) -> List[Tuple[Org, OrgRole]]:
        rows = self._db.query(
            "SELECT o.id, o.name, o.created_at, m.role FROM org_members m "
            "JOIN organizations o ON o.id = m.org_id WHERE m.user_id = ? ORDER BY o.name, o.id",
            (user_id,),
        )
        out = []
        for oid, name, created, role in rows:
            try:
                out.append((Org(oid, name, created), OrgRole(role)))
            except ValueError:
                continue
        return out

    def members(self, org_id: str) -> List[dict]:
        rows = self._db.query(
            "SELECT user_id, role, joined_at FROM org_members WHERE org_id = ? ORDER BY joined_at, user_id",
            (org_id,),
        )
        return [{"user_id": u, "role": r, "joined_at": j} for u, r, j in rows]

    def _owners(self, org_id: str) -> int:
        row = self._db.query_one("SELECT COUNT(*) FROM org_members WHERE org_id = ? AND role = ?",
                                 (org_id, OrgRole.OWNER.value))
        return row[0] if row else 0

    def set_role(self, org_id: str, user_id: str, role: OrgRole) -> None:
        current = self.role_of(org_id, user_id)
        if current is None:
            raise OrgError("that person isn't a member of this organisation")
        if current == OrgRole.OWNER and role != OrgRole.OWNER and self._owners(org_id) <= 1:
            raise LastOwner()
        self._db.execute("UPDATE org_members SET role = ? WHERE org_id = ? AND user_id = ?",
                         (role.value, org_id, user_id))

    def remove_member(self, org_id: str, user_id: str) -> None:
        current = self.role_of(org_id, user_id)
        if current is None:
            raise OrgError("that person isn't a member of this organisation")
        if current == OrgRole.OWNER and self._owners(org_id) <= 1:
            raise LastOwner()
        self._db.execute("DELETE FROM org_members WHERE org_id = ? AND user_id = ?", (org_id, user_id))

    def sole_owner_of_shared(self, user_id: str) -> List[Org]:
        """Orgs this user alone owns that still have other members: deleting the
        account would leave those members with an org nobody can administer."""
        out = []
        for org, role in self.orgs_for(user_id):
            if role == OrgRole.OWNER and self._owners(org.id) <= 1 and len(self.members(org.id)) > 1:
                out.append(org)
        return out

    def remove_user(self, user_id: str) -> List[str]:
        """Drop the user from every org. Orgs left with no members are deleted;
        their ids are returned so the caller can release their projects."""
        emptied = []
        for org, _ in self.orgs_for(user_id):
            self._db.execute("DELETE FROM org_members WHERE org_id = ? AND user_id = ?", (org.id, user_id))
            if not self.members(org.id):
                self.delete_org(org.id)
                emptied.append(org.id)
        return emptied

    # -- invitations --------------------------------------------------------------

    def _invitation(self, row) -> Invitation:
        return Invitation(id=row[0], org_id=row[1], email=row[2], role=OrgRole(row[3]),
                          invited_by=row[4], created_at=row[5], expires_at=row[6])

    def invite(self, org_id: str, email: str, role: OrgRole, invited_by: str,
               ttl_seconds: int = _INVITE_TTL_SECONDS) -> Tuple[Invitation, str]:
        """Issue an invitation; returns it and the plaintext token. A newer
        invitation for the same address replaces the older one."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        inv = Invitation(id=uuid.uuid4().hex[:12], org_id=org_id, email=email, role=role,
                         invited_by=invited_by, created_at=now, expires_at=now + ttl_seconds)
        with self._db.transaction() as tx:
            tx.execute("DELETE FROM org_invitations WHERE org_id = ? AND email = ?", (org_id, email))
            tx.execute(
                "INSERT INTO org_invitations (id, org_id, email, role, token_hash, invited_by, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (inv.id, org_id, email, role.value, _hash(token), invited_by, now, inv.expires_at),
            )
        return inv, token

    def pending_for_org(self, org_id: str) -> List[Invitation]:
        rows = self._db.query(
            f"SELECT {self._INVITE_COLUMNS} FROM org_invitations "  # nosec B608 — constant
            "WHERE org_id = ? AND expires_at > ? ORDER BY created_at",
            (org_id, time.time()),
        )
        return [self._invitation(r) for r in rows]

    def pending_for_email(self, email: str) -> List[Invitation]:
        rows = self._db.query(
            f"SELECT {self._INVITE_COLUMNS} FROM org_invitations "  # nosec B608 — constant
            "WHERE email = ? AND expires_at > ? ORDER BY created_at",
            (email, time.time()),
        )
        return [self._invitation(r) for r in rows]

    def by_token(self, token: str) -> Optional[Invitation]:
        if not token:
            return None
        row = self._db.query_one(
            f"SELECT {self._INVITE_COLUMNS} FROM org_invitations WHERE token_hash = ?",  # nosec B608
            (_hash(token),),
        )
        return self._invitation(row) if row else None

    def by_id(self, invitation_id: str) -> Optional[Invitation]:
        row = self._db.query_one(
            f"SELECT {self._INVITE_COLUMNS} FROM org_invitations WHERE id = ?",  # nosec B608
            (invitation_id,),
        )
        return self._invitation(row) if row else None

    def revoke_invitation(self, org_id: str, invitation_id: str) -> bool:
        return self._db.execute("DELETE FROM org_invitations WHERE id = ? AND org_id = ?",
                                (invitation_id, org_id)) > 0

    def accept(self, inv: Invitation, user_id: str) -> OrgRole:
        """Join, or keep the higher role if already a member. Single use: the
        invitation is deleted in the same transaction, so a concurrent second
        acceptance finds nothing to accept."""
        if inv.expires_at < time.time():
            raise OrgError("this invitation has expired; ask for a new one")
        current = self.role_of(inv.org_id, user_id)
        role = inv.role if current is None or not org_at_least(current, inv.role) else current
        with self._db.transaction() as tx:
            if tx.execute("DELETE FROM org_invitations WHERE id = ?", (inv.id,)) != 1:
                raise OrgError("this invitation has already been used or withdrawn")
            if current is None:
                tx.execute("INSERT INTO org_members (org_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                           (inv.org_id, user_id, role.value, time.time()))
            elif role != current:
                tx.execute("UPDATE org_members SET role = ? WHERE org_id = ? AND user_id = ?",
                           (role.value, inv.org_id, user_id))
        return role

    def decline(self, inv: Invitation) -> None:
        self._db.execute("DELETE FROM org_invitations WHERE id = ?", (inv.id,))


def create_org_store() -> OrgStore:
    """Follows `IACTRANSLATE_STORE` like every other store."""
    if backend() in {"sqlite", "postgres"}:
        return OrgStore(create_database())
    return OrgStore(Database("sqlite", ":memory:"))
