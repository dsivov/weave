"""The onboarding commands — `init`, `roles`, `project`, `agents` (P6, M6 gate).

The gate is *a clean machine reaching a live fleet by the published steps only,
with no Python called by hand*. That makes these commands the deliverable rather
than a convenience wrapper over one, so what is asserted here is what an operator
would actually notice:

- **each command reaches the state the server reads.** A local command that
  writes somewhere the server does not look is worse than a missing command,
  because it reports success;
- **`roles install` signs.** It is the same installer `POST /weave/bootstrap`
  calls (D-034), and a "local mode" that wrote faster and unsigned would be the
  third instance of that defect;
- **regenerating a role kit changes nothing** (R56);
- **`agents` writes state and dials nothing** (A15).

Every test runs against a `tmp_path` working directory. Nothing here deletes a
path it did not create.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from weave.cli import build_parser, main

pytestmark = pytest.mark.offline


def _run(*argv: str) -> int:
    """Invoke the CLI the way the shell does — through the real parser."""
    return main(list(argv))


def _json(capsys, *argv: str):
    capsys.readouterr()          # drop whatever an earlier command printed
    assert _run(*argv, "--json") == 0
    return json.loads(capsys.readouterr().out)


# ── init ─────────────────────────────────────────────────────────────────────


def test_init_writes_a_real_secret_the_server_will_accept(tmp_path, capsys):
    from weave.cli.server import ENV_FILENAME, PUBLISHED_DEFAULT_SECRET

    assert _run("init", "--working-dir", str(tmp_path)) == 0
    env_file = tmp_path / ENV_FILENAME
    body = env_file.read_text(encoding="utf-8")

    assert "WEAVE_TOKEN_SECRET=" in body
    assert PUBLISHED_DEFAULT_SECRET not in body, (
        "init wrote the secret the server refuses to start on"
    )


def test_the_secret_file_is_not_world_readable(tmp_path):
    """It signs every token the server issues; anyone holding it can mint an
    administrator. Mode is asserted because "we meant to chmod it" is not a
    property."""
    from weave.cli.server import ENV_FILENAME

    _run("init", "--working-dir", str(tmp_path))
    mode = stat.S_IMODE((tmp_path / ENV_FILENAME).stat().st_mode)
    assert mode == 0o600, f"weave.env is mode {oct(mode)}, expected 0o600"


def test_re_running_init_does_not_log_everybody_out(tmp_path, capsys):
    """Regenerating the secret invalidates every issued token. Re-running a setup
    command is something people do to be sure, and it must not be the thing that
    breaks the install."""
    from weave.cli.server import ENV_FILENAME

    _run("init", "--working-dir", str(tmp_path))
    first = (tmp_path / ENV_FILENAME).read_text(encoding="utf-8")

    assert _run("init", "--working-dir", str(tmp_path)) == 0
    assert (tmp_path / ENV_FILENAME).read_text(encoding="utf-8") == first
    assert "already exists" in capsys.readouterr().out

    assert _run("init", "--working-dir", str(tmp_path), "--force") == 0
    assert (tmp_path / ENV_FILENAME).read_text(encoding="utf-8") != first


def test_up_refuses_the_default_secret_rather_than_starting_open(tmp_path, monkeypatch):
    """The server refuses; `up` says so before getting there, with the fix.

    Reaching the server's own refusal would work too, but the operator would see
    it after a splash screen and a config load, phrased as a server problem
    rather than as the one command they still need to run.
    """
    from weave.cli.server import PUBLISHED_DEFAULT_SECRET

    monkeypatch.setenv("WEAVE_TOKEN_SECRET", PUBLISHED_DEFAULT_SECRET)
    with pytest.raises(SystemExit) as excinfo:
        _run("up", "--working-dir", str(tmp_path))
    assert "weave init" in str(excinfo.value)


# ── roles ────────────────────────────────────────────────────────────────────


def test_roles_install_signs_every_layer(tmp_path, capsys):
    """The same installer the bootstrap route calls, so the same guarantee.

    Checked through the ledger rather than through the report: the report is what
    the command *says* it did, and D-034 was a case of exactly that being true
    while no version existed.
    """
    from weave.cli import _local

    report = _json(capsys, "roles", "install",
                   "--working-dir", str(tmp_path), "--workspace", "team",
                   "--approver", "alice")
    assert report["ontology"] == 1 and report["rbac"] == 1

    args = build_parser().parse_args(
        ["roles", "install", "--working-dir", str(tmp_path), "--workspace", "team"])
    engine = _local.studio_engine(args)
    from weave.team import preset

    for _part, kind in preset.LAYERS:
        versions = engine.history("team", kind, kind)
        assert versions, f"'{kind}' was installed with no ledger version"
        assert versions[-1]["sign_off"]["approver"] == "alice"


def test_roles_install_refuses_to_sign_as_nobody(tmp_path, monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _run("roles", "install", "--working-dir", str(tmp_path),
             "--workspace", "team")
    assert "approver" in str(excinfo.value)


def test_a_role_kit_is_two_files_and_neither_is_ours(tmp_path):
    """A10 in file form: every role is an ordinary Claude Code session, so a kit
    is Claude Code's config and nothing bespoke."""
    out = tmp_path / "kit"
    assert _run("roles", "kit", "--working-dir", str(tmp_path),
                "--workspace", "team", "--role", "developer",
                "--out", str(out)) == 0

    assert sorted(p.name for p in out.iterdir()) == [".mcp.json", "CLAUDE.md"]
    config = json.loads((out / ".mcp.json").read_text(encoding="utf-8"))
    assert "weave" in config["mcpServers"]


