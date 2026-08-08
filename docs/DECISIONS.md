# Weave — Decision Log

Append an entry whenever a non-trivial choice is made or reversed. Keep them short.
Status: `accepted` · `superseded by D-NN` · `reversed`.

---

## D-001 · Adopt the house methodology for Weave
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** Project bootstrap — Weave needs a way of working before it needs code.
- **Options:** house methodology (docs-first, reviewed per milestone) / ad-hoc build-first
- **Decision:** House methodology. Templates and `house.css` copied into `docs/` so the project
  is self-contained and does not depend on the ONBOARDING repo's location.
- **Why:** The pipeline (BLOG → RFC ↔ DRP → CONSTRAINTS → ARCHITECTURE → WORK PLAN → reviews)
  forces the multi-user / multi-role model to be settled in writing before it is settled in code.
- **Consequences:** No build without an RFC/DRP and a work-plan task (R1); milestones advance on
  passing test gates and clean reviews (R3, R4); `docs/CONSTRAINTS.md` becomes binding once written (R11).

---

## D-002 · Standalone by copy, not by dependency
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_RFC.html
- **Context:** Weave exists and works inside the parent tree (`Context_Graph/context_graph/weave/`,
  ~3.6k LOC on the unmerged `feat/weave-p0` branch), but as a flagged subsystem of another product.
- **Options:** git submodule / upstream pip package / monorepo / one-way copy into a new repo
- **Decision:** Copy the selected modules into this repository and rename them. No submodule, no
  upstream package, no shared environment, no runtime import of the parent.
- **Why:** Weave needs its own release, brand and roadmap. Any upstream dependency drags the parent's
  naming, config surface and cadence along with it, which makes the rebrand impossible to complete.
- **Consequences:** This is a fork by intent. Picking up a later parent change is a deliberate,
  reviewed port with its own decision entry — never an automatic sync. `PROVENANCE.md` pins the
  source commit (`feat/weave-p0`, head `1260d109`). *(Superseded by **D-023**: the actual pin is `608401b8`. The reference above is left as written — the log is chronological and is not rewritten.)*

## D-003 · Zero writes to the parent tree, verified
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** The parent must keep working as a generic product while Weave forks from it.
- **Options:** "be careful" / assert it mechanically
- **Decision:** `Context_Graph/`'s git status and working-tree checksum are asserted unchanged in CI
  and at the M0 gate.
- **Why:** An intention is not a control.

## D-004 · Total rebrand, enforced by a CI name-guard
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Options:** rebrand UI only / rebrand incrementally per phase / total rebrand at P0
- **Decision:** No occurrence of `lightrag` or `context graph` in any filename, module path,
  environment variable, storage identifier, UI string or document. A grep runs on every commit and
  fails the build on a non-zero result.
- **Why:** A half-rebrand teaches everyone the old names anyway, and the cost of finishing it rises
  with every phase built on top.

## D-005 · Two Python packages: `weave_core/` (engine) + `weave/` (product)
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Options:** one flat `weave/` package / `weave/` + `weave_core/` / `weave/` + a separately-named engine
- **Decision:** `weave_core/` (graph, governance, studio, events, knowledge, llm), `weave/` (team,
  server, wizards, live, cli), `weave-ui/` (React app).
- **Why:** The engine has a plausible independent life, and the split stops the team layer reaching
  into graph internals. A third brand name for the engine was rejected as one more thing to explain.

## D-006 · conda as the Python dependency manager
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Asked, not assumed (R9)**
- **Options:** conda (house default) / uv / poetry / pip + venv
- **Decision:** conda, manifest `environment.yml`.
- **Why:** House default; handles the native/database client wheels directly. uv was the runner-up —
  the parent ships `pyproject.toml` + `uv.lock`, so it would have transferred with least friction —
  and was rejected only because conda is the house standard. The parent's manifests are read once as
  the version source of truth, then not carried.

