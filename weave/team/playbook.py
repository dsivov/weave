"""Weave role kits — the onboarding surface (P2 · M2).

Every participant in a Weave project — human or autonomous — operates *as a role*,
not as a person (see the RFC, D10). A **role kit** is everything one identity needs
to start operating in that role against the shared Weave: the MCP config to
wire into Claude Code, a ``CLAUDE.md`` loop that tells the session how this role
works, the governed actions and endpoints it may use, and a few slash-commands.

The kit layers *on top of* the generic ``/workspace/bootstrap`` bundle — that one is
domain-agnostic (mcp_config, playbook, backfill); this one is Weave-specific and
role-specific. Data-driven: :data:`ROLES` is the single source, and the human-facing
``CLAUDE.md`` / manifest are rendered from it so they never drift.

Runtimes (RFC, D10/D11):

* **interactive** — a human drives Claude Code (CLI or app). Manager & Architect are
  always interactive; a human developer can be too.
* **autonomous** — a headless ``claude -p`` loop in a container, self-claiming work.
* **lead** (optional, D11) — an interactive developer that *also* launches and
  supervises autonomous workers.
"""

from __future__ import annotations

from typing import Any, Dict, List

from weave.server.workspace_pool import WORKSPACE_HEADER

# ── the five roles, data-driven ─────────────────────────────────────────────
# Each entry is the whole kit for one role. `loop` is the operating cycle the
# session runs; `actions` are the governed actions RBAC grants it; `endpoints`
# are the Weave/Weave routes it calls; `commands` are suggested Claude Code slash
# commands to drop into `.claude/commands/`.

