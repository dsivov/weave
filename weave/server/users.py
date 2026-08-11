"""The user store — persisted people, not an environment variable (A14, D-009).

The gap this project exists to close. The source had no user store at all: a
single environment string parsed once at boot, no CRUD, no table, no membership.
Adding a person meant editing a file and restarting the server, which is why
"multi-user" was never really true.

Here a user is a record with a bcrypt hash, a governance role, a status, and an
explicit grant per workspace. Written against :mod:`weave_core.store.record` —
the one persistence port — so all three storage paths get the user store for
free and no module builds a database client of its own (A4, D-020).

**Membership is embedded in the user record, not stored beside it.** The data
model names ``WorkspaceMembership`` as a type and it is one; what it is not is a
second *record store*. Two stores would mean granting a workspace is two writes,
deleting a user is a cascade, and a crash between them leaves a grant pointing at
nobody. On the file-based path — whole-file read-modify-write (A4, R10) — that
window is wide. Embedded, a grant is one atomic write and orphans cannot exist.
Listing a workspace's members costs a scan of a small set, which is the right
trade at team scale.

**No endpoint may return a hash** (R17). That is enforced by shape rather than by
discipline: :meth:`User.public_dict` is the only serialiser the routes can reach,
and it has no branch that emits one.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import bcrypt

from weave_core.store.record import InMemoryRecordStore, JsonRecordStore, RecordStore
from weave_core.utils import logger

#: Users are not workspace-scoped — a person exists once and is *granted*
#: workspaces. The record port keys by workspace, so the user set lives in one
#: reserved realm rather than being duplicated per workspace.
SYSTEM_REALM = "_system"

#: bcrypt hashes at most 72 bytes and silently ignores the rest, so a 200-character
#: passphrase would be no stronger than its first 72 bytes. Reject rather than
#: quietly truncate: a password that is not what the user typed is a lie.
MAX_PASSWORD_BYTES = 72

MIN_PASSWORD_LENGTH = 8

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,64}$")

ACTIVE = "active"
DISABLED = "disabled"

#: Roles that can administer users. Deliberately narrow: the ability to create
#: and promote accounts is the ability to grant yourself anything.
#:
#: Defined here rather than in the router because the invariant it supports —
#: *an install always keeps at least one active administrator* — is a property
#: of the user store, not of HTTP. It used to live in the router, which meant
#: the guard protected only callers who arrived over the network; the console
#: could demote the last admin and brick the install it exists to rescue.
ADMIN_ROLES = {"admin", "manager"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserError(ValueError):
    """A bad request about a user — mapped to 400 by the router."""


class UserConflict(UserError):
    """The username is taken, mapped to 409."""


class UserNotFound(UserError):
    """No such user, mapped to 404."""


@dataclass
class WorkspaceMembership:
    """One explicit grant of one workspace to one user (R14).

    Carries who granted it and when, because "why does this person have access"
    is a question that gets asked during incidents, not during setup.
    """

    workspace: str
    granted_by: str = ""
    granted_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkspaceMembership":
        return cls(
            workspace=d["workspace"],
            granted_by=d.get("granted_by", ""),
            granted_at=d.get("granted_at", ""),
        )


@dataclass
class User:
    """A person who can sign in, and what they are allowed to be."""

    id: str
    username: str
    password_hash: str
    role: str = "user"
    display_name: str = ""
    email: str = ""
    status: str = ACTIVE
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_login_at: str = ""
    memberships: List[WorkspaceMembership] = field(default_factory=list)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """The full record, hash included. **Storage only** — never a response."""
        return {
            "id": self.id,
            "username": self.username,
            "password_hash": self.password_hash,
            "role": self.role,
            "display_name": self.display_name,
            "email": self.email,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
            "memberships": [m.to_dict() for m in self.memberships],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "User":
        return cls(
            id=d["id"],
            username=d["username"],
            password_hash=d.get("password_hash", ""),
            role=d.get("role", "user"),
            display_name=d.get("display_name", ""),
            email=d.get("email", ""),
            status=d.get("status", ACTIVE),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            last_login_at=d.get("last_login_at", ""),
            memberships=[WorkspaceMembership.from_dict(m) for m in d.get("memberships", [])],
        )

    def public_dict(self) -> Dict[str, Any]:
        """What an endpoint is allowed to return. **There is no hash in here.**

        The only serialiser the routers can reach, so R17 holds because the
        response shape cannot express a hash — not because every author
        remembered to strip one.
        """
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name,
            "email": self.email,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
            "workspaces": [m.workspace for m in self.memberships],
        }

    # -- membership ---------------------------------------------------------

    @property
    def workspaces(self) -> List[str]:
        return [m.workspace for m in self.memberships]

    def may_access(self, workspace: str) -> bool:
        """Whether this user may see a workspace at all (R14).

        A disabled account is refused everything: the status check lives here
        rather than at each call site so there is one answer to "may they".
        """
        if self.status != ACTIVE:
            return False
        return workspace in self.workspaces

    @property
    def is_active(self) -> bool:
        return self.status == ACTIVE


# ── stores ───────────────────────────────────────────────────────────────────
# Two lines each: the shape is the record port's, and that is the point (D-020).


class InMemoryUserStore(InMemoryRecordStore[User]):
    record_type = User


class JsonUserStore(JsonRecordStore[User]):
    record_type = User
    filename_prefix = "weave_users"


# ── password handling ────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """bcrypt, with the library's limits made explicit rather than silent."""
    validate_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise UserError("A password is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise UserError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
            f"({len(encoded)} given). bcrypt ignores anything beyond that, so a "
            "longer one would be weaker than it looks."
        )
    if "\x00" in password:
        raise UserError("A password may not contain a null byte.")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time where it matters, and never raises on a malformed hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # A corrupt or truncated hash is a failed login, not a 500.
        logger.warning("user store: refusing a login against an unreadable password hash")
        return False