## D-007 · Three storage paths: file-based, PostgreSQL, Neo4j
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** The parent ships 8 interchangeable backends, 18,740 LOC, dynamically loaded by name.
- **Options:** file-only / file+Postgres / file+Postgres+Neo4j / all 8
- **Decision:** Copy file-based (NetworkX/Json/Nano, 1,706), Postgres (5,778) and Neo4j (1,922) —
  9,406 LOC. Drop Mongo, Milvus, Memgraph, Redis, Qdrant and Faiss — 7,617 LOC.
- **Why:** File-based makes first run trivial; Postgres covers KV + vector + graph in one production
  service; Neo4j serves teams that want a dedicated graph engine. The other six could not be honestly
  gated per milestone.
- **Consequences:** Two production paths to test at every gate. If Neo4j cannot hold the bar it ships
  labelled experimental rather than quietly half-working.

## D-008 · Keep ingestion and retrieval; drop the web scraper
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Options:** full RAG surface incl. webingest / ingestion+retrieval only / backfill only / no RAG
- **Decision:** Keep document extraction and CGR3 retrieval (orient, precedent, curated backfill).
  Drop `webingest/` (1,819 LOC + lxml + playwright), `lightrag.tools` (4,936) and `evaluation` (1,041).
- **Why:** Semantic precedent is load-bearing for the team story; web crawling is a parent use case.

## D-009 · A real user store replaces `AUTH_ACCOUNTS`
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** The parent has no user store at all. `lightrag/api/auth.py:30` parses
  `AUTH_ACCOUNTS='admin:admin123,user1:pass456'` from an env var once at boot, with a parallel
  `AUTH_ROLES` var; there are zero user CRUD routes and no users table anywhere in the tree. Adding a
  person means editing `.env` and restarting the server.
- **Options:** extend the env string / external IdP in v1 / a persisted user store with an Admin UI
- **Decision:** Persisted users (bcrypt hashes, governance role, per-workspace membership,
  active/disabled) on all three storage paths, with CRUD routes and an Admin ▸ Users screen. Built on
  the existing `RecordStore` pattern; JWT issuance and server-side role assignment unchanged. The env
  vars are migrated on first boot and then removed from the config surface — the two never coexist.
- **Why:** It is the most-hit gap in the parent and a hard blocker for multi-user.
- **Consequences:** No new library — bcrypt and PyJWT are already dependencies.

## D-010 · Live updates over SSE; optimistic concurrency for shared edits
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** There is no WebSocket or SSE anywhere in the parent tree; the Weave board polls
  `setInterval(load, 4000)`. An in-process event bus with a durable ingress log already exists
  (`context_graph/events/`) with no consumer attached.
- **Options:** keep polling / WebSocket / SSE / CRDT co-editing
- **Decision:** SSE fed by the existing event bus, plus presence; version-checked writes on Studio
  artifacts returning 409 and a merge view. CRDT co-editing deferred behind a named transport seam.
- **Why:** The traffic is server→client, Starlette streams without a new library, and the Studio ledger
  already versions every artifact. WebSocket was rejected as bidirectional capability we do not need
  plus a uvicorn extra; CRDT was rejected for v1 as a large dependency ahead of demonstrated need.
- **Consequences:** Revisit CRDT if the artifact merge-conflict rate becomes a real complaint — a new
  decision entry, not a silent redesign.

## D-011 · Four answer-bearing node types added to the data model
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_RFC.html §Data model
- **Context:** Weave is the team's primary source of truth for humans and agents alike, which means
  four standing question classes: *what changed · why · what does it do · what did we learn*. The
  inherited model (14 object types, 18 link types) answers the first two. It has **no node type for
  functionality at all** — `Module` is code-structural, PRD/RFC are documents — and reviews and
  learnings are persisted as `reviews: List[Dict]` / `learnings: List[str]` fields on the task record
  (`context_graph/weave/store.py:41-44`), so they cannot be queried, linked or cited; the declared
  `reviewed_in` link type terminates on nothing.
