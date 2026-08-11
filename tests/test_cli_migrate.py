"""`weave migrate reviews` — the migration an operator can actually run (W11).

`migrate_reviews.py` has been correct since P2 and had **no way to invoke it**.
Running it meant hand-writing a script that constructed a task store and a graph,
which is exactly what seeding the demo tenant required — and exactly why the
migration sat unrun for four phases while its unit tests stayed green.

A migration an operator cannot invoke is a migration that will not be run, so
these tests exercise the **command**, not the library beneath it. The library's
own guarantees (100% by count and content, idempotent) are asserted in
`test_migrate_reviews.py`; what is new here is that a person can reach them.
"""

from __future__ import annotations

import json

import pytest

from weave.cli import main
from weave.team.store import JsonWeaveTaskStore, WeaveTask

pytestmark = pytest.mark.offline

WORKSPACE = "demo"


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """A working directory laid out the way the server lays one out."""
    monkeypatch.setenv("WEAVE_WORKING_DIR", str(tmp_path))
    tasks = JsonWeaveTaskStore(str(tmp_path / "weave"))
    tasks.save(WORKSPACE, WeaveTask(
        id="TASK-1", title="wire the guard",
        reviews=[{"verdict": "approve", "by": "architect", "notes": "looks right"},
                 {"verdict": "flag", "by": "review-agent", "notes": "governance"}],
        learnings=["a guard in an adapter protects only that adapter's callers",
                   "an allowlist entry is a claim and needs checking"],
    ))
    tasks.save(WORKSPACE, WeaveTask(id="TASK-2", title="second",
                                    learnings=["pin the claim tests"]))
    return tmp_path


def _run(*argv) -> int:
    # The natural order — flags after the subcommand, as the guide writes it.
    return main(["migrate", "reviews", "--workspace", WORKSPACE, "--json", *argv])


# ── the command reaches the migration ────────────────────────────────────────


def test_a_dry_run_reports_without_writing(store_dir, capsys):
    """The documented first step, and the number it prints is the number a real
    run will create — otherwise a dry run is decoration."""
    assert _run("--dry-run") == 0

    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["reviews_found"] == 2 and report["learnings_found"] == 3
    assert report["nodes_created"] == 5

    # A dry run creates no *nodes*. It does open the graph store, which creates
    # an empty graph file where a workspace had none — so the claim is "no nodes
    # were written", not "nothing touched the disk". Asserted as the former,
    # because the latter is not true and a test should not pretend otherwise.
    assert _run("--verify") == 1, "a dry run created nodes"


def test_applying_creates_the_nodes(store_dir, capsys):
    assert _run() == 0

    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is False
    assert report["nodes_created"] == 5


def test_a_second_run_creates_nothing(store_dir, capsys):
    """Idempotence through the command, not just the function — an operator who
    is unsure whether it ran should be able to simply run it again."""
    _run()
    capsys.readouterr()

    assert _run() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["nodes_created"] == 0
    assert report["nodes_already_present"] == 5


def test_verify_reports_complete_after_a_run(store_dir, capsys):
    _run()
    capsys.readouterr()

    assert _run("--verify") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is True
    assert report["checked"] == 5
    assert report["missing"] == [] and report["mismatched"] == []


def test_verify_before_migrating_reports_incomplete_and_exits_nonzero(store_dir, capsys):
    """The honest answer to "has this run?" — and a non-zero exit so a script can
    ask it without parsing prose."""
    assert _run("--verify") == 1

    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is False
    assert len(report["missing"]) == 5


def test_the_migration_survives_being_reached_across_workspaces(store_dir, capsys):
    """A workspace with nothing to migrate is a no-op, not an error — an operator
    sweeping several should not have to know which ones have data."""
    assert main(["migrate", "reviews", "--workspace", "empty", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["nodes_created"] == 0 and report["tasks"] == 0


# ── it is discoverable ───────────────────────────────────────────────────────


def test_migrate_is_a_documented_subcommand():
    """W11's actual complaint: the capability existed and could not be found."""
    from weave.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["migrate"])   # requires an action

    help_text = build_parser().format_help()
    assert "migrate" in help_text


def test_human_output_names_what_to_do_next(store_dir, capsys):
    """The non-JSON path is what a person sees, and it should end with the next
    step rather than a bare count."""
    main(["migrate", "reviews", "--workspace", WORKSPACE, "--dry-run"])
    out = capsys.readouterr().out

    assert "would create" in out
    assert "no nodes were created" in out
    assert "Re-run without --dry-run" in out
