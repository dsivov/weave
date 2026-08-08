# Weave — Architecture Contract

- **Version:** v3  ·  **Agreed:** 2026-08-08 by dsivov  ·  **Status:** **in force**
- **Sources:** [WEAVE_RFC.html](WEAVE_RFC.html) · [WEAVE_DRP.md](WEAVE_DRP.md) · [WEAVE_ARCHITECTURE.html](WEAVE_ARCHITECTURE.html) · decisions D-002…D-027 in [DECISIONS.md](DECISIONS.md)

**Guiding principle:** Weave is a standalone product built from a one-way copy of a proven engine — every
governed action is enforced by the graph, every answer resolves to a real document, and nothing here
depends on the tree it came from.

> **How to use this file (agent):** re-read it whenever a **tripwire** below fires. If a change
> would make any sentence here **false**, STOP — do not implement it. Report the drift to the
> human (constraint ID · what the contract says · what the change needs · why), offer
> *comply / amend / defer*, and wait. On approval, amend this file **first** (bump the version,
> add an amendment row), then log a `D-NN` in `DECISIONS.md`, then build. Never drift silently.

## Constraints

| ID | Area | Constraint | Why |
|----|------|------------|-----|
| A1 | Shape | Exactly three deployables: the **Weave server** (which also serves the built UI as static assets), the **dev-host daemon**, and the **dev-agent container image**. No fourth. | A fourth deployable is a new failure mode and a new thing to onboard. The UI is not one: it is built and served by the server. |
| A2 | Boundaries | `weave_core/` imports nothing from `weave/` **and imports no HTTP framework**; all HTTP lives in `weave/server/`. Nothing in this repository imports from, reads at runtime, or writes to the parent tree. Dependencies point inward only. | The engine stays separable and testable without a server, and standing alone is the point (D-002, D-003, D-005, D-020). |
| A3 | Naming | No occurrence of `lightrag` or `context graph` (any case or separator) in any filename, module path, environment variable, header, storage identifier, log string, UI string or **product document** — and none in a generated public contract either, such as an OpenAPI `operationId`. **Out of scope:** the seven pipeline artifacts that state this rule and trace the fork (`CONSTRAINTS.md`, `DECISIONS.md`, `WEAVE_RFC.html`, `WEAVE_DRP.md`, `WEAVE_WORK_PLAN.md`, `DOCS_INDEX.md`, `START.md`), which cannot say what is banned without naming it. Sole *content* exemption elsewhere: a `docs/BLOG_*.html` lineage passage carrying `<!-- nameguard:allow lineage -->`. | A half-rebrand teaches the old vocabulary anyway (D-004), and product documentation is most of what a new joiner reads. The seven are enumerated, not a category, so the carve-out cannot widen by argument; the guard reports every honoured exemption (D-014, D-027). |
| A4 | State | Exactly three storage paths: file-based, PostgreSQL, Neo4j. No fourth backend. **All persistence goes through the `RecordStore` / `GraphStore` ports; no module constructs a database client outside its own adapter.** | Each path kept must be gated at every milestone (D-007), and a direct client bypasses the abstraction that makes three paths one codebase (D-020). |
| A5 | State | Artifact nodes (PRD, RFC, ADR, Diagram, ChangeRequest, Task, Feature, Review, Insight) reference their source by `repo · path · rev` and never embed a copy of it. Ingested-document storage (`full_docs`, `text_chunks`) is the retrieval index and is not an artifact body. | An artifact with a copied body rots against the repo; the retrieval index is derived data that is rebuilt, not authored (D-012). |
| A6 | Data flow | Every action passes governance — RBAC 403, lifecycle 409, rule verdict — and the principal it is enforced against is derived from the authenticated identity, never from a client-supplied field. No endpoint bypasses either half. | Governance that is skippable on some routes is not governance, and a self-stamped principal makes the verdict meaningless. |
| A7 | Runtime | The event-bus adapter must match the deployment: a single-process deployment may use the in-process bus; **any multi-worker deployment uses the PostgreSQL `LISTEN/NOTIFY` adapter.** | The in-process bus does not fan out across gunicorn workers — a client on one worker would silently never receive events published on another, with no error and no log (D-019). |
| A8 | Data flow | What the runtime enforces is the signed ledger version. Roles, RBAC and lifecycle have no server-file config path. | A wizard that writes what the runtime does not read is a second source of truth. |
| A9 | Interface | Each question is served by one handler shared by REST/UI and MCP. The human and agent surfaces never diverge. | Two answer surfaces that disagree are worse than one. |
| A10 | Interface | **Every role is a Claude Code session.** Human roles (manager, architect, senior developer) run it interactively — CLI, desktop app or IDE extension; dev agents run it headless in a container. Both speak the same MCP surface over HTTP from any machine. The runtime and the authenticated identity differ; the client does not. No bespoke human client. | One client architecture, not two. The CLI is the primary way of working, including on remote machines, and a separate human surface would drift from the agents'. |
| A11 | Stack | Python 3.12 + conda (`environment.yml`); bun for the UI. One JWT library (PyJWT); one test runner per language — `pytest` for Python, `bun test` for the UI. No new external library without a `D-NN`. | One library per job; the copy is net subtraction (D-006, R10). |
| A12 | Runtime | No orchestrator model. Reasoning is first-party Claude Code sessions; coordination is deterministic graph logic. No model sits in the routing path. | The constraint the whole design was built to satisfy. |
| A13 | Trust | **Two LLM paths, never merged.** Every Claude Code client — human roles and dev agents alike — authenticates by subscription seat only: the `anthropic` SDK is not a dependency, and no API key, auth token or base-URL override reaches a Claude Code process (scrubbed, and the seat asserted before the session starts). Server-side LLM use — graph build, extraction, embedding, retrieval, rules — runs through the configurable backend connectors and is the **only** place a model credential exists. | Subscription-based Claude Code access is a hard limitation of this architecture: an SDK call in that path breaks it and meters the work. The server backend is separate on purpose so a team can repoint it. |
| A14 | Trust | Users are persisted records with bcrypt hashes and explicit per-workspace membership. No environment-variable accounts. | The gap this project exists to close (D-009). |
| A15 | Ops | One central hub, and it **never dials out**: dev hosts and workers are outbound-only, reaching the server by register/heartbeat and reconciling to state they read back. Dev agents are containers holding no git credentials and no metered-auth keys, and they cannot merge. | Outbound-only is what lets a dev host sit behind NAT or in a private VPC; the blast radius stays a branch and a PR. |

