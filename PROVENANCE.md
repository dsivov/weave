# Provenance

Weave is a **one-way copy** of selected modules from an existing tree, taken once,
rebranded completely, and never linked back (D-002). This file records what was
copied, from which commit, and every deliberate port since.

> **The source is a source, not a dependency.** Nothing here imports it, reads it
> at runtime, or writes to it (A2). Picking up a later change from it is a
> reviewed port with its own `D-NN` — never an automatic sync.

## The pinned commit

| | |
|---|---|
| **Commit** | `608401b84580c85f9473c1a5091a3a5ce04bed8b` |
| **Subject** | *fix(weave): a long-lived container must not lose its seat* |
| **Branch** | `feat/weave-p0` |
| **Copied on** | 2026-08-08 |
| **Copied by** | P0, work plan tasks P0.3 – P0.5 |
| **Baseline** | `.source-baseline.txt` — the source's head, status hash and tracked-content hash, asserted by `scripts/parent_checksum.sh verify` |

**The copy point is a commit, never a working tree** (D-022). At the moment of
copying, the source tree was dirty in four of the modules P0 carries across —
`weave/coordinator.py`, `weave/routes.py`, `weave/store.py`, `weave/worker.py`.
That work was **excluded, not committed** (D-023): committing it would have been a
write into a tree this project is forbidden to write to. It is a deliberate port
later, under its own decision entry, or it is never carried.

### Reading the tables below

The source has two top-level Python packages. Both names carry the source
product's brand, which constraint **A3** forbids anywhere in this repository —
including in this file. They are written here as:

- **⟨engine⟩/** — the retrieval/graph engine package
- **⟨platform⟩/** — the governance and team package
- **⟨webui⟩/** — the React application directory

With the pinned commit checked out there are exactly two Python packages and one
UI directory at top level, so the mapping resolves on sight.

## What was copied — the engine → `weave_core/`

| Destination | Source | LOC | Notes |
|---|---|---:|---|
| `weave_core/graph/engine.py` | `⟨engine⟩/⟨engine⟩.py` | 4,079 | the graph engine |
| `weave_core/graph/quadruple.py` | `⟨platform⟩/core.py` | 2,352 | the `(h,r,t,rc)` layer + CGR3 retrieve→rank→reason |
| `weave_core/graph/operate.py` | `⟨engine⟩/operate.py` | 5,433 | |
| `weave_core/graph/query.py` | `⟨engine⟩/utils_graph.py` | 1,753 | |
| `weave_core/graph/prompt.py` | `⟨engine⟩/prompt.py` | 794 | |
| `weave_core/graph/base.py` | `⟨engine⟩/base.py` | 915 | the storage roles |
| `weave_core/graph/types.py` | `⟨platform⟩/types.py` | 252 | `RelationContext`, 11 fields |
| `weave_core/graph/storage/files.py` | `⟨engine⟩/kg/{networkx,json_kv,json_doc_status,nano_vector_db}_impl.py` | 1,706 | **merged** into one module |
| `weave_core/graph/storage/postgres.py` | `⟨engine⟩/kg/postgres_impl.py` | 5,778 | |
| `weave_core/graph/storage/neo4j.py` | `⟨engine⟩/kg/neo4j_impl.py` | 1,922 | |
| `weave_core/graph/storage/__init__.py` | `⟨engine⟩/kg/__init__.py` | — | **rewritten**: registry cut to the three supported paths (A4) |
| `weave_core/store/record.py` | `⟨platform⟩/weave/recordstore.py` | 142 | promoted to the one persistence port (A4, D-020) |
| `weave_core/store/locks.py` | `⟨engine⟩/kg/shared_storage.py` | 1,717 | keyed locks, incl. the workspace-keyed claim lock |
| `weave_core/governance/rbac/` | `⟨platform⟩/rbac/` | 406 | |
| `weave_core/governance/lifecycle/` | `⟨platform⟩/lifecycle/` | 393 | |
| `weave_core/governance/actions/` | `⟨platform⟩/actions/` | 850 | |
| `weave_core/governance/rules/` | `⟨platform⟩/rules/` | 1,290 | incl. `gate.py`, the single verdict entry point |
| `weave_core/governance/ontology/` | `⟨platform⟩/ontology/` | 1,200 | |
| `weave_core/studio/` | `⟨platform⟩/studio/` | 900 | propose → diff → sign, versioned |
| `weave_core/studio/diagrams/` | `⟨platform⟩/diagrams/` | 632 | kept a package (see deviations) |
| `weave_core/studio/apps.py` | `⟨platform⟩/apps/schema.py` | 86 | `AppBundle` |
| `weave_core/events/schema.py` | `⟨platform⟩/events/schema.py` | 73 | |
| `weave_core/events/bus.py` + `inprocess.py` | `⟨platform⟩/events/service.py` | 47 | **split**: port and adapter separated (A7, D-019) |
| `weave_core/events/ingress.py` | `⟨platform⟩/events/store.py` | 137 | durable append-then-publish log |
| `weave_core/knowledge/{dedup,quality,community,connectivity}/` | the same-named `⟨platform⟩/` packages | 1,426 | |
| `weave_core/flows/` | `⟨platform⟩/flows/` | 1,037 | placed in the engine, not the product — see deviations |
| `weave_core/llm/` | `⟨engine⟩/llm/{openai,azure_openai,bedrock,gemini,jina,lollms,ollama,binding_options}.py` | 3,454 | **the 8 wired connectors only** |
| `weave_core/llm/rerank.py` | `⟨engine⟩/rerank.py` | 577 | |
| `weave_core/{utils,exceptions,constants,types,namespace}.py` | the `⟨engine⟩/` equivalents | ~3,700 | |
| `weave_core/jsonio.py` | `⟨platform⟩/jsonio.py` | 46 | |
| `weave_core/version.py` | — | — | **new**: holds `__api_version__` so the engine need not import the server (A2) |

## What was copied — the product → `weave/`

| Destination | Source | LOC | Notes |
|---|---|---:|---|
| `weave/team/coordinator.py` | `⟨platform⟩/weave/coordinator.py` | 558 | atomic claim, workspace-keyed lock |
| `weave/team/store.py` | `⟨platform⟩/weave/store.py` | 153 | task records |
| `weave/team/workers.py` | `⟨platform⟩/weave/workers.py` | 223 | fleet registry |
| `weave/team/worker.py` | `⟨platform⟩/weave/worker.py` | 526 | the loop + `scrub_api_auth()` / `preflight_subscription_auth()` (A13) |
| `weave/team/integration.py` | `⟨platform⟩/weave/integration.py` | 171 | environments + the merge gate |
| `weave/team/project.py` | `⟨platform⟩/weave/project.py` | 124 | |
| `weave/team/playbook.py` | `⟨platform⟩/weave/playbook.py` | 326 | `role_kit()`, `claude_md()`, `_mcp_config()` |
| `weave/team/preset.py` + `preset/*.json` | `⟨platform⟩/weave/preset.py` + `preset/` | 468 | |
| `weave/devhost/registry.py` | `⟨platform⟩/weave/devhost.py` | 313 | host records, `run·drain·pause·stop`, seat health |
| `weave/devhost/{daemon,runtime,worktree}.py` | `⟨platform⟩/weave/devhost_daemon.py` | 759 | **split** three ways along the seams the plan names |
| `weave/ingress/` | `⟨platform⟩/integration/` | 587 | external events in, typed and deduped |
| `weave/server/app.py` | `⟨engine⟩/api/⟨engine⟩_server.py` | 2,070 | |
| `weave/server/config.py` | `⟨engine⟩/api/config.py` | 625 | |
| `weave/server/auth.py` | `⟨engine⟩/api/auth.py` | 126 | JWT kept; the account source changes in P1 |
| `weave/server/utils.py` | `⟨engine⟩/api/utils_api.py` | 460 | |
| `weave/server/workspace_pool.py` | `⟨engine⟩/api/workspace_pool.py` | 220 | |
| `weave/server/mcp.py` | `⟨engine⟩/api/mcp_server.py` | 643 | Streamable HTTP; server name and workspace header rebranded (R55) |
| `weave/server/gunicorn.py` · `gunicorn_config.py` | `⟨engine⟩/api/{run_with_gunicorn,gunicorn_config}.py` | 444 | kept as two — see deviations |
| `weave/server/routers/` (13) | `⟨engine⟩/api/routers/` | ~7,500 | `_routes` suffix dropped from every filename |
| `weave/server/routers/team.py` | `⟨platform⟩/weave/routes.py` | 772 | incl. `/hosts/{register,heartbeat,control,scale}` |
| `weave/server/routers/reasoning.py` | `⟨platform⟩/api/routes.py` | 1,383 | CGR3 + relation-context inspection |
| `weave-ui/` | `⟨webui⟩/` | 26,659 | React 19 · Vite 7 · Tailwind 4 · zustand 5 |
| `deploy/dev-agent.Dockerfile` | `docker/weave-dev.Dockerfile` | 39 | `COPY` paths and entry point rebranded (R49); safety properties verbatim (R50) |
| `tests/` | `⟨platform⟩/tests/` (minus 6 scraper suites) | ~9,900 | carried with the code (R5) |

## What was deliberately **not** copied

| Left behind | Why |
|---|---|
| 6 storage backends — Mongo, Milvus, Memgraph, Redis, Qdrant, Faiss, plus AGE | 7,617 LOC that could not be honestly gated at every milestone (D-007, A4) |
| the web-ingestion / crawling package and its 6 test suites | a non-goal; leaves with `lxml` and `playwright` (D-008) |
| the evaluation harness and the engine's `tools` package | 5,977 LOC, neither wired into the server |
| 7 unwired model connectors | never mounted by the server's configuration |
| **the Anthropic SDK connector** | constraint-mandated, not merely unused: no SDK may sit in a Claude Code path (A13, D-015) |
| the Ollama model-emulation API routes | a compatibility surface for a product Weave is not; it was also the one route group answering without passing governance (A6) |
| `pyproject.toml`, `uv.lock` | read once as the version source of truth, then not carried — conda is the manager (D-006) |

## Deviations from the work plan, and why

Recorded here because a plan is a decision and departing from one silently is
how a fork stops being auditable.

1. **`⟨platform⟩/api/routes.py` → `weave/server/routers/reasoning.py`**, not
   `graph.py`. The engine's own `graph_routes.py` already claims that name; two
   different routers cannot be one file.
2. **`gunicorn.py` and `gunicorn_config.py` stay two files.** The launcher imports
   the config as a *module object* and sets attributes on it; merging them breaks
   that mechanism.
3. **`weave_core/studio/diagrams/` stays a package.** Collapsing four modules into
   one requires rewriting intra-package imports, which is not mechanical — and P0
   is gated on *no behaviour change*.
4. **`flows/` landed in `weave_core/`, not `weave/`.** The studio ledger validates
   `flow` artifacts, so with flows in the product layer the engine would import
   the product — which A2 forbids. Complying with the contract outranks the
   plan's file placement.
5. **Two packages the plan does not name were copied** because the server and the
   carried test suites need them: `⟨platform⟩/integration/` → `weave/ingress/`,
   and `⟨platform⟩/apps/` → `weave_core/studio/apps.py`.
6. **`__api_version__` moved into `weave_core/version.py`.** Two model connectors
   imported it from the server package — the engine reaching into the HTTP layer
   (A2). It is re-exported from `weave.server`, so callers there are unchanged.

## Port log

Deliberate ports of later source work. Each needs its own `D-NN` in
[docs/DECISIONS.md](docs/DECISIONS.md); there is no automatic sync.

| Date | From commit | What | Decision |
|---|---|---|---|
| _(none yet)_ | | | |

**Outstanding, known, and deliberately deferred:** the uncommitted `release()`
work in the source's claim path at the time of the copy — a task handed back,
`attempts` tracked, a move to `blocked` after a limit, and a `learnings` entry
appended per release. Excluded by D-023. If it is ever carried, it lands here
with its own decision entry.