- **Options:** leave them as task fields and answer by retrieval / promote them to first-class nodes
- **Decision:** Add `Feature`, `Review`, `Insight` and `Question` as object types with their link types
  (`implemented_by`, `specified_by`, `depicted_by`, `answered_by`), and migrate existing task
  `reviews`/`learnings` into nodes once, asserted by count and content.
- **Why:** An answer that cannot be traversed to, linked from, or cited is not an answer a team can
  build on — and retrieval over free text cannot tell you *what this system does*.
- **Consequences:** The migration must be idempotent and is gated at M2; the source fields stay
  readable until M2 is signed off, then are removed in one commit so the two never coexist.

## D-012 · Every artifact node carries a resolvable locator
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** The graph is an index over the team's repositories. Of the 14 object types, only
  `Module.path`, `PullRequest.url` and `Environment.url` carry any locator — PRD, RFC,
  ArchitectureDecisionRecord, Diagram, ChangeRequest and Task carry none, and `Commit` has only a
  `subject`, not a sha. So a role that needs the full document cannot get from the index to the file.
- **Options:** store document bodies in the graph / rely on path conventions / a locator + registered
  project layout
- **Decision:** Every artifact node carries `repo · path · rev · anchor`; `Commit` gains `sha`. A
  registered `ProjectLayout` (name → clone URL, local path, default rev) resolves a locator into a URL
  a human opens and a file an agent reads.
- **Why:** The graph must stay an index *over* the repositories, never a second copy of them — nothing
  duplicated means nothing to keep in sync, and a locator that stops resolving is a detectable defect
  rather than silent rot.
- **Consequences:** `rev` pins the locator to the revision it was written for, not a moving `HEAD`. A
  resolver check gates M2 on zero dangling locators and runs periodically thereafter.

## D-013 · Onboarding is a product surface, not a library
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** The onboarding pieces exist as library functions — `preset.install()`,
  `playbook.role_kit()`, `claude_md()`, `_mcp_config()` — and `docker/weave-dev.Dockerfile` is a
  well-built dev-agent image (no git credentials, no API keys, non-root, OAuth-token seat). But
  nothing assembles them: `scripts/` is empty, `docker-compose.yml` has a single service pulling the
  parent's upstream image with no Weave in it, and all four `[project.scripts]` console entry points
  are `lightrag-*`. There is no `weave` command.
- **Options:** document the manual steps better / build a CLI + bundle
- **Decision:** A `weave` CLI (`init`, `roles install`, `user add`, `project register`, `up`,
  `agents up/scale/down`) plus a compose bundle carrying the server, Postgres (optional Neo4j) and the
  dev-host daemon that manages the container dev agents.
- **Why:** The measured onboarding target — demonstrably faster than the parent — cannot be met by
  prose over a set of Python functions.
- **Consequences:** M6's gate runs on a clean machine following only published steps, and fails on any
  documented step that has no command behind it.

## D-014 · The name-guard exempts the marked lineage passage only
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_DRP.md R2a · CONSTRAINTS.md A4
- **Context:** D-004 forbids the parent's product names everywhere, but the BLOG names the parent to
  tell the project's origin — the concrete lineage is what makes its argument land.
- **Options:** exempt the lineage narrative / rewrite the blog to say "the parent tree" / exempt all
  of `docs/` and guard only code
- **Decision:** `scripts/nameguard.sh` scans everything — code, config, UI strings, and all
  documentation — with one exemption: a passage in `docs/BLOG_*.html` carrying
  `<!-- nameguard:allow lineage -->`. The guard reports which markers it honoured on each run.
- **Why:** The history is true and worth keeping, and the exemption is narrow, marked and greppable,
  so it cannot quietly widen. Exempting all of `docs/` was rejected because product documentation is
  most of what a new team member reads — the main thing A4 exists to protect.
- **Consequences:** The marker is in `docs/BLOG_THE_TEAM_IS_THE_PRODUCT.html`. Adding a second
  exemption is a contract amendment, not a commit.

