#!/usr/bin/env python3
"""Seed a demo tenant with a real scenario — Weave's own build, recorded in Weave.

**Why this data and not invented data.** Every locator in here resolves to a file
that actually exists in this repository, at a revision that actually contains it.
Invented demo data cannot do that: it either embeds its content (which A5 forbids)
or it points at nothing, and a demo whose links are dead teaches the opposite of
what this product claims. The scenario is therefore the project's own history —
the phases that were built, the commits that built them, the reviews that gated
them and the lessons those reviews produced.

It also exercises the four canonical questions with answers a human can check
against `git log`, which is the only honest way to demo an answer surface.

Idempotent: re-running updates in place rather than duplicating. Safe to point at
a live instance; writes only into the workspace given by ``--workspace``.

Usage:
    python scripts/seed_demo.py --url http://127.0.0.1:9800 \
        --user dsivov --password ... --workspace demo

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
        Tolerated codes are now passed per call and counted, so re-running is
        idempotent without any failure going unseen.
        """
        r = self._call("POST", path, body, tolerate)
        if isinstance(r, dict) and r.get("_tolerated"):
            self.tolerated.append(f"{path} → {r['_tolerated']}")
        return r

    def put(self, path, body, tolerate=()):
        return self._call("PUT", path, body, tolerate)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a demo tenant with Weave's own build history.")
    ap.add_argument("--url", default="http://127.0.0.1:9800")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--workspace", default="demo")
    ap.add_argument("--repo-root", default="/storage/Work/Weave",
                    help="checkout the locators resolve against")
    args = ap.parse_args()

    api = Api(args.url, args.workspace)
    api.login(args.user, args.password)
    print(f"seeding workspace '{args.workspace}' at {args.url}")

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
        api.post(f"/weave/tasks/{tid}/claim", {"worker": "demo-dev"}, tolerate=(409,))
        api.post(f"/weave/tasks/{tid}/commit", {"sha": sha, "subject": subject, "touches": touches})
        api.post(f"/weave/tasks/{tid}/pull-request", {
            "branch": f"feature/{tid.lower()}",
            "url": f"https://example.invalid/weave/pull/{tid}",
            "title": subject,
        }, tolerate=(409,))
        by_feature.setdefault(feat, []).append(tid)
    print(f"  {len(TASKS)} tasks created — claimed, committed, pull-requested")

    # 4 · reviews — what gated each milestone
    for tid, verdict, notes in REVIEWS:
        api.post(f"/weave/tasks/{tid}/review", {"verdict": verdict, "notes": notes})
    print(f"  {len(REVIEWS)} reviews recorded")

    # 5 · learnings — the lessons those reviews produced
    for tid, insight in LEARNINGS:
        api.post("/weave/learnings", {"insight": insight, "task": tid})
    print(f"  {len(LEARNINGS)} learnings recorded")

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
        api.post("/graph/entity/create", {
            "entity_name": f"Feature {key} — {title}",
            "entity_data": {
                "entity_type": "Feature", "description": summary,
                "locator_repo": REPO, "locator_path": "docs/WEAVE_WORK_PLAN.md",
                "locator_rev": "main", "locator_anchor": f"## {key}",
            },
        }, tolerate=(400, 409))
    print(f"  {len(FEATURES)} Feature nodes")

    for key, title, summary in FEATURES:
        cr = f"CR-{key}"
        api.post("/graph/entity/create", {
            "entity_name": cr,
            "entity_data": {
                "entity_type": "ChangeRequest",
                "description": f"{key}: {title} — the phase as a change request, gated on milestone M{key[1:]}.",
                "locator_repo": REPO, "locator_path": "docs/WEAVE_WORK_PLAN.md", "locator_rev": "main",
            },
        }, tolerate=(400, 409))
        api.post("/graph/relation/create", {
            "source_entity": cr, "target_entity": f"Feature {key} — {title}",
            "relation_data": {"description": "the change request that delivered this feature",
                              "keywords": "delivers", "weight": 1.0},
        }, tolerate=(400, 409))
    print(f"  {len(FEATURES)} ChangeRequest nodes, linked to their features")

    for trace, path, rationale in ADRS:
        api.post("/graph/entity/create", {
            "entity_name": trace,
            "entity_data": {
                "entity_type": "ArchitectureDecisionRecord", "description": rationale,
                "locator_repo": REPO, "locator_path": path, "locator_rev": "main",
                "locator_anchor": f"## {trace}",
            },
        }, tolerate=(400, 409))
    for trace, _p, _r in ADRS:
        for key, title, _s in FEATURES:
            if key in ADR_FEATURES.get(trace, ()):
                api.post("/graph/relation/create", {
                    "source_entity": f"Feature {key} — {title}", "target_entity": trace,
                    "relation_data": {"description": "the decision this feature rests on",
                                      "keywords": "justified_by", "weight": 1.0},
                }, tolerate=(400, 409))
    print(f"  {len(ADRS)} decision records, linked to the features they justify")

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
