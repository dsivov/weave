"""P3 · M3 — the autonomous developer loop + the D9 token boundary.

The reasoning (`claude -p`) and git steps are injected, so the loop is
deterministic: a FakeClient scripts the ready-set + control-state, and we assert
the loop drains the queue (one PR per task, never merging), halts cleanly on
pause/stop, skips lost claims, and records a learning when work doesn't pass.
The preflight is checked for scrubbing + subscription-only refusal.
"""

from __future__ import annotations

import os

import pytest

from weave.team.worker import (
    ClaimConflict, RunResult, ShellGit, SubscriptionAuthError, build_prompt,
    preflight_subscription_auth, run_worker, scrub_api_auth,
)


# ── the brief → prompt step ──────────────────────────────────────────────────
# A raw JSON brief handed to `claude -p` yields a description of the work rather
# than the work, so the prompt must actually instruct.

BRIEF = {
    "task": {"id": "t1", "title": "add sentence_count()", "description": "split on . ! ?"},
    "change_request": "CR-3 textkit v2",
    "touches": ["textkit"],
    "depends_on": [{"id": "t0", "status": "done"}],
    "precedent": [{"decision_trace": "we kept helpers pure and side-effect free"}],
}


@pytest.mark.offline
def test_prompt_carries_the_task_and_its_context():
    p = build_prompt(BRIEF)
    assert "add sentence_count()" in p and "split on . ! ?" in p
    assert "CR-3 textkit v2" in p
    assert "`textkit`" in p                       # the modules it may touch
    assert "t0 (done)" in p                       # dependency status
    assert "pure and side-effect free" in p       # precedent reaches the agent


@pytest.mark.offline
def test_prompt_tells_the_agent_to_test_and_to_leave_git_alone():
    p = build_prompt(BRIEF)
    assert "add or update tests" in p.lower()
    assert "run the test suite" in p.lower()
    # git is the worker's job — an agent that commits/branches breaks the chain
    assert "do not commit" in p.lower() and "worker handles git" in p.lower()
    assert "reuse what exists" in p.lower()       # the reuse-first guardrail


@pytest.mark.offline
def test_prompt_survives_a_bare_brief():
    p = build_prompt({"task": {"id": "t9", "title": "do the thing"}})
    assert "do the thing" in p and "## How to work" in p


# ── D9 token boundary ────────────────────────────────────────────────────────

@pytest.mark.offline
def test_scrub_removes_api_bedrock_vertex_but_keeps_subscription():
    env = {"ANTHROPIC_API_KEY": "sk-x", "AWS_ACCESS_KEY_ID": "a",
           "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-keep",
           "PATH": "/usr/bin"}
    out = scrub_api_auth(env)
    assert "ANTHROPIC_API_KEY" not in out and "AWS_ACCESS_KEY_ID" not in out
    assert "CLAUDE_CODE_USE_BEDROCK" not in out
    assert out["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-keep" and out["PATH"] == "/usr/bin"


@pytest.mark.offline
def test_preflight_accepts_subscription_and_returns_scrubbed_env():
    env = {"ANTHROPIC_API_KEY": "sk-x", "CLAUDE_CODE_OAUTH_TOKEN": "oauth"}
    scrubbed = preflight_subscription_auth(
        env=env, status_fn=lambda e: "Login Method: Claude Max (subscription)")
    assert "ANTHROPIC_API_KEY" not in scrubbed


@pytest.mark.offline
def test_preflight_refuses_when_status_shows_api_key():
    with pytest.raises(SubscriptionAuthError):
        preflight_subscription_auth(env={}, status_fn=lambda e: "Auth: API key (sk-...)")


@pytest.mark.offline
def test_preflight_refuses_bedrock_flag_outright():
    with pytest.raises(SubscriptionAuthError):
        preflight_subscription_auth(
            env={"CLAUDE_CODE_USE_BEDROCK": "1"}, status_fn=lambda e: "Claude Max")


# `claude auth status --json` is the probe a headless worker can actually run
# (`/status` is an in-session slash command). These pin its JSON contract.

def _auth_json(**over) -> str:
    import json

    payload = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
               "email": "dev@example.com", "subscriptionType": "max"}
    payload.update(over)
    return json.dumps(payload)