# ── the service ──────────────────────────────────────────────────────────────


class UserService:
    """Everything the routers, the CLI and the migration do to users.

    One place, so the HTTP layer stays a thin adapter over it (A9) and the CLI
    in P6 calls the same functions rather than a second implementation.
    """

    def __init__(self, store: RecordStore[User]) -> None:
        self._store = store

    # -- reads --------------------------------------------------------------

    def list_users(self) -> List[User]:
        return sorted(self._store.list(SYSTEM_REALM), key=lambda u: u.username.lower())

    def get(self, user_id: str) -> Optional[User]:
        return self._store.get(SYSTEM_REALM, user_id)

    def require(self, user_id: str) -> User:
        user = self.get(user_id)
        if user is None:
            raise UserNotFound(f"No user '{user_id}'.")
        return user

    def by_username(self, username: str) -> Optional[User]:
        target = (username or "").strip().lower()
        for user in self._store.list(SYSTEM_REALM):
            if user.username.lower() == target:
                return user
        return None

    @property
    def any_user_exists(self) -> bool:
        """Whether authentication is configured at all.

        Evaluated live rather than snapshotted at import, so creating the first
        user from the Admin UI takes effect on the next request instead of the
        next restart (R13, R39).
        """
        return any(u.is_active for u in self._store.list(SYSTEM_REALM))

    def members_of(self, workspace: str) -> List[User]:
        return [u for u in self.list_users() if u.may_access(workspace)]

    # -- writes -------------------------------------------------------------

    def create(
        self,
        username: str,
        password: str,
        *,
        role: str = "user",
        display_name: str = "",
        email: str = "",
        workspaces: Optional[List[str]] = None,
        granted_by: str = "",
    ) -> User:
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise UserError(
                "A username must be 2–64 characters of letters, digits, dot, dash "
                "or underscore."
            )
        if self.by_username(username) is not None:
            raise UserConflict(f"Username '{username}' is already taken.")

        user = User(
            id=uuid.uuid4().hex,
            username=username,
            password_hash=hash_password(password),
            role=role or "user",
            display_name=display_name or username,
            email=email or "",
            memberships=[
                WorkspaceMembership(workspace=w, granted_by=granted_by)
                for w in dict.fromkeys(workspaces or [])
            ],
        )
        self._store.save(SYSTEM_REALM, user)
        logger.info(f"user store: created '{username}' with role '{user.role}'")
        return user

    def update(
        self,
        user_id: str,
        *,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
    ) -> User:
        user = self.require(user_id)
        if status is not None and status not in (ACTIVE, DISABLED):
            raise UserError(f"Status must be '{ACTIVE}' or '{DISABLED}'.")

        losing_admin = role is not None and role not in ADMIN_ROLES
        losing_active = status == DISABLED
        if losing_admin or losing_active:
            self._require_another_administrator_remains(
                user,
                "demoting" if losing_admin else "disabling",
            )

        if display_name is not None:
            user.display_name = display_name
        if email is not None:
            user.email = email
        if role is not None:
            user.role = role
        if status is not None:
            user.status = status
        user.updated_at = _now()
        self._store.save(SYSTEM_REALM, user)
        return user

    def set_password(self, user_id: str, password: str) -> User:
        user = self.require(user_id)
        user.password_hash = hash_password(password)
        user.updated_at = _now()
        self._store.save(SYSTEM_REALM, user)
        logger.info(f"user store: password reset for '{user.username}'")
        return user

    def set_workspaces(self, user_id: str, workspaces: List[str], *, granted_by: str = "") -> User:
        """Replace the grant list, preserving the provenance of grants that stay.

        A re-grant is not a new grant: keeping ``granted_by``/``granted_at`` for
        workspaces already held means the audit trail survives an unrelated edit
        in the Admin UI.
        """
        user = self.require(user_id)
        existing = {m.workspace: m for m in user.memberships}
        user.memberships = [
            existing.get(w, WorkspaceMembership(workspace=w, granted_by=granted_by))
            for w in dict.fromkeys(workspaces)
        ]
        user.updated_at = _now()
        self._store.save(SYSTEM_REALM, user)
        return user

    def delete(self, user_id: str) -> bool:
        user = self.get(user_id)
        if user is None:
            return False
        self._require_another_administrator_remains(user, "deleting")
        ok = self._store.delete(SYSTEM_REALM, user_id)
        if ok:
            logger.info(f"user store: deleted '{user.username}'")
        return ok

    def _require_another_administrator_remains(self, target: User, verb: str) -> None:
        """Refuse a change that would leave the install with no way back in.

        The invariant is *at least one active administrator exists*, and it is
        enforced here rather than in an adapter so that it holds on every
        surface. It previously lived in the HTTP router, where it protected only
        callers arriving over the network — while the local console, which
        exists precisely because a locked-out install has no network route back,
        could demote or delete the last admin without complaint.

        Only fires when the target is itself the last active administrator, so
        ordinary edits to ordinary accounts are untouched.
        """
        if not (target.role in ADMIN_ROLES and target.status == ACTIVE):
            return
        others = sum(
            1
            for u in self._store.list(SYSTEM_REALM)
            if u.id != target.id and u.status == ACTIVE and u.role in ADMIN_ROLES
        )
        if others == 0:
            raise UserConflict(
                f"'{target.username}' is the only active administrator; {verb} it "
                "would leave this install with no way to administer users. "
                "Promote someone else first."
            )

    def record_login(self, user: User) -> None:
        user.last_login_at = _now()
        self._store.save(SYSTEM_REALM, user)

    # -- authentication -----------------------------------------------------

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """The credential check. Returns the user, or None — never a reason.

        A disabled account and a wrong password are the same answer on purpose:
        telling an attacker which one they got is telling them half the answer.
        The dummy verify keeps the timing of "no such user" close to the timing
        of "wrong password", so the response cannot be used to enumerate names.
        """
        user = self.by_username(username)
        if user is None:
            verify_password(password or "x", _TIMING_DUMMY_HASH)
            return None
        if not user.is_active:
            verify_password(password or "x", _TIMING_DUMMY_HASH)
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user


#: A real bcrypt hash of a random value, computed once, so an unknown username
#: costs the same work as a known one.
_TIMING_DUMMY_HASH = bcrypt.hashpw(
    secrets.token_bytes(16), bcrypt.gensalt()
).decode("utf-8")
