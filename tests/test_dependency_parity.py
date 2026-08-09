"""One dependency manifest, projected — never two (D-006, A11, R10).

`environment.yml` is the manifest. `deploy/requirements.txt` exists only because
conda buys nothing inside a container, where the image already *is* the
environment — so it carries the same pins, generated rather than authored.

The failure this prevents is quiet and slow: somebody adds a library to the
container file because that is where the build broke, the conda environment
never learns about it, and from then on the tests and the thing that ships have
different dependency sets. Every symptom of that appears somewhere other than
the cause.

Also asserts that the thirteen libraries P0 removed stay removed, in **both**
files — a dropped dependency that creeps back through the container is still a
dropped dependency that came back.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Dropped in P0 and required to stay dropped: `anthropic` by A13, the rest with
#: the modules that used them (D-007, D-008) or as duplicate tooling (R10).
REMOVED = [
    "anthropic", "python-jose", "lxml", "playwright", "redis", "pymongo",
    "pymilvus", "qdrant-client", "docling", "llama-index", "zhipuai",
    "aioboto3", "voyageai",
]


def _names(text: str) -> set[str]:
    """Distribution names, stripped of version specifiers and extras."""
    names = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lstrip("- ").strip()
        if not line or line.endswith(":"):
            continue
        name = re.split(r"[<>=!~\[]", line, 1)[0].strip()
        if name and not name.startswith(("python", "pip")):
            names.add(name.lower())
    return names


@pytest.mark.offline
def test_the_requirements_file_is_a_faithful_projection():
    """Regenerating must be a no-op. If it is not, the two have drifted."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "sync_requirements.py"), "--check"],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, (
        result.stdout + result.stderr
        + "\n\nRun scripts/sync_requirements.py — do not edit deploy/requirements.txt."
    )


@pytest.mark.offline
def test_the_container_installs_nothing_the_manifest_does_not_declare():
    """The direction that matters. An extra here is a library that ships to
    production and that no developer's environment has."""
    manifest = _names((REPO / "environment.yml").read_text())
    container = _names((REPO / "deploy" / "requirements.txt").read_text())
    extra = container - manifest
    assert not extra, (
        f"deploy/requirements.txt installs {sorted(extra)}, which environment.yml "
        "does not declare. Add it to the manifest and regenerate."
    )


@pytest.mark.offline
def test_no_channel_or_section_name_leaked_into_the_requirements():
    """`conda-forge` is a channel. pip would try to install it."""
    container = _names((REPO / "deploy" / "requirements.txt").read_text())
    for not_a_library in ("conda-forge", "dependencies", "channels", "name"):
        assert not_a_library not in container


@pytest.mark.offline
@pytest.mark.parametrize("library", REMOVED)
def test_a_removed_library_stays_removed_in_both_files(library):
    manifest = _names((REPO / "environment.yml").read_text())
    container = _names((REPO / "deploy" / "requirements.txt").read_text())
    assert library not in manifest, f"{library} came back in environment.yml"
    assert library not in container, f"{library} came back in deploy/requirements.txt"


@pytest.mark.offline
def test_the_container_file_says_it_is_generated():
    """A file people are meant not to hand-edit has to say so at the top."""
    head = (REPO / "deploy" / "requirements.txt").read_text()[:400]
    assert "DO NOT EDIT" in head
    assert "environment.yml" in head
