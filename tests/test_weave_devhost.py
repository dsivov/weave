"""P8 · M8 — dev hosts: machines that carry autonomous developers.

The registry is the Weave-side contract (presence, control, and the desired worker
count a machine reconciles to); the daemon is the machine-side loop that reads
it. Both are exercised without Docker: the container runtime is a seam, so what
gets asserted is the *decisions* — how many containers, which ones, and when the
daemon refuses to start any at all.
"""

from __future__ import annotations

import asyncio
import os
from urllib.error import HTTPError

import pytest

from weave.devhost.registry import (
    DevHostRegistry, HostOwnershipError, InMemoryDevHostStore,
)
from weave.team.project import (
    DEFAULT_TEST_COMMAND, InMemoryWeaveProjectStore, ProjectService,
)
from weave.devhost.daemon import (
    CREDENTIALS_FILE, Reconciler, SEAT_TOKEN_VAR, WorkerSpec, _branch_publisher,
    prepare_seat_dir, read_seat_token, refresh_seat_dirs, run_devhost,
    seat_expiry, task_branches,
)
from weave.team.worker import SubscriptionAuthError


def _registry(now=None):
    clock = now or (lambda: 1000.0)
    return DevHostRegistry(InMemoryDevHostStore(), now=clock)


def _register(reg, host_id="host-berlin", **kw):
    return asyncio.run(reg.register("ws", host_id, **kw))


# ── the registry ─────────────────────────────────────────────────────────────

@pytest.mark.offline
def test_a_host_reconciles_to_what_the_team_asked_for():
    """Scaling is state the machine reads, not a command sent to it — that is
    what lets a host behind NAT still be scaled from the board."""
    reg = _registry()
    _register(reg, seat="ok")
    assert reg.heartbeat("ws", "host-berlin")["desired_workers"] == 0
    reg.scale("ws", "host-berlin", 3)
    assert reg.heartbeat("ws", "host-berlin")["desired_workers"] == 3


@pytest.mark.offline
def test_a_paused_or_drained_host_is_told_to_hold_nothing_new():
    reg = _registry()
    _register(reg, seat="ok")
    reg.scale("ws", "host-berlin", 4)
    for action, control in (("drain", "drain"), ("resume", "run"), ("pause", "pause")):
        reg.set_control("ws", "host-berlin", action)
        reply = reg.heartbeat("ws", "host-berlin")
        assert reply["control"] == control
        # the team's number is remembered, but only served while running
        assert reply["desired_workers"] == (4 if control == "run" else 0)


@pytest.mark.offline
def test_restarting_a_machine_does_not_undo_a_supervisors_stop():
    """A box that reboots must not talk its way back into service."""
    reg = _registry()
    _register(reg)
    reg.set_control("ws", "host-berlin", "stop")
    again = _register(reg)
    assert again.control == "stop" and again.status == "stopped"
    with pytest.raises(ValueError):
        reg.set_control("ws", "host-berlin", "resume")


@pytest.mark.offline
def test_the_desired_count_outlives_the_daemon_process():
    reg = _registry()
    _register(reg, seat="ok")
    reg.scale("ws", "host-berlin", 2)
    _register(reg, seat="ok")                       # daemon restarted
    assert reg.heartbeat("ws", "host-berlin")["desired_workers"] == 2


@pytest.mark.offline
def test_a_machine_cannot_be_hijacked_or_kept_alive_by_a_stranger():
    reg = _registry()
    _register(reg, owner="alice")
    with pytest.raises(HostOwnershipError):
        _register(reg, owner="mallory")
    with pytest.raises(HostOwnershipError):
        reg.heartbeat("ws", "host-berlin", owner="mallory")


@pytest.mark.offline
def test_a_silent_machine_reads_offline_without_losing_its_control_state():
    clock = {"t": 1000.0}
    reg = _registry(now=lambda: clock["t"])
    _register(reg, seat="ok")
    reg.set_control("ws", "host-berlin", "drain")
    clock["t"] += 10_000
    view = reg.list("ws")[0]
    assert view["status"] == "offline" and view["stale"] is True
    assert view["control"] == "drain"               # stored intent is untouched


@pytest.mark.offline
def test_seat_health_is_reported_so_the_board_can_say_why_a_machine_is_idle():
    reg = _registry()
    _register(reg, seat="ok")
    reg.heartbeat("ws", "host-berlin", seat="expired", seat_detail="token no longer valid")
    view = reg.list("ws")[0]
    assert view["seat"] == "expired" and "no longer valid" in view["seat_detail"]