## D-015 · Every role is a Claude Code session; the two LLM paths never merge
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_DRP.md §3.8 · CONSTRAINTS.md A10, A13
- **Context:** Human roles (manager, architect, senior developer) work through ordinary Claude Code —
  CLI is the primary mode — including from remote machines. Subscription-based Claude Code access is a
  hard limitation of the existing architecture that must be preserved. The parent already enforces this
  for dev containers: `worker.py` scrubs `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
  `ANTHROPIC_BASE_URL` and asserts the seat via `preflight_subscription_auth()`. Human seats have no
  equivalent, and `lightrag/llm/anthropic.py` imports the SDK.
- **Options:** a bespoke human client / Claude Code for humans too / SDK-based agent runtime
- **Decision:** Every role is a Claude Code session. Humans run it interactively (CLI, desktop app, IDE
  extension); dev agents run it headless in a container. Both speak the same MCP surface over
  Streamable HTTP, so a role works unchanged on a remote machine. Every such seat is subscription-only:
  `anthropic` is not a dependency, and no key, token or base-URL override reaches a Claude Code
  process. Server-side LLM use — graph build, extraction, embedding, retrieval, rules — runs through
  the configurable backend connectors and is the only place a model credential exists.
- **Why:** One client architecture rather than two, so the human and agent surfaces cannot diverge; and
  the subscription boundary is what makes the economics of the whole design work.
- **Consequences:** The scrub-and-assert boundary extends from the worker path to human seats (R59).
  `lightrag/llm/anthropic.py` is dropped — now constraint-mandated, not merely unwired. The generated
  MCP entry and its `LIGHTRAG-WORKSPACE` header are rebranded, and existing kit holders must re-run
  `weave roles install` (R55, R56).

## D-016 · The dev-host bundle is copied whole, with its invariants pinned
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_DRP.md §3.9 · CONSTRAINTS.md A1, A15
- **Context:** The "dev server bundle" — a deployed process that proxies between a developer
  environment and Weave, manages Docker containers, registers with the server, and is the senior
  developer's entry point for monitoring and controlling actual coding — is already implemented in the
  parent: `context_graph/weave/devhost.py` (313 LOC) + `devhost_daemon.py` (759 LOC).
- **Options:** re-derive a fleet manager / copy the design / copy the code
- **Decision:** Copy the code whole into `weave/devhost/`, and pin its load-bearing properties as
  requirements (R62–R76) so a later rewrite cannot lose them silently: outbound-only register +
  heartbeat (nothing connects *to* the daemon, so a host can sit behind NAT); scaling as state the
  host reads (`desired_workers`) rather than a command sent to it; reconcile that scales down from the
  highest-numbered worker and leaves individually-held slots empty; four host control states including
  `drain` (claim nothing new, finish what you hold); exactly one Claude subscription seat per machine,
  propagated into every container, with seat health reported on each heartbeat; an **allowlist**
  container environment; a narrow `ContainerRuntime` protocol so reconcile is testable without Docker;
  host-side clone and worktrees so containers hold no git credentials.
- **Why:** Each of these is a non-obvious answer to a real failure. The allowlist in particular exists
  because the daemon normally runs beside the server and holds the workspace's LLM keys and the JWT
  signing secret — a denylist would silently fail the day a new secret is added, inside something that
  runs an agent unattended with write permission.
- **Consequences:** `weave agents scale N` writes `desired_workers` rather than dialling the host
  (R46). The daemon installs on a dev machine without the server's dependency set (R75).

## D-017 · Three constraints corrected against the implementation before the contract went in force
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** The draft contract was audited against the parent implementation rather than assumed.
  Three constraints were factually wrong.
- **Decision:** (1) **A1** — the web UI is *not* a deployable: `lightrag/api/webui/` is built static
  assets served by the server through `StaticFiles`. The three deployables are the server (serving the
  UI), the dev-host daemon, and the dev-agent container image. (2) **A5** — "the graph never stores
  document bodies" was false: `lightrag.py` maintains `full_docs` and `text_chunks` KV stores, which is
  how retrieval works. The rule is now scoped to *artifact nodes*, with the retrieval index named as
  derived data. (3) **A11** — "one test runner" was false: `pytest` for Python, `bun test` for the UI;
  it is now one runner per language.
- **Why:** A contract that contradicts the code it governs gets ignored on first contact.
- **Consequences:** None of the three had reached `in force`, so no amendment row is required.

## D-018 · The architecture contract is in force
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Approved by:** dsivov
- **Context:** R11 — the RFC + DRP agreement is the first point where a top-level design exists to
  hold anyone to. The draft was audited against the parent implementation first (D-017) and extended
  with the Claude Code / subscription boundary (D-015) and the dev-host properties (D-016).
- **Decision:** `docs/CONSTRAINTS.md` v1 (A1–A15) is in force from 2026-08-08. `WEAVE_RFC.html` and
  `WEAVE_DRP.md` move from draft to accepted. `CLAUDE.md` is created at the repository root and
  imports the contract with `@docs/CONSTRAINTS.md`, so it loads every session.
- **Why:** A contract nobody loads is a document, not a control.
- **Consequences:** A change that would make any of A1–A15 false stops the build: report the drift
  (ID · what the contract says · what the change needs · why · comply/amend/defer) and wait. An
  approved amendment edits `CONSTRAINTS.md` first — bump the version, add an amendment row — then
  logs a `D-NN`, then builds.

## D-019 · Event fan-out: two bus adapters behind the existing port
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_ARCHITECTURE.html §Trade-offs
- **Context:** D-010 chose SSE "fed by the existing event bus" without addressing worker topology.
  The bus is single-process by construction — its own docstring reads *"Single-process async pub/sub…
  not durable… swap to a broker later"* — while the server supports gunicorn `--workers N`. SSE on
  that bus unchanged means a client connected to worker 2 never receives an event published on
  worker 1: no error, no log, the board simply stops updating for some users.
- **Options:** pin every deployment to one worker / Redis pub-sub / a real broker (NATS, RabbitMQ) /
  a second adapter behind the existing `EventBus` port
- **Decision:** Keep the port; ship two adapters. `InProcessBus` for single-process deployments;
  a PostgreSQL `LISTEN/NOTIFY` adapter for any multi-worker deployment. The adapter must match the
  deployment — now constraint **A7**.
- **Why:** A broker is a new dependency and a fourth service to run, both barred without a decision
  (A11, A1). `asyncpg` is already installed and already required for the multi-user path, so the
  fan-out costs no new library. One worker forever would cap the product below its intended scale.
  The pairing is self-consistent: file-based storage is single-operator only (A4), so it only ever
  runs one worker — exactly where the in-process bus is correct.
- **Consequences:** A7 added; running the server multi-worker is now a tripwire. The bus adapter is
  selected by configuration alongside the storage path, and M3's gate must exercise the multi-worker
  path, not just the single-process one.

## D-020 · Persistence through ports; no HTTP in the engine
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_ARCHITECTURE.html §Boundaries
- **Options:** an ORM (SQLAlchemy) for the Postgres path / the existing `RecordStore` abstraction
- **Decision:** All persistence goes through the `RecordStore` and `GraphStore` ports; no module
  constructs a database client outside its own adapter. `weave_core/` imports no HTTP framework —
  all HTTP lives in `weave/server/`.
- **Why:** An ORM is a new library for a job a working, tested abstraction already does, and it would
  cover only the Postgres path — leaving the file path on a second mechanism, which is the two-tools-
  for-one-job defect (R10). Keeping HTTP out of the engine is what lets the engine be tested without
  a server and stay separable (D-005).
- **Consequences:** A2 and A4 extended; contract bumped to v2.

## D-021 · Every surface is an adapter; the UI is not a deployable
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** WEAVE_ARCHITECTURE.html §Principle
- **Context:** The guiding principle — *an adapter that holds state has become a second source of
  truth* — needs to be concrete to be enforceable.
- **Options:** independent MCP tool implementations (as in the source) vs adapters over shared
  service functions; a separately deployed front end vs the UI built into the server image
- **Decision:** MCP tools, REST routes, the CLI and the wizards are all thin adapters over the same
  service functions. `weave-ui/` is built into the server image and served as static assets.
- **Why:** Two implementations of one question drift, and the drift stays invisible until a human and
  an agent disagree during a review (A9). A separately deployed front end is a fourth deployable to
  run, version-match and onboard, against a measured onboarding target — and buys nothing, since the
  UI has no traffic profile the API server cannot serve.
- **Consequences:** A1's three deployables are the server (serving the UI), the dev-host daemon and
  the dev-agent image — confirmed, not changed.

## D-022 · The copy point is a commit, not a working tree
- **Date:** 2026-08-08  ·  **Status:** accepted
- **Context:** While this architecture was being written, the source tree changed. `coordinator.py`,
  `store.py`, `devhost_daemon.py` and `test_weave_devhost.py` were all modified on 2026-08-08, and
  `coordinator.py` holds an uncommitted 46-line addition — a `release()` path that hands a claimed
  task back, tracks `attempts`, moves a task to `blocked` after a limit, and appends a `learnings`
  entry per release. Those are precisely the modules P0 copies, and the pinned head `1260d109` no
  longer describes what is on disk.
- **Decision:** P0 copies from a named commit. Before P0 begins, `git status` is re-run in the source:
  if it is dirty in any copied module, that work is committed first and `PROVENANCE.md` pins the new
  sha — or it is explicitly excluded and ported later under its own `D-NN`. Never copy a working tree.
- **Why:** A fork taken from an uncommitted tree cannot be reproduced, diffed, or reasoned about
  later; "what did we actually copy" stops having an answer.
- **Consequences:** AS8 in the DRP records the observation as **false**, not merely unverified. Two
  planned changes now have a dependency on which sha is pinned: R25's migration must cover `learnings`
  written by `release()`, and R41's "claim tests pass unmodified" is evaluated against the copied
  version of the claim protocol.

## D-023 · P0 copies 608401b8 exactly; the source's uncommitted work is excluded
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Proposed by:** weave developer  ·  **Approved:** weave manager
- **Context:** D-022 required the copy point to be a commit. The source is dirty in four modules P0
  copies — `context_graph/weave/{coordinator,routes,store,worker}.py` — carrying an in-flight
  `release()` path (`attempts`/`blocked`, plus a `learnings` entry per release).
- **Options:** (a) the source's owner commits, and we pin the new sha · (b) pin `608401b8`, exclude
  the uncommitted work, port it later under its own `D-NN`
- **Decision:** **(b).** P0 copies `608401b8` exactly, extracted read-only via `git archive`. The
  in-flight `release()` work is not carried; it becomes a deliberate reviewed port later.
- **Why:** Option (a) is only available to the *source's own* session — the Weave developer committing
  in `Context_Graph/` would itself be a write to the parent, which D-003 forbids and the contract names
  as an explicit tripwire. That distinction was implicit in D-022 and is now explicit. Independently,
  (b) does not depend on another session's timing, and the parent session had not answered.
- **Consequences:** R25's `reviews`/`learnings` migration (P2) need not cover `release()`-written
  learnings at this sha. R41's "claim tests pass unmodified" (P5) is evaluated against the `608401b8`
  claim protocol. `PROVENANCE.md` pins `608401b8`; the RFC, DRP and ARCHITECTURE are corrected from
  the stale `1260d109`. The excluded work is ported later under its own decision entry, not merged in
  silently.

## D-024 · Config prefixing: ours gets `WEAVE_`, vendor-read variables never do
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Question from:** weave developer
- **Context:** R7 says all configuration is `WEAVE_*`. Some variables are read by third-party SDKs
  themselves (`OPENAI_API_KEY`, `AZURE_*`, `GOOGLE_*`) and break if prefixed; others
  (`POSTGRES_*`, `NEO4J_*`, `JWT_*`) are ours but carry no forbidden name, so A3 does not force them.
- **Decision:** Every variable **Weave itself reads** is `WEAVE_*`, including `POSTGRES_*`, `NEO4J_*`
  and `JWT_*` — and this happens in **P0**, not P1. Variables a **vendor library reads directly** are
  never prefixed; that exception is now written into R7 rather than left to judgment.
- **Why:** P0 is the one phase where renaming is free — mechanical, no behaviour change, and the gate
  is "the same tests still pass". Deferring to P1 means touching `config.py` twice and shipping a
  half-prefixed surface in between, which is exactly the ambiguity the rename exists to remove.
- **Consequences:** `USE_CONTEXT_GRAPH` → `WEAVE_ENABLE_QUADRUPLE` (an A3 hit, so it was never
  optional). The vendor-variable exception is documented in the DRP and is not an A3 violation, since
  those names contain no parent product name.

## D-025 · Work commits directly to `main` in the Weave repository
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Decided by:** dsivov
- **Context:** The work plan specified `feature/weave-fork`, following methodology R5 ("work on
  `feature/<name>`; never merge to `main` unverified"). Weave is now its own repository, with one
  developer, no release, no tags and no other consumer of `main`.
- **Options:** keep the feature branch / commit directly to `main`
- **Decision:** Commit directly to `main`. R5 is waived for this repository at this stage.
- **Why:** R5 protects a *shared, releasable* `main` from unverified work. None of those properties
  holds yet: there is nothing downstream to protect, and the milestone gates already carry the
  protective role the branch would have played. A branch here is ceremony, and ceremony that buys
  nothing gets ignored, which is worse than not having it.
- **Scope:** This is a **methodology deviation, not contract drift** — no constraint in
  `CONSTRAINTS.md` mentions branching, so no `A#` is made false and no amendment is required. The
  distinction matters: process choices must not erode the contract.
