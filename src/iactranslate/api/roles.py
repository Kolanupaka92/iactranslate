"""Project roles — separation of duties, not just ownership.

Before this there was exactly one relationship a user could have with a project:
they owned it, or it did not exist for them. That is a tenancy boundary, not
access control, and it makes several ordinary requests impossible to satisfy:

  * an auditor who must **read** a migration plan without being able to change it
  * a reviewer who approves a plan but does not run it
  * a contractor who runs translations but must not delete the workspace
  * anyone at all seeing a colleague's project

Four roles, ordered, because access control that cannot be ordered cannot be
compared and every check becomes a special case:

======== ====================================================================
viewer   Read the project, its plan, reports and downloads.
editor   Everything a viewer can do, plus upload and run.
approver Everything an editor can do, plus mark a plan approved. Kept distinct
         from `admin` because "may approve" and "may grant access" are the two
         permissions an auditor most wants separated — an editor who can also
         grant themselves approval defeats the point of having approval.
admin    Everything, plus granting and revoking access, and deletion.
======== ====================================================================

**The owner is always admin** and cannot be demoted or removed. A project whose
last administrator revoked themselves is unadministrable, and recovering it
means a database edit.

**Absence is still 404, never 403.** A 403 confirms the id exists, which lets an
attacker enumerate other tenants' projects — the reasoning that made ownership
404 in ADR 0027 applies unchanged to roles. The one deliberate exception is a
caller who *does* have some access but not enough: they already know the project
exists, so 403 tells them nothing new and 404 would be actively confusing.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Dict, List, Optional

from .sql import Database, backend, create_database


class Role(str, Enum):
    VIEWER = "viewer"
    EDITOR = "editor"
    APPROVER = "approver"
    ADMIN = "admin"


#: Rank for comparison. Higher includes every capability below it.
_RANK: Dict[Role, int] = {
    Role.VIEWER: 0,
    Role.EDITOR: 1,
    Role.APPROVER: 2,
    Role.ADMIN: 3,
}


def at_least(held: Role, required: Role) -> bool:
    """Does `held` satisfy `required`?"""
    return _RANK[held] >= _RANK[required]


def parse_role(value: str) -> Role:
    """Parse a role name, listing the alternatives when it is wrong.

    An API that rejects `"Editor"` with "invalid role" and nothing else costs
    the caller a round trip to the docs.
    """
    try:
        return Role(value.strip().lower())
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"unknown role '{value}' — expected one of "
            f"{', '.join(r.value for r in Role)}"
        ) from exc


class Membership:
    """Who may do what on which project.

    Kept separate from `ProjectStore` because a grant outlives no project and
    belongs to neither the project nor the user alone. The in-memory
    implementation is the single-node realization; the interface is what a
    Postgres-backed version would implement.
    """

    def __init__(self) -> None:
        self._grants: Dict[str, Dict[str, Role]] = {}

    def grant(self, project_id: str, user_id: str, role: Role) -> None:
        self._grants.setdefault(project_id, {})[user_id] = role

    def revoke(self, project_id: str, user_id: str) -> bool:
        """Remove a grant. Returns False when there was nothing to remove."""
        grants = self._grants.get(project_id)
        if not grants or user_id not in grants:
            return False
        del grants[user_id]
        if not grants:
            del self._grants[project_id]
        return True

    def role_for(self, project_id: str, user_id: str, owner_id: Optional[str]) -> Optional[Role]:
        """The role `user_id` holds, or None for no access at all.

        The owner is admin by construction, so an explicit grant can never
        reduce the owner's access — a project whose owner was demoted to viewer
        would be unadministrable by anyone.
        """
        if owner_id is not None and user_id == owner_id:
            return Role.ADMIN
        return self._grants.get(project_id, {}).get(user_id)

    def members(self, project_id: str, owner_id: Optional[str]) -> List[dict]:
        """Everyone with access, owner first."""
        out: List[dict] = []
        if owner_id is not None:
            out.append({"user_id": owner_id, "role": Role.ADMIN.value, "owner": True})
        for user_id, role in sorted(self._grants.get(project_id, {}).items()):
            if user_id != owner_id:
                out.append({"user_id": user_id, "role": role.value, "owner": False})
        return out

    def projects_for(self, user_id: str) -> List[str]:
        """Project ids shared with this user (excluding ones they own)."""
        return sorted(pid for pid, g in self._grants.items() if user_id in g)

    def drop_project(self, project_id: str) -> None:
        """Forget every grant on a deleted project, so a recycled id cannot
        inherit access from a project that no longer exists."""
        self._grants.pop(project_id, None)


class SqlMembership(Membership):
    """The same grants, persisted.

    Without this, a deployment with `IACTRANSLATE_STORE=sqlite` keeps its
    projects across a restart and loses *who may see them* — the surviving
    projects become visible only to their owners, and every grant an
    administrator made is silently revoked. Half-durable authorization is worse
    than none, because nobody notices until someone reports they lost access.

    Every read goes to the database (see below); the table is keyed on
    (project_id, user_id), so the per-request check is a primary-key lookup.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS project_members (
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            granted_at {REAL} NOT NULL,
            PRIMARY KEY (project_id, user_id)
        )
    """

    def __init__(self, db: "Database | str") -> None:
        super().__init__()
        # A path string opens SQLite, which is how this was always constructed.
        self._db = db if isinstance(db, Database) else Database("sqlite", db)
        self._db.execute(self._db.schema(self._SCHEMA))

    # Every read goes to the database. These used to be served from a
    # per-process cache loaded at startup, so on a deployment with several
    # instances a grant made on one was invisible on the others, and a
    # revocation kept working where the old grant was cached, until a restart.

    @staticmethod
    def _role(value: str) -> Optional[Role]:
        try:
            return Role(value)
        except ValueError:
            # A role written by a newer version: that one grant is unreadable,
            # which costs one person access instead of failing the request.
            return None

    def role_for(self, project_id: str, user_id: str, owner_id: Optional[str]) -> Optional[Role]:
        if owner_id is not None and user_id == owner_id:
            return Role.ADMIN
        row = self._db.query_one(
            "SELECT role FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id)
        )
        return self._role(row[0]) if row else None

    def members(self, project_id: str, owner_id: Optional[str]) -> List[dict]:
        out: List[dict] = []
        if owner_id is not None:
            out.append({"user_id": owner_id, "role": Role.ADMIN.value, "owner": True})
        for user_id, role in self._db.query(
            "SELECT user_id, role FROM project_members WHERE project_id=? ORDER BY user_id", (project_id,)
        ):
            if user_id != owner_id and self._role(role) is not None:
                out.append({"user_id": user_id, "role": role, "owner": False})
        return out

    def projects_for(self, user_id: str) -> List[str]:
        return [r[0] for r in self._db.query(
            "SELECT project_id FROM project_members WHERE user_id=? ORDER BY project_id", (user_id,)
        )]

    def grant(self, project_id: str, user_id: str, role: Role) -> None:
        # Both engines implement this spelling of upsert identically.
        self._db.execute(
            "INSERT INTO project_members (project_id, user_id, role, granted_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
            (project_id, user_id, role.value, time.time()),
        )

    def revoke(self, project_id: str, user_id: str) -> bool:
        return self._db.execute(
            "DELETE FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id)
        ) > 0

    def drop_project(self, project_id: str) -> None:
        self._db.execute("DELETE FROM project_members WHERE project_id=?", (project_id,))


#: The engine-specific name used before the two were unified.
SqliteMembership = SqlMembership


def create_membership() -> Membership:
    """Follows `IACTRANSLATE_STORE`, like the project store and the job queue.

    One switch, so a deployment cannot end up persisting some of its state and
    losing the rest.
    """
    if backend() in {"sqlite", "postgres"}:
        return SqlMembership(create_database())
    return Membership()