@pytest.mark.offline
def test_a_nonsense_seat_state_is_recorded_as_unknown_not_stored_verbatim():
    reg = _registry()
    h = _register(reg, seat="definitely-fine")
    assert h.seat == "unknown"


# ── the reconciler ───────────────────────────────────────────────────────────

class FakeRuntime:
    def __init__(self, failing=()):
        self.started, self.stopped, self._running = [], [], []
        self._failing = set(failing)

    def start(self, worker_id, spec):
        if worker_id in self._failing:
            raise RuntimeError("image pull failed")
        self.started.append((worker_id, spec))
        self._running.append(worker_id)
        return f"cid-{worker_id}"

    def stop(self, worker_id):
        self.stopped.append(worker_id)
        if worker_id in self._running:
            self._running.remove(worker_id)

    def running(self):
        return sorted(self._running)


def _reconciler(runtime, max_workers=8):
    spec = WorkerSpec(image="img", server="http://cg", workspace="ws", workdir="/w")
    return Reconciler(runtime, "host-berlin", make_spec=lambda w: spec,
                      max_workers=max_workers)


@pytest.mark.offline
def test_reconcile_starts_exactly_the_missing_containers():
    rt = FakeRuntime()
    r = _reconciler(rt)
    out = r.reconcile(3)
    assert out["started"] == ["host-berlin-dev-1", "host-berlin-dev-2", "host-berlin-dev-3"]
    assert rt.running() == out["started"]
    # already at target — a second pass changes nothing
    assert r.reconcile(3)["started"] == []


@pytest.mark.offline
def test_scaling_down_keeps_the_low_numbered_workers():
    """Worker 1 stays put across a shrink, so a machine's board rows and logs
    stay readable instead of reshuffling on every change."""
    rt = FakeRuntime()
    r = _reconciler(rt)
    r.reconcile(4)
    out = r.reconcile(2)
    assert out["stopped"] == ["host-berlin-dev-4", "host-berlin-dev-3"]
    assert rt.running() == ["host-berlin-dev-1", "host-berlin-dev-2"]


@pytest.mark.offline
def test_one_container_that_will_not_start_does_not_sink_the_machine():
    rt = FakeRuntime(failing={"host-berlin-dev-2"})
    out = _reconciler(rt).reconcile(3)
    assert out["failed"] == ["host-berlin-dev-2"]
    assert set(out["started"]) == {"host-berlin-dev-1", "host-berlin-dev-3"}


@pytest.mark.offline
def test_the_machine_ceiling_is_enforced_locally():
    """Containers here share one subscription seat, so the machine caps itself
    rather than trusting whatever number the board sent."""
    rt = FakeRuntime()
    out = _reconciler(rt, max_workers=2).reconcile(50)
    assert len(out["started"]) == 2


@pytest.mark.offline
def test_a_worker_a_supervisor_stopped_is_not_quietly_restarted():
    """Two control planes meet here: the host count says how many developers, a
    per-worker stop says not that one. The machine must not undo the human."""
    rt = FakeRuntime()
    r = _reconciler(rt)
    r.reconcile(3)
    rt.stop("host-berlin-dev-2")                 # supervisor stopped it; container exits
    out = r.reconcile(3, held=["host-berlin-dev-2"])
    assert "host-berlin-dev-2" not in out["running"]
    assert out["started"] == []                  # and no substitute was started
    assert rt.running() == ["host-berlin-dev-1", "host-berlin-dev-3"]


@pytest.mark.offline
def test_a_held_worker_that_is_still_up_gets_taken_down():
    rt = FakeRuntime()
    r = _reconciler(rt)
    r.reconcile(2)
    out = r.reconcile(2, held=["host-berlin-dev-1"])
    assert out["stopped"] == ["host-berlin-dev-1"]


@pytest.mark.offline
def test_resuming_a_held_worker_brings_it_back_on_the_next_pass():
    rt = FakeRuntime()
    r = _reconciler(rt)
    r.reconcile(2, held=["host-berlin-dev-2"])
    assert rt.running() == ["host-berlin-dev-1"]
    out = r.reconcile(2)                         # supervisor resumed it
    assert out["started"] == ["host-berlin-dev-2"]


