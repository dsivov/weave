"""A broken test command must not become a false `Insight` (W9).

Two halves, and the second is the one that matters.

**The trigger:** `ShellGit.test_cmd` defaulted to `["python", …]`, and Debian,
Ubuntu and most containers ship `python3` with no `python` at all — so a dev
agent failed at the test step of every task it attempted.

**The defect:** the loop could not tell *"the test command could not run"* from
*"the tests failed"*, so it recorded a **learning** either way. P2 turned
learnings into `Insight` nodes, and `/ask/learnings` serves those to humans and
agents **as fact**. A machine missing an interpreter therefore did not merely
fail its work — it wrote a false statement about the repository into the graph,
where the next reader meets it as a finding, cited and traversable.

A wrong task outcome is recoverable. A fabricated insight is read later as
evidence, and nothing about it says it came from a misconfigured host.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from weave.team.worker import (
    DEFAULT_TEST_CMD,
    RunResult,
    ShellGit,
    UnrunnableTestCommand,
    run_worker,
)

pytestmark = pytest.mark.offline


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "w@example.com")
    _git(root, "config", "user.name", "W")
    (root / "README.md").write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


class RecordingClient:
    """Records what the loop reported, so the absence of a learning is provable."""

    def __init__(self, ready_batches):
        self._ready = list(ready_batches)
        self.learnings = []
        self.prs = []
        self.claims = []

    def register(self, worker_id, *, role="developer", host="", goal=""):
        pass

    def heartbeat(self, worker_id, *, current_task=None, step=None):
        return {"control": "run", "current_task": current_task}

    def wait_for_ready(self, timeout=25.0):
        return self._ready.pop(0) if self._ready else []

    def claim(self, task_id, worker_id):
        self.claims.append(task_id)
        return {"id": task_id, "status": "in_progress"}

    def brief(self, task_id):
        return {"task": {"id": task_id}, "precedent": []}

    def record_commit(self, task_id, *, sha, subject=""):
        pass

    def open_pull_request(self, task_id, *, branch="", url=""):
        self.prs.append(task_id)
        return {"task": task_id}

    def record_decision(self, *, src, tgt, relation, decision_trace):
        pass

    def record_learning(self, insight, *, task_id=None):
        self.learnings.append((task_id, insight))


class _Git:
    """A git seam whose `run_tests` behaves as asked."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.branches = []

    def new_branch(self, branch):
        self.branches.append(branch)

    def run_tests(self):
        if self.behaviour == "unavailable":
            raise UnrunnableTestCommand("no such interpreter: 'python'")
        return self.behaviour == "pass"


def _runner():
    return lambda brief: RunResult(ok=True, summary=f"did {brief['task']['id']}",
                                   commit_sha="abc123")


# ── the defect: a could-not-run must write no learning ───────────────────────


def test_an_unrunnable_test_command_records_no_learning():
    """The assertion this file exists for.

    Nothing is written, because there is nothing true to write: the code was
    never tested.
    """
    client = RecordingClient([[{"id": "t1"}], [{"id": "t2"}]])

    out = run_worker(client, worker_id="dev-1", code_runner=_runner(),
                     git=_Git("unavailable"), max_iterations=3,
                     sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert client.learnings == [], (
        "a machine that could not run its tests wrote a statement about the "
        f"code into the graph: {client.learnings}"
    )
    assert out["completed"] == []
    assert "halted" in out, "the operational fault was swallowed"


def test_a_genuine_failure_still_records_a_learning():
    """The other half — the distinction has to cut both ways, or it is just a
    silenced error. A test that ran and failed *is* a fact about the code."""
    client = RecordingClient([[{"id": "t1"}], []])

    run_worker(client, worker_id="dev-1", code_runner=_runner(),
               git=_Git("fail"), max_iterations=3,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert [t for t, _ in client.learnings] == ["t1"]
    assert client.prs == [], "a failing task opened a PR"


def test_a_pass_opens_a_pull_request_and_writes_no_learning():
    client = RecordingClient([[{"id": "t1"}], []])

    run_worker(client, worker_id="dev-1", code_runner=_runner(),
               git=_Git("pass"), max_iterations=3,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert client.prs == ["t1"]
    assert client.learnings == []


def test_the_loop_stops_rather_than_fabricating_one_insight_per_task():
    """Grinding on would produce a false insight for **every** queued task, so
    the queue's length would decide how much fiction entered the graph."""
    client = RecordingClient([[{"id": f"t{i}"}] for i in range(5)])

    run_worker(client, worker_id="dev-1", code_runner=_runner(),
               git=_Git("unavailable"), max_iterations=10,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert client.learnings == []
    assert len(client.claims) == 1, "the loop kept claiming work it could not test"


# ── the trigger: an interpreter that exists ──────────────────────────────────


def test_the_default_test_command_uses_an_interpreter_that_exists():
    """`sys.executable` rather than a bare `python`. The old default failed on
    every host shipping only `python3` — which is most of them."""
    assert DEFAULT_TEST_CMD[0] == sys.executable
    assert DEFAULT_TEST_CMD[0] != "python"
    assert DEFAULT_TEST_CMD[1:] == ["-m", "pytest", "-q"]


def test_shellgit_defaults_to_that_command(repo):
    assert ShellGit(workdir=str(repo)).test_cmd == DEFAULT_TEST_CMD


def test_a_missing_command_raises_rather_than_reading_as_failure(repo):
    """The seam that makes the distinction possible. Returning False here is what
    made every misconfiguration look like broken code."""
    git = ShellGit(workdir=str(repo), test_cmd=["definitely-not-a-real-binary"])

    with pytest.raises(UnrunnableTestCommand) as exc:
        git.run_tests()
    assert "definitely-not-a-real-binary" in str(exc.value)


def test_a_command_that_runs_and_fails_returns_false_not_raises(repo):
    """`false` exists and exits 1: a real failure, reported as one."""
    assert ShellGit(workdir=str(repo), test_cmd=["false"]).run_tests() is False


def test_a_command_that_runs_and_passes_returns_true(repo):
    assert ShellGit(workdir=str(repo), test_cmd=["true"]).run_tests() is True


def test_command_not_found_via_exit_code_is_also_unavailable(repo):
    """Some runners surface "command not found" as exit 127 rather than raising.
    Still not a test result."""
    git = ShellGit(workdir=str(repo), test_cmd=["sh", "-c", "exit 127"])

    with pytest.raises(UnrunnableTestCommand):
        git.run_tests()
