#!/usr/bin/env python3
"""Seed a demo tenant with a real scenario — Weave's own build, recorded in Weave.

**Why this data and not invented data.** Every locator in here resolves to a file
that actually exists in this repository, at a revision that actually contains it.
Invented demo data cannot do that: it either embeds its content (which A5 forbids)
or it points at nothing, and a demo whose links are dead teaches the opposite of
what this product claims.

**That sentence was false for two years of this project's life, and not because
the paths were wrong** (W44). `/graph/entity/create` keeps six fields and
discards the rest without a warning, so every `Feature`, `ChangeRequest` and
`ArchitectureDecisionRecord` node this script created carried **no locator at
all** — there was nothing to resolve, which is also why `check_locators.py` had
so little to report. `Api.upsert_entity` below works around it and says where the
workaround ends. `tests/test_seed_covers_the_ontology.py` now asserts the paths
rather than leaving the claim to a docstring.

**All 18 ontology object types, not 8.** Ten were never seeded — `Task`, `PRD`,
`RFC`, `Diagram`, `Module`, `Question`, `Worker`, `DevHost`, `Environment`,
`IntegrationRun` — so most of the vocabulary the answer surface is built on had
never existed in any instance, and every gate that read this demo exercised
fewer than half of it (D-050). The scenario is therefore the project's own history —
the phases that were built, the commits that built them, the reviews that gated
them and the lessons those reviews produced.

It also exercises the four canonical questions with answers a human can check
against `git log`, which is the only honest way to demo an answer surface.

**Idempotent — and this line used to say so before it was true** (W22). Tasks and
pull requests answer 409 on a repeat, so they were always safe; commits, reviews
and learnings **append**, returned 200 twice, and doubled the tenant in silence.
The demo carried 26 learning nodes holding 13 statements, each exactly twice, and
nobody saw it until U3 made the text readable instead of rendering raw ids. Those
three steps now read the task's chain back and skip what is already there.

Safe to point at a live instance; writes only into the workspace given by
``--workspace``.

**Two identities, not one.** Step 0 installs the preset, and the preset's Task
machine lets only a **developer** claim — supervisors bootstrap, developers do the
work. Pass ``--dev-user``/``--dev-password`` or the lifecycle steps are refused.

Usage:
    python scripts/seed_demo.py --url http://127.0.0.1:9800 \
        --user dsivov --password ... --workspace demo \
        --dev-user demo-dev --dev-password ...

Documented in ``docs/DEMO_SCENARIO.md`` — keep the two in step (R6).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# ── the scenario ────────────────────────────────────────────────────────────
#
# Six phases, each a Feature; the tasks that built them; the commit that landed
# each; the milestone review that gated it; and the insight it produced. Commit
# SHAs are real — `git show <sha>` works for every one.

REPO = "weave"

FEATURES = [
    ("P0", "Fork and rebrand", "92k LOC copied from a proven engine and renamed, with a guard that fails the build on any surviving parent name."),
    ("P1", "Standalone server and user store", "Users become records with bcrypt hashes and per-workspace membership. The gap the project exists to close."),
    ("P2", "Data model and the answer surface", "Feature, Review, Insight and Question become nodes; every artifact references its source by repo·path·rev and never embeds it."),
    ("P3", "The live, multi-user surface", "SSE and presence over a bus adapter that must match the deployment, plus 409-and-merge on shared edits."),
    ("P4", "Team-vocabulary wizards", "RBAC and lifecycle become signed ledger kinds, so a wizard needs no special write path."),
    ("P5", "The senior-developer seat", "Supervision as recorded intent — dispatch, pause and redirect are state a host reads back, never a call outward."),
]

TASKS = [
    # (id, feature, title, sha, commit subject, touches)
    ("T-P0-FORK", "P0", "Copy and rebrand the engine", "8610914",
     "P0 · the fork, rebranded — 92k LOC that still passes its own tests", ["weave_core/", "weave/"]),
    ("T-P1-USERS", "P1", "Build the user store and close the env-account gap", "778d70a",
     "P1 · a user store, and a door that no longer opens for everyone", ["weave/server/users.py"]),
    ("T-P2-TENANT", "P2", "Scope the locator resolver to the workspace", "6a3061b",
     "P2.1b · Locator and ProjectLayout — the resolver lives inside the tenant boundary", ["weave/model/project_layout.py"]),
    ("T-P2-HEADER", "P2", "Fix the workspace header that never matched", "bd70c36",
     "Critical · the workspace header never matched, so every request answered from one tenant", ["weave/server/workspace_pool.py"]),
    ("T-P3-BUS", "P3", "Ship the PostgreSQL bus with the refusal that makes it mean something", "4af22da",
     "P3.1 · the PostgreSQL bus, and the refusal that makes it mean something (A7, W3)", ["weave_core/events/postgres.py"]),
    ("T-P4-LEDGER", "P4", "Make RBAC and lifecycle ledger kinds", "96adb41",
     "P4.1 · RBAC and lifecycle become ledger kinds", ["weave_core/studio/service.py"]),
    ("T-P5-SEAT", "P5", "Build the senior-developer seat, outbound-only", "19145ea",
     "P5 · the senior-developer seat — supervision as recorded intent, never a call out", ["weave/team/supervisor.py"]),
]

REVIEWS = [
    # (task, verdict, notes — drawn from the real milestone reviews)
    ("T-P0-FORK", "approved",
     "M0: 0 Critical, 1 High. Gate verified independently — 569 passed in the declared conda env, 0 name hits, parent tree byte-identical. See docs/WEAVE_CODE_REVIEW.md."),
    ("T-P1-USERS", "approved",
     "M1: 0 Critical, 1 High (an A4 decision, not a defect). 679 passed against live PostgreSQL and Neo4j; AS2 and AS3 verified rather than assumed. See docs/WEAVE_CODE_REVIEW_M1.md."),
    ("T-P2-HEADER", "approved",
     "M2: the finding that started as High and was re-graded Critical. Every request resolved to the default workspace; the tenant boundary was not enforced at all. See docs/WEAVE_CODE_REVIEW_M2.md."),
    ("T-P3-BUS", "approved",
     "M3: 0 Critical, 0 High. Both measured criteria reproduced by the reviewer — p95 2.44 ms against a 1000 ms gate, exactly one winner of twenty on every storage path. See docs/WEAVE_CODE_REVIEW_M3.md."),
    ("T-P4-LEDGER", "approved",
     "M4: the wizard needed no special write path, because making RBAC and lifecycle ledger kinds removed the temptation. The High belonged to a path P4 revealed rather than introduced. See docs/WEAVE_CODE_REVIEW_M4.md."),
    ("T-P5-SEAT", "approved",
     "M5: claim tests verified byte-identical to the P0 fork commit rather than taken on trust. A15 asserted structurally — the seat holds no transport at all. See docs/WEAVE_CODE_REVIEW_M5.md."),
]

LEARNINGS = [
    # (task, insight) — these are the project's real lessons, not illustrations.
    ("T-P2-HEADER",
     "A renamed literal is safe when both sides of the comparison were renamed together, and broken when the other side is an external contract. Neither name-guard catches this class: the string is neither the old brand nor misspelled."),
    ("T-P2-HEADER",
     "A gate that can pass without the data layer participating does not test the data layer. M1's 'sees only granted workspaces' was satisfied entirely at the token layer, which is how a total tenancy collapse survived two reviews."),
    ("T-P3-BUS",
     "An adapter that removes a silent failure, shipped without the configuration that still permits the failure, is not a fix. A7 is a pairing; half of it reintroduces what the other half removes."),
    ("T-P4-LEDGER",
     "Removing the temptation beats resisting it. Making RBAC and lifecycle ledger kinds meant no special write path could do anything the ordinary one could not."),
    ("T-P5-SEAT",
     "Assert the class, not the instance — and give the exclusions in a class assertion the same scrutiny as the rule. A guard whose exclusion list contains the largest hole reads as coverage it does not provide."),
    ("T-P5-SEAT",
     "A constraint enforced at the moment a thing comes into being cannot be got around. The Neo4j single-workspace limit is checked where a workspace is created, not in the adapter and not at read time."),
    ("T-P1-USERS",
     "Run the gate by hand on a live server, not only in the suite. Every milestone so far has found a defect that way which no unit test surfaced — an operator lockout, a schema leak to an unauthorised caller, unmounted routers, a 404 at the server's front door."),
]

DECISIONS = [
    # (src, tgt, relation, trace, rationale)
    ("T-P2-TENANT", "T-P2-HEADER", "depends_on", "D-028",
     "ProjectLayout is workspace-scoped: resolve() returns file content, so a global registry would let one tenant read another's source."),
    ("T-P3-BUS", "T-P1-USERS", "depends_on", "D-019",
     "Two bus adapters behind the existing port. The in-process bus cannot fan out across workers, and the failure is silent."),
    ("T-P4-LEDGER", "T-P5-SEAT", "depends_on", "D-032",
     "What the runtime enforces must be the signed ledger version. Onboarding wrote enforced rules with no signature; three write paths existed, not two."),
]


ADRS = [
    ("D-028", "docs/DECISIONS.md", "ProjectLayout is workspace-scoped; the locator resolver never crosses a tenant. resolve() returns file content, so a global registry would let one tenant read another's source."),
    ("D-029", "docs/DECISIONS.md", "The Neo4j path is experimental and single-workspace, and the second workspace is refused in code. A qualification annotates the failure but leaves it available."),
    ("D-019", "docs/DECISIONS.md", "Two bus adapters behind the existing port. The in-process bus cannot fan out across gunicorn workers, and the failure is silent — no error, no log."),
    ("D-032", "docs/DECISIONS.md", "What the runtime enforces must be the signed ledger version. Onboarding wrote enforced rules with no signature; there were three write paths, not two."),
    ("D-030", "docs/DECISIONS.md", "A renamed literal is safe when both sides of the comparison moved together, and broken when the other side is an external contract."),
]

#: Which features each decision justifies — so `/ask/why` has somewhere to walk.
ADR_FEATURES = {
    "D-028": ("P2",), "D-029": ("P2",), "D-019": ("P3",),
    "D-032": ("P4",), "D-030": ("P2",),
}

# ── the rest of the ontology (P15, D-050) ───────────────────────────────────
#
# **The ontology declares 18 object types and this script used to produce 8.**
# `Task`, `PRD`, `RFC`, `Diagram`, `Module`, `Question`, `Worker`, `DevHost`,
# `Environment` and `IntegrationRun` had never been seeded — so ten of the types
# the answer surface is built on had never existed in any instance, and every
# gate that read this demo exercised fewer than half the vocabulary it claims to
# serve. A type declared in the ontology and absent from every instance is a type
# nobody has ever seen work.
#
# **Four of the ten have real product paths, and are created through them below**
# rather than written here as data: `Environment` (`POST /weave/environment`),
# `IntegrationRun` (`/weave/integration/run`), `Worker` (`/weave/workers/register`)
# and `DevHost` (`/weave/hosts/register`). Seeding those by hand would demo a
# state the product cannot reach, which is the same reason tasks below walk their
# real lifecycle instead of being written straight into `review`.
#
# **The other six have no product path at all — that is W43**, and it is the
# honest answer to why the Features tab reads thin. `FEATURE_TYPES` is
# `Feature · Module · PRD · RFC · Diagram`, and the runtime creates **none** of
# them; `Task` is worse, because `create_task` writes a record and never a node
# while `/ask/changes` and `/ask/why` both seed on `Task`. Until that is closed
# these six are seeded the way `Feature`, `ChangeRequest` and
# `ArchitectureDecisionRecord` already were — as nodes whose locators resolve to
# real files, which is the A5 claim made checkable. **Seeding them does not close
# W43**, and this comment is here so the next reader does not mistake a populated
# demo for a working mechanism.

#: (entity_id, entity_type, path, anchor, description) — every path is a file in
#: this repository, so `check_locators.py` resolves all of them.
ARTIFACTS = [
    ("PRD — Weave requirements", "PRD", "docs/WEAVE_DRP.md", "## 5",
     "The requirements and the per-milestone gates. Section 5 is the gate list every phase is judged against — a milestone is done when its gate passes, not when its code exists."),
    ("RFC — the team is the product", "RFC", "docs/WEAVE_RFC.html", "",
     "The proposal: a standalone multi-user system for running an AI development team, humans and agents planning and reviewing on one governed graph."),
    ("RFC — extraction reads the signed ontology", "RFC", "docs/WEAVE_EXTRACTION_TAXONOMY_CHANGE_REQUEST.md", "",
     "CR-003: three hand-written type lists each duplicated an authority the workspace already installs as signed governance, and the overlap with it was none."),
    ("Diagram — the fleet is outbound-only", "Diagram", "docs/WEAVE_ARCHITECTURE.html", "#flows",
     "Dev hosts and workers reach the server by register and heartbeat and reconcile to state they read back. The server never dials out, which is what lets a host sit behind NAT."),
    ("Diagram — ONBOARDING and Weave in step", "Diagram", "docs/guides/WEAVE_USER_GUIDE.html", "#methodology",
     "How the methodology kit and Weave stay in step: the kit authors the artifact, Weave publishes it, and the plan refuses to release tasks over a graph missing it."),
]

#: (entity_id, path, description) — a Module points at the file that owns the
#: boundary, not at a directory: the locator resolver returns file content.
MODULES = [
    ("weave_core", "weave_core/__init__.py",
     "The engine. Imports nothing from `weave/` and no HTTP framework — the boundary that keeps it separable and testable without a server (A2)."),
    ("weave/server", "weave/server/app.py",
     "All HTTP. Composes the routers, binds governance to every route, and serves the built UI as static assets — which is why the UI is not a fourth deployable (A1)."),
    ("weave/team", "weave/team/coordinator.py",
     "The team's deterministic coordination: the claim, the artifact chain, the merge gate. No model sits in this path (A12)."),
    ("weave/model", "weave/model/answers.py",
     "One handler per question, shared by REST/UI and MCP, so the human and agent surfaces cannot answer differently (A9)."),
]

#: Which feature each Module, PRD, RFC and Diagram describes. **Without these the
#: nodes exist and the Features view still cannot show them:** `/ask/features`
#: with no argument seeds on `Feature` and walks its neighbours, so an unlinked
#: `Module` is reachable only by asking for it by name. Measured — before these
#: edges the view returned six `Feature` nodes and nothing else, which is most of
#: why the tab reads thin.
DESCRIBES = {
    "weave_core": "P0", "weave/server": "P1", "weave/team": "P5", "weave/model": "P2",
    "PRD — Weave requirements": "P0",
    "RFC — the team is the product": "P0",
    "RFC — extraction reads the signed ontology": "P2",
    "Diagram — the fleet is outbound-only": "P5",
    "Diagram — ONBOARDING and Weave in step": "P2",
}

#: (entity_id, path, anchor, description) — questions that were actually asked
#: and produced a decision. Each resolves to the decision it produced.
QUESTIONS = [
    ("Are the three storage paths really interchangeable?", "docs/DECISIONS.md", "## D-029",
     "Asked at M8. They are not: only PostgreSQL enforces the workspace boundary at the storage layer, and Community-Edition Neo4j cannot give a workspace its own database. The contract now ranks them and refuses a second workspace on the Neo4j path."),
    ("Why does ANTHROPIC_API_KEY appear at all if agents are subscription-only?", "docs/PROJECT_REVIEW_2026-08-15.md", "",
     "Asked when reading the agent runtime. It appears in a scrub list and two comments; nothing reads it. Sixteen variables are removed before an agent runs and a preflight refuses if the seat is not subscription-authenticated (A13)."),
    ("Are the Learnings and Features tabs thin because of the demo data?", "docs/DECISIONS.md", "## D-050",
     "Asked at P14. No — extraction never read the workspace's signed ontology, so every node the pipeline produced was typed in a vocabulary the answer surface does not look for. The overlap was none."),
]

class Api:
    def __init__(self, url: str, workspace: str):
        self.url = url.rstrip("/")
        self.workspace = workspace
        self.token = ""
        self.tolerated: list[str] = []

    def _call(self, method: str, path: str, body=None, tolerate=()):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("WEAVE-WORKSPACE", self.workspace)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in tolerate:
                return {"_tolerated": e.code}
            raise SystemExit(f"{method} {path} → {e.code}: {e.read().decode()[:300]}")

    def login(self, user: str, password: str):
        form = urllib.parse.urlencode({"username": user, "password": password}).encode()
        req = urllib.request.Request(f"{self.url}/login", data=form, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as r:
            self.token = json.loads(r.read())["access_token"]

    def post(self, path, body, tolerate=()):
        """No blanket tolerance — a swallowed 409 is a seed that lies.

        The first version of this script tolerated 409 everywhere and reported
        "6 reviews recorded" when it had recorded none: reviews require a pull
        request first, every call was refused, and the summary said otherwise.
        Tolerated codes are now passed per call and counted, so no failure goes
        unseen.

        **This docstring used to end "so re-running is idempotent". It was
        wrong** (W22). Per-call tolerance makes a *repeat* visible; it says
        nothing about endpoints that never refuse. Commits, reviews and
        learnings **append**, so they returned 200 twice and doubled the tenant
        silently — 26 learning nodes holding 13 statements. Idempotency comes
        from reading the chain back before writing, in `main`, not from this
        method. Corrected here rather than deleted, because the sentence was the
        reason nobody looked.
        """
        r = self._call("POST", path, body, tolerate)
        if isinstance(r, dict) and r.get("_tolerated"):
            self.tolerated.append(f"{path} → {r['_tolerated']}")
        return r

    def get(self, path, tolerate=()):
        """Read state back, so a second run can tell what it already wrote."""
        r = self._call("GET", path, None, tolerate)
        return None if isinstance(r, dict) and r.get("_tolerated") else r

    def put(self, path, body, tolerate=()):
        return self._call("PUT", path, body, tolerate)

    def upsert_entity(self, name: str, data: dict):
        """Create the node, or edit the one that is already there.

        **The `edit` is no longer a workaround, and that is worth recording.**
        Until W44 was fixed, `/graph/entity/create` kept six fields and silently
        discarded the rest — the four `locator_*` fields among them — so this
        method had to edit *unconditionally* to put back what a fresh create had
        just thrown away. `create` now carries the caller's fields through, and
        the unconditional second call is gone.

        What remains is the case W43 describes: recording a decision upserts its
        `src` and `tgt`, so a node named after a task **already exists**, typed
        `ENTITY`. `create` answers 400 for those and only `edit` can give them
        their real type. Keep this until `create_task` writes its own node.
        """
        r = self.post("/graph/entity/create",
                      {"entity_name": name, "entity_data": data},
                      tolerate=(400, 409))
        if isinstance(r, dict) and r.get("_tolerated"):
            self.post("/graph/entity/edit",
                      {"entity_name": name, "updated_data": data}, tolerate=(404, 500))
        return r


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a demo tenant with Weave's own build history.")
    ap.add_argument("--url", default="http://127.0.0.1:9800")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--workspace", default="demo")
    ap.add_argument("--repo-root", default="/storage/Work/Weave",
                    help="checkout the locators resolve against")
    ap.add_argument("--dev-user", default="",
                    help="a developer identity; the preset lets only developers claim a task")
    ap.add_argument("--dev-password", default="")
    args = ap.parse_args()

    api = Api(args.url, args.workspace)
    api.login(args.user, args.password)
    print(f"seeding workspace '{args.workspace}' at {args.url}")

    # ── the seed needs two identities, and this took a clean tenant to notice ──
    #
    # Step 0 installs the preset, and the preset's Task machine gates
    # `pending → in_progress` to **developer / architect / integrator**. A
    # manager may bootstrap and may not claim, which is the pipeline working as
    # designed — developers claim work, supervisors do not.
    #
    # **So this script has not completed on a fresh workspace since `1e4d427`**,
    # the commit that made bootstrap step 0. It went unnoticed because the only
    # tenant anyone re-ran it against already had its tasks claimed from before
    # that change: every repeat 409'd, the tolerated-code counter absorbed it,
    # and the visible effect was the *appending* endpoints doubling (W22). One
    # defect hid the other, and both needed a **clean** tenant to see — which is
    # the state every reader of the guide will be in, and none of us was.
    dev = api
    if args.dev_user:
        dev = Api(args.url, args.workspace)
        dev.login(args.dev_user, args.dev_password)
        print(f"  lifecycle steps run as '{args.dev_user}' (developer)")
    else:
        print("  ⚠ no --dev-user given: claim/commit/review will be attempted as "
              f"'{args.user}' and the preset will refuse them if that role may not claim")

    # 0 · bootstrap the workspace's governance FIRST.
    #
    # Without this the board reads "not bootstrapped" and every governed action
    # is refused — the tenant has tasks and nodes but no ontology, RBAC,
    # lifecycle, rules or actions to enforce them against. Installing is a
    # supervisor act (manager/architect), which is why this script authenticates
    # as one. It signs all five layers into the ledger (D-034), so the demo's
    # governance is attributable from its first version rather than from its
    # second.
    api.post("/weave/bootstrap", {}, tolerate=(409,))
    print("  governance bootstrapped — ontology, rbac, lifecycle, rules, actions")

    # 1 · the project the locators resolve against (P2, R22)
    api.post("/projects", {
        "name": REPO,
        "local_path": args.repo_root,
        "clone_url": "https://example.invalid/weave.git",
        "default_rev": "main",
        "description": "Weave itself — the repository this scenario is about.",
    })
    api.put("/weave/project", {
        "repo": REPO, "base_branch": "main",
        "description": "Weave building Weave. Every locator below resolves to a real file.",
        "test_command": ["pytest", "tests/", "-q"],
    })
    print(f"  project '{REPO}' registered → {args.repo_root}")

    # 2 · the plan
    api.post("/weave/plan/publish", {
        "plan_ref": "docs/WEAVE_WORK_PLAN.md",
        "plan_kind": "work_plan",
        "summary": "Seven phases P0–P6 to milestones M0–M6, each gated on a test that must pass before the next begins.",
    })
    print("  plan published → docs/WEAVE_WORK_PLAN.md")

    # 3 · tasks, each with the commit that landed it
    by_feature = {}
    for tid, feat, title, sha, subject, touches in TASKS:
        api.post("/weave/tasks", {
            "id": tid, "title": title, "priority": "high",
            "description": f"{feat} — {dict((f[0], f[2]) for f in FEATURES)[feat]}",
            "touches": touches,
        })
        # A review is only legal once there is something to review, so the task
        # walks its real lifecycle: claimed → committed → pull request → review.
        # Short-circuiting that would demo a state the product cannot reach.
        dev.post(f"/weave/tasks/{tid}/claim", {"worker": "demo-dev"}, tolerate=(409,))
        dev.post(f"/weave/tasks/{tid}/pull-request", {
            "branch": f"feature/{tid.lower()}",
            "url": f"https://example.invalid/weave/pull/{tid}",
            "title": subject,
        }, tolerate=(409,))
        by_feature.setdefault(feat, []).append(tid)
    print(f"  {len(TASKS)} tasks created — claimed, pull-requested")

    # ── 3b · read back what each task already carries (W22) ─────────────────
    #
    # **Commits, reviews and learnings append; they do not upsert.** Tasks and
    # pull requests answer 409 on a repeat, so they were always safe — these
    # three are lists on the task, and `record_learning` ends in
    # `t.learnings.append(insight)` with no dedup. Running this script twice
    # therefore doubled every one of them, and the demo tenant proved it: 26
    # learning nodes carrying 13 distinct statements, each exactly twice.
    #
    # **It stayed invisible until U3 made the text readable**, because the
    # answer surface rendered raw ids and two ids are indistinguishable at a
    # glance. A cosmetic-looking defect was the lid on a data one.
    #
    # **Fixed here rather than in `record_learning`.** Deduping identical text
    # server-side would silently swallow a *legitimate* repeat — the same lesson
    # genuinely learned twice on one task — so the product keeps appending and
    # the script stops asking twice. The defect was never that the graph
    # recorded what it was told; it was that this script told it twice.
    chains = {}
    for tid, *_ in TASKS:
        chains[tid] = dev.get(f"/weave/tasks/{tid}/chain", tolerate=(404,)) or {}

    def _already(tid, field, needle):
        """Is *needle* already on this task's chain? Compared on content, not on
        count — a count is what let this go unnoticed in the first place."""
        # `ensure_ascii=False` is load-bearing: the default escapes every em dash
        # to `\u2014`, so a needle containing one never matched its own record.
        # Three reviews and two learnings leaked through on every run until this
        # was fixed — a dedup that silently half-works is worse than none.
        return any(needle in json.dumps(item, ensure_ascii=False)
                   for item in chains.get(tid, {}).get(field, []))

    fresh = 0
    for tid, feat, title, sha, subject, touches in TASKS:
        if _already(tid, "commits", sha):
            continue
        dev.post(f"/weave/tasks/{tid}/commit",
                 {"sha": sha, "subject": subject, "touches": touches})
        fresh += 1
    print(f"  commits: {fresh} recorded, {len(TASKS) - fresh} already present")

    # 4 · reviews — what gated each milestone
    fresh = 0
    for tid, verdict, notes in REVIEWS:
        if _already(tid, "reviews", notes):
            continue
        dev.post(f"/weave/tasks/{tid}/review", {"verdict": verdict, "notes": notes})
        fresh += 1
    print(f"  reviews: {fresh} recorded, {len(REVIEWS) - fresh} already present")

    # 5 · learnings — the lessons those reviews produced
    fresh = 0
    for tid, insight in LEARNINGS:
        if _already(tid, "learnings", insight):
            continue
        dev.post("/weave/learnings", {"insight": insight, "task": tid})
        fresh += 1
    print(f"  learnings: {fresh} recorded, {len(LEARNINGS) - fresh} already present")

    # 6 · decisions — why the shape is what it is
    for src, tgt, relation, trace, rationale in DECISIONS:
        api.post("/weave/decisions", {
            "src": src, "tgt": tgt, "relation": relation,
            "decision_trace": trace, "rationale": rationale,
        })
    print(f"  {len(DECISIONS)} decisions linked")

    # 7 · the graph layer the answer surface actually traverses.
    #
    # `/ask/*` admits neighbours by their stored ``entity_type``, not by edge
    # label, so a demo that only writes task records answers nothing. Features,
    # change requests and decision records are created as nodes with locators
    # that resolve — which is the A5 claim made checkable rather than asserted.
    for key, title, summary in FEATURES:
        api.upsert_entity(f"Feature {key} — {title}", {
            "entity_type": "Feature", "description": summary,
            "locator_repo": REPO, "locator_path": "docs/WEAVE_WORK_PLAN.md",
            "locator_rev": "main", "locator_anchor": f"## {key}",
        })
    print(f"  {len(FEATURES)} Feature nodes")

    for key, title, summary in FEATURES:
        cr = f"CR-{key}"
        api.upsert_entity(cr, {
            "entity_type": "ChangeRequest",
            "description": f"{key}: {title} — the phase as a change request, gated on milestone M{key[1:]}.",
            "locator_repo": REPO, "locator_path": "docs/WEAVE_WORK_PLAN.md", "locator_rev": "main",
        })
        api.post("/graph/relation/create", {
            "source_entity": cr, "target_entity": f"Feature {key} — {title}",
            "relation_data": {"description": "the change request that delivered this feature",
                              "keywords": "delivers", "weight": 1.0},
        }, tolerate=(400, 409))
    print(f"  {len(FEATURES)} ChangeRequest nodes, linked to their features")

    for trace, path, rationale in ADRS:
        api.upsert_entity(trace, {
            "entity_type": "ArchitectureDecisionRecord", "description": rationale,
            "locator_repo": REPO, "locator_path": path, "locator_rev": "main",
            "locator_anchor": f"## {trace}",
        })
    for trace, _p, _r in ADRS:
        for key, title, _s in FEATURES:
            if key in ADR_FEATURES.get(trace, ()):
                api.post("/graph/relation/create", {
                    "source_entity": f"Feature {key} — {title}", "target_entity": trace,
                    "relation_data": {"description": "the decision this feature rests on",
                                      "keywords": "justified_by", "weight": 1.0},
                }, tolerate=(400, 409))
    print(f"  {len(ADRS)} decision records, linked to the features they justify")

    # ── 8 · the six types with no product path (W43) ────────────────────────
    #
    # Seeded as nodes because nothing else creates them. `create_task` writes a
    # record and never a node, and `Module`, `PRD`, `RFC`, `Diagram` and
    # `Question` are created by no code path at all — while `/ask/features`
    # seeds on four of them. **Populating the demo does not close W43**; it makes
    # the questions answerable and makes the gap visible in one place.
    # **`create` is the wrong verb here, and finding that out is the point.**
    # Recording the claim and the decisions already upserted a node per task —
    # `emit_decision_trace` writes its `src` and `tgt` — so `entity/create`
    # answered **400 for all seven** and the tolerated-code counter absorbed it.
    # The nodes were in the graph the whole time, typed **`ENTITY`**, which is
    # the vocabulary `/ask/changes` and `/ask/why` do not query. **Same shape as
    # W40 one layer over: the data exists and the type hides it.** So this edits
    # the node that is there rather than pretending to create one.
    for tid, feat, title, sha, subject, touches in TASKS:
        node = {
            "entity_type": "Task", "description": title,
            "locator_repo": REPO, "locator_path": "docs/WEAVE_WORK_PLAN.md",
            "locator_rev": "main", "locator_anchor": f"## {feat}",
        }
        api.upsert_entity(tid, node)
        api.post("/graph/relation/create", {
            "source_entity": tid,
            "target_entity": f"Feature {feat} — {dict((f[0], f[1]) for f in FEATURES)[feat]}",
            "relation_data": {"description": "the task that delivered this feature",
                              "keywords": "delivers", "weight": 1.0},
        }, tolerate=(400, 409))
    print(f"  {len(TASKS)} Task nodes — the record store had them; the graph did not (W43)")

    for name, etype, path, anchor, description in ARTIFACTS:
        api.upsert_entity(name, {
            "entity_type": etype, "description": description,
            "locator_repo": REPO, "locator_path": path,
            "locator_rev": "main", "locator_anchor": anchor,
        })
    print(f"  {len(ARTIFACTS)} PRD/RFC/Diagram nodes — the types `weave docs publish` writes")

    for name, path, description in MODULES:
        api.upsert_entity(name, {
            "entity_type": "Module", "description": description,
            "locator_repo": REPO, "locator_path": path, "locator_rev": "main",
        })
    print(f"  {len(MODULES)} Module nodes")

    for name, path, anchor, description in QUESTIONS:
        api.upsert_entity(name, {
            "entity_type": "Question", "description": description,
            "locator_repo": REPO, "locator_path": path,
            "locator_rev": "main", "locator_anchor": anchor,
        })
    print(f"  {len(QUESTIONS)} Question nodes — each resolves to the decision it produced")

    ftitle = dict((f[0], f[1]) for f in FEATURES)
    for name, key in DESCRIBES.items():
        api.post("/graph/relation/create", {
            "source_entity": name, "target_entity": f"Feature {key} — {ftitle[key]}",
            "relation_data": {"description": "describes this capability",
                              "keywords": "describes", "weight": 1.0},
        }, tolerate=(400, 409))
    for name, path, anchor, _d in QUESTIONS:
        trace = anchor.replace("## ", "").strip()
        if any(trace == a[0] for a in ADRS):
            api.post("/graph/relation/create", {
                "source_entity": name, "target_entity": trace,
                "relation_data": {"description": "the decision this question produced",
                                  "keywords": "answered_by", "weight": 1.0},
            }, tolerate=(400, 409))
    print(f"  {len(DESCRIBES)} describes-edges — without them the Features view shows only Features")

    # ── 9 · the four fleet types, through the routes that create them ───────
    #
    # **Not written as data.** `Environment`, `IntegrationRun`, `Worker` and
    # `DevHost` have real product paths, and a demo that hand-writes them shows a
    # state the product cannot reach — the same reason the tasks above walk their
    # real lifecycle. Registration is outbound-only (A15): the host and the
    # worker announce themselves, and nothing here dials them.
    api.post("/weave/environment", {
        "id": "staging", "name": "Shared staging",
        "url": "https://staging.example.invalid",
    }, tolerate=(403, 409, 503))
    api.post("/weave/hosts/register", {
        "host": "demo-host", "machine": "demo-workstation",
        "capabilities": ["python", "bun"], "repo": REPO, "base_branch": "main",
        "image": "weave-dev-agent:demo", "version": "0.1.0",
        "seat": "subscription",
        "seat_detail": "claude reports subscription auth; no metered credential present (A13)",
    }, tolerate=(403, 409, 503))
    api.post("/weave/workers/register", {
        "worker": "demo-dev", "host": "demo-host",
        "capabilities": ["python"], "goal": "Deliver the phases in the published plan.",
    }, tolerate=(403, 409, 503))
    # **Declaring an environment does not create its node — deploying to it does.**
    # `register_environment` writes a record; the only `entity_type: Environment`
    # in the product is inside `deploy`. Measured on a clean tenant: the record
    # existed, `run_integration` accepted against it, and the graph still held no
    # `Environment` node. So the seed deploys, which is what a real integrator
    # does before recording a run anyway.
    api.post("/weave/integration/deploy", {
        "environment": "staging", "tasks": [t[0] for t in TASKS[:3]], "ref": "main",
    }, tolerate=(403, 404, 409, 503))
    api.post("/weave/integration/run", {
        "environment": "staging", "tasks": [t[0] for t in TASKS[:3]],
        "kind": "e2e", "passed": True,
        "summary": "The gate for M0–M2: suite green in the declared environment, tenancy asserted at the data layer.",
    }, tolerate=(403, 404, 409, 503))
    print("  Environment, DevHost, Worker, IntegrationRun — via their own routes, not written by hand")

    if api.tolerated:
        print(f"\n  {len(api.tolerated)} call(s) tolerated as already-present (idempotent re-run):")
        for line in api.tolerated[:8]:
            print(f"    {line}")

    print(f"\ndone. Ask the four questions against workspace '{args.workspace}':")
    for q in ("changes", "why", "features", "learnings"):
        print(f"  curl -H 'WEAVE-WORKSPACE: {args.workspace}' -H 'Authorization: Bearer <token>' {args.url}/ask/{q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