@pytest.mark.offline
def test_the_heartbeat_tells_the_host_which_of_its_workers_are_held():
    """The host cannot see the fleet view, so Weave has to name the held workers."""
    class FakeWorkers:
        def list(self, ws):
            return [{"id": "host-berlin-dev-1", "control": "run", "host": "host-berlin"},
                    {"id": "host-berlin-dev-2", "control": "stop", "host": "host-berlin"},
                    {"id": "host-berlin-dev-3", "control": "pause", "host": "host-berlin"},
                    {"id": "elsewhere-dev-1", "control": "stop", "host": "host-nyc"}]

    reg = DevHostRegistry(InMemoryDevHostStore(), now=lambda: 1000.0,
                          worker_registry=FakeWorkers())
    _register(reg, seat="ok")
    held = reg.heartbeat("ws", "host-berlin")["held_workers"]
    assert held == ["host-berlin-dev-2", "host-berlin-dev-3"]   # another box's is not ours


# ── the daemon loop ──────────────────────────────────────────────────────────

class FakeClient:
    """Scripts the heartbeat replies the daemon will receive."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.registered = None
        self.heartbeats = []

    def register_host(self, host_id, **kw):
        self.registered = (host_id, kw)
        return {"host": host_id}

    def heartbeat_host(self, host_id, **kw):
        self.heartbeats.append((host_id, kw))
        return self._replies.pop(0) if self._replies else {"control": "run",
                                                           "desired_workers": 0}


class FlakyClient(FakeClient):
    """A Weave that is down for the first *outages* heartbeats, then comes back."""

    def __init__(self, replies, outages, error=None):
        super().__init__(replies)
        self._left = outages
        self._error = error or ConnectionRefusedError("connection refused")
        self.registrations = 0

    def register_host(self, host_id, **kw):
        if self._left:
            self._left -= 1
            raise self._error
        self.registrations += 1
        return super().register_host(host_id, **kw)

    def heartbeat_host(self, host_id, **kw):
        if self._left:
            self._left -= 1
            raise self._error
        return super().heartbeat_host(host_id, **kw)


def _ok_preflight(**_):
    return {"PATH": "/usr/bin"}


def _run(client, runtime, rounds, **kw):
    kw.setdefault("preflight", _ok_preflight)
    kw.setdefault("seat_token", "sk-ant-oat-fake")
    kw.setdefault("image", "img")
    kw.setdefault("worktree_root", "/wt")
    return run_devhost(
        client, host_id="host-berlin", runtime=runtime, server="http://cg",
        workspace="ws", repo_root="/repo",
        rounds=rounds, sleep=lambda s: None, **kw)


@pytest.mark.offline
def test_the_daemon_starts_what_the_heartbeat_asks_for():
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2}])
    out = _run(client, rt, rounds=1)
    assert out["seat"] == "ok"
    assert rt.running() == ["host-berlin-dev-1", "host-berlin-dev-2"]


@pytest.mark.offline
def test_the_seat_is_propagated_into_every_container_and_metered_auth_is_not():
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1}])

    def preflight(**_):
        # A daemon started with a stray API key still must not hand one on.
        return {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-should-be-scrubbed"}

    _run(client, rt, rounds=1, preflight=preflight, seat_token="sk-ant-oat-fake")
    _, spec = rt.started[0]
    assert spec.env[SEAT_TOKEN_VAR] == "sk-ant-oat-fake"
    assert "ANTHROPIC_API_KEY" not in spec.env


@pytest.mark.offline
def test_the_machines_own_secrets_do_not_cross_into_a_container():
    """The daemon usually runs beside the server, so its environment holds the
    workspace's LLM keys and the JWT secret that mints supervisor roles. None of
    that belongs in something running an agent unattended with full write
    permission, and none of it is on the metered-auth denylist — so the env a
    container gets is composed from an allowlist, not inherited and filtered."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1}])

    def preflight(**_):
        return {"PATH": "/usr/bin",
                "TOKEN_SECRET": "mints-architect-tokens",
                "LLM_BINDING_API_KEY": "sk-proj-the-workspaces-openai-key",
                "HTTPS_PROXY": "http://proxy:3128"}

    _run(client, rt, rounds=1, preflight=preflight)
    _, spec = rt.started[0]
    assert "TOKEN_SECRET" not in spec.env
    assert "LLM_BINDING_API_KEY" not in spec.env
    # ...while the one thing a container legitimately needs from the host still
    # crosses: without a proxy it may have no route out at all.
    assert spec.env["HTTPS_PROXY"] == "http://proxy:3128"