def test_regenerating_a_kit_changes_nothing(tmp_path, capsys):
    """R56. Asserted byte-for-byte and by mtime: "equivalent" is not the promise
    — an operator rerunning this should see that nothing moved."""
    out = tmp_path / "kit"
    _run("roles", "kit", "--working-dir", str(tmp_path), "--workspace", "team",
         "--role", "developer", "--out", str(out))
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()}

    capsys.readouterr()
    report = _json(capsys, "roles", "kit", "--working-dir", str(tmp_path),
                   "--workspace", "team", "--role", "developer", "--out", str(out))

    assert report["written"] == [] and sorted(report["unchanged"]) == [
        ".mcp.json", "CLAUDE.md"]
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()}
    assert before == after, "a regenerated kit rewrote a file that had not changed"


def test_one_generator_serves_human_and_agent_roles_alike(tmp_path):
    """R52a. The manager's kit and the developer's differ in content — never in
    how they are produced, and never in which files exist."""
    from weave.cli.roles import _kit_contents

    human = dict(_kit_contents("manager", "team", "http://s:9800"))
    agent = dict(_kit_contents("developer", "team", "http://s:9800"))

    assert set(human) == set(agent) == {".mcp.json", "CLAUDE.md"}
    assert human[".mcp.json"] == agent[".mcp.json"], (
        "the two roles reach a different MCP surface — A9 says one surface"
    )
    assert human["CLAUDE.md"] != agent["CLAUDE.md"]


