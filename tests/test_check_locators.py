"""The resolver check counts what the M2 gate gates on (R24).

The gate requires **zero** dangling locators, so this is the number the gate
reads. A check that cannot tell "nothing is broken" from "I could not look" is
worse than no check, which is why the outcomes are counted separately and the
exit codes are distinct: 0 clean, 1 dangling, 2 could not run.

The distinction that matters most in practice is **dangling** versus
**unregistered**. A dangling locator is rot — the artifact points at something
that is not there. An unregistered repository means the pointer may be perfectly
good and the *registry* incomplete. They have different fixes, and telling an
operator to fix the wrong one wastes their afternoon, so only the first counts
against the gate.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts.check_locators import ARTIFACT_TYPES, check_workspace
from weave.model.project_layout import (
    InMemoryProjectLayoutStore,
    ProjectLayoutRegistry,
)

pytestmark = pytest.mark.offline

WORKSPACE = "probe"


class FakeGraph:
    def __init__(self, nodes: dict) -> None:
        self._nodes = {k: {"entity_id": k, **v} for k, v in nodes.items()}

    async def get_node(self, node_id):
        return self._nodes.get(node_id)

    async def get_all_labels(self):
        return sorted(self._nodes)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "real.md").write_text("the document an artifact cites\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one")
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
    ).stdout.strip()
    return root, rev


@pytest.fixture
def registry(repo):
    root, _ = repo
    reg = ProjectLayoutRegistry(InMemoryProjectLayoutStore())
    reg.register(
        WORKSPACE, "demo",
        clone_url="https://github.com/example/demo",
        local_path=str(root),
    )
    return reg


def _artifact(path: str, rev: str, **extra) -> dict:
    return {
        "entity_type": "RFC",
        "locator_repo": "demo",
        "locator_path": path,
        "locator_rev": rev,
        **extra,
    }


# ── the four outcomes ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_resolving_locator_is_clean(repo, registry):
    _, rev = repo
    graph = FakeGraph({"RFC-1": _artifact("real.md", rev)})

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["resolved"] == 1
    assert result["failures"] == 0


@pytest.mark.asyncio
async def test_a_dangling_locator_is_reported_with_the_reason(repo, registry):
    """git's own message names whether the path or the revision was wrong, which
    is the distinction someone fixing this needs."""
    _, rev = repo
    graph = FakeGraph({"RFC-2": _artifact("gone.md", rev)})

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["failures"] == 1
    (item,) = result["dangling"]
    assert item["node"] == "RFC-2"
    assert item["locator"]["path"] == "gone.md"
    assert "gone.md" in item["reason"]


@pytest.mark.asyncio
async def test_an_unregistered_repository_is_not_counted_as_rot(repo, registry):
    """Different problem, different fix. The pointer may be fine and the registry
    incomplete, so this must not fail the gate."""
    _, rev = repo
    graph = FakeGraph({
        "RFC-3": {"entity_type": "RFC", "locator_repo": "somewhere-else",
                  "locator_path": "x.md", "locator_rev": rev},
    })

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["failures"] == 0
    assert result["unregistered"] == [{"node": "RFC-3", "repo": "somewhere-else"}]


@pytest.mark.asyncio
async def test_an_artifact_with_no_locator_is_reported_but_does_not_fail(registry):
    """`Commit` and `Module` nodes reflected from the task chain legitimately
    have none yet. Failing the gate on them would make it unpassable for a
    reason unrelated to rot."""
    graph = FakeGraph({"CR-1": {"entity_type": "ChangeRequest", "title": "x"}})

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["without_locator"] == ["CR-1"]
    assert result["failures"] == 0


@pytest.mark.asyncio
async def test_a_partial_locator_counts_as_a_failure(registry):
    """A locator that cannot be parsed is a locator that cannot be followed — and
    a `rev`-less one would resolve against a moving HEAD, which is the rot the
    field exists to prevent."""
    graph = FakeGraph({
        "RFC-4": {"entity_type": "RFC", "locator_repo": "demo",
                  "locator_path": "real.md"},   # no rev
    })

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["failures"] == 1
    assert result["malformed"][0]["node"] == "RFC-4"


# ── scope and counting ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_four_outcomes_are_counted_separately_in_one_pass(repo, registry):
    _, rev = repo
    graph = FakeGraph({
        "RFC-1": _artifact("real.md", rev),
        "RFC-2": _artifact("gone.md", rev),
        "RFC-3": {"entity_type": "RFC", "locator_repo": "elsewhere",
                  "locator_path": "x.md", "locator_rev": rev},
        "CR-1": {"entity_type": "ChangeRequest", "title": "no locator"},
    })

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["resolved"] == 1
    assert len(result["dangling"]) == 1
    assert len(result["unregistered"]) == 1
    assert len(result["without_locator"]) == 1
    assert result["failures"] == 1


@pytest.mark.asyncio
async def test_non_artifact_nodes_are_skipped_entirely(repo, registry):
    """A `Role` is not an artifact and is not expected to point at a document.
    Counting it as "without locator" would bury the ones that matter."""
    _, rev = repo
    graph = FakeGraph({
        "RFC-1": _artifact("real.md", rev),
        "ROLE-1": {"entity_type": "Role", "description": "architect"},
        "WORKER-1": {"entity_type": "Worker", "status": "idle"},
    })

    result = await check_workspace(WORKSPACE, graph, registry)

    assert result["without_locator"] == []
    assert result["resolved"] == 1


@pytest.mark.asyncio
async def test_the_check_covers_every_type_a5_calls_an_artifact():
    """A5 lists the artifact node types. If one is added to the ontology and not
    here, its locators go unchecked and the gate silently narrows."""
    assert {
        "PRD", "RFC", "ArchitectureDecisionRecord", "Diagram", "ChangeRequest",
        "Task", "Feature", "Review", "Insight",
    } <= ARTIFACT_TYPES


@pytest.mark.asyncio
async def test_the_check_is_scoped_to_one_workspace(repo, registry):
    """`resolve` is workspace-scoped, so a repository registered elsewhere reads
    as unregistered here rather than resolving across the boundary (R22a)."""
    _, rev = repo
    graph = FakeGraph({"RFC-1": _artifact("real.md", rev)})

    result = await check_workspace("another-workspace", graph, registry)

    assert result["resolved"] == 0
    assert len(result["unregistered"]) == 1
    assert result["failures"] == 0
