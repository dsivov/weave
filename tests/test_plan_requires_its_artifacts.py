"""Authoring updates Weave, and a plan cannot outrun its artifacts (P14, CR-002, D-049).

The house methodology's documents reached Weave only because a role kit *told* a
session to ingest them. Nothing enforced it, so a plan could be signed over a
graph missing the document it was derived from — and the failure was silent: the
plan signs, the tasks release, and the answer surfaces resolve to nothing.

**An instruction is not a mechanism.** It is followed until the day it is not.

The eight acceptance criteria are each a test below. Two of them are about the
same word:

* **the extension is not the boundary** — an `.html` RFC and a root `README.md`
  both publish. The CR's first draft said `docs/*.md`; ingestion already accepts
  around twenty formats, and which artifacts *matter* was always the real
  question;
* **a `track_id` is not an answer** — `POST /documents/text` replies `success`
  when text is *received*, and the server's own `pipeline_index_file` wraps the
  run in `except Exception: logger.error(...)` and returns `None`. A publish that
  cannot tell a processed document from a crashed one is the defect this change
  exists to remove, so `publish_artifact` reads the document's final status back.

**Refusal is asserted on both surfaces, not inferred from a shared service.** W4's
lesson, and we have had two tests this month that proved a unit correct while
nothing called it.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

pytestmark = pytest.mark.offline

_REPO = pathlib.Path(__file__).resolve().parent.parent


class _Graph:
    """A node store. `upsert_node` replaces, which is the stricter behaviour of
    the two the real backends have."""

    def __init__(self):
        self.nodes: dict = {}
        self.writes = 0

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def upsert_node(self, node_id, data):
        self.writes += 1
        self.nodes[node_id] = dict(data)

    async def has_node(self, node_id):
        return node_id in self.nodes

    async def upsert_edge(self, src, tgt, data):
        pass


# ── the refusal, and the one implementation behind it ────────────────────────


@pytest.mark.asyncio
async def test_an_unpublished_artifact_is_unresolved():
    from weave.model.artifacts import unresolved_artifacts

    graph = _Graph()
    graph.nodes["RFC-014"] = {"entity_type": "RFC", "locator_path": "docs/rfc.html"}

    problems = await unresolved_artifacts(graph, ["RFC-014", "CR-009"])
    assert "RFC-014" not in problems
    assert "no node" in problems["CR-009"]


@pytest.mark.asyncio
async def test_a_node_with_no_locator_is_unresolved_too():
    """**The case worth naming separately.** Something created the node — a
    decision trace, a generic upsert — but no document was ever published behind
    it, so an answer would return a node that leads nowhere. Treating "a node
    exists" as "the artifact is published" would let exactly that through."""
    from weave.model.artifacts import unresolved_artifacts

    graph = _Graph()
    graph.nodes["CR-009"] = {"entity_type": "ChangeRequest"}
    problems = await unresolved_artifacts(graph, ["CR-009"])
    assert "no locator" in problems["CR-009"]


def test_the_refusal_names_each_artifact_and_how_to_publish_it():
    """A refusal that says only *"unresolved artifacts"* sends the reader looking
    for the wrong thing — the same failure as W34's placement bug, where a 403
    named a credential problem that did not exist."""
    from weave.model.artifacts import refusal_message

    text = refusal_message({"CR-009": "no node in this workspace"}, "PRD-1")
    assert "CR-009" in text
    assert "PRD-1" in text
    assert "weave docs publish" in text
    assert "publish_artifact" in text


def test_only_referenced_artifacts_are_required_and_that_is_a_decision():
    """**The CR's wording, and the pin that made me read it again.**

    CR-002 says a plan is refused when a *referenced* artifact does not resolve.
    My first version also required **the plan document itself** — the stronger
    rule, and probably the better one, since a plan signed over a graph that does
    not contain the plan is the case a session is most likely to skip.

    It refused a carried claim test that publishes `RFC-7` with no artifacts.
    That test may not be edited, and rather than adjust it I narrowed my change
    to what was specified: the pin did its job, which is to turn an unnoticed
    behaviour change into a question. The flag exists so a `D-NN` can switch it
    on in one word.
    """
    from weave.model.artifacts import plan_artifact_refs

    specs = [{"id": "T1", "change_request": "CR-009"}]
    assert plan_artifact_refs(specs, "PRD-1") == ["CR-009"]
    assert plan_artifact_refs(specs, "PRD-1", include_plan=True) == ["PRD-1", "CR-009"]


