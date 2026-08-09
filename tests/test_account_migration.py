"""Environment accounts migrate once, then that variable is dead (R16, D-009).

Replace-and-remove, never coexist. An install configured the old way —
``WEAVE_AUTH_ACCOUNTS='alice:pw1,bob:pw2'`` — must come up on P1 with those
people intact, and must then be servable with the variable unset.

The assertion that matters most is idempotence. A migration that ran on every
boot would silently reset a password an administrator had changed in the UI: the
second restart would undo the first day of real use, and nobody would connect the
two events.

:func:`test_nothing_outside_the_migration_reads_the_variable` is the structural
half of R16 and part of the M1 gate. Two sources of truth for a password is not
a bug that shows up in a test — it shows up months later as "why did my password
change back", which is unfalsifiable after the fact.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from weave.server.migrate_accounts import (
    ACCOUNTS_VAR,
    ROLES_VAR,
    migrate_env_accounts,
    parse_accounts,
    parse_roles,
)
from weave.server.users import InMemoryUserStore, UserService

REPO = pathlib.Path(__file__).resolve().parent.parent


def _service() -> UserService:
    return UserService(InMemoryUserStore())


# ── parsing the old format ───────────────────────────────────────────────────


@pytest.mark.offline
def test_accounts_parse():
    assert parse_accounts("alice:pw1,bob:pw2") == {"alice": "pw1", "bob": "pw2"}


@pytest.mark.offline
def test_a_password_may_contain_a_colon():
    """Everything after the *first* colon is the password.

    This is exactly why roles were ever a second variable, and getting it wrong
    would silently truncate somebody's password to its first segment — they
    would simply be unable to sign in, with no error that says why.
    """
    assert parse_accounts("alice:pw:with:colons") == {"alice": "pw:with:colons"}


@pytest.mark.offline
@pytest.mark.parametrize("raw", ["", "   ", "garbage", ",,,", "novalue"])
def test_malformed_entries_are_skipped_not_fatal(raw):
    assert parse_accounts(raw) == {}


@pytest.mark.offline
def test_roles_parse_separately():
    assert parse_roles("alice:architect, bob:manager") == {
        "alice": "architect", "bob": "manager"}


# ── the migration ────────────────────────────────────────────────────────────


@pytest.mark.offline
def test_accounts_become_real_users_with_their_roles():
    users = _service()
    result = migrate_env_accounts(users, env={
        ACCOUNTS_VAR: "alice:a-good-password,bob:another-password",
        ROLES_VAR: "alice:architect",
    })

    assert sorted(result.migrated) == ["alice", "bob"]
    alice = users.by_username("alice")
    assert alice is not None and alice.role == "architect"
    assert users.by_username("bob").role == "user"
    # the password came across, hashed
    assert users.authenticate("alice", "a-good-password") is not None
    assert alice.password_hash != "a-good-password"


@pytest.mark.offline
def test_migrated_accounts_keep_working_by_default():
    """They could see everything under the old scheme; they must still be able to."""
    users = _service()
    migrate_env_accounts(users, env={ACCOUNTS_VAR: "alice:a-good-password"})
    assert users.by_username("alice").may_access("default")


@pytest.mark.offline
def test_a_second_run_is_a_no_op():
    """THE ONE THAT MATTERS. Re-running must not reset a changed password."""
    users = _service()
    env = {ACCOUNTS_VAR: "alice:the-original-password"}
    migrate_env_accounts(users, env=env)

    alice = users.by_username("alice")
    users.set_password(alice.id, "the-password-she-chose")

    second = migrate_env_accounts(users, env=env)

    assert second.migrated == []
    assert second.skipped == ["alice"]
    assert users.authenticate("alice", "the-password-she-chose") is not None, (
        "the migration reset a password an administrator had changed"
    )
    assert users.authenticate("alice", "the-original-password") is None


@pytest.mark.offline
def test_nothing_happens_when_the_variable_is_unset():
    users = _service()
    result = migrate_env_accounts(users, env={})
    assert not result.ran
    assert users.list_users() == []


@pytest.mark.offline
def test_the_install_serves_with_the_variable_unset_afterwards():
    """The M1 gate: migrate on boot, then run without it."""
    users = _service()
    migrate_env_accounts(users, env={ACCOUNTS_VAR: "alice:a-good-password"})

    # Reboot, this time with a clean environment.
    migrate_env_accounts(users, env={})

    assert users.any_user_exists
    assert users.authenticate("alice", "a-good-password") is not None


@pytest.mark.offline
def test_one_unmigratable_account_does_not_stop_the_others():
    """A password too short for the store must not block a server boot — and
    must not vanish silently either."""
    users = _service()
    result = migrate_env_accounts(users, env={
        ACCOUNTS_VAR: "alice:a-good-password,bob:short",
    })
    assert result.migrated == ["alice"]
    assert "bob" in result.failed
    assert users.by_username("bob") is None


# ── the structural half of R16 ───────────────────────────────────────────────


@pytest.mark.offline
def test_nothing_outside_the_migration_reads_the_variable():
    """`grep -r WEAVE_AUTH_ACCOUNTS` finds one module, and it is this one.

    Part of the M1 gate. If configuration also read it, a stale environment
    string and a persisted user would disagree about somebody's password the
    moment either changed — and the disagreement would be invisible until a
    restart picked the other one.
    """
    pattern = re.compile(r"\bWEAVE_AUTH_(ACCOUNTS|ROLES)\b")
    offenders = []
    for pkg in ("weave", "weave_core"):
        for path in (REPO / pkg).rglob("*.py"):
            if "webui" in path.parts or "__pycache__" in path.parts:
                continue
            if path.name == "migrate_accounts.py":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:90]}")
    assert not offenders, (
        "environment accounts are read outside the migration — the store and the "
        "environment would be two sources of truth for a password:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.offline
def test_configuration_exposes_no_account_settings():
    """The variables are gone from the config surface, not merely unused."""
    from weave.server import config

    args = config.global_args
    assert not hasattr(args, "auth_accounts"), (
        "config still carries auth_accounts; something can still read it"
    )
    assert not hasattr(args, "auth_roles")
