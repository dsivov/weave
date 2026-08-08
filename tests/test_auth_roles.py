"""A person who signs in must be able to *be* something.

Governance asks what a principal is: a workspace grants `architect` the right to
steer a fleet, not "whoever is logged in". Logging every human in as a generic
`user` left every governed board read-only for real people while service tokens
minted whatever role they liked — so the account's role is configured on the
server and read from there at login.
"""

from __future__ import annotations

import pytest

from weave.server.auth import AuthHandler


def _handler(monkeypatch, accounts: str, roles: str) -> AuthHandler:
    """An AuthHandler built against a given account/role configuration."""
    import weave.server.auth as auth_mod

    class Args:
        auth_accounts = accounts
        auth_roles = roles
        token_secret = "test-secret"
        jwt_algorithm = "HS256"
        token_expire_hours = 1
        guest_token_expire_hours = 1

    monkeypatch.setattr(auth_mod, "global_args", Args)
    return AuthHandler()


@pytest.mark.offline
def test_an_account_logs_in_as_its_configured_role(monkeypatch):
    h = _handler(monkeypatch, "dima:pw,sam:pw", "dima:architect,sam:manager")
    assert h.role_for("dima") == "architect"
    assert h.role_for("sam") == "manager"


@pytest.mark.offline
def test_an_unlisted_account_is_still_a_plain_user(monkeypatch):
    """Adding roles must not change what existing deployments hand out."""
    h = _handler(monkeypatch, "dima:pw", "")
    assert h.role_for("dima") == "user"
    assert h.role_for("nobody") == "user"


@pytest.mark.offline
def test_a_password_containing_a_colon_survives(monkeypatch):
    """Why roles are their own variable rather than a third field in
    AUTH_ACCOUNTS: a password is everything after the first colon there, so a
    third field would quietly truncate one."""
    h = _handler(monkeypatch, "dima:pw:with:colons", "dima:architect")
    assert h.accounts["dima"] == "pw:with:colons"
    assert h.role_for("dima") == "architect"


@pytest.mark.offline
def test_the_role_reaches_the_token(monkeypatch):
    """The role has to be *in* the token — that is what every governance check
    reads, and it is never taken from the client (D5)."""
    h = _handler(monkeypatch, "dima:pw", "dima:architect")
    payload = h.validate_token(h.create_token("dima", role=h.role_for("dima")))
    assert payload["role"] == "architect"
    assert payload["username"] == "dima"


@pytest.mark.offline
def test_whitespace_around_entries_is_tolerated(monkeypatch):
    h = _handler(monkeypatch, "dima:pw", " dima : architect ")
    assert h.role_for("dima") == "architect"