@pytest.mark.offline
def test_preflight_accepts_a_first_party_subscription_seat():
    scrubbed = preflight_subscription_auth(
        env={"ANTHROPIC_API_KEY": "sk-x", "PATH": "/usr/bin"},
        status_fn=lambda e: _auth_json())
    assert "ANTHROPIC_API_KEY" not in scrubbed and scrubbed["PATH"] == "/usr/bin"


@pytest.mark.offline
def test_preflight_refuses_a_logged_out_seat():
    with pytest.raises(SubscriptionAuthError, match="not logged in"):
        preflight_subscription_auth(env={}, status_fn=lambda e: _auth_json(loggedIn=False))


@pytest.mark.offline
@pytest.mark.parametrize("provider", ["bedrock", "vertex"])
def test_preflight_refuses_a_non_first_party_provider(provider):
    with pytest.raises(SubscriptionAuthError, match=provider):
        preflight_subscription_auth(
            env={}, status_fn=lambda e: _auth_json(apiProvider=provider))


@pytest.mark.offline
def test_preflight_refuses_an_api_key_seat():
    with pytest.raises(SubscriptionAuthError, match="API key"):
        preflight_subscription_auth(
            env={}, status_fn=lambda e: _auth_json(authMethod="apiKey", subscriptionType=""))


@pytest.mark.offline
def test_preflight_refuses_a_seat_without_a_subscription():
    with pytest.raises(SubscriptionAuthError, match="no Claude subscription"):
        preflight_subscription_auth(
            env={}, status_fn=lambda e: _auth_json(subscriptionType="none"))


# ── the loop ─────────────────────────────────────────────────────────────────

class FakeClient:
    """Scripts ready-sets + control-states and records what the loop did."""

    def __init__(self, ready_batches, *, controls=None, claim_conflicts=(),
                 tests_pass=True):
        self._ready = list(ready_batches)          # list of ready-set lists
        self._controls = list(controls or [])      # popped per heartbeat
        self._conflicts = set(claim_conflicts)     # task ids that 409
        self.tests_pass = tests_pass
        self.registered = None
        self.claims, self.prs, self.commits, self.decisions, self.learnings = ([] for _ in range(5))
        self.heartbeats = 0
        self.steps: list = []

    def register(self, worker_id, *, role="developer", host="", goal=""):
        self.registered = {"worker": worker_id, "role": role, "goal": goal}

    def heartbeat(self, worker_id, *, current_task=None, step=None):
        self.heartbeats += 1
        # Recorded, not merely tolerated: the step is the whole point of P10.5,
        # and a fake that swallowed it would let the loop stop reporting without
        # any test noticing.
        if step is not None:
            self.steps.append(step)
        ctl = self._controls.pop(0) if self._controls else "run"
        return {"control": ctl, "current_task": current_task, "step": step}

    def wait_for_ready(self, timeout=25.0):
        return self._ready.pop(0) if self._ready else []

    def claim(self, task_id, worker_id):
        if task_id in self._conflicts:
            raise ClaimConflict(task_id)
        self.claims.append(task_id)
        return {"id": task_id, "status": "in_progress"}

    def brief(self, task_id):
        return {"task": {"id": task_id}, "precedent": []}

    def record_commit(self, task_id, *, sha, subject=""):
        self.commits.append((task_id, sha))

    def open_pull_request(self, task_id, *, branch="", url=""):
        self.prs.append({"task": task_id, "branch": branch})
        return {"task": task_id, "status": "review"}

    def record_decision(self, *, src, tgt, relation, decision_trace):
        self.decisions.append((tgt, relation))

    def record_learning(self, insight, *, task_id=None):
        self.learnings.append((task_id, insight))


class FakeGit:
    def __init__(self, tests_pass=True):
        self.tests_pass = tests_pass
        self.branches = []

    def new_branch(self, branch):
        self.branches.append(branch)

    def run_tests(self):
        return self.tests_pass


def _runner(commit_sha="deadbeef", ok=True):
    return lambda brief: RunResult(ok=ok, summary=f"did {brief['task']['id']}",
                                   commit_sha=commit_sha)


@pytest.mark.offline
def test_loop_drains_queue_one_pr_per_task_never_merges():
    client = FakeClient([[{"id": "t1"}], [{"id": "t2"}]])   # two rounds, then empty
    out = run_worker(client, worker_id="dev-1", code_runner=_runner(), git=FakeGit(),
                     idle_rounds_before_exit=1)
    assert out["completed"] == ["t1", "t2"] and out["count"] == 2
    assert client.registered["worker"] == "dev-1"
    assert [p["task"] for p in client.prs] == ["t1", "t2"]
    assert [p["branch"] for p in client.prs] == ["weave/t1", "weave/t2"]
    assert [c[0] for c in client.commits] == ["t1", "t2"]
    assert [d[1] for d in client.decisions] == ["implemented", "implemented"]