# ── keeping a container's seat alive ─────────────────────────────────────────

def _seat(path, expires_at_ms):
    """Write a credential file with a given expiry."""
    import json as _json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump({"claudeAiOauth": {"accessToken": "x",
                                      "expiresAt": expires_at_ms}}, fh)


@pytest.mark.offline
def test_a_container_whose_credential_expired_is_re_seated(tmp_path):
    """A token lasts hours; a container can last days. Its copy is taken once at
    startup, so without this the worker keeps claiming tasks it cannot run —
    work sits in_progress forever with nothing in the log to say why."""
    host, seats = tmp_path / "host", tmp_path / "seats"
    _seat(str(host / CREDENTIALS_FILE), 9_000_000_000_000)      # host: fresh
    _seat(str(seats / "dev-1" / CREDENTIALS_FILE), 1_000)       # container: long expired

    assert refresh_seat_dirs(str(seats), ["dev-1"], source=str(host)) == ["dev-1"]
    assert seat_expiry(str(seats / "dev-1" / CREDENTIALS_FILE)) == 9_000_000_000.0


@pytest.mark.offline
def test_a_container_that_refreshed_itself_is_left_alone(tmp_path):
    """`claude` refreshes its own credential in place, and that result is often
    fresher than the host's. Overwriting it would take away a working seat."""
    host, seats = tmp_path / "host", tmp_path / "seats"
    _seat(str(host / CREDENTIALS_FILE), 5_000_000_000_000)
    _seat(str(seats / "dev-1" / CREDENTIALS_FILE), 9_000_000_000_000)   # newer

    assert refresh_seat_dirs(str(seats), ["dev-1"], source=str(host)) == []
    assert seat_expiry(str(seats / "dev-1" / CREDENTIALS_FILE)) == 9_000_000_000.0


@pytest.mark.offline
def test_a_corrupt_credential_is_replaced(tmp_path):
    """Seen in the wild: a container's file left with expiresAt 0 after a failed
    refresh. Unreadable must lose to readable, not win by being unparseable."""
    host, seats = tmp_path / "host", tmp_path / "seats"
    _seat(str(host / CREDENTIALS_FILE), 9_000_000_000_000)
    (seats / "dev-1").mkdir(parents=True)
    (seats / "dev-1" / CREDENTIALS_FILE).write_text("{ not json")

    assert refresh_seat_dirs(str(seats), ["dev-1"], source=str(host)) == ["dev-1"]


@pytest.mark.offline
def test_a_host_with_nothing_to_give_changes_nothing(tmp_path):
    seats = tmp_path / "seats"
    _seat(str(seats / "dev-1" / CREDENTIALS_FILE), 1_000)
    assert refresh_seat_dirs(str(seats), ["dev-1"], source=str(tmp_path / "no-host")) == []
    assert seat_expiry(str(seats / "dev-1" / CREDENTIALS_FILE)) == 1.0


@pytest.mark.offline
def test_a_worker_we_never_seated_is_skipped(tmp_path):
    host, seats = tmp_path / "host", tmp_path / "seats"
    _seat(str(host / CREDENTIALS_FILE), 9_000_000_000_000)
    seats.mkdir()
    assert refresh_seat_dirs(str(seats), ["dev-1"], source=str(host)) == []


# ── getting the work off the machine ─────────────────────────────────────────

@pytest.mark.offline
def test_a_workers_own_scratch_branch_is_not_published():
    """Each worktree sits on `weave/<worker>-base` between tasks. It holds no
    work and means nothing to anyone else."""
    local = ["weave/e1", "weave/host-berlin-dev-1-base", "weave/e2"]
    assert task_branches(local, ["host-berlin-dev-1"]) == ["weave/e1", "weave/e2"]


@pytest.mark.offline
def test_a_task_actually_called_base_is_still_published():
    """Matched by worker id, not by a name pattern — otherwise a task named
    `base` would silently never leave the machine."""
    local = ["weave/base", "weave/dev-1-base"]
    assert task_branches(local, ["dev-1"]) == ["weave/base"]


