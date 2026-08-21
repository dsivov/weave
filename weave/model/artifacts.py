"""Publishing an artifact into Weave — one implementation, beneath both adapters (P14, D-049, CR-002).

The house methodology's documents reach Weave today only because a role kit
*tells* a session to ingest them. Nothing enforces it, so a plan can be published
over a graph that is missing the document it was derived from, and the answer
surfaces resolve to nothing. **An instruction is not a mechanism.**

`publish_artifact` is that mechanism, and it does three things that belong
together:

1. **ingests the file** through the pipeline the server already uses;
2. **waits for the outcome** — see below, this is the part that was missing;
3. **creates or updates the artifact node** with its locator (`repo · path ·
   rev`), which is what `PublishPlan` later checks and what A5 requires: an
   artifact references its source and never embeds a copy.

**Why it waits.** `POST /documents/text` answers `success` when the text has been
*received*, and the manager reproduced a `200` followed by a document sitting in
`failed`. Worse, the server's own `pipeline_index_file` wraps the whole run in
`except Exception: logger.error(...)` and returns `None` — so the caller cannot
tell a processed document from a crashed one. **A `track_id` for a document that
never landed is not success**, so this reads the document's final status back and
raises when it is not `processed`.

That is also why this calls `pipeline_enqueue_file` and
`apipeline_process_enqueue_documents` rather than the `pipeline_index_file`
wrapper around them: the wrapper is the thing that discards the answer. The
extraction of `.html`, `.pdf`, `.docx` and the rest is the server's, unchanged —
reused, not reimplemented. **The extension was never the boundary**; which
artifacts matter is.

**Two callers, no second implementation** — `weave/server/mcp.py` (a role session
publishes what it just authored) and `weave/cli/docs.py` (hooks and CI).
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any, Dict

from weave.model.locator import Locator

#: The document statuses that mean the pipeline finished with the document in the
#: graph. Anything else — `failed`, or still `pending`/`processing` after the
#: pipeline returned — is a publish that did not happen.
_LANDED = "processed"


class PublishError(RuntimeError):
    """The artifact did not land. Carries what to do about it."""


def resolve_revision(repo_root: pathlib.Path) -> str:
    """The revision a locator resolves against.

    **The rev is the load-bearing field** (`weave/model/locator.py`): a document
    that moved last week still resolves for the review that cited it, because the
    review cited a revision. An unversioned working copy gets `"working"` rather
    than a fabricated sha — a locator that claims a revision it does not have is
    worse than one that admits it has none.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "working"


async def publish_artifact(
    rag,
    *,
    path: str,
    entity_id: str,
    entity_type: str,
    repo: str = "",
    rev: str = "",
    anchor: str = "",
    title: str = "",
    workspace: str = "",
) -> Dict[str, Any]:
    """Ingest a document and point an artifact node at it.

    Returns `{artifact, path, locator, ingested, changed, status}`. `changed` is
    False on a re-publish of an unchanged file — running this twice is something
    a hook does constantly, and the second run must write nothing.

    Raises `PublishError` when the document does not reach `processed`, naming
    the file and the status it ended in.
    """
    source = pathlib.Path(path).resolve()
    if not source.is_file():
        raise PublishError(
            f"no such file: {source}. Publish the artifact that exists, or pass "
            "the path relative to the repository root."
        )

    repo_root = _repo_root(source)
    locator = Locator(
        repo=repo or repo_root.name,
        path=str(_relative(source, repo_root)),
        rev=rev or resolve_revision(repo_root),
        anchor=anchor or "",
    )

    graph = getattr(rag, "chunk_entity_relation_graph", None)
    if graph is None:
        raise PublishError(
            "this workspace has no graph, so an artifact cannot be published into it"
        )

    # **Unchanged means unchanged.** Compared before ingesting, so a re-publish
    # of the same revision does no work at all rather than re-ingesting and then
    # noticing. A hook runs this on every commit.
    existing = await graph.get_node(entity_id)
    already = bool(existing) and all(
        str(existing.get(f"locator_{field}") or "") == str(getattr(locator, field) or "")
        for field in ("repo", "path", "rev")
    )
    if already:
        return {
            "artifact": entity_id, "path": locator.path, "locator": locator.to_dict(),
            "ingested": False, "changed": False, "status": "unchanged",
        }

    status = await _ingest_and_wait(rag, source)

    node = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "locator_repo": locator.repo,
        "locator_path": locator.path,
        "locator_rev": locator.rev,
    }
    if locator.anchor:
        node["locator_anchor"] = locator.anchor
    if title:
        node["title"] = title
    # Merged over whatever is there: publishing a document must not blank the
    # fields a governed write put on the node (W17's mechanism, and D-043's).
    if existing:
        merged = dict(existing)
        merged.update(node)
        node = merged
    await graph.upsert_node(entity_id, node)
    await _persist(graph)

    return {
        "artifact": entity_id, "path": locator.path, "locator": locator.to_dict(),
        "ingested": True, "changed": True, "status": status,
        "workspace": workspace,
    }


