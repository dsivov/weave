"""P1 test gate — the pull scheduler + atomic claim.

The coordination heart of Weave (M1): a task queue that is the graph state, a
deterministic ready-set (deps + touches), and a claim that exactly one worker
wins. Offline; a real Weave lifecycle (installed from the preset) enforces the
role-gated transition.
"""

from __future__ import annotations

import pytest

from weave_core.governance.lifecycle import InMemoryLifecycleStore, LifecycleService
from weave.team import preset
from weave.team.coordinator import (
    WeaveConflict, WeaveCoordinator, WeaveForbidden, WeaveNotFound,
)
from weave.team.store import InMemoryWeaveTaskStore


def _coordinator():
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    preset.install("w", lifecycle_service=lifecycle)   # the Weave Task machine
    return WeaveCoordinator(InMemoryWeaveTaskStore(), lifecycle_service=lifecycle)


@pytest.mark.offline
def test_ready_set_honours_deps_touches_and_priority():
    c = _coordinator()
    c.create_task("w", "t1", title="base")
    c.create_task("w", "t2", title="needs t1", depends_on=["t1"])
    c.create_task("w", "t3", title="urgent", priority="high", touches=["auth"])
    ready = [t.id for t in c.ready("w")]
    # t2 is blocked on t1; t3 (high) sorts before t1 (normal)
    assert ready == ["t3", "t1"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_claim_is_atomic_one_winner():
    c = _coordinator()
    c.create_task("w", "t1", title="base")
    won = await c.claim("w", "t1", worker="dev-1", role="developer")
    assert won.status == "in_progress" and won.assignee == "dev-1"
    # a second claimer loses
    with pytest.raises(WeaveConflict):
        await c.claim("w", "t1", worker="dev-2", role="developer")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_claim_respects_role_gate():
    c = _coordinator()
    c.create_task("w", "t1")
    # the lifecycle only lets developer/architect/integrator take pending->in_progress
    with pytest.raises(WeaveForbidden):
        await c.claim("w", "t1", worker="pm", role="manager")
    won = await c.claim("w", "t1", worker="dev-1", role="developer")
    assert won.status == "in_progress"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_touches_conflict_defers_the_second_task():
    c = _coordinator()
    c.create_task("w", "a", touches=["authmod"])
    c.create_task("w", "b", touches=["authmod"])
    await c.claim("w", "a", worker="dev-1", role="developer")
    # b overlaps a's modules → not ready, and a claim is refused
    assert "b" not in [t.id for t in c.ready("w")]
    with pytest.raises(WeaveConflict):
        await c.claim("w", "b", worker="dev-2", role="developer")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_claim_blocked_on_unmet_dependency():
    c = _coordinator()
    c.create_task("w", "t1")
    c.create_task("w", "t2", depends_on=["t1"])
    with pytest.raises(WeaveConflict):
        await c.claim("w", "t2", worker="dev-1", role="developer")
    # once t1 is done, t2 becomes claimable
    t1 = c.get("w", "t1")
    t1.status = "done"
    c.store.save("w", t1)
    assert "t2" in [t.id for t in c.ready("w")]
    assert (await c.claim("w", "t2", worker="dev-1", role="developer")).status == "in_progress"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_brief_carries_task_and_dependency_status():
    c = _coordinator()
    c.create_task("w", "t1", title="base")
    c.create_task("w", "t2", title="needs t1", depends_on=["t1"], change_request="CR-1",
                  description="do the thing", touches=["mod"])
    b = await c.brief("w", "t2")
    assert b["task"]["title"] == "needs t1"
    assert b["change_request"] == "CR-1"
    assert b["touches"] == ["mod"]
    assert b["depends_on"] == [{"id": "t1", "status": "pending"}]
    assert b["precedent"] == []          # no rag → no precedent, not an error
    with pytest.raises(WeaveNotFound):
        await c.brief("w", "nope")


class _FakeRag:
    """Records emit_decision_trace and returns canned precedent."""

    def __init__(self):
        self.decisions = []
        self.precedents = [{"decision_trace": "used JWT before", "score": 0.9}]

    async def emit_decision_trace(self, src, tgt, rt, rc, upsert=True):
        self.decisions.append((src, tgt, rt, rc.decision_trace))

        class _GD:
            outcome = "PASS"
            audit = {"outcome": "PASS"}
        return _GD()

    async def find_precedents(self, query, top_k=10, min_confidence=0.0):
        return self.precedents[:top_k]


def _coordinator_with_rag(with_integration=False):
    rag = _FakeRag()
    lifecycle = LifecycleService(InMemoryLifecycleStore())
    preset.install("w", lifecycle_service=lifecycle)
    from weave.team.integration import InMemoryIntegrationStore
    c = WeaveCoordinator(
        InMemoryWeaveTaskStore(), lifecycle_service=lifecycle, rag_resolver=lambda ws: rag,
        integration_store=InMemoryIntegrationStore() if with_integration else None)
    return c, rag


async def _to_approved(c, tid="t1"):
    """Drive a fresh task pending → in_progress → review → approved."""
    c.create_task("w", tid, title="auth")
    await c.claim("w", tid, worker="dev-1", role="developer")
    await c.open_pull_request("w", tid, role="developer")
    await c.advance_task("w", tid, "approved", role="architect")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_brief_includes_precedent_when_available():
    c, rag = _coordinator_with_rag()
    c.create_task("w", "t1", title="add auth", description="jwt login")
    b = await c.brief("w", "t1")
    assert b["precedent"] == rag.precedents      # orient: real precedent for the agent


@pytest.mark.offline
@pytest.mark.asyncio
async def test_record_decision_dual_writes():
    c, rag = _coordinator_with_rag()
    out = await c.record_decision(
        "w", src="architect", tgt="RFC-1", relation="designs",
        decision_trace="chose Postgres over Mongo", by="architect",
        rationale="relational + transactions")
    assert out["outcome"] == "PASS" and out["tgt"] == "RFC-1"
    assert rag.decisions[-1][1] == "RFC-1"       # it reached emit_decision_trace


@pytest.mark.offline
@pytest.mark.asyncio
async def test_record_decision_is_must_succeed():
    # a raising emit is NOT swallowed (unlike cohermes's best-effort telemetry)
    class _BadRag:
        async def emit_decision_trace(self, *a, **k):
            raise RuntimeError("decision index down")

    lifecycle = LifecycleService(InMemoryLifecycleStore())
    preset.install("w", lifecycle_service=lifecycle)
    c = WeaveCoordinator(InMemoryWeaveTaskStore(), lifecycle_service=lifecycle,
                         rag_resolver=lambda ws: _BadRag())
    with pytest.raises(RuntimeError):
        await c.record_decision("w", src="a", tgt="b", relation="decided",
                                decision_trace="x", by="a")


# ── P2 · the planning gate ──────────────────────────────────────────────────

@pytest.mark.offline
@pytest.mark.asyncio
async def test_publish_plan_signs_then_releases_tasks():
    c, rag = _coordinator_with_rag()
    out = await c.publish_plan(
        "w", plan_ref="RFC-1", by="architect", role="architect", plan_kind="RFC",
        summary="auth subsystem",
        tasks=[{"id": "t1", "title": "jwt"},
               {"id": "t2", "title": "login", "depends_on": ["t1"]}])
    assert out["plan_ref"] == "RFC-1" and out["tasks"] == ["t1", "t2"]
    assert out["decision"]["outcome"] == "PASS"
    # the signature is a recorded decision on the plan ref (dual-write)
    assert rag.decisions[-1][1] == "RFC-1"
    # the tasks are now on the queue; the dependency is honoured
    assert [t.id for t in c.ready("w")] == ["t1"]
    # each task traces back to the plan that authorised it
    assert c.get("w", "t2").change_request == "RFC-1"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_publish_plan_is_role_gated():
    c, rag = _coordinator_with_rag()
    with pytest.raises(WeaveForbidden):
        await c.publish_plan("w", plan_ref="R", by="dev", role="developer", tasks=[])
    # a missing role fails CLOSED (no permissive fall-through)
    with pytest.raises(WeaveForbidden):
        await c.publish_plan("w", plan_ref="R", by="anon", role=None, tasks=[])
    # the gate refused before signing anything
    assert rag.decisions == []


# ── P3 · the artifact chain (Task → Commit* → PullRequest → Review*) ─────────

@pytest.mark.offline
@pytest.mark.asyncio
async def test_artifact_chain_from_claim_to_learning():
    c, rag = _coordinator_with_rag()
    c.create_task("w", "t1", title="auth", touches=["auth"])
    await c.claim("w", "t1", worker="dev-1", role="developer")     # → in_progress
    await c.record_commit("w", "t1", sha="abc12345", subject="add jwt")
    pr = await c.open_pull_request("w", "t1", branch="feat/t1", url="http://pr/1", role="developer")
    assert pr["status"] == "review" and pr["pull_request"]["branch"] == "feat/t1"
    await c.record_review("w", "t1", verdict="approve", by="architect")
    await c.record_learning("w", insight="jwt lib X is solid", task_id="t1", by="developer")

    chain = c.trace_chain("w", "t1")
    assert chain["task"]["status"] == "review"
    assert [x["sha"] for x in chain["commits"]] == ["abc12345"]
    assert chain["pull_request"]["branch"] == "feat/t1"
    assert chain["reviews"][0]["verdict"] == "approve"
    assert chain["learnings"] == ["jwt lib X is solid"]
    # the learning is a must-succeed decision (precedent for future tasks)
    assert rag.decisions[-1][1] == "t1"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_open_pr_requires_in_progress_and_is_single():
    c, _ = _coordinator_with_rag()
    c.create_task("w", "t1")                                       # pending
    with pytest.raises(WeaveConflict):
        await c.open_pull_request("w", "t1", role="developer")     # not in_progress
    await c.claim("w", "t1", worker="d", role="developer")
    await c.open_pull_request("w", "t1", role="developer")
    with pytest.raises(WeaveConflict):
        await c.open_pull_request("w", "t1", role="developer")     # already has a PR


@pytest.mark.offline
@pytest.mark.asyncio
async def test_open_pr_is_role_gated_and_review_needs_a_pr():
    c, _ = _coordinator_with_rag()
    c.create_task("w", "t1")
    await c.claim("w", "t1", worker="d", role="developer")
    # the lifecycle only lets developer/architect take in_progress → review
    with pytest.raises(WeaveForbidden):
        await c.open_pull_request("w", "t1", role="manager")
    # and a review needs a PR to review
    with pytest.raises(WeaveConflict):
        await c.record_review("w", "t1", verdict="approve")


# ── P4 · the integration merge gate ─────────────────────────────────────────

@pytest.mark.offline
@pytest.mark.asyncio
async def test_advance_task_is_role_gated():
    c, _ = _coordinator_with_rag(with_integration=True)
    c.create_task("w", "t1")
    await c.claim("w", "t1", worker="d", role="developer")     # in_progress
    await c.open_pull_request("w", "t1", role="developer")     # review
    # a developer can't approve its own PR (review → approved is architect/manager)
    with pytest.raises(WeaveForbidden):
        await c.advance_task("w", "t1", "approved", role="developer")
    out = await c.advance_task("w", "t1", "approved", role="architect")
    assert out["to"] == "approved" and c.get("w", "t1").status == "approved"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_merge_gate_promotes_only_on_a_green_run():
    c, rag = _coordinator_with_rag(with_integration=True)
    await _to_approved(c, "t1")
    c.register_environment("w", "env", name="staging", url="http://app")

    # nothing green yet → merge blocked
    with pytest.raises(WeaveConflict):
        await c.promote("w", "t1", env_id="env", role="integrator")
    # a red run doesn't unblock
    await c.run_integration("w", "env", tasks=["t1"], passed=False, kind="e2e")
    with pytest.raises(WeaveConflict):
        await c.promote("w", "t1", env_id="env", role="integrator")
    # green run present, but only the Integrator may promote
    await c.run_integration("w", "env", tasks=["t1"], passed=True, kind="e2e")
    with pytest.raises(WeaveForbidden):
        await c.promote("w", "t1", env_id="env", role="developer")
    # Integrator promotes to done, recorded as a decision
    out = await c.promote("w", "t1", env_id="env", role="integrator")
    assert out["status"] == "done" and c.get("w", "t1").status == "done"
    assert out["decision"]["outcome"] == "PASS"
    assert rag.decisions[-1][1] == "t1"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_promote_requires_an_approved_task():
    c, _ = _coordinator_with_rag(with_integration=True)
    c.create_task("w", "t1")
    await c.claim("w", "t1", worker="d", role="developer")     # in_progress, not approved
    c.register_environment("w", "env")
    await c.run_integration("w", "env", tasks=["t1"], passed=True)
    with pytest.raises(WeaveConflict):
        await c.promote("w", "t1", env_id="env", role="integrator")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_deploy_and_run_need_a_declared_environment():
    c, _ = _coordinator_with_rag(with_integration=True)
    c.create_task("w", "t1")
    with pytest.raises(WeaveNotFound):
        await c.deploy("w", "ghost", tasks=["t1"])
    with pytest.raises(WeaveNotFound):
        await c.run_integration("w", "ghost", tasks=["t1"], passed=True)


# ── P4 · the two-tier automated review pass ─────────────────────────────────

@pytest.mark.offline
@pytest.mark.asyncio
async def test_review_pass_flags_architecture_touching_prs():
    c, _ = _coordinator_with_rag(with_integration=True)
    # a clean PR (touches a plain module) → auto-approve, no architect needed
    c.create_task("w", "t1", title="ui tweak", touches=["web"])
    await c.claim("w", "t1", worker="d", role="developer")
    await c.open_pull_request("w", "t1", role="developer")
    clean = await c.review_pass("w", "t1")
    assert clean["verdict"] == "approve" and clean["requires_architect"] is False

    # a PR touching an architecture-sensitive module → flagged for the Architect
    c.create_task("w", "t2", title="auth change", touches=["auth"])
    await c.claim("w", "t2", worker="d", role="developer")
    await c.open_pull_request("w", "t2", role="developer")
    flagged = await c.review_pass("w", "t2")
    assert flagged["verdict"] == "flag" and flagged["requires_architect"] is True
    assert c.get("w", "t2").reviews[-1]["by"] == "review-agent"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_review_pass_needs_a_pr():
    c, _ = _coordinator_with_rag(with_integration=True)
    c.create_task("w", "t1", touches=["web"])
    with pytest.raises(WeaveConflict):
        await c.review_pass("w", "t1")