@pytest.mark.offline
def test_loop_stops_cleanly_on_stop_control():
    # first heartbeat says stop → no work claimed
    client = FakeClient([[{"id": "t1"}]], controls=["stop"])
    out = run_worker(client, worker_id="dev-1", code_runner=_runner(), git=FakeGit())
    assert out["completed"] == [] and client.claims == []


@pytest.mark.offline
def test_loop_pauses_then_resumes():
    # pause once (idle), then run and drain t1, then empty → exit
    calls = {"n": 0}

    def sleeper(_):
        calls["n"] += 1
    client = FakeClient([[{"id": "t1"}]], controls=["pause", "run", "run", "run", "run"])
    out = run_worker(client, worker_id="dev-1", code_runner=_runner(), git=FakeGit(),
                     idle_rounds_before_exit=1, sleep=sleeper)
    assert calls["n"] == 1                      # paused exactly once
    assert out["completed"] == ["t1"]


@pytest.mark.offline
def test_loop_skips_task_lost_to_a_conflict():
    client = FakeClient([[{"id": "t1"}], [{"id": "t2"}]], claim_conflicts={"t1"})
    out = run_worker(client, worker_id="dev-1", code_runner=_runner(), git=FakeGit(),
                     idle_rounds_before_exit=1)
    assert out["completed"] == ["t2"]           # t1 was lost, no PR for it
    assert [p["task"] for p in client.prs] == ["t2"]


@pytest.mark.offline
def test_loop_records_learning_when_tests_fail_and_opens_no_pr():
    client = FakeClient([[{"id": "t1"}]])
    out = run_worker(client, worker_id="dev-1", code_runner=_runner(),
                     git=FakeGit(tests_pass=False), idle_rounds_before_exit=1)
    assert out["completed"] == [] and client.prs == []
    assert client.learnings and client.learnings[0][0] == "t1"


@pytest.mark.offline
def test_a_run_that_changed_nothing_opens_no_pull_request():
    """A session that edits no files must not put an empty PR in front of a
    human. The shell runner reports that as ok=False; the loop must honour it
    even though the test suite still passes on the untouched tree."""
    client = FakeClient([[{"id": "t1"}]])
    barren = lambda brief: RunResult(ok=False, summary="the session produced no change to commit.")  # noqa: E731
    out = run_worker(client, worker_id="dev-1", code_runner=barren,
                     git=FakeGit(tests_pass=True), idle_rounds_before_exit=1)
    assert out["completed"] == [] and client.prs == [] and client.commits == []
    assert "no change to commit" in client.learnings[0][1]


# ── ShellGit against a real repository ───────────────────────────────────────
# This one shells out for real: the behaviour worth protecting is which commit
# the branch starts from, and a fake git would just re-assert the fake.

def _git(repo, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          env={"HOME": str(repo), "PATH": os.environ.get("PATH", ""),
                               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "seed.txt").write_text("seed\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "seed")
    return r


@pytest.mark.offline
def test_each_task_branches_from_base_not_from_the_previous_task(repo):
    """Task branches must not stack. Branching off the last task's branch makes
    every PR carry the previous task's commits, so a reviewer can no longer see
    what one task changed."""
    git = ShellGit(workdir=str(repo))
    base = _git(repo, "rev-parse", "main").stdout.strip()

    git.new_branch("weave/t1")
    (repo / "t1.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "t1")

    git.new_branch("weave/t2")
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base
    assert not (repo / "t1.txt").exists()          # t1's work is not in t2's PR


@pytest.mark.offline
def test_leftovers_from_an_abandoned_run_do_not_ride_into_the_next_branch(repo):
    """The worktree is the worker's own, so a half-finished edit from a failed
    task must not show up in the next task's diff."""
    git = ShellGit(workdir=str(repo))
    git.new_branch("weave/t1")
    (repo / "junk.txt").write_text("half-done\n")   # untracked leftover
    (repo / "seed.txt").write_text("modified\n")    # dirty tracked file

    git.new_branch("weave/t2")
    assert not (repo / "junk.txt").exists()
    assert (repo / "seed.txt").read_text() == "seed\n"
