"""A person who signs in must be able to *be* something.

Governance asks what a principal is: a workspace grants `architect` the right to
steer a fleet, not "whoever is logged in". Logging every human in as a generic
`user` left every governed board read-only for real people while service tokens
minted whatever role they liked — so the account's role is resolved on the
server and read from there at login, never from the client (A6, R15).

**Carried across P1, with the mechanism swapped underneath.** This suite arrived
with the fork and tested the same intents against environment accounts. P1
replaced that source with the persisted user store (A14, D-009), so the tests
now exercise the store — every original assertion is still here, asking the same
question of the thing that answers it now. Deleting them and calling the
replacement covered would have quietly dropped the only regression tests that
say *why* roles are configured server-side.
"""

from __future__ import annotations

import pytest

from weave.server.auth import AuthHandler
from weave.server.migrate_accounts import ACCOUNTS_VAR, ROLES_VAR, migrate_env_accounts
from weave.server.users import InMemoryUserStore, UserService


def _handler(users: UserService) -> AuthHandler:
    """An AuthHandler bound to a store, as the application binds it at startup."""
    import weave.server.auth as auth_mod

    class Args:
        token_secret = "a-test-secret-that-is-not-the-published-default"
        jwt_algorithm = "HS256"
        token_expire_hours = 1
        guest_token_expire_hours = 1

    original = auth_mod.global_args
    auth_mod.global_args = Args
    try:
        handler = AuthHandler()
    finally:
        auth_mod.global_args = original
    handler.bind_user_service(users)
    return handler


def _with_accounts(accounts: str, roles: str = "") -> UserService:
    """A store populated the way a migrated install would be."""
    users = UserService(InMemoryUserStore())
    migrate_env_accounts(users, env={ACCOUNTS_VAR: accounts, ROLES_VAR: roles})
    return users


@pytest.mark.offline
def test_an_account_logs_in_as_its_configured_role():
    h = _handler(_with_accounts(
        "dima:a-good-password,sam:a-good-password", "dima:architect,sam:manager"))
    assert h.role_for("dima") == "architect"
    assert h.role_for("sam") == "manager"


@pytest.mark.offline
def test_an_unlisted_account_is_still_a_plain_user():
    """Adding roles must not change what existing deployments hand out."""
    h = _handler(_with_accounts("dima:a-good-password"))
    assert h.role_for("dima") == "user"
    assert h.role_for("nobody") == "user"


@pytest.mark.offline
def test_a_password_containing_a_colon_survives():
    """Why roles were their own variable rather than a third field in the old
    account string: a password is everything after the first colon, so a third
    field would quietly truncate one.

    The migration still has to honour that, or somebody who had been signing in
    for a year suddenly cannot — with no error that says why.
    """
    users = _with_accounts("dima:pw:with:colons", "dima:architect")
    h = _handler(users)
    assert h.authenticate("dima", "pw:with:colons") is not None
    assert h.authenticate("dima", "pw") is None, "the password was truncated at the colon"
    assert h.role_for("dima") == "architect"


@pytest.mark.offline
def test_the_role_reaches_the_token():
    """The role has to be *in* the token — that is what every governance check
    reads, and it is never taken from the client (A6)."""
    h = _handler(_with_accounts("dima:a-good-password", "dima:architect"))
    payload = h.validate_token(h.create_token("dima", role=h.role_for("dima")))
    assert payload["role"] == "architect"
    assert payload["username"] == "dima"


@pytest.mark.offline
def test_whitespace_around_entries_is_tolerated():
    h = _handler(_with_accounts("dima:a-good-password", " dima : architect "))
    assert h.role_for("dima") == "architect"


# ── what changed in P1, asserted rather than assumed ─────────────────────────


@pytest.mark.offline
def test_the_role_now_comes_from_a_record_that_can_be_edited():
    """The point of the replacement.

    Under environment accounts a promotion meant editing a file and restarting.
    The role is a stored field now, so it changes for the next login without
    anything being restarted (R13, R39).
    """
    users = _with_accounts("dima:a-good-password", "dima:developer")
    h = _handler(users)
    assert h.role_for("dima") == "developer"

    users.update(users.by_username("dima").id, role="architect")

    assert h.role_for("dima") == "architect"
    payload = h.validate_token(h.create_token("dima", role=h.role_for("dima")))
    assert payload["role"] == "architect"


@pytest.mark.offline
def test_a_handler_with_no_store_authenticates_nobody():
    """Fail closed. An unbound handler must not fall back to letting people in."""
    import weave.server.auth as auth_mod

    class Args:
        token_secret = "a-test-secret-that-is-not-the-published-default"
        jwt_algorithm = "HS256"
        token_expire_hours = 1
        guest_token_expire_hours = 1

    original = auth_mod.global_args
    auth_mod.global_args = Args
    try:
        handler = AuthHandler()
    finally:
        auth_mod.global_args = original

    assert handler.auth_configured is False
    assert handler.authenticate("dima", "a-good-password") is None
    assert handler.role_for("dima") == "user"


@pytest.mark.offline
def test_auth_is_configured_only_once_an_active_user_exists():
    """`auth_configured` decides whether the server hands out guest tokens, so
    it has to track the store rather than a boot-time snapshot."""
    users = UserService(InMemoryUserStore())
    h = _handler(users)
    assert h.auth_configured is False

    created = users.create("dima", "a-good-password")
    assert h.auth_configured is True, "creating a user did not close the guest window"

    users.update(created.id, status="disabled")
    assert h.auth_configured is False, "a disabled-only install still looked configured"
