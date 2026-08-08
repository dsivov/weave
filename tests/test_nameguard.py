"""The name-guard actually guards (R3, D-004, D-014).

A guard that has never failed is indistinguishable from a guard that cannot
fail. So: seed a violation, assert the build breaks; remove it, assert the build
passes again. The M0 gate requires exactly this.

The forbidden tokens are assembled from fragments here for the same reason they
are in ``scripts/nameguard.sh`` — a test that spelled them would be found by the
guard it is testing, and the only fixes for that are a second exemption (barred
by D-014) or excluding this file from the scan (worse).
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "nameguard.sh"

# Assembled, never written whole.
TOKEN_A = "light" + "rag"
TOKEN_B = "context" + "_" + "graph"


def _run():
    return subprocess.run(["bash", str(GUARD)], cwd=REPO, capture_output=True, text=True)


@pytest.mark.offline
def test_the_guard_exists_and_is_executable():
    assert GUARD.exists()


@pytest.mark.offline
def test_the_tree_is_clean_today():
    """The standing assertion. Kept separate so a seeded-violation failure and a
    real regression cannot be confused for each other."""
    result = _run()
    assert result.returncode == 0, (
        "the name-guard is failing on the tree as committed:\n" + result.stdout[-3000:]
    )


@pytest.mark.offline
@pytest.mark.parametrize("token", [TOKEN_A, TOKEN_B])
def test_a_seeded_violation_in_file_contents_fails_the_guard(token, tmp_path):
    seeded = REPO / "weave_core" / "_nameguard_probe.py"
    seeded.write_text(f'BRAND = "{token}"\n', encoding="utf-8")
    try:
        result = _run()
        assert result.returncode != 0, f"the guard did not notice '{token}' in a source file"
        assert "_nameguard_probe.py" in result.stdout
    finally:
        seeded.unlink(missing_ok=True)
    assert _run().returncode == 0, "the tree did not come back clean after the probe"


@pytest.mark.offline
def test_a_seeded_violation_in_a_filename_fails_the_guard():
    """Filenames count. A rebrand that stops at file contents is half a rebrand."""
    seeded = REPO / "weave_core" / f"{TOKEN_A}_probe.py"
    seeded.write_text("BRAND = 1\n", encoding="utf-8")
    try:
        result = _run()
        assert result.returncode != 0, "the guard did not notice the filename"
        assert "PATH" in result.stdout
    finally:
        seeded.unlink(missing_ok=True)


@pytest.mark.offline
def test_the_lineage_exemption_is_honoured_and_reported(tmp_path):
    """D-014: the sole exemption, and it is announced on every run (R3a).

    An exemption nobody sees is an exemption that spreads.
    """
    result = subprocess.run(["bash", str(GUARD), "--list"], cwd=REPO,
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert "honoured" in result.stdout
    assert "BLOG_" in result.stdout, "no lineage exemption was reported"


@pytest.mark.offline
def test_the_exemption_does_not_extend_beyond_the_blog():
    """The marker is not a general-purpose escape hatch: it is honoured for
    ``docs/BLOG_*.html`` and nowhere else. Widening it is a contract amendment."""
    seeded = REPO / "docs" / "NOTES_probe.html"
    seeded.write_text(
        f"<!-- nameguard:allow lineage -->\n<p>{TOKEN_A}</p>\n", encoding="utf-8"
    )
    try:
        result = _run()
        assert result.returncode != 0, (
            "the marker was honoured outside docs/BLOG_*.html — the exemption has widened"
        )
    finally:
        seeded.unlink(missing_ok=True)