ROLES: Dict[str, Dict[str, Any]] = {
    "manager": {
        "title": "Manager",
        "runtime": "interactive",
        "human_only": True,
        "summary": (
            "Intake and framing. Turns a request into a PRD, a global task list, and a "
            "discussion doc, then signs the plan so its tasks are released to the queue."),
        "loop": [
            "Interview the stakeholder; capture the goal, constraints, and success criteria.",
            "Scaffold the project with `/new-project`, then author the vision (`/write-blog`) "
            "and the requirements (`/write-drp`) as house-style docs in `docs/`.",
            "Ingest those docs into Weave (`POST /documents/text`) so the project is retrievable "
            "and every plan can reference a real document.",
            "Open the change request (`CreateChangeRequest`) that the docs specify.",
            "Hand the DRP to the Architect for the RFC + work-plan decomposition; stay available "
            "for scope calls.",
        ],
        "skills": ["/new-project", "/write-blog", "/write-drp"],
        "actions": ["CreateChangeRequest", "AdvanceChangeRequest", "CreateTask", "PublishPlan"],
        "endpoints": ["POST /documents/text", "POST /weave/plan/publish", "POST /weave/tasks",
                      "GET /weave/tasks", "POST /weave/decisions",
                      "GET /diagrams", "GET /diagrams/{id}"],
        "commands": ["intake", "publish-plan"],
        "guardrails": [
            "Docs before code (methodology R1): the BLOG/DRP exist and are ingested before any task.",
            "A human always approves the plan before it reaches the Architect.",
            "Record scope decisions — the graph is the audit trail.",
        ],
    },
    "architect": {
        "title": "Architect",
        "runtime": "interactive",
        "human_only": True,
        "summary": (
            "Design authority. Produces the RFC/README, diagrams, and the reviewable task "
            "breakdown, and owns the two-tier review standard."),
        "loop": [
            "Read the Manager's BLOG/DRP + change request; query the graph for precedent and "
            "affected modules.",
            "Author the approach (`/write-rfc`) and the design (`/write-architecture`) as "
            "house-style docs, each with at least one diagram.",
            "Save every diagram to the shared workspace (`save_diagram` / `POST /diagrams`) with "
            "`depicts` set — teammates read the same picture, and each save is a signed version.",
            "Run `/make-workplan` to turn the DRP + architecture into phases → milestones → "
            "tasks with **test gates**; give each task precise `touches` + `depends_on`.",
            "Ingest the RFC / architecture / work-plan into Weave (`POST /documents/text`).",
            "Publish the plan (`POST /weave/plan/publish`) with `plan_ref` pointing at the RFC / "
            "work-plan doc and the tasks taken from it — signing releases them to the queue.",
            "Review flagged / architecture-touching PRs (`/milestone-review`); record the decisions.",
        ],
        "skills": ["/write-rfc", "/write-architecture", "/make-workplan", "/milestone-review"],
        "actions": ["RecordDecision", "CreateTask", "ProposeModule", "AdvanceTask",
                    "PublishPlan", "MergeToMain"],
        "endpoints": ["POST /documents/text", "POST /weave/plan/publish", "POST /weave/tasks",
                      "GET /weave/tasks", "POST /weave/decisions",
                      "GET /diagrams", "GET /diagrams/{id}", "POST /diagrams"],
        "commands": ["design-rfc", "decompose", "review-pr"],
        "guardrails": [
            "Reuse first: always search the graph and codebase for an existing module, feature, "
            "integration, or pattern before designing a new one — do not reinvent what we already have.",
            "Diagrams are shared artifacts: `list_diagrams` before you draw, and revise the "
            "existing id rather than starting a parallel picture of the same thing.",
            "`plan_ref` must point at an ingested RFC / work-plan doc — the graph plan and the "
            "`docs/` artifact are the same thing (methodology R1), never a parallel pipeline.",
            "Every milestone carries an explicit test gate (methodology R3); write it with the milestone.",
            "Two-tier review: an automated agent pass on every PR + your sign-off on flagged changes.",
            "`touches` is how parallelism stays collision-free — set it deliberately.",
        ],
    },
    "developer": {
        "title": "Developer",
        "runtime": "autonomous",
        "human_only": False,
        "summary": (
            "Builds one task at a time. Pulls the ready-set, claims a task, implements it on a "
            "branch, runs unit tests, opens a PR, and records the why."),
        "loop": [
            "Long-poll `GET /weave/tasks/wait` for claimable work (or read `GET /weave/tasks/ready`).",
            "Pick the top task and claim it (`POST /weave/tasks/{id}/claim`) — one winner; a 409 means move on.",
            "Read the brief (`GET /weave/tasks/{id}/brief`); pull more context via the Weave MCP on demand.",
            "Implement on a branch; run unit tests for your slice.",
            "Open a PR (`OpenPullRequest`), record the decision (`POST /weave/decisions`), loop.",
        ],
        "skills": [],   # a developer builds code; it authors no methodology docs
        "actions": ["ClaimTask", "AdvanceTask", "ProposeModule", "OpenPullRequest", "CreateChangeRequest"],
        "endpoints": ["GET /weave/tasks/wait", "GET /weave/tasks/ready",
                      "POST /weave/tasks/{id}/claim", "GET /weave/tasks/{id}/brief",
                      "POST /weave/decisions"],
        "commands": ["claim-next", "record-why"],
        "guardrails": [
            "Reuse first: check the brief's precedent and the existing code for something to extend "
            "before writing new — do not reinvent what we already have.",
            "Never stand up the full stack — that's the Integrator's env. PR is your hand-back.",
            "A claim you lose (409) is normal; recompute the ready-set and take the next one.",
        ],
    },
    "integrator": {
        "title": "Integrator",
        "runtime": "interactive",
        "human_only": False,
        "summary": (
            "Owns the one centralised integration environment. Assembles merged work, runs "
            "integration + e2e, and gates the merge to main."),
        "loop": [
            "Watch for tasks in `review`/`approved`; deploy them into the shared integration env.",
            "Run integration + e2e suites against the assembled stack.",
            "On green, merge to main (`MergeToMain`) and advance the task to `done`.",
            "On red, kick the task back with a recorded decision citing the failure.",
        ],
        "skills": [],   # the Integrator runs the env; it authors no methodology docs
        "actions": ["AdvanceTask", "MergeToMain"],
        "endpoints": ["GET /weave/tasks", "POST /weave/decisions"],
        "commands": ["integrate", "gate-merge"],
        "guardrails": [
            "There is exactly one persistent integration env — it is the merge gate.",
            "Integration/e2e green is required before a task reaches `done`.",
        ],
    },
    "lead": {
        "title": "Lead developer",
        "runtime": "lead",
        "human_only": False,
        "optional": True,
        "composed_of": ["developer", "architect"],
        "summary": (
            "Optional. An interactive developer who reads the Architect's & Manager's artifacts, "
            "drives a slice of the plan, and launches + supervises autonomous workers."),
        "loop": [
            "Read the RFC/DRP + graph state; pick an epic to own.",
            "Run `/make-workplan` on the epic, then split it into sub-tasks with `touches`/"
            "`depends_on` and publish via `POST /weave/plan/publish`.",
            "Launch autonomous developer workers; they self-claim from the ready-set.",
            "Watch the board; review their PRs (`/milestone-review`, two-tier, with the "
            "Architect on flagged changes).",
            "Integrate the reviewed work and record the decisions.",
        ],
        "skills": ["/make-workplan", "/milestone-review"],
        "actions": ["ClaimTask", "AdvanceTask", "ProposeModule", "OpenPullRequest",
                    "PublishPlan"],
        "endpoints": ["POST /weave/plan/publish", "GET /weave/tasks/ready",
                      "POST /weave/tasks/{id}/claim", "GET /weave/tasks/{id}/brief",
                      "POST /weave/decisions", "GET /diagrams", "POST /diagrams"],
        "commands": ["own-epic", "launch-fleet", "review-pr"],
        "guardrails": [
            "Dispatched workers are ordinary developer principals — governance is unchanged.",
            "You are the first-tier reviewer for the fleet; escalate flagged changes to the Architect.",
        ],
    },
}