@pytest.mark.offline
def test_branches_are_published_before_a_stop_is_obeyed():
    """A machine told to stop has usually just finished something. Taking its
    containers down without pushing would strand exactly that work."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1},
                         {"control": "stop"}])
    calls = []
    _run(client, rt, rounds=2,
         publish_branches=lambda running, base: calls.append(list(running)) or ["weave/e1"])
    assert len(calls) == 2                      # including the round that stopped it
    assert rt.running() == []


@pytest.mark.offline
def test_a_publish_failure_never_takes_the_machine_down():
    """A repo this host cannot push to is a bad afternoon, not a reason to stop
    hosting developers."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1}])

    def explode(_running, _base):
        raise RuntimeError("permission denied (publickey)")

    out = _run(client, rt, rounds=1, publish_branches=explode)
    assert rt.running() == ["host-berlin-dev-1"]
    assert out["history"][0]["control"] == "run"


# ── the publisher against real repositories ──────────────────────────────────

def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=False)


def _origin_and_clone(tmp_path):
    """A bare origin with one commit on main, and a clone of it."""
    import subprocess

    seed, origin, clone = tmp_path / "seed", tmp_path / "origin.git", tmp_path / "clone"
    seed.mkdir()
    _git(".", "init", "-q", "-b", "main", str(seed))
    (seed / "README").write_text("seed\n")
    for args in (("add", "-A"), ("-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-qm", "seed")):
        _git(seed, *args)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=False)
    _git(seed, "push", "-q", str(origin), "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)],
                   capture_output=True, check=False)
    return origin, clone


def _commit_on(clone, branch, filename):
    _git(clone, "checkout", "-q", "-b", branch, "main")
    (clone / filename).write_text("work\n")
    _git(clone, "add", "-A")
    _git(clone, "-c", "user.email=w@w", "-c", "user.name=w", "commit", "-qm", branch)
    _git(clone, "checkout", "-q", "main")


@pytest.mark.offline
def test_a_task_branch_reaches_the_origin(tmp_path):
    """The whole point: after this, somebody else can fetch the work."""
    origin, clone = _origin_and_clone(tmp_path)
    _commit_on(clone, "weave/e1", "e1.txt")

    published = _branch_publisher(str(clone))(["dev-1"], "main")
    assert published == ["weave/e1"]
    assert "weave/e1" in _git(origin, "for-each-ref", "--format=%(refname:short)",
                              "refs/heads/").stdout


@pytest.mark.offline
def test_a_branch_with_no_work_on_it_yet_is_not_published(tmp_path):
    """The branch is cut when the task is claimed, long before there is anything
    on it. Publishing then would put a branch in the shared repo for every task
    ever started, including the ones that failed and never committed."""
    origin, clone = _origin_and_clone(tmp_path)
    _git(clone, "branch", "weave/e1", "main")          # cut, nothing committed

    assert _branch_publisher(str(clone))(["dev-1"], "main") == []
    assert "weave/e1" not in _git(origin, "for-each-ref", "--format=%(refname:short)",
                                  "refs/heads/").stdout


@pytest.mark.offline
def test_publishing_twice_pushes_once(tmp_path):
    """Every heartbeat runs this. It has to be quiet when there is nothing new."""
    _, clone = _origin_and_clone(tmp_path)
    _commit_on(clone, "weave/e1", "e1.txt")
    publish = _branch_publisher(str(clone))

    assert publish(["dev-1"], "main") == ["weave/e1"]
    assert publish(["dev-1"], "main") == []


@pytest.mark.offline
def test_new_work_on_a_published_branch_is_published_again(tmp_path):
    _, clone = _origin_and_clone(tmp_path)
    _commit_on(clone, "weave/e1", "e1.txt")
    publish = _branch_publisher(str(clone))
    publish(["dev-1"], "main")

    _git(clone, "checkout", "-q", "weave/e1")
    (clone / "more.txt").write_text("more\n")
    _git(clone, "add", "-A")
    _git(clone, "-c", "user.email=w@w", "-c", "user.name=w", "commit", "-qm", "more")
    _git(clone, "checkout", "-q", "main")

    assert publish(["dev-1"], "main") == ["weave/e1"]


