<!-- TEMPLATE: CLAUDE.md — auto-loaded into Claude's context at every session start.
     new-project copies this to the project root (or .claude/CLAUDE.md). Keep it under
     ~200 lines. If the project already has a CLAUDE.md, MERGE this section in. -->

# {{PROJECT}} — working agreement

This project runs on the **house methodology** (docs-first, evidence-driven, reviewed at
every milestone). Full spec: `ONBOARDING/METHODOLOGY.md`.

## Pipeline
`BLOG → RFC ↔ DRP → CONSTRAINTS → ARCHITECTURE / CHANGE-REQUEST → WORK PLAN → milestone reviews`
Artifacts live in `docs/` (map: `docs/DOCS_INDEX.md`; decision log: `docs/DECISIONS.md`).
RFC & DRP are **co-authored** (RFC leads on approach, DRP on detail), agreed before the plan.

## The architecture contract — always in context

@docs/CONSTRAINTS.md

The import above loads the contract every session (it stays inert until the file exists — it's
created once the RFC + DRP are agreed). **Never build past it.** Re-read it before: adding or
swapping a **dependency / service / datastore** · creating or moving a **top-level directory or
deployable** · crossing a **stated boundary** · changing a **public contract** (API, schema, CLI,
event, file format) · introducing **state, concurrency, caching or background work** · changing
**auth / tenancy / trust** or the **deployment target** · adding a second tool for a job something
already does · anything on its **non-goals** list.

**The test:** would this change make a sentence in `CONSTRAINTS.md` false? Then **STOP** — don't
implement it and report afterwards. Report first: constraint **ID** · what the contract says ·
what the change needs · why · options (**comply / amend / defer**), and wait for a decision. If an
amendment is approved, edit `CONSTRAINTS.md` first (bump version + amendment row), log a `D-NN` in
`DECISIONS.md`, then build.

## Rules (do these — they're not optional)
- **R1 Docs before code** — no build without an RFC/DRP and a work-plan task.
- **R2 Measure every claim** — perf/accuracy/"better-than" ships with a reproducible harness in
  `scripts/`; otherwise call it a hypothesis. An honest "parity" beats an unverified win.
- **R3 Test gates** — a milestone is done when its listed gate passes, not when code exists.
- **R4 Review before advancing** — code review each milestone; fix Critical/High first.
- **R5 Branches** — work on `feature/<name>`; never merge to `main` unverified; commit/push only when asked.
- **R6 Honest & current docs** — describe the destination, not the journey; fix docs when a measurement corrects a belief.
- **R7 Log decisions** in `docs/DECISIONS.md`; don't re-litigate them.
- **R8 Persist the non-obvious** — project memory the repo and git history don't already record.
- **R9 In planning, ask when unsure** — during discussion/RFC/DRP, don't guess at ambiguous scope
  or approach: ask, with suggested options (recommended first + trade-offs). Log the answer.
- **R10 Layout, libraries, reuse** — every RFC/DRP/architecture/CR carries a **file-system layout**
  and an **external-library table** (name, version, purpose, why over the alternative). Python?
  **Ask** the dependency manager (`conda` default; uv/poetry/pip+venv). Existing code? Inventory the
  current layout, used libs, DBs and integrations first and **reuse them** — never a second library
  for a job something already does; replacing an incumbent means planning its removal.
- **R11 Check the contract, don't remember it** — `docs/CONSTRAINTS.md` (imported above) holds the
  agreed top-level architecture as ~15 falsifiable sentences (`A1…`). Written contract-check at
  every milestone start and in every review; **tripwire** re-read before the edits listed above.
  Drift ⇒ stop and ask; approved change ⇒ amend the file **first**, then log `D-NN`. Never silent.

## GitHub cycle
Branch per feature · commit per task · push per milestone · merge only when the gate passes
**and** review is clean. `main` is always releasable. Prefer squash-merge.

## House style
Every doc is illustrated: **inline SVG** in HTML, **mermaid** in Markdown. Shared design
system: `docs/assets/house.css`. App UIs use the `frontend-kit` tokens.

## Skills
`/write-blog · /write-rfc · /write-drp · /write-architecture · /make-workplan · /milestone-review`
generate each artifact from `docs/templates/`.

## Project-specific notes
{{Add anything specific to THIS project: stack, run commands, gotchas, key paths.}}