- **Revert trigger:** reinstate feature branches when **any** of these becomes true — a second
  contributor commits, the first release or tag is cut, or `main` acquires a consumer (CI publishing,
  a deployment, another repository depending on it). At that point `main` becomes something to
  protect and R5 applies again on its own terms.

## D-026 · Copying the source name into this repository is itself an A3 violation
- **Date:** 2026-08-08  ·  **Status:** accepted  ·  **Source:** P0.1 · A3 · R2
- **Context:** Two P0 artifacts wanted to name the source directly: `scripts/parent_checksum.sh`
  (which must find the source checkout to assert it unchanged) and `PROVENANCE.md` (which must
  record the module selection, i.e. source paths). Both would have planted the exact strings A3
  forbids — and the name-guard proved it by rewriting its own verification script during the
  rebrand sweep. A third case is subtler: `nameguard.sh` cannot spell the tokens it forbids, or it
  flags itself.
- **Options:** exempt these files from the guard / rename around the problem / never write the name
- **Decision:** Never write the name. The source location comes from `WEAVE_SOURCE_DIR` with no
  default; the baseline (`.source-baseline.txt`) stores **hashes, not paths or status text**;
  `PROVENANCE.md` writes the two source packages as `⟨engine⟩/` and `⟨platform⟩/`, which resolve on
  sight against the pinned commit; and `nameguard.sh` and its tests assemble the forbidden pattern
  from fragments.
- **Why:** Every alternative adds an exemption, and D-014 fixed the exemption count at one for good
  reason. An exempted file is also the one file nobody is watching — and here that file would have
  been the guard itself.
- **Consequences:** Off the machine that holds the source, `parent_checksum.sh` cannot run; it says
  so and exits rather than passing vacuously. CI therefore does not run it — the assertion is made
  where the source lives, and its result is recorded at each milestone review.
