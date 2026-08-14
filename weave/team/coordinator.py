"""WeaveCoordinator — the pull scheduler + atomic claim (P1).

The task queue is the graph state, not a broker. Dispatch is *pull*: idle workers
ask for the ready-set and race to :meth:`claim` — a lifecycle-guarded, per-task
atomic transition ``pending -> in_progress`` that exactly one worker wins (the
loser gets a conflict). The ready-set is deterministic — a task is claimable when
it is ``pending``, all its ``depends_on`` are ``done``, and none of the modules it
``touches`` overlap an in-progress task (so parallel work stays collision-free).

On claim the coordinator assembles a curated :meth:`brief` (the task, its change
request, its dependencies, the modules it touches, and precedent) — the context
the worker hands to ``claude -p``; the run pulls more via the Weave MCP on demand.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Callable, Dict, List, Optional

from weave_core.utils import logger

from weave_core.graph.types import RelationContext
from weave.model.insights import (
    insight_node, project_insight_node, review_node,
)
from weave.team.store import PRIORITY_RANK, WeaveTask, WeaveTaskStore

# Who may publish a plan (the planning gate). Manager/Architect own intake and
# design; everyone else (developers, integrators, autonomous workers) receives
# tasks — they don't author the plan. A "lead" is a composite persona that
# authenticates with its architect claim, so it plans *as* architect and needs
# no separate entry here. Enforced in the coordinator so it holds even without a
# REST RBAC layer in front — an absent/unknown role fails closed.
PLANNER_ROLES = frozenset({"manager", "architect"})

# Modules whose names mark an architecture-sensitive change. A PR touching one is
# FLAGGED by the automated review pass and needs the Architect's sign-off; a clean
# PR does not — "review is the Architect's authority, not the Architect's chore".
ARCHITECTURE_SENSITIVE = (
    "auth", "security", "rbac", "schema", "migration", "infra", "core", "payment")


class WeaveError(Exception):
    """Base for coordinator errors (mapped to HTTP status by the router)."""


class WeaveNotFound(WeaveError):
    """No such task (404)."""


class WeaveConflict(WeaveError):
    """The task can't be claimed right now — already taken, blocked, or a
    touches-conflict (409)."""


class WeaveForbidden(WeaveError):
    """The role may not make this transition (403)."""


class WeaveCoordinator:
    def __init__(
        self,
        task_store: WeaveTaskStore,
        *,
        lifecycle_service: Any = None,
        rag_resolver: Optional[Callable[[str], Any]] = None,
        integration_store: Any = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._tasks = task_store
        self._lifecycle = lifecycle_service
        self._resolve_rag = rag_resolver
        self._integration = integration_store
        self._now = now

    @property
    def store(self) -> WeaveTaskStore:
        return self._tasks

    # -- authoring -----------------------------------------------------------

    def create_task(
        self, workspace: str, task_id: str, *,
        title: str = "", priority: str = "normal", description: str = "",
        change_request: Optional[str] = None,
        touches: Optional[List[str]] = None, depends_on: Optional[List[str]] = None,
        created_by: Optional[str] = None,
    ) -> WeaveTask:
        """Create a pending task (Manager/Architect). Idempotent on id — a repeat
        returns the existing task rather than resetting it."""
        existing = self._tasks.get(workspace, task_id)
        if existing is not None:
            return existing
        task = WeaveTask(
            id=task_id, title=title, priority=priority, description=description,
            change_request=change_request, touches=list(touches or []),
            depends_on=list(depends_on or []), created_by=created_by, status="pending")
        self._tasks.save(workspace, task)
        return task

    def get(self, workspace: str, task_id: str) -> Optional[WeaveTask]:
        return self._tasks.get(workspace, task_id)

    def list(self, workspace: str, *, status: Optional[str] = None) -> List[WeaveTask]:
        return self._tasks.list(workspace, status=status)

    # -- the ready-set (pull scheduling) -------------------------------------

    def ready(self, workspace: str) -> List[WeaveTask]:
        """Claimable tasks: pending, deps done, no touches-conflict. Priority-
        then-id ordered so any worker computes the same next task."""
        tasks = self._tasks.list(workspace)
        done = {t.id for t in tasks if t.status == "done"}
        busy = self._in_progress_touches(tasks)
        out = [
            t for t in tasks
            if t.status == "pending"
            and all(d in done for d in t.depends_on)
            and not (set(t.touches) & busy)
        ]
        out.sort(key=lambda t: (PRIORITY_RANK.get(t.priority, 2), t.id))
        return out

    @staticmethod
    def _in_progress_touches(tasks: List[WeaveTask]) -> set:
        busy: set = set()
        for t in tasks:
            if t.status == "in_progress":
                busy |= set(t.touches)
        return busy

    # -- the claim (atomic, one-winner) --------------------------------------

    async def claim(self, workspace: str, task_id: str, *, worker: str,
                    role: Optional[str] = None) -> WeaveTask:
        """Atomically claim a task for *worker*. Exactly one concurrent claimer
        wins ``pending -> in_progress``; others raise :class:`WeaveConflict`.
        Also refuses a claim blocked on deps, a touches-conflict, or a role the
        lifecycle doesn't allow. Records the claim as a decision on the graph."""
        async with self._claim_lock(workspace):
            t = self._tasks.get(workspace, task_id)
            if t is None:
                raise WeaveNotFound(f"no task '{task_id}'")
            if t.status != "pending":
                raise WeaveConflict(f"task '{task_id}' is {t.status}, not claimable")

            tasks = self._tasks.list(workspace)
            done = {x.id for x in tasks if x.status == "done"}
            unmet = [d for d in t.depends_on if d not in done]
            if unmet:
                raise WeaveConflict(f"task '{task_id}' is blocked on {unmet}")
            overlap = set(t.touches) & self._in_progress_touches(tasks)
            if overlap:
                raise WeaveConflict(f"task '{task_id}' touches-conflict on {sorted(overlap)}")

            if self._lifecycle is not None:
                d = self._lifecycle.check(workspace, "Task", "pending", "in_progress", role=role)
                if not d.allowed:
                    raise WeaveForbidden(d.reason)

            t.status = "in_progress"
            t.assignee = worker
            self._tasks.save(workspace, t)

        await self._record_claim(workspace, t, worker)
        logger.info(f"Weave: '{worker}' claimed task '{task_id}' in '{workspace}'")
        return t

    # -- the brief (context delivery) ----------------------------------------

    async def brief(self, workspace: str, task_id: str) -> Dict[str, Any]:
        """The curated task brief the worker hands to ``claude -p``. Compact and
        high-signal — the task, its change request, its dependency statuses, the
        modules it touches, and **precedent** (prior decisions on similar work).
        The run deepens it via the Weave MCP on demand."""
        t = self._tasks.get(workspace, task_id)
        if t is None:
            raise WeaveNotFound(f"no task '{task_id}'")
        deps = [{"id": d, "status": (dt.status if (dt := self._tasks.get(workspace, d)) else "unknown")}
                for d in t.depends_on]
        return {
            "task": t.to_dict(),
            "change_request": t.change_request,
            "depends_on": deps,
            "touches": list(t.touches),
            "precedent": await self._precedent(workspace, t),
        }

    async def _precedent(self, workspace: str, task: WeaveTask) -> List[Dict[str, Any]]:
        """Prior decisions semantically similar to this task — the "orient" the
        agent gets before it builds. Best-effort: empty when the decision index
        isn't reachable (e.g. offline)."""
        if self._resolve_rag is None:
            return []
        rag = self._resolve_rag(workspace)
        finder = getattr(rag, "find_precedents", None)
        query = f"{task.title} {task.description}".strip()
        if finder is None or not query:
            return []
        try:
            return list(await finder(query, top_k=3)) or []
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Weave precedent lookup skipped: {e}")
            return []

    # -- record the why (dual-write, must-succeed) ---------------------------

    async def record_decision(
        self, workspace: str, *, src: str, tgt: str, relation: str,
        decision_trace: str, by: str, rationale: Optional[str] = None,
        policy_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record a decision on the graph. **Must-succeed** — unlike cohermes's
        best-effort telemetry, a failure propagates so the caller knows the
        decision wasn't captured. ``emit_decision_trace`` dual-writes it (into
        ``relationships_vdb`` for normal retrieval and ``decisions_vdb`` for
        precedent search), so it is queryable the instant it returns."""
        if self._resolve_rag is None:
            raise WeaveError("no graph configured to record a decision")
        rag = self._resolve_rag(workspace)
        if not hasattr(rag, "emit_decision_trace"):
            raise WeaveError("this workspace cannot record decisions (Weave off)")
        rc = RelationContext(
            decision_trace=decision_trace, approved_by=by, approved_via="system",
            provenance=f"weave:decision:{tgt}",
            supporting_sentences=[rationale] if rationale else [],
            policy_ref=policy_ref, confidence_score=1.0)
        decision = await rag.emit_decision_trace(src, tgt, relation, rc)
        return {
            "src": src, "tgt": tgt, "relation": relation,
            "outcome": getattr(decision, "outcome", "RECORDED") if decision else "RECORDED",
            "audit": getattr(decision, "audit", None) if decision else None,
        }

    # -- the artifact chain (Task → Commit* → PullRequest → Review*) ---------

    async def record_commit(
        self, workspace: str, task_id: str, *, sha: str, subject: str = "",
        touches: Optional[List[str]] = None, by: str = "developer",
    ) -> Dict[str, Any]:
        """Record a commit the loop produced against a task. Appends to the task's
        chain (source of truth) and reflects a ``Commit`` node + ``produced``
        audit edge onto the graph (best-effort).

        The ontology link type is ``produced`` as of P2: ``implemented_by`` was
        retargeted to Feature→Task, which is what R19 and the DRP class diagram
        mean by it. The edge itself is unchanged — graph edges carry a prose
        relation, not the link-type name — so this was a vocabulary correction
        and not a data migration.
        """
        t = self._require(workspace, task_id)
        entry = {"sha": sha, "subject": subject,
                 "touches": list(touches if touches is not None else t.touches)}
        t.commits.append(entry)
        self._tasks.save(workspace, t)
        await self._reflect_node(
            workspace, sha, {"entity_type": "Commit", "subject": subject},
            src=task_id, relation="implemented by a commit", tgt=sha, by=by,
            why=f"commit {sha[:8]}: {subject}".strip())
        return {"task": task_id, "sha": sha, "commits": len(t.commits)}

    async def open_pull_request(
        self, workspace: str, task_id: str, *, branch: str = "", url: str = "",
        title: str = "", by: str = "developer", role: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Open the PR for a completed task — the code-in hand-back. Transitions
        the task ``in_progress -> review`` (role-gated by the lifecycle), records
        the PullRequest on the chain, and reflects it onto the graph. It **cannot
        merge** (that's the Integrator's ``MergeToMain`` in P4)."""
        t = self._require(workspace, task_id)
        if t.pull_request is not None:
            raise WeaveConflict(f"task '{task_id}' already has an open pull request")
        if t.status != "in_progress":
            raise WeaveConflict(f"task '{task_id}' is {t.status}, cannot open a PR")
        if self._lifecycle is not None:
            d = self._lifecycle.check(workspace, "Task", "in_progress", "review", role=role)
            if not d.allowed:
                raise WeaveForbidden(d.reason)
        pr = {"branch": branch, "url": url, "title": title or t.title, "status": "open"}
        t.pull_request = pr
        t.status = "review"
        self._tasks.save(workspace, t)
        pr_id = f"PR:{task_id}"
        await self._reflect_node(
            workspace, pr_id,
            {"entity_type": "PullRequest", "title": pr["title"], "url": url, "branch": branch},
            src=task_id, relation="submitted as a pull request", tgt=pr_id, by=by,
            why=f"opened PR for {task_id}: {pr['title']}")
        logger.info(f"Weave: PR opened for '{task_id}' → review in '{workspace}'")
        return {"task": task_id, "pull_request": pr, "status": t.status}

    async def record_review(
        self, workspace: str, task_id: str, *, verdict: str, by: str = "architect",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Record a review outcome on the task's PR (two-tier: an automated pass +
        an Architect sign-off). Verdict is free-form (``approve`` / ``flag`` /
        ``reject``); advancing the task is a separate lifecycle move."""
        t = self._require(workspace, task_id)
        if t.pull_request is None:
            raise WeaveConflict(f"task '{task_id}' has no pull request to review")
        entry = {"verdict": verdict, "by": by, "notes": notes}
        t.reviews.append(entry)
        self._tasks.save(workspace, t)

        # The typed `Review` node, written here rather than by a migration
        # (D-043). `/ask/learnings` seeds on the type, so until P10.1 this
        # method recorded a review that *what did we learn* could not find —
        # on every workspace, until someone ran `weave migrate reviews`.
        #
        # The id is the task plus this entry's position, which is what the
        # migration would compute from the same append-only list, so a
        # migration run afterwards upserts the same node and creates nothing.
        node = review_node(task_id, len(t.reviews) - 1, entry)
        await self._write_artifact_node(workspace, node)

        # **Node first, edge second, and that order is load-bearing.**
        # `emit_decision_trace` creates a missing endpoint as a generic
        # `ENTITY`; it will not touch one that already exists. Emitting the
        # edge first would create the review as untyped and the typed write
        # would then have to correct it — which is the shape of W17, and it is
        # cheaper to be ordered than to be repaired.
        #
        # The edge runs task → review, matching the migration rather than the
        # `PR:` source used before: `/ask/learnings?scope=TASK` walks out from
        # the task and admits only `Review`/`Insight`, so an edge that hops
        # through the PR node is a path the walk cannot follow. Live and
        # migrated workspaces have to answer the scoped question the same way.
        await self._reflect_node(
            workspace, f"PR:{task_id}", {"entity_type": "PullRequest", "status": verdict},
            src=task_id, relation="reviewed in", tgt=node["entity_id"], by=by,
            why=f"review of {task_id}: {verdict}. {notes}".strip())
        return {"task": task_id, "verdict": verdict, "reviews": len(t.reviews)}

    async def review_pass(
        self, workspace: str, task_id: str, *, by: str = "review-agent",
        sensitive: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """The **automated** tier of the two-tier review. Runs on every PR: it
        flags a change that touches an architecture-sensitive module (or a
        critical task), and passes a clean one. The result is recorded as a review
        with ``requires_architect`` — so the Architect's sign-off (``advance`` to
        ``approved``) is reserved for the flagged / architecture-touching PRs, not
        every PR. It never approves on its own; the lifecycle still gates the move
        to ``approved`` on an Architect/Manager."""
        t = self._require(workspace, task_id)
        if t.pull_request is None:
            raise WeaveConflict(f"task '{task_id}' has no pull request to review")
        sens = tuple(sensitive) if sensitive is not None else ARCHITECTURE_SENSITIVE
        hits = sorted({m for m in t.touches for s in sens if s in m.lower()})
        reasons: List[str] = []
        if hits:
            reasons.append(f"touches architecture-sensitive modules: {hits}")
        if t.priority == "critical":
            reasons.append("critical priority")
        flagged = bool(reasons)
        verdict = "flag" if flagged else "approve"
        await self.record_review(workspace, task_id, verdict=verdict, by=by,
                                 notes="; ".join(reasons) or "automated pass: clean")
        return {"task": task_id, "verdict": verdict, "flagged": flagged,
                "requires_architect": flagged, "reasons": reasons}

    async def record_learning(
        self, workspace: str, *, insight: str, task_id: Optional[str] = None,
        by: str = "developer",
    ) -> Dict[str, Any]:
        """Record an insight the loop learned — a **must-succeed** decision so it
        is precedent-searchable for future tasks (the shared brain grows). When a
        task is named the insight is also stapled to its chain.

        **The typed `Insight` node is written here** (D-043). The decision trace
        below is precedent — searchable by text, which is what `find_precedents`
        needs — but `/ask/learnings` seeds on `entity_type`, and a trace has no
        type. Recording only the trace is why a clean workspace answered *what
        did we learn* with nothing while holding every learning it had been told
        (W23).
        """
        tgt = task_id or "project"
        if task_id is not None:
            t = self._require(workspace, task_id)
            t.learnings.append(insight)
            self._tasks.save(workspace, t)
            node = insight_node(task_id, len(t.learnings) - 1, insight)
        else:
            # No task means no list and therefore no position to derive an id
            # from; `project_insight_node` hashes the statement instead. The
            # asymmetry is deliberate and documented where the id is built.
            node = project_insight_node(insight)
        # **Artifact first, precedent last.** The node and its edge are the
        # thing that was learned; the decision trace is the record that the
        # team learned it. Writing them in that order also keeps the `learned`
        # decision the most recent one on the workspace, which is what a caller
        # reading back the last decision means by "the learning".
        await self._write_artifact_node(workspace, node)

        if task_id is not None:
            # task → insight, the edge the migration writes, for the same
            # reason: the scoped question walks out from the task.
            #
            # **The edge alone — `_reflect_node` would write the node again.**
            # The first version called it here, and a negative control caught
            # what that costs: with two writers the *swallowing* one was enough
            # to satisfy every assertion, so removing the must-succeed write
            # changed nothing a test could see. A backend failure would then
            # have lost the learning silently — the defect this phase is about,
            # reintroduced by the code meant to fix it.
            await self._audit_edge(
                workspace, src=task_id, relation="yielded",
                tgt=node["entity_id"], by=by, why=f"{task_id} yielded: {insight}")

        decision = await self.record_decision(
            workspace, src=by, tgt=tgt, relation="learned",
            decision_trace=insight, by=by, policy_ref="weave:insight")
        return {"target": tgt, "insight": insight, "decision": decision,
                "node": node["entity_id"]}

    def trace_chain(self, workspace: str, task_id: str) -> Dict[str, Any]:
        """Reconstruct a task's full artifact chain from Weave state — the change
        request it serves, its commits, its PR, its reviews, and what it taught."""
        t = self._require(workspace, task_id)
        return {
            "task": t.to_dict(), "change_request": t.change_request,
            "commits": [dict(c) for c in t.commits],
            "pull_request": dict(t.pull_request) if t.pull_request else None,
            "reviews": [dict(r) for r in t.reviews], "learnings": list(t.learnings),
        }

    def _require(self, workspace: str, task_id: str) -> WeaveTask:
        t = self._tasks.get(workspace, task_id)
        if t is None:
            raise WeaveNotFound(f"no task '{task_id}'")
        return t

    # -- lifecycle transitions + the integration merge gate (P4) -------------

    async def advance_task(self, workspace: str, task_id: str, to: str, *,
                           role: Optional[str] = None) -> Dict[str, Any]:
        """Governed lifecycle transition of a task (the ``AdvanceTask`` action) —
        e.g. an Architect moving ``review -> approved``. Role-gated by the Task
        state machine; refuses an illegal edge."""
        t = self._require(workspace, task_id)
        frm = t.status
        if self._lifecycle is not None:
            d = self._lifecycle.check(workspace, "Task", frm, to, role=role)
            if not d.allowed:
                raise WeaveForbidden(d.reason)
        t.status = to
        self._tasks.save(workspace, t)
        return {"task": task_id, "from": frm, "to": to}

    def register_environment(self, workspace: str, env_id: str, *, name: str = "",
                             url: str = "", config: Optional[Dict[str, Any]] = None):
        """Declare the shared integration environment (the Integrator owns it)."""
        from weave.team.integration import WeaveEnvironment

        if self._integration is None:
            raise WeaveError("no integration store configured")
        env = WeaveEnvironment(id=env_id, name=name or env_id, url=url,
                               config=dict(config or {}), status="ready")
        self._integration.save_env(workspace, env)
        logger.info(f"Weave: environment '{env_id}' declared in '{workspace}'")
        return env

    def environments(self, workspace: str):
        return self._integration.list_envs(workspace) if self._integration else []

    async def deploy(self, workspace: str, env_id: str, *, tasks: List[str],
                     ref: str = "", by: str = "integrator") -> Dict[str, Any]:
        """Deploy approved work into the shared environment. Marks the tasks
        ``deployed_to`` the env (audit) — it does not itself promote them."""
        if self._integration is None or self._integration.get_env(workspace, env_id) is None:
            raise WeaveNotFound(f"no environment '{env_id}'")
        for tid in tasks:
            await self._reflect_node(
                workspace, env_id, {"entity_type": "Environment"},
                src=tid, relation="deployed to", tgt=env_id, by=by,
                why=f"deployed {tid} to {env_id} ({ref})".strip())
        return {"environment": env_id, "tasks": list(tasks), "ref": ref}

    async def run_integration(self, workspace: str, env_id: str, *, tasks: List[str],
                              passed: bool, kind: str = "e2e", summary: str = "",
                              by: str = "integrator") -> Dict[str, Any]:
        """Record an integration / e2e run against the environment — the merge
        gate's evidence. The Integrator supplies the outcome (``passed``); the run
        is stored and written to the graph as a decision."""
        from weave.team.integration import IntegrationRun

        if self._integration is None or self._integration.get_env(workspace, env_id) is None:
            raise WeaveNotFound(f"no environment '{env_id}'")
        n = len(self._integration.list_runs(workspace)) + 1
        run = IntegrationRun(
            id=f"{env_id}-run-{n}", environment=env_id, kind=kind,
            status="passed" if passed else "failed", tasks=list(tasks),
            summary=summary, at=self._now())
        self._integration.add_run(workspace, run)
        await self._reflect_node(
            workspace, run.id, {"entity_type": "IntegrationRun", "status": run.status},
            src=run.id, relation="verifies", tgt=(tasks[0] if tasks else env_id), by=by,
            why=f"{kind} run {run.status} for {tasks or env_id}: {summary}".strip())
        return run.to_dict()

    async def promote(self, workspace: str, task_id: str, *, env_id: str,
                      role: Optional[str] = None, by: str = "integrator") -> Dict[str, Any]:
        """The **merge gate** (Integrator). Promotes an approved task to ``done``
        only when a green integration run covers it. Refuses if the task isn't
        approved, if no passing run exists, or if the role can't make the
        transitions. Records a must-succeed Promote decision."""
        t = self._require(workspace, task_id)
        if t.status != "approved":
            raise WeaveConflict(f"task '{task_id}' is {t.status}, not approved — cannot promote")
        runs = self._integration.list_runs(workspace) if self._integration else []
        green = [r for r in runs if r.passed and task_id in r.tasks]
        if not green:
            raise WeaveConflict(f"task '{task_id}' has no green integration run — merge blocked")
        # approved → testing → done, both Integrator-gated by the lifecycle
        if self._lifecycle is not None:
            for frm, to in (("approved", "testing"), ("testing", "done")):
                d = self._lifecycle.check(workspace, "Task", frm, to, role=role)
                if not d.allowed:
                    raise WeaveForbidden(d.reason)
        t.status = "done"
        self._tasks.save(workspace, t)
        decision = await self.record_decision(
            workspace, src=by, tgt=task_id, relation="promotes to done",
            decision_trace=f"promoted {task_id} to done after green {green[-1].kind} "
                           f"run {green[-1].id} in {env_id}",
            by=by, policy_ref="weave:merge-gate")
        logger.info(f"Weave: task '{task_id}' promoted to done in '{workspace}'")
        return {"task": task_id, "status": "done", "environment": env_id,
                "run": green[-1].id, "decision": decision}

    async def _write_artifact_node(self, workspace: str, node: Dict[str, Any]) -> None:
        """Write a typed artifact node — **must-succeed** (D-043).

        The counterpart to `_reflect_node`, and the difference is which copy is
        the record. A `Commit` or `PullRequest` node is a reflection: the task
        store holds the truth and the graph is an audit view, so a failed write
        loses an audit row and is rightly best-effort. A `Review` or `Insight`
        node is not a reflection — it is the only thing `/ask/learnings` reads,
        and swallowing its failure is W23 arriving one write at a time, with no
        error at the moment the answer is lost.

        **There is no `try`, and that is the whole difference.** A backend that
        refuses the write raises through to the caller, exactly as
        `record_decision` does.

        A rag with no `chunk_entity_relation_graph` at all is skipped rather
        than refused: a real workspace cannot be in that state — every engine
        carries one, and a workspace with Weave off fails earlier, in
        `record_decision`, for want of `emit_decision_trace`. The only objects
        that reach this branch are test doubles standing in for a graph they do
        not have, so refusing here would assert something about production by
        way of a shape production cannot take.
        """
        if self._resolve_rag is None:
            raise WeaveError("no graph configured to record an artifact node")
        rag = self._resolve_rag(workspace)
        graph = getattr(rag, "chunk_entity_relation_graph", None)
        if graph is None:
            return
        await graph.upsert_node(node["entity_id"], dict(node))

    async def _reflect_node(
        self, workspace: str, node_id: str, node_data: Dict[str, Any], *,
        src: str, relation: str, tgt: str, by: str, why: str,
    ) -> None:
        """Best-effort: upsert an artifact node + write the audit edge as a
        decision. The task store is the source of truth; the graph is the audit
        reflection (skipped cleanly when no graph is reachable)."""
        if self._resolve_rag is None:
            return
        rag = self._resolve_rag(workspace)
        graph = getattr(rag, "chunk_entity_relation_graph", None)
        if graph is not None:
            try:
                await graph.upsert_node(node_id, {"entity_id": node_id, **node_data})
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Weave artifact node upsert skipped: {e}")
        await self._audit_edge(workspace, src=src, relation=relation, tgt=tgt,
                               by=by, why=why)

    async def _audit_edge(self, workspace: str, *, src: str, relation: str,
                          tgt: str, by: str, why: str) -> None:
        """The audit edge on its own — for a node already written elsewhere.

        Split out of `_reflect_node` so a caller that has *already* written its
        node must-succeed does not write it a second time best-effort. Two
        writers of one node means the swallowing one decides whether the record
        survives, which is the guarantee `_write_artifact_node` exists to make.
        """
        if self._resolve_rag is None:
            return
        rag = self._resolve_rag(workspace)
        if hasattr(rag, "emit_decision_trace"):
            rc = RelationContext(decision_trace=why, approved_by=by, approved_via="system",
                                 provenance=f"weave:artifact:{tgt}", confidence_score=1.0)
            try:
                await rag.emit_decision_trace(src, tgt, relation, rc)
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Weave artifact audit skipped: {e}")

    # -- the planning gate (sign the plan, then release its tasks) -----------

    async def publish_plan(
        self, workspace: str, *, plan_ref: str, by: str,
        role: Optional[str] = None, plan_kind: str = "PRD",
        tasks: Optional[List[Dict[str, Any]]] = None, summary: str = "",
    ) -> Dict[str, Any]:
        """The **planning gate** (M2): a Manager/Architect (or a lead) signs a
        plan — a PRD or RFC — and, in the same governed step, releases the tasks
        it decomposes into onto the queue.

        The signature is a **must-succeed** recorded decision (dual-write), so an
        unsigned plan releases no work: the decision is written *first*, and only
        then are the tasks created. Each task's ``change_request`` defaults to the
        plan ref, so the queue traces back to the document that authorised it.
        """
        if role not in PLANNER_ROLES:
            raise WeaveForbidden(f"role '{role}' may not publish a plan")
        specs = list(tasks or [])
        note = summary or f"{role or by} published {plan_kind} {plan_ref} ({len(specs)} tasks)"
        decision = await self.record_decision(
            workspace, src=by, tgt=plan_ref, relation="signs a plan",
            decision_trace=note, by=by, rationale=summary or None,
            policy_ref=f"weave:plan:{plan_kind}")
        created: List[str] = []
        for spec in specs:
            t = self.create_task(
                workspace, spec["id"], title=spec.get("title", ""),
                priority=spec.get("priority", "normal"),
                description=spec.get("description", ""),
                change_request=spec.get("change_request") or plan_ref,
                touches=spec.get("touches"), depends_on=spec.get("depends_on"),
                created_by=role or by)
            created.append(t.id)
        logger.info(f"Weave: {role or by} published {plan_kind} '{plan_ref}' "
                    f"→ {len(created)} tasks in '{workspace}'")
        return {"plan_ref": plan_ref, "plan_kind": plan_kind,
                "tasks": created, "decision": decision}

    # -- internals -----------------------------------------------------------

    async def _record_claim(self, workspace: str, task: WeaveTask, worker: str) -> None:
        """Write the claim as an audit decision on the graph (best-effort — the
        store transition already succeeded)."""
        if self._resolve_rag is None:
            return
        rag = self._resolve_rag(workspace)
        if not hasattr(rag, "emit_decision_trace"):
            return
        rc = RelationContext(
            decision_trace=f"{worker} claimed task {task.id}: {task.title}",
            approved_by=worker, approved_via="system",
            provenance=f"weave:task:{task.id}", confidence_score=1.0)
        try:
            await rag.emit_decision_trace(worker, task.id, "claims a task", rc)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Weave claim audit failed for {task.id}: {e}")

    def _claim_lock(self, workspace: str):
        """Serialise *all* claims in a workspace, not one task. The claim checks a
        cross-task invariant (a candidate's ``touches`` must not overlap any
        in-progress task), so two workers claiming *different* tasks must still
        exclude each other — a per-task lock would let both pass the busy-set
        check and both transition. A workspace-scoped, cross-process keyed lock
        also serialises the JSON store's read-modify-write so a stale snapshot
        can't revert a just-claimed task. No-op when shared storage isn't
        initialised (offline tests, where the single event loop already
        serialises the await-free critical section)."""
        try:
            from weave_core.store.locks import get_storage_keyed_lock

            return get_storage_keyed_lock([f"claim:{workspace}"], namespace="Weave")
        except (RuntimeError, ImportError):
            return contextlib.nullcontext()