async def _persist(graph) -> None:
    """Flush the node to disk (W41).

    **A write outside the pipeline is a write nobody persists.** The file-based
    adapters keep their data in memory and write it out in
    `index_done_callback`, which the engine calls from `_insert_done` at the end
    of an ingest — and `publish_artifact` upserts the artifact node *after* that,
    so the node existed in memory, the call returned success, and the process
    exited without the node ever reaching the file. The graph afterwards had 92
    nodes and no artifact.

    **It hid because the server hides it.** In a long-lived server the in-memory
    graph is shared, so the node is readable and the next ingest flushes it; only
    a short-lived process — `weave docs publish` — loses it outright. And the
    24 tests could not see it at all: their fake graph was a dict, which has no
    persistence boundary to fail at.
    """
    callback = getattr(graph, "index_done_callback", None)
    if callback is not None:
        await callback()


async def _ingest_and_wait(rag, source: pathlib.Path) -> str:
    """Run the document through the pipeline and **read the answer back**.

    The two calls below are the body of the server's `pipeline_index_file`,
    without the `except Exception` that swallows the result. Everything about
    *how* a `.pdf` or a `.docx` becomes text is the server's, untouched.

    **A copy is ingested, never the file itself** (W38). On success
    `pipeline_enqueue_file` does `file_path.rename(file_path.parent /
    "__enqueued__" / ...)` — it *moves* the input into an archive, because it was
    written for files the server owns in its own input directory. Handed a
    repository path it did exactly that: publishing `docs/WEAVE_RFC.html`
    **removed it from the repository** and left the locator pointing at a path
    that no longer existed. Recoverable only because the file happened to be
    committed; an artifact a session has just authored is precisely the case
    where it is not.

    That inverts the premise the whole change rests on — *the repository stays
    the source of truth, Weave holds a reference and never a copy.* So the
    source is copied into a scratch directory and the pipeline is given that;
    the archive move then happens inside the scratch directory and is removed
    with it.

    The copy keeps the **original filename**, because the pipeline records
    `file_path.name` as the document's citation. A temp-prefixed name would be
    tidied up by the `finally` in that function and would put `__tmp__…` in
    every citation.
    """
    import shutil
    import tempfile

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="weave-publish-"))
    try:
        return await _ingest_copy(rag, source, shutil.copy2(source, scratch / source.name))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _ingest_copy(rag, source: pathlib.Path, copied) -> str:
    from weave.server.routers.documents import pipeline_enqueue_file

    accepted, track_id = await pipeline_enqueue_file(rag, pathlib.Path(copied))
    if not accepted:
        raise PublishError(
            f"{source.name} was not accepted for ingestion. It may be an "
            "unsupported format, empty, or unreadable."
        )

    await rag.apipeline_process_enqueue_documents()

    documents = await rag.doc_status.get_docs_by_track_id(track_id)
    if not documents:
        raise PublishError(
            f"{source.name} produced no document record (track_id {track_id}). "
            "A track id is not an answer: nothing landed."
        )

    for doc_id, document in documents.items():
        status = str(getattr(document, "status", None) or
                     (document.get("status") if isinstance(document, dict) else ""))
        status = status.split(".")[-1].lower()
        if status != _LANDED:
            error = (getattr(document, "error_msg", None) or "")
            raise PublishError(
                f"{source.name} did not land: document {doc_id} ended in "
                f"'{status}'"
                + (f" — {error}" if error else "")
                + ". The artifact node was not written, so a plan referencing it "
                "will still be refused."
            )
    return _LANDED