def roles() -> List[Dict[str, Any]]:
    """Lightweight role directory — for `GET /weave/roles`."""
    return [
        {
            "role": key,
            "title": r["title"],
            "runtime": r["runtime"],
            "human_only": r.get("human_only", False),
            "optional": r.get("optional", False),
            "summary": r["summary"],
            "skills": list(r.get("skills", [])),
        }
        for key, r in ROLES.items()
    ]


def _mcp_config(ws: str, server_url: str, token: str = "") -> Dict[str, Any]:
    """The `.mcp.json` an agent wires into Claude Code — the single Weave tool surface.

    **The token is why this file is now a credential** (W33). `/mcp` used to
    answer without one, so the workspace header alone was enough to reach a
    tenant; it is now behind the same authentication as every REST route, and
    the header selects among the workspaces the *token* holds rather than
    granting one.

    Written empty when no token is supplied, so the file still documents the
    shape and an operator can paste one in — an incomplete config that says what
    is missing beats one that silently omits the line.
    """
    headers = {WORKSPACE_HEADER: ws}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return {
        "mcpServers": {
            "weave": {
                "type": "http",
                "url": f"{server_url}/mcp",
                "headers": headers,
            }
        }
    }


def role_kit(role: str, ws: str, server_url: str, token: str = "") -> Dict[str, Any]:
    """The full role kit bundle for `GET /weave/kit?role=…`.

    Everything one identity needs to operate as *role* in *ws*: the MCP config, a
    rendered ``CLAUDE.md`` loop, the governed actions/endpoints it may use, and the
    generic playbook/manifest URLs it should also read.
    """
    r = ROLES.get(role)
    if r is None:
        raise KeyError(role)
    server_url = server_url.rstrip("/")
    q = f"?role={role}"
    composed = r.get("composed_of")
    auth_step = (
        f"Authenticate holding the {' + '.join(f'`{c}`' for c in composed)} role claims — "
        f"`{role}` is a composite (one identity, several roles)."
        if composed else
        f"Authenticate with a token whose role claim is `{role}` (a person may hold several roles)."
    )
    return {
        "workspace": ws,
        "role": role,
        "title": r["title"],
        "runtime": r["runtime"],
        "human_only": r.get("human_only", False),
        "optional": r.get("optional", False),
        "composed_of": list(r.get("composed_of", [])),
        "summary": r["summary"],
        "mcp_config": _mcp_config(ws, server_url, token),
        "claude_md": claude_md(role, ws, server_url),
        "loop": list(r["loop"]),
        "skills": list(r.get("skills", [])),
        "actions": list(r["actions"]),
        "endpoints": list(r["endpoints"]),
        "slash_commands": list(r["commands"]),
        "guardrails": list(r["guardrails"]),
        # also read the generic, workspace-accurate playbook/manifest
        "playbook_url": f"{server_url}/workspace/playbook{q}",
        "manifest_url": f"{server_url}/workspace/manifest{q}",
        "next_steps": [
            "Write `mcp_config` to `.mcp.json` at your repo root and (re)connect the MCP client.",
            "Write `claude_md` to `CLAUDE.md` — it is your operating loop for this role.",
            auth_step,
            "Read `playbook_url` for the live, workspace-accurate object types + guardrails.",
            "Start the loop.",
        ],
    }


