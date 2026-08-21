#!/usr/bin/env python
"""Measure what the extraction prompt actually pulls out of this project's documents (P11, D-041, R2).

**Why a harness and not a quiet edit.** Changing the few-shot examples changes how
every ingested document is read, on every instance. R2 says a claim like *"the
extractor is better calibrated"* ships with a way to reproduce it, or it is a
hypothesis — and *"a before number nobody can reconstruct later is not a
baseline"*.

What it reports, per run:

* **entity and relation counts, and the count by type** — the shape of what the
  extractor believes it is reading;
* **leaked example entities** — the names the prompt's own examples use, found in
  the output. This is the symptom D-041 was reported through: dsivov saw
  *"Premium Wireless Speaker"* and *"AudioRival"* in the Decisions tab of the
  demo tenant and read it as bad seeding. It was the shipped prompt, and 5 of
  that example's entities were real nodes in a 924-node graph.

**The leak names are not a fixed list.** They are read out of the live prompt, so
the check follows whatever the examples currently say. A hard-coded list of the
five we happened to find would pass the moment somebody wrote a new example.

Usage — needs a configured server-side model (A13: the only place a model
credential exists):

    python scripts/measure_extraction.py --corpus docs --out before.json
    #   … change weave_core/graph/prompt.py …
    python scripts/measure_extraction.py --corpus docs --out after.json
    python scripts/measure_extraction.py --compare before.json after.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import tempfile
from collections import Counter
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: Documents that are *about* this project, not about the world. The corpus is
#: fixed so the two runs are comparable — a before and an after taken over
#: different inputs measure nothing.
DEFAULT_CORPUS_GLOBS = ("docs/*.md", "docs/**/*.md")

#: How many characters of each document to feed. Extraction is chunked anyway;
#: the cap keeps a run affordable and, more importantly, identical between runs.
DEFAULT_CHARS = 6000


def example_entity_names() -> List[str]:
    """Every entity name the prompt's own examples use — read from the prompt.

    Both example encodings are covered: the delimiter form
    (`entity{tuple_delimiter}NAME{tuple_delimiter}type{...}`) and the JSON form
    (`"entity_name": "NAME"`). Missing one of them is how a sweep reports success
    over half a surface, and this prompt file carries **three** example blocks —
    the two the plan named, plus a third inside the JSON-mode system prompt that
    held the same sales conversation.
    """
    from weave_core.graph.prompt import PROMPTS

    blocks: List[str] = []
    for key, value in PROMPTS.items():
        if "example" not in key:
            continue
        blocks.extend(value if isinstance(value, list) else [str(value)])

    names: set[str] = set()
    for block in blocks:
        names.update(re.findall(r"entity\{tuple_delimiter\}([^{]+)\{tuple_delimiter\}", block))
        names.update(re.findall(r'"entity_name":\s*"([^"]+)"', block))
    return sorted(n.strip() for n in names if n.strip())


#: Example names that could not plausibly arise from a real document unless they
#: were copied out of the prompt: issue keys, commit shas, run ids, and the
#: sentence-length insight statements.
#:
#: **This distinction is a cost of the fix, and it is worth naming.** The old
#: examples leaked visibly because they were about speakers and a sales pipeline
#: — "AudioRival" in a software graph is unmistakable. Examples drawn from the
#: domain the product actually serves are *harder* to catch leaking, because
#: "PostgreSQL" or "auth" is exactly what a real document would produce. So the
#: gate turns on the identifiers, which stay unmistakable, while the report
#: still lists everything for a human to read.
_DISTINCTIVE = re.compile(
    r"^(RFC-\d+|TASK-\d+|CR-\d+|ADR-\d+|PR #\d+|e2e-\d+|[0-9a-f]{8})$|^.{40,}$")


def distinctive(names) -> List[str]:
    return sorted(n for n in names if _DISTINCTIVE.match(n))


def extracted_denominator(total: int, invented: int) -> int:
    """How many of these nodes extraction actually produced (W47).

    A function rather than a subtraction inline because a control showed the
    subtraction was untested: the test supplied its own numbers to `compare()`
    and never exercised what `measure()` computes — the same mistake as
    asserting on a dict the test built itself.
    """
    return max(0, total - invented)


def _was_invented(node: dict) -> bool:
    """Did the pipeline conjure this node rather than extract it? (W47)

    Marked explicitly at the two sites that invent one, because typing them
    `Other` — which makes them ontology-legal — also makes them indistinguishable
    from the model's own `Other`. The legacy spellings are still recognised so a
    graph written before that change measures correctly.
    """
    from weave_core.constants import INVENTED_MARKER, PLACEHOLDER_ENTITY_TYPES

    if node.get(INVENTED_MARKER):
        return True
    return str(node.get("entity_type") or "") in PLACEHOLDER_ENTITY_TYPES


def _ontology_types() -> set:
    """The vocabulary the answer surface actually queries.

    Read from the shipped preset rather than restated here — a fourth
    hand-written list would be this phase's own defect in its own harness.
    """
    try:
        from weave.team import preset

        return {o["name"] for o in (preset.load_part("ontology") or {}).get(
            "object_types", []) if o.get("name")}
    except Exception:
        return set()


def _corpus(root: pathlib.Path, globs) -> List[pathlib.Path]:
    seen: List[pathlib.Path] = []
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.append(path)
    return seen


async def _extract(rag, text: str) -> Dict[str, Any]:
    """One document through the real extraction path."""
    await rag.ainsert(text)
    graph = rag.chunk_entity_relation_graph
    labels = list(await graph.get_all_labels() or [])
    edges = list(await graph.get_all_edges() or [])
    types: Counter = Counter()
    for label in labels:
        node = await graph.get_node(label)
        types[(node or {}).get("entity_type", "UNKNOWN")] += 1
    return {"entities": labels, "edges": len(edges), "by_type": dict(types)}


def _product_engine(working_dir: str, quadruple: bool = True):
    """The engine **the server would use**, wired from the same variables (W37).

    The first version of this script built `WeaveGraph(working_dir=…)` directly
    and died on `embedding_func is required for vector storage` before reading a
    single document — so the measuring path had never run anywhere, while
    `--names-only` and `--compare` worked and made the harness read like one that
    did. *The paths that were exercised sat next to the path that mattered.*

    Constructing the app rather than re-wiring the backends here is deliberate:
    an embedding model chosen by this script would make its numbers
    incomparable to anything the product produces, which is the one thing a
    baseline must not be. `WEAVE_EMBEDDING_*` and `WEAVE_LLM_*` are read by
    `parse_args()` exactly as the server reads them.
    """
    import sys as _sys

    # **The swap has to cover the imports, not just the call.**
    # `weave.server.config.global_args` is a lazy proxy that parses `sys.argv` on
    # first access, and `weave.server.utils` touches it at import time — so
    # importing the app under this script's own flags made the *server's* parser
    # reject `--corpus`. Restoring argv around `parse_args()` alone was not
    # enough, which the first run showed and no amount of reading would have.
    argv = _sys.argv
    _sys.argv = ["weave-server"]
    try:
        from weave.server.app import create_app
        from weave.server.config import parse_args

        args = parse_args()
        args.working_dir = working_dir
        args.workers = 1
        # **Which extraction path — and therefore which examples** (W51).
        #
        # `WeaveGraph` (quadruple) reads `cg_entity_extraction_examples`;
        # `WeaveEngine` reads `entity_extraction_examples`. Forcing quadruple
        # here meant the harness could not reach the second block at all — and
        # A4 v6 says PostgreSQL **cannot** run quadruple mode, so that block is
        # what every PostgreSQL deployment actually extracts with. It was
        # unmeasured because the instrument could not ask, not because nothing
        # used it.
        args.use_quadruple = quadruple
        # Unconditionally, because the server refuses to start on the published
        # default (S1) and this script serves no requests — it needs the engine,
        # not a trustworthy signer. Conditioning on "does the secret look like
        # the default" was a guess about a string, and it was wrong.
        args.token_secret = "measure-extraction-serves-no-requests"
        app = create_app(args)
    finally:
        _sys.argv = argv
    pool = getattr(app.state, "workspace_pool", None)
    if pool is None:
        raise SystemExit(
            "this build does not publish app.state.workspace_pool, so the "
            "harness cannot borrow the product's engine (W37)")
    return pool


async def measure(corpus: List[pathlib.Path], working_dir: str, chars: int,
                  quadruple: bool = True, workspace: str = "measure") -> Dict[str, Any]:
    from weave_core.store.locks import initialize_share_data

    initialize_share_data(1)
    pool = _product_engine(working_dir, quadruple=quadruple)
    # **A distinct workspace per run** (W53).
    #
    # A separate working directory was not enough: the shared-storage namespace
    # registry is process-global and keyed by *(namespace, workspace)*, not by
    # directory. So runs 2..N of `--repeat` found run 1's documents already
    # enqueued — *"No new unique documents were found"* — extracted nothing, and
    # the aggregate reported the last, empty run.
    #
    # That is worse than the crash it replaced: a crash stops, this returns a
    # number. Only the entities-are-zero guard caught it.
    rag = await pool.get_rag(workspace)
    try:
        for path in corpus:
            await _extract(rag, path.read_text(encoding="utf-8")[:chars])
        graph = rag.chunk_entity_relation_graph
        labels = list(await graph.get_all_labels() or [])
        edges = list(await graph.get_all_edges() or [])
        types: Counter = Counter()
        invented = 0
        for label in labels:
            node = await graph.get_node(label) or {}
            types[node.get("entity_type", "UNKNOWN")] += 1
            if _was_invented(node):
                invented += 1
    finally:
        await pool.shutdown()

    # **The number this phase exists to move** (P15).
    #
    # Entity and relation counts say how much was extracted; they said nothing
    # about whether any of it could be *answered*. A real publish produced 92
    # nodes — `artifact 23 · concept 31 · method 6 · objection 2 · person 1 ·
    # UNKNOWN 14` — and **not one Weave type**, so `/ask/features` seeding on
    # `Feature` found only what a human had created by hand. The extractor was
    # productive and the answer surface was blind to all of it.
    from weave_core.constants import PLACEHOLDER_ENTITY_TYPES
    from weave_core.utils import normalize_type

    # Compared through the same key the answer surface uses (W46) — a count of
    # "answerable" that matched more strictly than the questions do would report
    # a number the product cannot deliver.
    answerable_types = {normalize_type(t) for t in _ontology_types()}
    placeholders = {normalize_type(t) for t in PLACEHOLDER_ENTITY_TYPES}

    answerable = sum(c for kind, c in types.items()
                     if normalize_type(kind) in answerable_types)
    # **The denominator is what extraction produced** (W47).
    #
    # It was every node in the graph, which counted endpoints the *pipeline*
    # conjured to keep edges attached and governance nodes written by bootstrap
    # — 15 of 76, none of them extraction output. A percentage over that
    # denominator flatters or punishes the extractor for work it never did, and
    # it is how a wrong figure got published.
    extracted = extracted_denominator(len(labels), invented)
    # **Split, because they are different failures** (W47). A placeholder is a
    # node *we* invented for an edge endpoint nobody described; an off-ontology
    # type is the model answering with a word the workspace does not declare.
    # One number for both says only that something is wrong.
    placeholder = sum(c for kind, c in types.items()
                      if normalize_type(kind) in placeholders)
    off_ontology = len(labels) - answerable - placeholder

    examples = example_entity_names()
    leaked = sorted(set(labels) & set(examples))
    return {
        "documents": [str(p) for p in corpus],
        "extraction_path": "WeaveGraph (quadruple)" if quadruple else "WeaveEngine",
        "entities": len(labels),
        "relations": len(edges),
        "by_type": dict(sorted(types.items(), key=lambda kv: -kv[1])),
        "answerable_nodes": answerable,
        "extracted_nodes": extracted,
        "invented_nodes": invented,
        "answerable_pct_of_extracted": (
            round(100.0 * answerable / extracted, 1) if extracted else 0.0),
        "placeholder_nodes": placeholder,
        "off_ontology_nodes": off_ontology,
        "answerable_pct": round(100.0 * answerable / len(labels), 1) if labels else 0.0,
        "ontology_types": sorted(answerable_types),
        "example_entities_in_prompt": example_entity_names(),
        "leaked_example_entities": leaked,
        "leaked_count": len(leaked),
        "leaked_distinctive": distinctive(leaked),
        "leaked_distinctive_count": len(distinctive(leaked)),
    }


#: The figure a prompt change is judged on. Named once so the aggregate, the
#: comparison and the refusal all read the same key.
HEADLINE = "answerable_pct_of_extracted"


def aggregate(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Several runs of one condition, as one report with its spread (W48).

    **A percentage without a spread is not evidence.** Two runs of the unchanged
    prompt scored 73.1 and 56.7 — a sixteen-point gap from per-run model
    sampling alone — and a prompt change was then judged on a one-point
    difference in means. The number looked like a measurement and was noise.

    So a single run no longer reports a bare figure: it reports what it is, how
    many runs it averages, and how far they ranged.
    """
    values = [float(r.get(HEADLINE, 0.0)) for r in runs]
    merged = dict(runs[-1]) if runs else {}
    merged.update({
        # **The headline is the mean, not the last run.** Merging `runs[-1]`
        # left the per-run line showing one sample while the summary showed the
        # average — two numbers for one thing, on the same screen, which is the
        # exact defect this instrument keeps being used to find.
        HEADLINE: round(sum(values) / len(values), 1) if values else 0.0,
        "runs": len(values),
        f"{HEADLINE}_values": values,
        f"{HEADLINE}_mean": round(sum(values) / len(values), 1) if values else 0.0,
        f"{HEADLINE}_min": round(min(values), 1) if values else 0.0,
        f"{HEADLINE}_max": round(max(values), 1) if values else 0.0,
        "documents_measured": len(runs[-1].get("documents", [])) if runs else 0,
    })
    return merged