def _repo_root(source: pathlib.Path) -> pathlib.Path:
    """The repository the locator's `path` is relative to.

    Walks up to the git root so a **root `README.md`** and a nested
    `docs/rfc.html` produce comparable locators. The first draft of CR-002 said
    `docs/*.md`; neither the directory nor the extension was ever the boundary.
    """
    for candidate in [source.parent, *source.parent.parents]:
        if (candidate / ".git").exists():
            return candidate
    return source.parent


def _relative(source: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    try:
        return source.relative_to(root)
    except ValueError:
        return pathlib.Path(source.name)


async def unresolved_artifacts(graph, refs) -> Dict[str, str]:
    """Which of `refs` cannot be resolved, and why — for `PublishPlan` (CR-002).

    **The backstop.** Without it the whole mechanism rests on a role session
    remembering to publish, which is what it rests on today and the reason this
    change exists. Returns `{ref: reason}`; empty means every reference resolves.

    A reference resolves when a node exists **and** carries a locator. A node
    with no locator is the case worth naming separately: something created it —
    a decision trace, a generic upsert — but no document was ever published
    behind it, so the answer surfaces would return a node that leads nowhere.
    """
    problems: Dict[str, str] = {}
    for ref in refs:
        if not ref:
            continue
        node = await graph.get_node(ref)
        if node is None:
            problems[ref] = "no node in this workspace"
            continue
        if not str(node.get("locator_path") or "").strip():
            problems[ref] = "the node exists but has no locator — nothing was published behind it"
    return problems


def refusal_message(problems: Dict[str, str], plan_ref: str = "") -> str:
    """What an author is told, with the command that fixes it.

    A refusal that only says *"unresolved artifacts"* sends the reader looking
    for the wrong thing. Each name, each reason, and the publish that resolves
    it.
    """
    lines = [
        f"cannot publish {plan_ref or 'this plan'}: it references "
        f"{len(problems)} artifact(s) that are not in this workspace."
    ]
    for ref, reason in sorted(problems.items()):
        lines.append(f"  - {ref}: {reason}")
    lines.append("")
    lines.append("  Publish each one, then publish the plan again:")
    lines.append("      weave docs publish --path <file> --artifact <id> --type <ObjectType>")
    lines.append("  or call the `publish_artifact` MCP tool from the session that authored it.")
    return "\n".join(lines)


def plan_artifact_refs(specs, plan_ref: str = "", *, include_plan: bool = False) -> list:
    """Every artifact a plan **references**.

    `include_plan` is off by default, and that is a decision rather than an
    oversight. CR-002 says *"`PublishPlan` refuses when a **referenced** artifact
    does not resolve"* — the plan document is the thing being published, not
    something it references.

    Requiring the plan document itself is the stronger rule and probably the
    better one: a plan signed over a graph that does not contain the plan is the
    case a session is most likely to skip. It is off because it is **not what
    the CR specifies**, and turning it on changes behaviour for every existing
    caller — including a carried claim test that publishes `RFC-7` with no
    artifacts, which no longer passes. That test may not be edited, and the pin
    is what surfaced the question rather than letting it through. Raised for a
    `D-NN`; the flag is here so switching it on is one word.
    """
    refs = [plan_ref] if (plan_ref and include_plan) else []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        for key in ("change_request", "artifact", "depends_on_artifact"):
            value = spec.get(key)
            if isinstance(value, str) and value:
                refs.append(value)
    seen: list = []
    for ref in refs:
        if ref not in seen:
            seen.append(ref)
    return seen