## Non-goals (deliberately not built)

- Any modification of `Context_Graph/`, or any runtime link to it.
- Mongo, Milvus, Memgraph, Redis, Qdrant, Faiss storage backends.
- Web ingestion / crawling, the evaluation harness, `lightrag.tools`.
- CRDT co-editing (409-and-merge is the v1 answer).
- Federation of per-developer graphs.
- External IdP / SSO, email invitations, scoped API tokens.
- A bespoke desktop/CLI client for human roles, or any Anthropic-SDK-based agent runtime.
- Storing document bodies in the graph.
- Reaching for one of these is a drift, not a feature — same protocol as above.

## Tripwires — re-read this file when any of these is about to happen

- Adding, replacing, or removing an **external dependency**, service, or datastore.
- Creating a **top-level directory**, module, or deployable — or moving one.
- Crossing a **stated boundary** (`weave_core/` reaching into `weave/`; anything reaching into the parent tree).
- **Anything that requires the server to open a connection to a dev host or worker** — it breaks the outbound-only property remote fleets depend on.
- Changing a **public contract**: API endpoint, schema, CLI, event, or file format.
- Introducing **state, concurrency, caching, or background work** where there was none.
- Changing the **auth / tenancy / trust** model, or the **deployment target**.
- Adding a second tool for a job something already does (R10).
- **Running the server with more than one worker**, or changing the event-bus adapter — the pairing in A7 is what makes SSE correct.
- Anything the **Non-goals** list names.
- **Touching the claim protocol, lifecycle guards, or the `touches` collision rule** — a fleet race here is invisible until it corrupts work.
- **Adding an object type or link type to the ontology**, or changing a locator field.
- **Writing anything into `Context_Graph/`** — including a stray formatter, test artifact, or `__pycache__`.
- **Anything that puts a model credential, SDK call, or base-URL override anywhere near a Claude Code process** — human seat or dev container. Adding the `anthropic` package is this tripwire firing.
- Adding a **client surface for humans** that is not ordinary Claude Code or the web UI.

## Amendments

> One row per approved change of direction. The constraint text above is edited in place;
> this is the audit trail. No entry without a human's explicit approval.

| Date | v | Constraint | Was → Now | Approved by | Decision |
|------|---|------------|-----------|-------------|----------|
| 2026-08-08 | v2 | A2 | was: no import from `weave/` → now: **and no HTTP framework in the core** | dsivov (pending) | D-020 |
| 2026-08-08 | v2 | A4 | was: three storage paths → now: **+ all persistence through the store ports** | dsivov (pending) | D-020 |
| 2026-08-08 | v2 | A6 | was: two rows (governance enforced; principal authenticated) → now: **merged into one**, to make room for A7 | dsivov (pending) | D-019 |
| 2026-08-08 | v2 | A7 | **new** — the event-bus adapter must match the deployment | dsivov (pending) | D-019 |
| 2026-08-08 | v3 | A3 | was: no occurrence in any **document**, one blog exemption → now: scoped to **product** documents, with the **seven pipeline artifacts enumerated out of scope**, and extended to cover **generated** public contracts | dsivov | D-027 |