def resolvable(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    """Is the difference between two conditions bigger than the noise in either?

    Deliberately crude — the widest observed within-condition range, against the
    difference of means. It is not a t-test and does not pretend to be; it is
    the smallest rule that stops a one-point difference being reported as a
    result when the same condition varies by twenty.

    **The instrument refuses rather than letting the reader infer.** That is the
    same rule as *a count of zero problems is only evidence when you know what
    was inspected*, applied to a percentage.
    """
    # **One run cannot resolve anything**, and this is the dangerous case rather
    # than an edge case: nobody passes `--repeat` by default, a single run has a
    # spread of zero, and every difference then clears it. The first version of
    # this rule said a 1.0-point move between two single runs was real — which
    # is precisely the reading W48 exists to stop.
    if before.get("runs", 1) < 2 or after.get("runs", 1) < 2:
        return False

    spread = max(
        before.get(f"{HEADLINE}_max", 0.0) - before.get(f"{HEADLINE}_min", 0.0),
        after.get(f"{HEADLINE}_max", 0.0) - after.get(f"{HEADLINE}_min", 0.0),
    )
    difference = abs(after.get(f"{HEADLINE}_mean", 0.0) - before.get(f"{HEADLINE}_mean", 0.0))
    return difference > spread


def compare(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """The two numbers side by side, and the one that must go to zero."""
    lines = [
        "                        before     after",
        f"  entities            {before['entities']:>7}   {after['entities']:>7}",
        f"  relations           {before['relations']:>7}   {after['relations']:>7}",
        f"  leaked example      {before['leaked_count']:>7}   {after['leaked_count']:>7}",
        f"  answerable nodes    {before.get('answerable_nodes', 0):>7}   "
        f"{after.get('answerable_nodes', 0):>7}"
        f"   ({before.get('answerable_pct', 0)}% → {after.get('answerable_pct', 0)}%)",
        f"  off-ontology        {before.get('off_ontology_nodes', 0):>7}   "
        f"{after.get('off_ontology_nodes', 0):>7}   the model's word, not ours",
        f"  invented            {before.get('invented_nodes', 0):>7}   "
        f"{after.get('invented_nodes', 0):>7}   endpoints we conjured, not extracted",
        f"  extracted (denom.)  {before.get('extracted_nodes', 0):>7}   "
        f"{after.get('extracted_nodes', 0):>7}",
        f"  answerable/extracted"
        f"  {before.get('answerable_pct_of_extracted', 0)}%"
        f"   →  {after.get('answerable_pct_of_extracted', 0)}%",
        "",
    ]
    if after.get("leaked_distinctive_count"):
        lines.append("  STILL LEAKING: " + ", ".join(after["leaked_distinctive"]))
    elif after["leaked_count"]:
        lines.append("  overlapping domain words, not copied identifiers: "
                     + ", ".join(after["leaked_example_entities"]))
    else:
        lines.append("  no example entity from the prompt reached the output")
    # ── what the headline can and cannot support ────────────────────────────
    if before.get("runs") or after.get("runs"):
        lines.append("")
        lines.append(
            f"  headline    {before.get(f'{HEADLINE}_mean', 0)}%"
            f"  →  {after.get(f'{HEADLINE}_mean', 0)}%"
            f"   ({before.get('runs', 1)} run(s) over "
            f"{before.get('documents_measured', 0)} docs, range "
            f"{before.get(f'{HEADLINE}_min', 0)}–{before.get(f'{HEADLINE}_max', 0)}"
            f"  →  {after.get('runs', 1)} run(s), range "
            f"{after.get(f'{HEADLINE}_min', 0)}–{after.get(f'{HEADLINE}_max', 0)})")
        if resolvable(before, after):
            lines.append("  the difference exceeds the spread within either condition")
        elif before.get("runs", 1) < 2 or after.get("runs", 1) < 2:
            lines.append(
                "  NOT RESOLVABLE: one run per condition estimates no spread, so\n"
                "  any difference clears it. Use --repeat before reading this.")
        else:
            lines.append(
                "  NOT RESOLVABLE: the difference is inside the run-to-run spread.\n"
                "  This says nothing about the change. Measure more documents per\n"
                "  run, or more runs, before reading anything into it.")
    lines.append("")
    lines.append("  Counts moving is not by itself an improvement — a different")
    lines.append("  extractor finds different things. Parity is an honest result;")
    lines.append("  what the gate turns on is the leak, and the types being the")
    lines.append("  project's own rather than a sales pipeline's.")
    return "\n".join(lines)


async def _repeat(corpus, args):
    """Every run, **inside one event loop** (W49).

    `--repeat` called `asyncio.run()` per run, and `weave_core`'s storage locks
    are module-level `asyncio.Lock`s created on first use. They bind to the loop
    that created them, so run 2 died with *"is bound to a different event loop"*
    ten documents in.

    **`--repeat 1` is the default and works**, so every test and every manual
    check passed: the flag broke only at the value it exists for. That is W20's
    family — a control correct at its default and wrong when used.

    Returns `(completed_runs, failure_or_None)` rather than raising, so a crash
    on run 3 still reports runs 1 and 2 instead of losing the whole measurement.
    """
    runs: List[Dict[str, Any]] = []
    for index in range(max(1, args.repeat)):
        try:
            runs.append(await measure(corpus, f"{args.working_dir}-{index}", args.chars,
                                      quadruple=not args.no_quadruple,
                                      workspace=f"measure{index}"))
        except Exception as error:  # noqa: BLE001 - the report is the point
            return runs, f"run {index + 1} of {max(1, args.repeat)} failed: {error!r}"
    return runs, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default="docs", help="directory to read documents from")
    parser.add_argument("--chars", type=int, default=DEFAULT_CHARS,
                        help="characters per document (identical between runs)")
    parser.add_argument("--working-dir", default="",
                        help="scratch storage; not a Weave working directory. "
                             "Defaults to a temp directory, never the repository")
    parser.add_argument("--out", default="", help="write the report as JSON here")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="print a comparison of two saved reports and exit")
    parser.add_argument("--no-quadruple", action="store_true",
                        help="measure the WeaveEngine path instead of WeaveGraph — "
                             "the one a PostgreSQL deployment runs (A4 v6, D-039), "
                             "and the only one that reaches "
                             "entity_extraction_examples")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run the corpus this many times and report the mean "
                             "and range. One run is a sample, not a measurement: "
                             "the same prompt scored 73.1 and 56.7 on consecutive "
                             "runs (W48)")
    parser.add_argument("--names-only", action="store_true",
                        help="print the example entity names the prompt teaches, and exit "
                             "(needs no model)")
    args = parser.parse_args()

    if args.compare:
        before = json.loads(pathlib.Path(args.compare[0]).read_text(encoding="utf-8"))
        after = json.loads(pathlib.Path(args.compare[1]).read_text(encoding="utf-8"))
        print(compare(before, after))
        return 0 if after.get("leaked_distinctive_count", after["leaked_count"]) == 0 else 1

    if args.names_only:
        for name in example_entity_names():
            print(name)
        return 0

    root = pathlib.Path(args.corpus).resolve()
    corpus = _corpus(root, ("*.md", "**/*.md")) if root.is_dir() else [root]
    if not corpus:
        print(f"no documents under {root}", file=sys.stderr)
        return 2

    # **Never inside the repository** (W52). The default was
    # `./.measure_extraction`, so a run from the repo root wrote 3 MB of
    # ingested document text into the working tree — and the name-guard failed
    # on it, correctly, because the ingested content includes the very strings
    # A3 bans. A measurement instrument that dirties the tree it measures is a
    # defect whoever runs it.
    if not args.working_dir:
        args.working_dir = tempfile.mkdtemp(prefix="measure-extraction-")
        print(f"scratch storage: {args.working_dir}", file=sys.stderr)

    runs, failure = asyncio.run(_repeat(corpus, args))
    if not runs:
        print(f"\nNOT A MEASUREMENT: no run completed — {failure}", file=sys.stderr)
        return 4
    report = aggregate(runs)
    if failure is not None:
        # **Say what it managed** (W49). The first version died mid-repeat and
        # wrote nothing at all: no partial report, no summary, a stack trace in a
        # log. The absence of a file was the only signal that anything had gone
        # wrong, and only because someone was watching the pass count.
        report["incomplete"] = True
        report["failure"] = failure
        report["runs_requested"] = max(1, args.repeat)

    # **An empty graph is not a clean baseline** (W37, second half).
    #
    # With the backend unreachable, extraction logs its failure and returns; the
    # run then reported `entities: 0, leaked: 0` and exited 0 — a *green*
    # result from a run in which nothing was extracted. That is the same defect
    # this harness exists to measure, in the harness: a check that quietly means
    # less than it says. Zero entities over a non-empty corpus is a failed run.
    if report["entities"] == 0 and corpus:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(
            f"\nNOT A MEASUREMENT: {len(corpus)} document(s) produced 0 entities.\n"
            "  Extraction did not run — check the model and embedding backends "
            "above for\n  connection errors. A baseline of zero is not a baseline.",
            file=sys.stderr)
        return 3

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["leaked_distinctive_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