@pytest.mark.offline
def test_the_scratch_branch_never_reaches_the_origin(tmp_path):
    origin, clone = _origin_and_clone(tmp_path)
    _commit_on(clone, "weave/dev-1-base", "scratch.txt")

    assert _branch_publisher(str(clone))(["dev-1"], "main") == []
    assert "weave/dev-1-base" not in _git(
        origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout


@pytest.mark.offline
def test_a_rejected_push_is_reported_not_forced(tmp_path):
    """Task branches are cut fresh from base, so a non-fast-forward means
    something unexpected is already published under that name. Overwriting
    someone else's history to resolve it is not this daemon's call."""
    origin, clone = _origin_and_clone(tmp_path)

    # somebody else publishes a diverging weave/e1 first
    other = tmp_path / "other"
    _git(".", "clone", "-q", str(origin), str(other))
    _commit_on(other, "weave/e1", "theirs.txt")
    _git(other, "push", "-q", "origin", "weave/e1")
    theirs = _git(other, "rev-parse", "weave/e1").stdout.strip()

    _commit_on(clone, "weave/e1", "ours.txt")
    assert _branch_publisher(str(clone))(["dev-1"], "main") == []
    # theirs is untouched
    assert _git(origin, "rev-parse", "weave/e1").stdout.strip() == theirs


@pytest.mark.offline
def test_a_machine_that_has_not_cloned_yet_publishes_nothing(tmp_path):
    assert _branch_publisher(str(tmp_path / "nothing-here"))(["dev-1"], "main") == []


# ── surviving a Weave outage ─────────────────────────────────────────

@pytest.mark.offline
def test_a_server_restart_does_not_kill_the_machine():
    """Weave restarts. If that took every dev host down with it, a routine server
    restart would mean SSHing to the whole fleet to bring the machines back."""
    rt = FakeRuntime()
    client = FlakyClient([{"control": "run", "desired_workers": 1}], outages=2)
    out = _run(client, rt, rounds=3)
    assert [h["control"] for h in out["history"]] == [
        "unreachable", "unreachable", "run"]
    assert rt.running() == ["host-berlin-dev-1"]     # it recovered and did the work


@pytest.mark.offline
def test_containers_are_left_running_while_the_server_is_away():
    """A worker talks to Weave itself and retries on its own, so an outage must not
    reach in and tear down work that is still in flight."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2}])
    _run(client, rt, rounds=1)
    assert rt.running() == ["host-berlin-dev-1", "host-berlin-dev-2"]

    # now the server goes away for the rest of the machine's life
    down = FlakyClient([], outages=99)
    down.registered = ("host-berlin", {})
    out = run_devhost(
        down, host_id="host-berlin", runtime=rt, server="http://cg",
        workspace="ws", repo_root="/repo", worktree_root="/wt", image="img",
        preflight=_ok_preflight, seat_token="sk-ant-oat-fake",
        rounds=3, sleep=lambda s: None)
    assert rt.running() == ["host-berlin-dev-1", "host-berlin-dev-2"]
    assert all(h["control"] == "unreachable" for h in out["history"])


@pytest.mark.offline
def test_a_machine_started_before_the_server_waits_instead_of_exiting():
    """Boot order across a fleet is not something anyone should have to arrange."""
    rt = FakeRuntime()
    client = FlakyClient([{"control": "run", "desired_workers": 1}], outages=1)
    out = _run(client, rt, rounds=2)
    assert client.registrations == 1          # registered once it could, not never
    assert out["history"][0]["control"] == "unreachable"
    assert rt.running() == ["host-berlin-dev-1"]


@pytest.mark.offline
def test_a_forgotten_host_reintroduces_itself():
    """If Weave comes back with its state reset it no longer knows this machine.
    A 404 is that case, and the fix is to register again rather than heartbeat
    forever into a host record that does not exist."""
    class Forgot(FakeClient):
        def __init__(self):
            super().__init__([{"control": "run", "desired_workers": 1}])
            self.registrations = 0
            self.first = True

        def register_host(self, host_id, **kw):
            self.registrations += 1
            return super().register_host(host_id, **kw)

        def heartbeat_host(self, host_id, **kw):
            if self.first:
                self.first = False
                raise HTTPError("http://cg", 404, "no dev host", {}, None)
            return super().heartbeat_host(host_id, **kw)

    rt = FakeRuntime()
    client = Forgot()
    _run(client, rt, rounds=2)
    assert client.registrations == 2
    assert rt.running() == ["host-berlin-dev-1"]


@pytest.mark.offline
def test_a_machine_with_no_seat_starts_nothing_and_says_so():
    """Starting containers that cannot authenticate just crash-loops them; the
    useful behaviour is to hold at zero and let the board show why."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 3}])

    def refuses(**_):
        raise SubscriptionAuthError("no subscription auth on this box")

    out = _run(client, rt, rounds=1, preflight=refuses)
    assert out["seat"] == "missing"
    assert rt.running() == []
    assert out["history"][0]["blocked"] == "seat"
    assert client.registered[1]["seat"] == "missing"


@pytest.mark.offline
def test_a_logged_in_host_with_no_propagatable_token_is_expired_not_ok(monkeypatch, tmp_path):
    """The host itself authenticates, but nothing can be handed to a container —
    a distinct failure from never having logged in, and worth distinguishing.

    Both propagation paths are pointed somewhere empty: this runs on developer
    machines that really are logged in, and the test must assert the code's
    behaviour rather than whoever happens to be signed in on the box."""
    monkeypatch.delenv(SEAT_TOKEN_VAR, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-login"))
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2}])
    out = _run(client, rt, rounds=1, seat_token="")
    assert out["seat"] == "expired"
    assert rt.running() == []