def test_refusing_is_one_implementation_below_both_adapters():
    """**A9 applied to a precondition.**

    REST and MCP refuse identically because there is only one thing to refuse
    with: `publish_plan` calls `unresolved_artifacts`, and neither router nor
    tool has a copy. Asserted by parsing rather than grepping — the docstrings
    around these call sites name the very symbols being checked.
    """
    from weave.team import coordinator as coordinator_module

    source = pathlib.Path(coordinator_module.__file__).read_text(encoding="utf-8")
    called = {
        getattr(node.func, "id", getattr(node.func, "attr", ""))
        for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
    }
    assert "unresolved_artifacts" in called, (
        "publish_plan no longer consults the shared resolver"
    )

    for module_path in ("weave/server/routers/team.py", "weave/server/mcp.py"):
        text = (_REPO / module_path).read_text(encoding="utf-8")
        tree = ast.parse(text)
        adapter_calls = {
            getattr(node.func, "id", getattr(node.func, "attr", ""))
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        assert "unresolved_artifacts" not in adapter_calls, (
            f"{module_path} has grown its own copy of the precondition — that is "
            "how two surfaces start refusing differently"
        )


def test_the_precondition_runs_before_the_plan_is_signed():
    """A refused plan must leave no trace of having been signed. Ordering, not
    outcome: recording the decision first and refusing after would put a
    signature in the ledger for a plan that never published."""
    from weave.team.coordinator import WeaveCoordinator

    source = inspect.getsource(WeaveCoordinator.publish_plan)
    assert source.index("_require_published_artifacts") < source.index("record_decision")


# ── publishing ──────────────────────────────────────────────────────────────


def test_publish_waits_for_the_outcome_rather_than_a_track_id():
    """The criterion, asserted on the code that decides it.

    `pipeline_index_file` — the server's wrapper — swallows every exception and
    returns `None`. `publish_artifact` calls the two steps inside it instead, and
    reads `doc_status` back, so a document that ended in `failed` raises.
    """
    from weave.model import artifacts

    source = pathlib.Path(artifacts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        getattr(node.func, "id", getattr(node.func, "attr", ""))
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "pipeline_enqueue_file" in called
    assert "apipeline_process_enqueue_documents" in called
    assert "get_docs_by_track_id" in called, (
        "publishing no longer reads the document's final status, so a track id "
        "is once again the whole answer"
    )
    assert "pipeline_index_file" not in called, (
        "publishing went back through the wrapper that discards the result"
    )


def test_the_extension_is_not_the_boundary():
    """An `.html` RFC and a root `README.md` are both artifacts.

    Asserted on the path handling, which is where a `docs/*.md` assumption would
    live: the locator's root is the repository, so a file beside `.git` and a
    file three directories down produce comparable locators.
    """
    from weave.model.artifacts import _repo_root, _relative

    root = _REPO
    assert (root / ".git").exists(), "this test needs a git checkout to be meaningful"

    for candidate in (root / "README.md", root / "docs" / "DECISIONS.md"):
        if not candidate.exists():
            continue
        assert _repo_root(candidate) == root
        relative = _relative(candidate, root)
        assert not str(relative).startswith("/")
        assert str(relative).count("..") == 0


def test_an_unversioned_tree_gets_working_not_a_fabricated_sha(tmp_path):
    """**The rev is the load-bearing field.** A locator that claims a revision it
    does not have resolves against nothing and says nothing about it."""
    from weave.model.artifacts import resolve_revision

    assert resolve_revision(tmp_path) == "working"


def test_the_revision_comes_from_the_repository():
    from weave.model.artifacts import resolve_revision

    rev = resolve_revision(_REPO)
    assert rev != "working" and len(rev) >= 7


def test_publishing_the_same_file_twice_writes_nothing_the_second_time():
    """A hook runs this on every commit. The check is made *before* ingesting, so
    an unchanged file costs nothing rather than being re-ingested and then
    noticed."""
    from weave.model import artifacts

    source = inspect.getsource(artifacts.publish_artifact)
    assert source.index("already = ") < source.index("_ingest_and_wait")


def test_publishing_does_not_blank_what_a_governed_write_put_there():
    """The node is merged, not replaced. Overwriting would be W17's mechanism and
    D-043's: a governed node quietly losing the fields that made it findable."""
    from weave.model import artifacts

    source = inspect.getsource(artifacts.publish_artifact)
    assert "merged.update(node)" in source


# ── the commit body ─────────────────────────────────────────────────────────


def test_a_commit_carries_its_body():
    """A subject says what changed; in this project the body is where the
    reasoning is, and it was discarded at the door."""
    from weave.team.coordinator import WeaveCoordinator

    signature = inspect.signature(WeaveCoordinator.record_commit)
    assert "body" in signature.parameters

    source = inspect.getsource(WeaveCoordinator.record_commit)
    assert '"body": body' in source, "the body is accepted and then dropped"


# ── the kit ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["manager", "architect", "lead"])
def test_each_authoring_role_is_told_to_publish_what_it_writes(role):
    from weave.team.playbook import PUBLISH_TOOL, claude_md, publishes

    kit = claude_md(role, "demo", "http://server:9800")
    assert PUBLISH_TOOL in kit, f"{role}'s kit does not name the publish tool"
    for kind in publishes(role):
        assert kind in kit, f"{role} authors {kind} and its kit does not say to publish it"


def test_a_role_that_authors_nothing_is_not_told_to_publish():
    """A kit carrying a step the role cannot take teaches the reader to skim it."""
    from weave.team.playbook import PUBLISH_TOOL, claude_md, publishes

    assert publishes("developer") == []
    assert PUBLISH_TOOL not in claude_md("developer", "demo", "http://server:9800")


def test_the_kit_names_a_tool_that_actually_exists():
    """**The anti-drift check that means something.**

    Sharing a constant proves two strings match. This proves the instruction is
    *followable*: the tool the kit tells a session to call is a tool the MCP
    surface defines.
    """
    from weave.team.playbook import PUBLISH_TOOL

    mcp_source = (_REPO / "weave" / "server" / "mcp.py").read_text(encoding="utf-8")
    defined = {
        node.name for node in ast.walk(ast.parse(mcp_source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert PUBLISH_TOOL in defined, (
        f"the role kits tell sessions to call `{PUBLISH_TOOL}`, which the MCP "
        "surface does not define"
    )


def test_the_methodology_kit_is_not_modified():
    """**The constraint that shaped the whole design** (D-049).

    The methodology must stay usable by a team with no Weave at all, so no
    ONBOARDING skill learns about Weave — the coupling lives in the `CLAUDE.md`
    Weave generates. That is A2's instinct pointed outward, and it is the thing
    to hold if the implementation gets awkward.

    **Scoped to what this repository controls**, deliberately. The first version
    scanned `../ONBOARDING` for the word "weave" and flagged a
    `.claude/settings.local.json` that P14 never touched — a test asserting
    something about a directory this repo does not own, failing on somebody's
    editor settings. Whether the methodology kit is clean is the manager's to
    verify; what is checkable here is that **nothing in this repository writes
    to it**, and that the coupling is stated where it lives.
    """
    kit_text = (_REPO / "weave" / "team" / "playbook.py").read_text(encoding="utf-8")
    assert "ONBOARDING" in kit_text and "know nothing about Weave" in kit_text, (
        "the kit no longer states that the skills are Weave-unaware"
    )

    offenders = []
    for path in sorted(_REPO.glob("weave/**/*.py")) + sorted(_REPO.glob("scripts/*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "ONBOARDING/" in node.value or node.value.endswith("ONBOARDING"):
                    offenders.append(f"{path.relative_to(_REPO)}:{node.lineno}")
    assert not offenders, (
        "this repository references the methodology kit by path — the coupling "
        "must go the other way:\n  " + "\n  ".join(offenders)
    )


# ── the function actually runs ───────────────────────────────────────────────
#
# **Everything above this line reads code.** W37 was a harness whose measuring
# path had never executed while the paths beside it worked, and asserting the
# shape of `publish_artifact` without ever calling it would be the same mistake
# one file later. Ingestion needs a model, so the ingest step is stubbed — the
# locator, the idempotency and the merge are the real body.


class _Rag:
    def __init__(self, graph, docs=None):
        self.chunk_entity_relation_graph = graph
        self.doc_status = _DocStatus(docs or {})
        self.processed = 0

    async def apipeline_process_enqueue_documents(self):
        self.processed += 1


class _DocStatus:
    def __init__(self, docs):
        self._docs = docs

    async def get_docs_by_track_id(self, track_id):
        return self._docs


@pytest.mark.asyncio
async def test_publishing_writes_the_locator(monkeypatch, tmp_path):
    from weave.model import artifacts

    document = tmp_path / "rfc.html"
    document.write_text("<h1>RFC-014</h1>", encoding="utf-8")

    async def _landed(rag, source):
        return "processed"

    monkeypatch.setattr(artifacts, "_ingest_and_wait", _landed)
    graph = _Graph()
    report = await artifacts.publish_artifact(
        _Rag(graph), path=str(document), entity_id="RFC-014", entity_type="RFC",
        rev="abc1234", title="Outbound-only dev hosts")

    assert report["changed"] is True and report["ingested"] is True
    node = graph.nodes["RFC-014"]
    assert node["entity_type"] == "RFC"
    assert node["locator_path"].endswith("rfc.html")
    assert node["locator_rev"] == "abc1234"
    assert node["title"] == "Outbound-only dev hosts"


@pytest.mark.asyncio
async def test_the_second_publish_writes_nothing(monkeypatch, tmp_path):
    """Run twice, the second reports unchanged and writes nothing — a hook does
    this on every commit."""
    from weave.model import artifacts

    document = tmp_path / "README.md"
    document.write_text("# a root readme", encoding="utf-8")

    async def _landed(rag, source):
        return "processed"

    monkeypatch.setattr(artifacts, "_ingest_and_wait", _landed)
    graph = _Graph()
    rag = _Rag(graph)
    common = dict(path=str(document), entity_id="README", entity_type="PRD", rev="abc1234")

    first = await artifacts.publish_artifact(rag, **common)
    writes_after_first = graph.writes
    second = await artifacts.publish_artifact(rag, **common)

    assert first["changed"] is True
    assert second["changed"] is False and second["status"] == "unchanged"
    assert graph.writes == writes_after_first, "the second publish wrote to the graph"


@pytest.mark.asyncio
async def test_publishing_keeps_what_a_governed_write_put_on_the_node(monkeypatch, tmp_path):
    from weave.model import artifacts

    document = tmp_path / "cr.md"
    document.write_text("# CR-009", encoding="utf-8")

    async def _landed(rag, source):
        return "processed"

    monkeypatch.setattr(artifacts, "_ingest_and_wait", _landed)
    graph = _Graph()
    graph.nodes["CR-009"] = {"entity_id": "CR-009", "entity_type": "ChangeRequest",
                             "status": "approved", "priority": "high"}

    await artifacts.publish_artifact(
        _Rag(graph), path=str(document), entity_id="CR-009",
        entity_type="ChangeRequest", rev="abc1234")

    node = graph.nodes["CR-009"]
    assert node["status"] == "approved" and node["priority"] == "high"
    assert node["locator_rev"] == "abc1234"


@pytest.mark.asyncio
async def test_a_document_that_ends_in_failed_is_not_published(monkeypatch, tmp_path):
    """**The criterion, exercised rather than read.** A `200` followed by a
    document in `failed` was the reported case."""
    from weave.model import artifacts

    document = tmp_path / "broken.md"
    document.write_text("# whatever", encoding="utf-8")

    async def _enqueue(rag, source):
        return True, "track-1"

    monkeypatch.setattr(
        "weave.server.routers.documents.pipeline_enqueue_file", _enqueue)
    graph = _Graph()
    rag = _Rag(graph, docs={"doc-1": {"status": "failed", "error_msg": "no parser"}})

    with pytest.raises(artifacts.PublishError) as refusal:
        await artifacts.publish_artifact(
            rag, path=str(document), entity_id="X", entity_type="PRD", rev="abc1234")

    assert "failed" in str(refusal.value)
    assert "X" not in graph.nodes, "the artifact node was written for a document that never landed"


@pytest.mark.asyncio
async def test_a_missing_file_is_refused_by_name(tmp_path):
    from weave.model import artifacts

    with pytest.raises(artifacts.PublishError) as refusal:
        await artifacts.publish_artifact(
            _Rag(_Graph()), path=str(tmp_path / "absent.md"),
            entity_id="X", entity_type="PRD")
    assert "no such file" in str(refusal.value)


# ── W38: publishing must not consume the document ────────────────────────────


class _RecordingRag(_Rag):
    """A rag whose enqueue does what the server's really does: **moves** the file.

    `pipeline_enqueue_file` archives its input with
    `file_path.rename(file_path.parent / "__enqueued__" / …)` on success, because
    it was written for files the server owns in its own input directory. That is
    the behaviour these tests reproduce — a stub that merely read the file would
    pass while the real path deleted a repository document.
    """

    def __init__(self, graph):
        super().__init__(graph)
        self.ingested: list = []

    async def enqueue(self, path: pathlib.Path):
        self.ingested.append(pathlib.Path(path).read_bytes())
        archive = pathlib.Path(path).parent / "__enqueued__"
        archive.mkdir(exist_ok=True)
        pathlib.Path(path).rename(archive / pathlib.Path(path).name)
        return True, "track-1"


@pytest.mark.asyncio
async def test_publishing_leaves_the_document_byte_identical(monkeypatch, tmp_path):
    """**W38, and the assertion is byte-identical rather than exists.**

    Publishing `docs/WEAVE_RFC.html` removed it from the repository and left the
    locator pointing at a path that no longer existed — recoverable only because
    that file happened to be committed, which is exactly what an artifact a
    session has just authored is not.

    "Still exists" would pass against a pipeline that rewrote the file in place;
    the premise being defended is *the repository stays the source of truth*, and
    that means unchanged, not merely present.
    """
    from weave.model import artifacts

    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    document = repo / "docs" / "rfc.html"
    original = b"<h1>RFC-014</h1>\n<p>outbound only</p>\n"
    document.write_bytes(original)

    graph = _Graph()
    rag = _RecordingRag(graph)
    monkeypatch.setattr(
        "weave.server.routers.documents.pipeline_enqueue_file",
        lambda _rag, path: rag.enqueue(path))
    rag.doc_status = _DocStatus({"doc-1": {"status": "processed"}})

    await artifacts.publish_artifact(
        rag, path=str(document), entity_id="RFC-014", entity_type="RFC", rev="abc1234")

    assert document.exists(), "publishing deleted the document it published"
    assert document.read_bytes() == original, "publishing rewrote the document"
    assert rag.ingested == [original], "the pipeline did not receive the content"


@pytest.mark.asyncio
async def test_publishing_leaves_no_archive_beside_the_document(monkeypatch, tmp_path):
    """The archive directory is `file_path.parent / "__enqueued__"`, so ingesting
    a repository path also **created a directory inside the repository**. The
    copy is ingested from scratch space, so the move happens there."""
    from weave.model import artifacts

    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    document = repo / "docs" / "cr.md"
    document.write_bytes(b"# CR-009\n")

    graph = _Graph()
    rag = _RecordingRag(graph)
    monkeypatch.setattr(
        "weave.server.routers.documents.pipeline_enqueue_file",
        lambda _rag, path: rag.enqueue(path))
    rag.doc_status = _DocStatus({"doc-1": {"status": "processed"}})

    await artifacts.publish_artifact(
        rag, path=str(document), entity_id="CR-009", entity_type="ChangeRequest",
        rev="abc1234")

    assert not (repo / "docs" / "__enqueued__").exists(), (
        "publishing created an archive directory inside the repository"
    )
    assert sorted(p.name for p in (repo / "docs").iterdir()) == ["cr.md"]


@pytest.mark.asyncio
async def test_publishing_twice_is_now_testable_and_is_a_no_op(monkeypatch, tmp_path):
    """**The criterion that could never run.** The second publish had no input,
    because the first had eaten it — so idempotency was in the CR's gate and
    untestable in practice."""
    from weave.model import artifacts

    repo = tmp_path / "repo"
    repo.mkdir()
    document = repo / "README.md"
    document.write_bytes(b"# a root readme\n")

    graph = _Graph()
    rag = _RecordingRag(graph)
    monkeypatch.setattr(
        "weave.server.routers.documents.pipeline_enqueue_file",
        lambda _rag, path: rag.enqueue(path))
    rag.doc_status = _DocStatus({"doc-1": {"status": "processed"}})

    common = dict(path=str(document), entity_id="README", entity_type="PRD", rev="abc1234")
    first = await artifacts.publish_artifact(rag, **common)
    second = await artifacts.publish_artifact(rag, **common)

    assert first["changed"] is True and second["changed"] is False
    assert len(rag.ingested) == 1, "the second publish re-ingested the document"
    assert document.exists() and document.read_bytes() == b"# a root readme\n"


def test_the_source_path_is_never_handed_to_the_pipeline():
    """The mechanism, not just the outcome: a copy is made and the copy is what
    the pipeline receives."""
    from weave.model import artifacts

    source = inspect.getsource(artifacts._ingest_and_wait)
    assert "shutil.copy2" in source, "the document is no longer copied before ingestion"
    assert "mkdtemp" in source, "the copy is not made outside the repository"


# ── W41: a write nobody persists ─────────────────────────────────────────────


async def _real_graph(working_dir):
    """A real file-based graph store, because that is where this defect lives."""
    from weave_core.graph.storage.files import NetworkXStorage
    from weave_core.store.locks import initialize_share_data

    initialize_share_data(1)
    store = NetworkXStorage(
        namespace="chunk_entity_relation", workspace="",
        global_config={"working_dir": str(working_dir), "embedding_batch_num": 8},
        embedding_func=None)
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_the_artifact_node_survives_the_process(monkeypatch, tmp_path):
    """**W41, and the reason the other 24 tests could not see it.**

    The file-based adapters keep their data in memory and write it out in
    `index_done_callback`, which only the ingest pipeline calls — and
    `publish_artifact` upserts *after* the pipeline's last flush. So the node
    existed in memory, the call reported success, and `weave docs publish` exited
    without it ever reaching the file: 92 nodes afterwards and no artifact.

    Every earlier test used a dict as the graph. **A dict has no persistence
    boundary, so there was nothing there to fail.** This one reads the graph back
    through a *second* store instance over the same directory — the only shape
    that can tell "written" from "written down".
    """
    from weave.model import artifacts

    document = tmp_path / "rfc.html"
    document.write_bytes(b"<h1>RFC-014</h1>")

    working = tmp_path / "store"
    working.mkdir()
    graph = await _real_graph(working)

    async def _landed(rag, source):
        return "processed"

    monkeypatch.setattr(artifacts, "_ingest_and_wait", _landed)

    class _R:
        chunk_entity_relation_graph = graph

    await artifacts.publish_artifact(
        _R(), path=str(document), entity_id="RFC-014", entity_type="RFC", rev="abc1234")

    reopened = await _real_graph(working)
    node = await reopened.get_node("RFC-014")
    assert node is not None, (
        "the artifact node did not survive: it was written to memory and never "
        "persisted, so a short-lived process publishes nothing"
    )
    assert node["locator_path"].endswith("rfc.html")


def test_every_out_of_pipeline_node_write_is_persisted():
    """**The class, not the instance.**

    `publish_artifact` was the one that showed the symptom, but the coordinator
    writes `Review` and `Insight` nodes the same way — outside the pipeline, with
    nothing to flush them. D-043 made those nodes the record `/ask/learnings`
    reads, so losing them on a restart is that defect one layer down.
    """
    from weave.model import artifacts
    from weave.team import coordinator as coordinator_module

    for module in (artifacts, coordinator_module):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        upserts = {
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "upsert_node"
        }
        flushes = {
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) in
            {"callback", "index_done_callback", "_persist"}
        }
        assert upserts, f"{module.__name__} no longer writes nodes at all"
        assert flushes, (
            f"{module.__name__} upserts nodes and never persists them — on the "
            "file-based path those writes are lost with the process"
        )