def claude_md(role: str, ws: str, server_url: str) -> str:
    """Render the role's operating loop as a `CLAUDE.md` a session runs verbatim."""
    r = ROLES.get(role)
    if r is None:
        raise KeyError(role)
    server_url = server_url.rstrip("/")
    runtime_line = {
        "interactive": "You drive this role interactively in Claude Code (CLI or app).",
        "autonomous": "You run this role headless (`claude -p`) in a loop, self-claiming work.",
        "lead": "You drive interactively AND launch/supervise autonomous workers.",
    }.get(r["runtime"], "")
    loop = "\n".join(f"{i}. {step}" for i, step in enumerate(r["loop"], 1))
    actions = ", ".join(f"`{a}`" for a in r["actions"])
    guards = "\n".join(f"- {g}" for g in r["guardrails"])
    skills = r.get("skills", [])
    skills_section = ""
    if skills:
        skills_section = (
            "\n## Methodology skills (ONBOARDING)\n"
            "Author every artifact with these skills so the docs come out house-style and "
            "consistent, then **ingest each into Weave** (`POST /documents/text`) — the `docs/` file "
            "and the graph are one artifact, and ingestion is what makes the project retrievable:\n"
            + ", ".join(f"`{s}`" for s in skills)
            + "\n(Skills resolve if the ONBOARDING kit is installed at the user or project level.)\n")
    diagrams_section = ""
    if any(e.endswith("/diagrams") or "/diagrams/" in e for e in r["endpoints"]):
        writes = "POST /diagrams" in r["endpoints"]
        diagrams_section = (
            "\n## Shared diagrams\n"
            "Project diagrams live on the server, one set per workspace — the picture you read is "
            "the picture your teammates read. Reach them with the `list_diagrams` / `get_diagram`"
            + (" / `save_diagram`" if writes else "") + " MCP tools, or the `/diagrams` endpoints.\n"
            "- **Read before you draw:** `list_diagrams` (optionally filtered by `depicts`) — then "
            "revise that diagram's id instead of starting a second picture of the same thing.\n"
            + ("- **Save is governed:** a structural change (different nodes or edges) needs an "
               "`approver` and a `reason`; restyling or relabelling is cosmetic and auto-approves. "
               "Either way it versions in the signed ledger.\n"
               "- Set `depicts` to the change request / module / task ids the diagram covers, so it "
               "surfaces for the people working on them.\n" if writes else ""))
    return f"""# Weave — {r['title']} ({role})

You operate in the Weave workspace **{ws}** as the **{r['title']}** role.
{runtime_line}

{r['summary']}

## Your loop
{loop}
{skills_section}{diagrams_section}
## Governed actions you may invoke
{actions}
(Discover the live set with the `get_manifest` MCP tool — RBAC filters it to your role.)

## Guardrails
{guards}

## Ground rules
- Coordinate only through the Weave — it is the single source of truth. No side channels.
- Author docs with the methodology skills, **ingest them** (`POST /documents/text`), and point
  `plan_ref` at the ingested RFC / work-plan — one artifact, not a parallel pipeline.
- Query before you build; **record the why** after every non-trivial decision (`POST /weave/decisions`).
- Attribution is by your authenticated role claim, never a self-stamped body field.
- The Weave MCP + playbook are at {server_url}/mcp and {server_url}/workspace/playbook?role={role}.
"""
