"""A pause lands between steps, on a clean worktree (M5 gate).

The gate says *a pause is honoured between steps, with a clean `git status`
asserted*. That wording matters: a pause that stopped a worker mid-edit would
leave half-written files in a worktree, and the next person to look would find a
change nobody made deliberately and no record of who to ask. "Paused" has to mean
"between two complete things", not "frozen wherever it happened to be".

Two halves are asserted, and neither implies the other:

1. the loop **checks control before starting a step**, so a pause takes effect at
   a boundary rather than wherever the code happened to be;
2. the worktree is **actually clean** afterwards — asserted against a real
   repository with a real `git status`, because that is the thing a person walks
   up to.

The fakes are the ones `test_weave_worker.py` already uses (R10). What is new
here is a real `ShellGit` over a real repository, because a `FakeGit` cannot have
a dirty worktree and so cannot fail this test in the way that matters.

`test_cmd=["true"]` is passed deliberately: this file is about *when a pause
lands*, not about running a project's tests, and `ShellGit`'s default
`["python", "-m", "pytest", "-q"]` would make the assertions depend on whether
the host has a `python` on PATH — see watch item W9.
"""

from __future__ import annotations

import subprocess

import pytest

from weave.team.worker import RunResult, ShellGit, run_worker

pytestmark = pytest.mark.offline


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _status(repo) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "worker@example.com")
    _git(root, "config", "user.name", "Worker")
    (root / "README.md").write_text("start\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


class PausingClient:
    """A client whose control state is *state*, not a script.

    An earlier version popped from a list of controls, which broke: the loop
    heartbeats again mid-task (`current_task=`), so a scripted list is consumed
    in an order the test did not intend and the assertions drift from what is
    being claimed. Modelling it as a condition — *paused once N steps have
    completed* — is both robust to the number of heartbeats and closer to what a
    supervisor actually does: they pause a worker that is mid-run, and the pause
    takes effect at the next boundary.
    """

    def __init__(self, ready_batches, *, pause_after_steps=None,
                 stop_after_steps=None, paused_from_start=False):
        self._ready = list(ready_batches)
        self._pause_after = pause_after_steps
        self._stop_after = stop_after_steps
        self._paused_from_start = paused_from_start
        self.heartbeats = 0
        self.claims = []
        self.steps_started = 0
        self.steps_completed = 0
        self.observed = []          # (control, steps_completed) at each heartbeat

    def resume(self) -> None:
        """Lift the hold. Called from the loop's `sleep` hook in the resume test,
        which is exactly when a real supervisor's resume would be observed: while
        the worker is idling on a pause, between steps."""
        self._paused_from_start = False
        self._pause_after = None

    def _control(self) -> str:
        if self._paused_from_start:
            return "pause"
        if self._stop_after is not None and self.steps_completed >= self._stop_after:
            return "stop"
        if self._pause_after is not None and self.steps_completed >= self._pause_after:
            return "pause"
        return "run"

    def register(self, worker_id, *, role="developer", host="", goal=""):
        pass

    def heartbeat(self, worker_id, *, current_task=None):
        self.heartbeats += 1
        ctl = self._control()
        self.observed.append((ctl, self.steps_completed))
        return {"control": ctl, "current_task": current_task}

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
        return {"task": task_id, "status": "review"}

    def record_decision(self, *, src, tgt, relation, decision_trace):
        pass

    def record_learning(self, insight, *, task_id=None):
        pass


def _committing_runner(repo, client):
    """A step that edits and **commits** — a complete unit of work.

    The commit is the point: a step that ended with uncommitted changes would
    leave the worktree dirty by its own design, and the test would be measuring
    the fake rather than the loop.
    """

    def run(brief):
        client.steps_started += 1
        task_id = brief["task"]["id"]
        (repo / f"{task_id}.py").write_text(f"# work for {task_id}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"work for {task_id}")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                             capture_output=True, text=True).stdout.strip()
        client.steps_completed += 1
        return RunResult(ok=True, summary=f"did {task_id}", commit_sha=sha)

    return run


# ── the pause lands at a boundary ────────────────────────────────────────────


def test_a_pause_stops_the_worker_before_it_starts_a_step(repo):
    """Control is read at the top of the loop, so a pause arriving between steps
    prevents the *next* one rather than interrupting the current one."""
    client = PausingClient(
        ready_batches=[[{"id": "t1"}], [{"id": "t2"}]], pause_after_steps=1,
    )
    git = ShellGit(workdir=str(repo), test_cmd=["true"])

    run_worker(client, worker_id="dev-1", code_runner=_committing_runner(repo, client),
               git=git, max_iterations=3, sleep=lambda _s: None,
               idle_rounds_before_exit=1)

    assert client.steps_started == 1, "the paused round started a second step"
    assert client.claims == ["t1"], "a paused worker claimed more work"


def test_the_worktree_is_clean_when_the_worker_pauses(repo):
    """The gate criterion, against a real `git status`.

    This is what someone actually walks up to: no half-written file, nothing
    staged, nothing to explain.
    """
    client = PausingClient(
        ready_batches=[[{"id": "t1"}], [{"id": "t2"}]], pause_after_steps=1,
    )

    run_worker(client, worker_id="dev-1", code_runner=_committing_runner(repo, client),
               git=ShellGit(workdir=str(repo), test_cmd=["true"]), max_iterations=3,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert _status(repo) == "", (
        f"the worktree is dirty after a pause — a partial edit was left behind:\n"
        f"{_status(repo)}"
    )


def test_the_completed_step_was_not_rolled_back(repo):
    """The other side of clean: pausing must not discard finished work either.
    A pause that reverted the step it interrupted would be just as surprising."""
    client = PausingClient(
        ready_batches=[[{"id": "t1"}], [{"id": "t2"}]], pause_after_steps=1,
    )

    run_worker(client, worker_id="dev-1", code_runner=_committing_runner(repo, client),
               git=ShellGit(workdir=str(repo), test_cmd=["true"]), max_iterations=3,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert (repo / "t1.py").exists(), "the finished step's work disappeared"
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "work for t1" in log


def test_a_pause_observed_before_any_work_starts_nothing(repo):
    """Paused from the outset: no claim, no branch, no edit."""
    client = PausingClient(ready_batches=[[{"id": "t1"}]], paused_from_start=True)

    run_worker(client, worker_id="dev-1", code_runner=_committing_runner(repo, client),
               git=ShellGit(workdir=str(repo), test_cmd=["true"]), max_iterations=2,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert client.steps_started == 0
    assert client.claims == []
    assert _status(repo) == ""


def test_a_stop_mid_task_still_leaves_a_clean_worktree(repo):
    """`stop` is checked again *after* the step runs, so a supervisor halting a
    worker mid-task lands after the step completed and committed — not inside it.
    """
    client = PausingClient(
        ready_batches=[[{"id": "t1"}], [{"id": "t2"}]], stop_after_steps=1,
    )

    run_worker(client, worker_id="dev-1", code_runner=_committing_runner(repo, client),
               git=ShellGit(workdir=str(repo), test_cmd=["true"]), max_iterations=3,
               sleep=lambda _s: None, idle_rounds_before_exit=1)

    assert _status(repo) == ""
    assert (repo / "t1.py").exists()


def test_resuming_lets_the_worker_carry_on(repo):
    """A pause is not terminal. If it were, "pause" would be a worse-documented
    "stop" and nobody would use it."""
    client = PausingClient(
        ready_batches=[[{"id": "t1"}], [{"id": "t2"}], []], paused_from_start=True,
    )

    # The loop calls `sleep` while it is idling on a pause — which is precisely
    # when a supervisor's resume would be observed. Lifting the hold there makes
    # this a real pause→resume rather than a worker that was never held.
    run_worker(client, worker_id="dev-1", code_runner=_committing_runner(repo, client),
               git=ShellGit(workdir=str(repo), test_cmd=["true"]), max_iterations=6,
               sleep=lambda _s: client.resume(), idle_rounds_before_exit=1)

    assert ("pause", 0) in client.observed, "the worker was never actually paused"
    assert client.steps_started >= 1, "the worker never resumed after the hold lifted"
    assert _status(repo) == ""


# ── the loop reads control before it acts ────────────────────────────────────


def test_control_is_checked_before_the_ready_set_is_drawn(repo):
    """Ordering, not just outcome: heartbeat → control → *then* work. Reading
    control after claiming would mean a paused worker still holds a task."""
    import inspect

    source = inspect.getsource(run_worker)
    control_at = source.index('ctl = client.heartbeat')
    ready_at = source.index("client.wait_for_ready")
    claim_at = source.index("client.claim(")

    assert control_at < ready_at < claim_at, (
        "the loop draws work before reading its control state — a paused worker "
        "would claim a task and then idle holding it"
    )