@pytest.mark.offline
def test_draining_leaves_in_flight_work_alone_but_starts_nothing_new():
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2},
                         {"control": "drain", "desired_workers": 0}])
    _run(client, rt, rounds=2)
    assert rt.running() == ["host-berlin-dev-1", "host-berlin-dev-2"]
    assert rt.stopped == []


@pytest.mark.offline
def test_pausing_takes_the_containers_down_now():
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2},
                         {"control": "pause", "desired_workers": 0}])
    _run(client, rt, rounds=2)
    assert rt.running() == []


@pytest.mark.offline
def test_stop_is_terminal_and_ends_the_loop_early():
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2},
                         {"control": "stop"},
                         {"control": "run", "desired_workers": 5}])
    out = _run(client, rt, rounds=10)
    assert out["rounds"] == 2 and rt.running() == []
    assert len(client.heartbeats) == 2          # it stopped asking


@pytest.mark.offline
def test_the_daemon_reports_what_is_actually_running_not_what_it_intended():
    rt = FakeRuntime(failing={"host-berlin-dev-2"})
    client = FakeClient([{"control": "run", "desired_workers": 2},
                         {"control": "run", "desired_workers": 2}])
    _run(client, rt, rounds=2)
    _, kw = client.heartbeats[1]
    assert kw["workers"] == ["host-berlin-dev-1"]


# ── onboarding: how a fresh machine learns the project ───────────────────────

@pytest.mark.offline
def test_the_workspace_tells_a_host_what_project_to_work_on():
    """A new box is started with a server URL and nothing else; the repository,
    branch and test command come from the team's definition in Weave."""
    projects = ProjectService(InMemoryWeaveProjectStore())
    projects.set("ws", repo="git@github.com:acme/app.git", base_branch="develop",
                 image="weave-dev:2", test_command=["pytest", "-x"])
    reg = DevHostRegistry(InMemoryDevHostStore(), now=lambda: 1000.0,
                          project_service=projects)
    _register(reg, seat="ok")                       # registered with no repo at all
    reply = reg.heartbeat("ws", "host-berlin")
    assert reply["repo"] == "git@github.com:acme/app.git"
    assert reply["base_branch"] == "develop"
    assert reply["project"]["test_command"] == ["pytest", "-x"]


@pytest.mark.offline
def test_changing_the_base_branch_reaches_every_machine_without_touching_them():
    projects = ProjectService(InMemoryWeaveProjectStore())
    projects.set("ws", repo="r", base_branch="main")
    reg = DevHostRegistry(InMemoryDevHostStore(), now=lambda: 1000.0,
                          project_service=projects)
    _register(reg, seat="ok")
    assert reg.heartbeat("ws", "host-berlin")["base_branch"] == "main"
    projects.set("ws", base_branch="release/2")
    assert reg.heartbeat("ws", "host-berlin")["base_branch"] == "release/2"


@pytest.mark.offline
def test_setting_one_project_field_does_not_reset_the_others():
    projects = ProjectService(InMemoryWeaveProjectStore())
    projects.set("ws", repo="r", base_branch="develop", test_command=["pytest", "-x"])
    projects.set("ws", image="weave-dev:3")
    p = projects.get("ws")
    assert p.repo == "r" and p.base_branch == "develop"
    assert p.test_command == ["pytest", "-x"] and p.image == "weave-dev:3"


@pytest.mark.offline
def test_a_host_asking_before_anyone_configured_a_project_gets_a_usable_answer():
    projects = ProjectService(InMemoryWeaveProjectStore())
    p = projects.get("never-configured")
    assert p.repo == "" and p.base_branch == "main"
    assert p.test_command == list(DEFAULT_TEST_COMMAND)