def test_an_unknown_role_names_the_ones_that_exist(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        _run("roles", "kit", "--working-dir", str(tmp_path),
             "--role", "wizard", "--out", str(tmp_path / "k"))
    assert "developer" in str(excinfo.value)


# ── project ──────────────────────────────────────────────────────────────────


def test_project_register_reaches_what_a_heartbeat_serves(tmp_path, capsys):
    """The property that makes the command worth having: a host asking "what am I
    building?" gets what was registered here, through the same service the
    server's heartbeat path uses."""
    from weave.cli import _local

    _run("project", "register", "--working-dir", str(tmp_path),
         "--workspace", "team", "--repo", "git@github.com:acme/thing.git",
         "--test-command", "python3 -m pytest -q")

    args = build_parser().parse_args(
        ["project", "show", "--working-dir", str(tmp_path), "--workspace", "team"])
    onboarding = _local.project_service(args).get("team").onboarding()
    assert onboarding["repo"] == "git@github.com:acme/thing.git"
    assert onboarding["test_command"] == ["python3", "-m", "pytest", "-q"]


def test_setting_one_field_does_not_reset_the_others(tmp_path, capsys):
    _run("project", "register", "--working-dir", str(tmp_path),
         "--workspace", "team", "--repo", "r", "--test-command", "make test")
    _run("project", "register", "--working-dir", str(tmp_path),
         "--workspace", "team", "--image", "weave-dev-agent:1")

    shown = _json(capsys, "project", "show",
                  "--working-dir", str(tmp_path), "--workspace", "team")
    assert shown["repo"] == "r" and shown["test_command"] == ["make", "test"]
    assert shown["image"] == "weave-dev-agent:1"


def test_an_unconfigured_workspace_answers_instead_of_failing(tmp_path, capsys):
    """"Nothing registered" is the answer to `show`, not an error — a scripted
    walkthrough of the published steps should not stop on a query that
    succeeded."""
    assert _run("project", "show", "--working-dir", str(tmp_path),
                "--workspace", "team") == 0
    out = capsys.readouterr().out
    assert "no repository registered" in out
    assert "weave project register" in out, "the fix should be in the message"


# ── agents ───────────────────────────────────────────────────────────────────


def _register_host(tmp_path, host_id="berlin-01"):
    from weave.cli import _local

    args = build_parser().parse_args(
        ["agents", "list", "--working-dir", str(tmp_path), "--workspace", "team"])
    registry = _local.host_registry(args)
    asyncio.run(registry.register("team", host_id, seat="ok"))
    return registry


def test_scaling_writes_state_a_host_reads_back(tmp_path, capsys):
    """A15, through the command. Nothing is sent; the number is there for the
    machine's next heartbeat to collect."""
    registry = _register_host(tmp_path)

    assert _run("agents", "scale", "--working-dir", str(tmp_path),
                "--workspace", "team", "--host", "berlin-01", "--count", "3") == 0
    assert "Nothing has started yet" in capsys.readouterr().out

    assert registry.heartbeat("team", "berlin-01")["desired_workers"] == 3


def test_up_and_scale_are_the_same_operation(tmp_path):
    registry = _register_host(tmp_path)
    _run("agents", "up", "--working-dir", str(tmp_path), "--workspace", "team",
         "--host", "berlin-01", "--count", "2")
    assert registry.get("team", "berlin-01")["desired_workers"] == 2


def test_down_retires_a_machines_developers(tmp_path):
    registry = _register_host(tmp_path)
    _run("agents", "scale", "--working-dir", str(tmp_path), "--workspace", "team",
         "--host", "berlin-01", "--count", "3")
    _run("agents", "down", "--working-dir", str(tmp_path), "--workspace", "team",
         "--host", "berlin-01")
    assert registry.get("team", "berlin-01")["desired_workers"] == 0


def test_a_stopped_host_says_so_rather_than_refusing_blankly(tmp_path):
    """`stop` is terminal (R73). The natural next move after a bare refusal is to
    try again, so the message has to say why that will not help."""
    _register_host(tmp_path)
    _run("agents", "down", "--working-dir", str(tmp_path), "--workspace", "team",
         "--host", "berlin-01", "--control", "stop")

    with pytest.raises(SystemExit) as excinfo:
        _run("agents", "down", "--working-dir", str(tmp_path), "--workspace", "team",
             "--host", "berlin-01", "--control", "resume")
    assert "terminal" in str(excinfo.value)


def test_scaling_a_machine_nobody_registered_says_how_one_joins(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        _run("agents", "scale", "--working-dir", str(tmp_path),
             "--workspace", "team", "--host", "ghost", "--count", "1")
    assert "weave agents list" in str(excinfo.value)


def test_an_empty_fleet_is_not_a_failure(tmp_path, capsys):
    assert _run("agents", "list", "--working-dir", str(tmp_path),
                "--workspace", "team") == 0
    assert "python3 -m weave.devhost" in capsys.readouterr().out


# ── the layout the local commands and the server must agree on ───────────────


def test_the_cli_and_the_server_lay_out_storage_the_same_way(tmp_path):
    """The failure this prevents reports success: a command that writes to a
    directory the server never reads.

    `weave/server/app.py` is the authority. If it moves a governance directory,
    this fails here rather than leaving an operator to discover that their signed
    RBAC policy is somewhere nothing enforces.
    """
    import pathlib
    import re

    from weave.cli import _local

    app = (pathlib.Path(__file__).resolve().parent.parent
           / "weave" / "server" / "app.py").read_text(encoding="utf-8")

    for name, directory in _local.GOVERNANCE_DIRS.items():
        pattern = rf'working_dir\), *"{re.escape(directory)}"'
        assert re.search(pattern, app), (
            f"the CLI writes '{name}' to <working-dir>/{directory}, and app.py "
            "does not read it from there"
        )

    assert f'working_dir), "{_local.TEAM_DIR}"' in app.replace("str(args.", "").replace(
        "args.", ""), "the CLI and the server disagree on where team state lives"


def test_the_role_init_tells_you_to_create_can_do_something(tmp_path, capsys):
    """`init` prints the next command, and that hint is what gets copied.

    It said `--role admin`, which the governance preset grants nothing to — the
    same trap as the guide's, in the tool's own output where a documentation fix
    could not reach it. Asserted against the preset rather than against the
    string 'manager', so changing either side without the other fails here.
    """
    import re

    from weave.team import preset

    _run("init", "--working-dir", str(tmp_path))
    out = capsys.readouterr().out

    suggested = re.findall(r"weave user add \S+ --role (\S+)", out)
    assert suggested, "init no longer suggests how to create the first user"

    granted = set((preset.load_part("rbac") or {}).get("roles", {}))
    for role in suggested:
        assert role in granted, (
            f"`weave init` suggests creating the first user as '{role}', which "
            f"the preset grants nothing to (it grants: {', '.join(sorted(granted))})"
        )
