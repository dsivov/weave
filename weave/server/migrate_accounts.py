"""One-time migration of environment accounts into the user store (R16, D-009).

The source configured people as an environment string —
``WEAVE_AUTH_ACCOUNTS='alice:pw1,bob:pw2'`` with a parallel
``WEAVE_AUTH_ROLES='alice:architect'`` — parsed once at boot. An install that
already runs that way must not be stranded by P1, so on first boot the values are
read, turned into real records, and never read again.

**The two never coexist**, which is the point of R16 and the reason this module
is the only thing in the repository that reads those variables. They are gone
from ``weave/server/config.py``: nothing else can consult them, so there is no
window where a stale environment string and a persisted user disagree about
somebody's password. `grep` for the name and this file is the only hit.

Idempotent by construction: a username that already exists is left exactly as it
is. A migration that overwrote on every boot would silently reset a password an
administrator had changed in the UI — the second boot would undo the first day of
real use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from weave.server.users import UserError, UserService
from weave_core.utils import logger

#: Read here and nowhere else.
ACCOUNTS_VAR = "WEAVE_AUTH_ACCOUNTS"
ROLES_VAR = "WEAVE_AUTH_ROLES"


@dataclass
class MigrationResult:
    migrated: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return bool(self.migrated or self.skipped or self.failed)


def parse_accounts(raw: str) -> Dict[str, str]:
    """``'alice:pw1,bob:pw2'`` → ``{'alice': 'pw1', 'bob': 'pw2'}``.

    A password is everything after the *first* colon, which is why roles were
    ever a second variable: a third field here would corrupt any password
    containing a colon.
    """
    out: Dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, password = entry.split(":", 1)
        username = username.strip()
        if username:
            out[username] = password
    return out


def parse_roles(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, role = entry.split(":", 1)
        out[username.strip()] = role.strip()
    return out


def migrate_env_accounts(
    users: UserService,
    *,
    env: Optional[Dict[str, str]] = None,
    workspaces: Optional[List[str]] = None,
) -> MigrationResult:
    """Move any environment-configured accounts into the store, once.

    Args:
        users: the destination store.
        env: the environment to read (defaults to the real one).
        workspaces: workspaces to grant each migrated account. Defaults to
            ``["default"]`` — an install that was working before must keep
            working after, and under the old scheme every account could see
            everything.

    Returns:
        What happened, so the caller can log it rather than guess.
    """
    env = os.environ if env is None else env
    raw_accounts = env.get(ACCOUNTS_VAR, "")
    result = MigrationResult()
    if not raw_accounts.strip():
        return result

    accounts = parse_accounts(raw_accounts)
    roles = parse_roles(env.get(ROLES_VAR, ""))
    grants = list(workspaces) if workspaces is not None else ["default"]

    for username, password in accounts.items():
        if users.by_username(username) is not None:
            # Already migrated on an earlier boot, or created in the UI since.
            # Leaving it alone is what makes a second boot a no-op instead of a
            # password reset.
            result.skipped.append(username)
            continue
        try:
            users.create(
                username=username,
                password=password,
                role=roles.get(username, "user"),
                display_name=username,
                workspaces=grants,
                granted_by="migration",
            )
            result.migrated.append(username)
        except UserError as e:
            # One malformed account must not stop a server booting, and must not
            # be silent either: an operator whose password was too short for the
            # store needs to know which name to fix.
            result.failed[username] = str(e)

    if result.migrated:
        logger.warning(
            f"Migrated {len(result.migrated)} account(s) from {ACCOUNTS_VAR} into the "
            f"user store: {', '.join(sorted(result.migrated))}. "
            f"Remove {ACCOUNTS_VAR} and {ROLES_VAR} from the environment — they are "
            "not read anywhere else, and the store is now the only source of accounts."
        )
    if result.skipped:
        logger.info(
            f"{ACCOUNTS_VAR} still set, but {len(result.skipped)} account(s) already "
            "exist in the store; left untouched. Safe to remove the variable."
        )
    for username, why in result.failed.items():
        logger.error(f"Could not migrate account '{username}': {why}")

    return result