@pytest.mark.offline
def test_the_daemon_uses_the_project_it_learned_not_the_flags_it_started_with():
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1,
                          "project": {"repo": "git@github.com:acme/app.git",
                                      "base_branch": "develop",
                                      "image": "weave-dev:2",
                                      "test_command": ["pytest", "-x"],
                                      "setup_command": []}}])
    _run(client, rt, rounds=1, image="stale-local-image")
    _, spec = rt.started[0]
    assert spec.image == "weave-dev:2"
    assert spec.base_branch == "develop"
    assert spec.test_command == ["pytest", "-x"]


@pytest.mark.offline
def test_a_container_runs_with_the_full_grant_because_the_container_is_the_boundary():
    """On a bare host the worker is fenced by an allow-list; in a container the
    task must be able to run to completion without a human to ask."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1}])
    _run(client, rt, rounds=1)
    _, spec = rt.started[0]
    assert spec.permission_mode == "bypassPermissions"


# ── seat token discovery ─────────────────────────────────────────────────────

@pytest.mark.offline
def test_the_seat_token_can_come_from_the_environment_or_a_file(tmp_path):
    assert read_seat_token({SEAT_TOKEN_VAR: "from-env"}) == "from-env"
    f = tmp_path / "seat"
    f.write_text("from-file\n")
    assert read_seat_token({}, seat_file=str(f)) == "from-file"
    assert read_seat_token({}, seat_file=str(tmp_path / "missing")) == ""


@pytest.mark.offline
def test_the_container_gets_a_git_identity_and_a_resolvable_worktree():
    """Two things a container silently lacks: a ~/.gitconfig (so `git commit`
    refuses) and the clone a worktree's `.git` file points at (so every git
    command fails). Both have to be arranged by the daemon."""
    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 1}])
    _run(client, rt, rounds=1, worktree_root="/srv/weave/worktrees",
         mount_root="/srv/weave")
    _, spec = rt.started[0]
    assert spec.env["GIT_AUTHOR_NAME"] == "host-berlin-dev-1"
    assert spec.env["GIT_AUTHOR_EMAIL"].endswith("@weave.local")
    # the mount covers the clone as well as the worktree, at an identical path
    assert spec.mount_root == "/srv/weave"
    assert spec.workdir.startswith("/srv/weave/worktrees/")


# ── seat propagation: the machine's own `claude` login reaches its containers ──

@pytest.mark.offline
def test_only_the_credential_is_copied_never_the_whole_config_dir(tmp_path):
    """A host's ~/.claude also holds its history, projects and memory. None of
    it helps the agent, and handing it to something running unattended with full
    write permission would be careless."""
    src = tmp_path / "host-claude"
    src.mkdir()
    (src / CREDENTIALS_FILE).write_text('{"claudeAiOauth": {"accessToken": "x"}}')
    (src / "history.jsonl").write_text("a private conversation")
    (src / "projects").mkdir()

    out = prepare_seat_dir(str(tmp_path / "seat"), source=str(src))
    assert sorted(os.listdir(out)) == [CREDENTIALS_FILE]
    assert oct(os.stat(os.path.join(out, CREDENTIALS_FILE)).st_mode)[-3:] == "600"


@pytest.mark.offline
def test_no_credential_on_the_host_means_no_seat_dir_to_mount(tmp_path):
    assert prepare_seat_dir(str(tmp_path / "seat"), source=str(tmp_path / "nope")) == ""


@pytest.mark.offline
def test_each_container_gets_its_own_copy_so_refreshes_cannot_collide(tmp_path, monkeypatch):
    """One seat shared by N containers is the chosen design; sharing one *file*
    between them is not — a token refresh would rewrite it under the others."""
    src = tmp_path / "host-claude"
    src.mkdir()
    (src / CREDENTIALS_FILE).write_text('{"claudeAiOauth": {"accessToken": "x"}}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(src))

    rt = FakeRuntime()
    client = FakeClient([{"control": "run", "desired_workers": 2}])
    _run(client, rt, rounds=1, seat_root=str(tmp_path / "seats"))
    dirs = [spec.seat_dir for _, spec in rt.started]
    assert len(set(dirs)) == 2 and all(d for d in dirs)
    assert all(os.path.exists(os.path.join(d, CREDENTIALS_FILE)) for d in dirs)
