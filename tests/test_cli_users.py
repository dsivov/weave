"""`weave user …` — the local administration command (P2.0, M1 review M3).

This command exists because M1's gate found a real hole: after migrating
environment accounts, **nobody could administer users**. The HTTP bootstrap
window closes on the first user, and a migrated install has users but no admin,
because the scheme it migrated from had no such concept. A local command is the
right answer — running it already requires access to the machine and its
storage, which is more authority than any HTTP caller has.

What is asserted here is mostly that it is *not a second implementation*: the
rules live in `UserService`, and the console must be a thin adapter over the
same object the routers call (A9). A CLI that quietly accepted a weak password,
or let the last administrator demote themselves, would be a governance bypass
wearing a friendly face.
"""

from __future__ import annotations

import json

import pytest

from weave.cli import main
from weave.server.users import ACTIVE, JsonUserStore, UserService


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """A working directory the command discovers the way an operator's would."""
    monkeypatch.setenv("WEAVE_WORKING_DIR", str(tmp_path))
    return tmp_path


def _service(store_dir) -> UserService:
    """Read the store back independently of the command that wrote it."""
    return UserService(JsonUserStore(str(store_dir)))


# ── the four subcommands ─────────────────────────────────────────────────────


@pytest.mark.offline
def test_add_creates_a_user_that_persists(store_dir, capsys):
    code = main(["user", "add", "alice", "--role", "admin",
                 "--password", "a-good-password", "--workspaces", "alpha,beta"])

    assert code == 0
    assert "Created 'alice'" in capsys.readouterr().out

    alice = _service(store_dir).by_username("alice")
    assert alice is not None, "the command reported success but wrote nothing"
    assert alice.role == "admin" and alice.status == ACTIVE
    assert alice.workspaces == ["alpha", "beta"]
    assert alice.may_access("alpha") and not alice.may_access("gamma")


@pytest.mark.offline
def test_add_stores_a_hash_and_never_the_password(store_dir):
    main(["user", "add", "alice", "--password", "a-good-password"])

    on_disk = json.dumps([json.loads(p.read_text()) for p in store_dir.glob("*.json")])
    assert "a-good-password" not in on_disk, "the password was written to disk"

    alice = _service(store_dir).by_username("alice")
    assert alice.password_hash and alice.password_hash != "a-good-password"
    assert alice.password_hash.startswith("$2")  # bcrypt


@pytest.mark.offline
def test_list_reports_role_status_and_grants(store_dir, capsys):
    main(["user", "add", "alice", "--role", "admin", "--password", "a-good-password",
          "--workspaces", "alpha"])
    main(["user", "add", "bob", "--role", "developer", "--password", "a-good-password"])
    capsys.readouterr()

    assert main(["user", "list"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "admin" in out and "alpha" in out
    assert "bob" in out and "developer" in out
    # A user with no grants reads as "-", not as an empty column.
    assert "-" in out


@pytest.mark.offline
def test_list_on_an_empty_store_says_so(store_dir, capsys):
    assert main(["user", "list"]) == 0
    assert "No users yet." in capsys.readouterr().out


@pytest.mark.offline
def test_promote_changes_the_role(store_dir, capsys):
    main(["user", "add", "alice", "--role", "developer", "--password", "a-good-password"])
    # A second admin, so the last-administrator guard is not what is under test.
    main(["user", "add", "root", "--role", "admin", "--password", "a-good-password"])
    capsys.readouterr()

    assert main(["user", "promote", "alice", "--role", "admin"]) == 0
    assert _service(store_dir).by_username("alice").role == "admin"


@pytest.mark.offline
def test_passwd_sets_a_new_password(store_dir):
    main(["user", "add", "alice", "--password", "a-good-password"])
    before = _service(store_dir).by_username("alice").password_hash

    assert main(["user", "passwd", "alice", "--password", "another-good-password"]) == 0

    service = _service(store_dir)
    after = service.by_username("alice").password_hash
    assert after != before
    assert service.authenticate("alice", "another-good-password") is not None
    assert service.authenticate("alice", "a-good-password") is None


# ── it is an adapter, not a second implementation (A9) ───────────────────────


@pytest.mark.offline
def test_a_weak_password_is_refused_by_the_console_too(store_dir):
    """The policy lives in UserService. If the console could route around it,
    the easiest way to create a weak account would be the local one."""
    with pytest.raises(SystemExit) as exc:
        main(["user", "add", "alice", "--password", "short"])

    assert "password" in str(exc.value).lower()
    assert _service(store_dir).by_username("alice") is None


@pytest.mark.offline
def test_a_duplicate_username_is_refused(store_dir):
    main(["user", "add", "alice", "--password", "a-good-password"])
    with pytest.raises(SystemExit):
        main(["user", "add", "alice", "--password", "a-good-password"])
    assert len(_service(store_dir).list_users()) == 1


@pytest.mark.offline
def test_the_last_administrator_cannot_be_demoted_from_the_console(store_dir):
    """The guard that stops an irreversible lockout is enforced in the service,
    so it must hold here exactly as it does over HTTP."""
    main(["user", "add", "root", "--role", "admin", "--password", "a-good-password"])

    with pytest.raises(SystemExit):
        main(["user", "promote", "root", "--role", "developer"])

    assert _service(store_dir).by_username("root").role == "admin"


@pytest.mark.offline
@pytest.mark.parametrize("argv", [
    ["user", "promote", "nobody", "--role", "admin"],
    ["user", "passwd", "nobody", "--password", "a-good-password"],
])
def test_an_unknown_user_is_named_not_silently_ignored(store_dir, argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert "nobody" in str(exc.value)


# ── the store is discovered the way an operator expects ──────────────────────


@pytest.mark.offline
def test_working_dir_flag_overrides_the_environment(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("WEAVE_WORKING_DIR", str(tmp_path / "ignored"))

    main(["user", "--working-dir", str(elsewhere), "add", "alice",
          "--password", "a-good-password"])

    assert UserService(JsonUserStore(str(elsewhere))).by_username("alice") is not None
    assert not (tmp_path / "ignored").exists(), "wrote to the environment path anyway"


# ── one surface, not two (the milestone rule) ────────────────────────────────


@pytest.mark.offline
def test_the_superseded_entry_point_is_gone():
    """`python -m weave.server.users` was P1's emergency hatch. The plan folds it
    into `weave user`, and the house rule is that both do not survive the
    milestone that replaces one with the other — a second admin surface is a
    second place for the rules to drift."""
    import weave.server.users as users_module

    assert not hasattr(users_module, "main"), (
        "weave.server.users still exposes a CLI entry point; `weave user` "
        "replaced it and two admin surfaces must not both survive"
    )


@pytest.mark.offline
def test_the_console_script_is_declared():
    """`weave` must be an installed command, not just an importable function —
    an operator locked out of HTTP cannot be told to write Python."""
    import tomllib
    import pathlib

    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts["weave"] == "weave.cli:main"
