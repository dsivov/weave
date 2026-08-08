"""The dev-host daemon — one machine's worth of autonomous developers (P8).

Runs on a machine that should carry developer agents. It registers the machine
with Weave, then heartbeats; each reply says how many developers the
team wants running there, and the daemon starts or stops containers until
reality matches. Nothing ever connects *to* this process, which is what lets a
dev host live behind NAT, on a laptop, or inside a private VPC.

    supervisor ──► Weave  ◄── heartbeat ── daemon ──► docker ──► dev-1, dev-2, …

**Why containers, not processes.** Each developer runs `claude -p` with edit
permission in a git worktree. A container bounds what a bad run can reach and
makes "throw it away and start clean" a one-liner, which matters when the thing
being supervised writes code unattended.

**The seat (D9).** The machine has exactly one Claude subscription seat,
provisioned once by an interactive login on the box. The daemon propagates that
one credential into every container it starts; API-key, Bedrock and Vertex
variables are scrubbed on the way in, so a container cannot silently fall back
to metered auth. Because all containers on a host share the seat, concurrency is
capped and rate-limit failures are treated as backpressure rather than as the
task's fault — see :meth:`Reconciler.reconcile`.

**What this daemon is not.** It does not decide what anyone works on. Tasks come
from the queue a planner published into, and each container's worker claims its
own — the daemon only decides *how many* claimants exist on this machine.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set

from weave_core.utils import logger

from weave.team.worker import (
    SubscriptionAuthError, preflight_subscription_auth, scrub_api_auth,
)
from weave.devhost.runtime import ContainerRuntime, DockerRuntime, WorkerSpec
from weave.devhost.worktree import task_branches, _branch_publisher, _worktree_maker

# The one variable that carries a subscription seat into a container. It is
# deliberately absent from SUBSCRIPTION_SCRUB_VARS: scrubbing removes metered
# auth, and this is the opposite of metered auth.
SEAT_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

# How long between heartbeats. A host has nothing to poll for — its containers do
# their own long-polling — so this only needs to be fast enough that scaling from
# the board feels responsive.
HEARTBEAT_INTERVAL = 20.0




class Reconciler:
    """Closes the gap between how many developers the team wants here and how
    many are actually running."""

    def __init__(self, runtime: ContainerRuntime, host_id: str, *,
                 make_spec: Callable[[str], WorkerSpec], max_workers: int = 8) -> None:
        self._runtime = runtime
        self._host_id = host_id
        self._make_spec = make_spec
        self._max = max_workers

    def worker_id(self, n: int) -> str:
        return f"{self._host_id}-dev-{n}"

    def reconcile(self, desired: int, held: Iterable[str] = ()) -> Dict[str, Any]:
        """Start or stop containers until `desired` are running.

        Scaling *down* stops the highest-numbered workers first, so worker 1 is
        the stable one across a shrink — it makes a machine's logs and board rows
        readable over time rather than shuffling on every change.

        `held` are workers a supervisor paused or stopped individually from the
        fleet view. They are excluded rather than replaced: a held slot stays
        empty, so pausing one developer leaves the rest alone instead of the
        machine quietly starting a substitute. Without this the host count would
        override every per-container decision a human makes.

        A container that fails to start is reported, not raised: one bad slot
        must not take down a machine that is otherwise working.
        """
        desired = max(0, min(desired, self._max))
        held = set(held)
        running = set(self._runtime.running())
        want = {self.worker_id(n) for n in range(1, desired + 1)} - held

        started, stopped, failed = [], [], []
        for wid in sorted(running - want, reverse=True):
            try:
                self._runtime.stop(wid)
                stopped.append(wid)
            except Exception as e:
                logger.warning(f"devhost: could not stop {wid}: {e}")
                failed.append(wid)
        for wid in sorted(want - running):
            try:
                self._runtime.start(wid, self._make_spec(wid))
                started.append(wid)
            except Exception as e:
                logger.warning(f"devhost: could not start {wid}: {e}")
                failed.append(wid)

        return {"started": started, "stopped": stopped, "failed": failed,
                "running": sorted((running | set(started)) - set(stopped))}


# The one file in a Claude config directory that carries the subscription seat.
CREDENTIALS_FILE = ".credentials.json"

# What a container may inherit from the machine that starts it.
#
# A developer container is not a smaller copy of the host. It needs a git
# identity, its seat, a way to reach Weave, and a route out to the network —
# nothing else. Composing that env as an **allowlist** rather than "the host's
# environment minus the metered-auth names" is what keeps the daemon's own
# secrets out of it: the daemon is normally started beside the server, so its
# environment holds the workspace's LLM keys and the JWT signing secret that
# mints supervisor roles. A denylist would have to enumerate every one of those
# to stay safe, and would silently fail the day a new one is added — inside
# something that runs an agent unattended with full write permission.
CONTAINER_ENV_PASSTHROUGH = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
)


def host_seat_source(env: Optional[Dict[str, str]] = None) -> str:
    """Where this machine's own `claude` login keeps its credential."""
    env = dict(os.environ if env is None else env)
    return env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def seat_expiry(path: str) -> float:
    """When the credential at *path* stops working, as a unix timestamp.

    ``0.0`` for anything unreadable, unparseable, or without an expiry — which
    reads as "already expired" everywhere this is used, and that is the safe
    direction: a credential we cannot vouch for should lose to one we can.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            oauth = json.load(fh).get("claudeAiOauth") or {}
        return float(oauth.get("expiresAt") or 0) / 1000.0
    except Exception:                              # noqa: BLE001 - absent or malformed
        return 0.0


def refresh_seat_dirs(seat_root: str, worker_ids: Iterable[str],
                      source: str = "") -> List[str]:
    """Re-hand the machine's credential to containers whose copy has gone stale.

    A subscription token expires within the day, and a container gets its copy
    once, when it starts. A container that outlives that copy loses its seat: the
    worker keeps claiming tasks and `claude` cannot run, so work sits
    ``in_progress`` forever with nothing in the log to say why. The host's own
    login refreshes itself, so the fix is simply to let the containers ride
    along on it.

    Only ever replaces a copy with a **longer-lived** one. A container refreshes
    its own credential in place, and that result can easily be fresher than the
    host's — clobbering it would take a working seat away.
    """
    src_dir = source or host_seat_source()
    host_until = seat_expiry(os.path.join(src_dir, CREDENTIALS_FILE))
    if not host_until:
        return []
    refreshed = []
    for worker_id in worker_ids:
        target = os.path.join(seat_root, worker_id)
        if not os.path.isdir(target):
            continue                               # not a container we seated
        if seat_expiry(os.path.join(target, CREDENTIALS_FILE)) >= host_until:
            continue                               # its own is at least as good
        if prepare_seat_dir(target, source=src_dir):
            refreshed.append(worker_id)
    return refreshed


def prepare_seat_dir(target: str, source: str = "") -> str:
    """Copy the host's subscription credential into a config dir for a container.

    The machine is logged in once with `claude` (the interactive login stays a
    human step — D9 is subscription-only, and there is no headless way to become
    a subscriber). Propagating it is then just this file.

    **Only the credential is copied, never the whole config directory.** A host's
    `~/.claude` also holds its conversation history, its projects and its memory;
    handing all of that to an agent that runs unattended with full write
    permission would be careless, and none of it helps the agent do the work.

    Each container gets its own copy rather than sharing one directory, so
    concurrent token refreshes cannot corrupt a file out from under each other.

    Returns the prepared directory, or "" if the host has no credential to give.
    """
    import shutil

    src = os.path.join(source or host_seat_source(), CREDENTIALS_FILE)
    if not os.path.exists(src):
        return ""
    os.makedirs(target, exist_ok=True)
    dst = os.path.join(target, CREDENTIALS_FILE)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o600)
    os.chmod(target, 0o700)
    return target


def read_seat_token(env: Optional[Dict[str, str]] = None,
                    seat_file: str = "") -> str:
    """The machine's subscription seat, as minted by an interactive login.

    Either exported into the daemon's environment or kept in a file beside it —
    a file being the friendlier option for a service, since it survives a reboot
    without anyone re-exporting anything.
    """
    env = dict(os.environ if env is None else env)
    token = env.get(SEAT_TOKEN_VAR, "").strip()
    if token:
        return token
    if seat_file and os.path.exists(seat_file):
        with open(seat_file, encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


def run_devhost(
    client: Any,
    *,
    host_id: str,
    runtime: ContainerRuntime,
    server: str,
    workspace: str,
    image: str,
    repo_root: str,
    worktree_root: str,
    machine: str = "",
    base_branch: str = "main",
    seat_token: str = "",
    cg_token: str = "",
    max_workers: int = 8,
    mount_root: str = "",
    seat_root: str = "",
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
    rounds: Optional[int] = None,
    sleep: Callable[[float], None] = time.sleep,
    preflight: Callable[..., Dict[str, str]] = preflight_subscription_auth,
    prepare_worktree: Optional[Callable[[str], str]] = None,
    publish_branches: Optional[Callable[[List[str], str], List[str]]] = None,
) -> Dict[str, Any]:
    """Register this machine, then heartbeat and reconcile until told to stop.

    `rounds` bounds the loop for tests; left as ``None`` the daemon runs until a
    supervisor stops it.
    """
    # ── the seat, before anything else ──────────────────────────────────────
    # A machine with no valid seat can host nothing, and the useful thing to do
    # is say so on the board rather than fail silently or crash-loop.
    seat, seat_detail, base_env = "unknown", "", {}
    try:
        base_env = preflight()
        seat, seat_detail = "ok", "subscription auth confirmed on the host"
    except SubscriptionAuthError as e:
        seat, seat_detail = "missing", str(e)[:200]
        base_env = scrub_api_auth(dict(os.environ))
        logger.error(f"devhost '{host_id}': {e}")

    # How the seat reaches a container. The machine was logged in with `claude`,
    # so the credential that login produced is what gets propagated — a copy of
    # it per container, as CLAUDE_CONFIG_DIR. A pre-minted token is accepted as
    # an alternative for machines provisioned that way.
    have_credential = os.path.exists(os.path.join(host_seat_source(), CREDENTIALS_FILE))
    if seat == "ok" and not have_credential:
        seat_token = seat_token or read_seat_token()
        if not seat_token:
            # The host is logged in, but nothing can be handed to a container.
            # Distinguishable on the board from "never logged in".
            seat, seat_detail = "expired", (
                "host is authenticated but its credential is not readable and no "
                f"{SEAT_TOKEN_VAR} is set — nothing can be propagated to containers")
            logger.error(f"devhost '{host_id}': {seat_detail}")
    elif seat == "ok":
        seat_detail = "subscription seat propagated from this machine's `claude` login"

    # The project the workspace is working on, as last learned from a heartbeat.
    # Seeded from this machine's own flags so a first reconcile before any reply
    # still has something coherent to use.
    project: Dict[str, Any] = {"repo": repo_root, "base_branch": base_branch,
                               "image": image, "test_command": [], "setup_command": []}

    def make_spec(worker_id: str) -> WorkerSpec:
        workdir = (prepare_worktree(worker_id, project) if prepare_worktree
                   else os.path.join(worktree_root, worker_id))
        env = {k: v for k, v in base_env.items() if k in CONTAINER_ENV_PASSTHROUGH}
        if seat_token:
            env[SEAT_TOKEN_VAR] = seat_token
        if cg_token:
            env["WEAVE_CG_TOKEN"] = cg_token
        # A container has no ~/.gitconfig, so `git commit` would refuse outright.
        # Naming the worker as the author is also the honest attribution: the
        # commit really was made by that agent, and the board row, the branch and
        # the git history then all say the same name.
        env.setdefault("GIT_AUTHOR_NAME", worker_id)
        env.setdefault("GIT_COMMITTER_NAME", worker_id)
        env.setdefault("GIT_AUTHOR_EMAIL", f"{worker_id}@weave.local")
        env.setdefault("GIT_COMMITTER_EMAIL", f"{worker_id}@weave.local")
        # The worktree is owned by the host user, not by the container's `dev`,
        # and git refuses to operate on a checkout it thinks belongs elsewhere.
        env.setdefault("GIT_CONFIG_COUNT", "1")
        env.setdefault("GIT_CONFIG_KEY_0", "safe.directory")
        env.setdefault("GIT_CONFIG_VALUE_0", "*")
        # Scrub at the boundary too. The allowlist above already makes metered
        # auth unreachable; this stays as the second lock, so a later passthrough
        # entry cannot quietly reopen the D9 token boundary.
        # Each container gets its own copy of the machine's credential, so a
        # token refresh in one cannot corrupt the file another is reading.
        seat_dir = prepare_seat_dir(os.path.join(seat_root, worker_id)) if seat_root else ""
        return WorkerSpec(image=project.get("image") or image, server=server,
                          workspace=workspace, workdir=workdir,
                          base_branch=project.get("base_branch") or base_branch,
                          env=scrub_api_auth(env), cg_token=cg_token,
                          test_command=list(project.get("test_command") or []),
                          mount_root=mount_root or worktree_root,
                          seat_dir=seat_dir)

    reconciler = Reconciler(runtime, host_id, make_spec=make_spec, max_workers=max_workers)

    def register():
        client.register_host(
            host_id, machine=machine or _hostname(), repo=repo_root,
            base_branch=base_branch, image=image, seat=seat, seat_detail=seat_detail,
            capabilities=[])
        logger.info(f"devhost '{host_id}' registered [seat={seat}]")

    registered = False
    history: List[Dict[str, Any]] = []
    # Whether Weave was reachable last round, so an outage is logged once when it
    # starts and once when it ends rather than on every beat.
    reachable = True
    n = 0
    while rounds is None or n < rounds:
        n += 1
        running = runtime.running()

        # ── surviving a Weave outage ────────────────────────────────
        # A dev host is a remote machine, often one of many, and Weave restarts.
        # Exiting here would orphan every container on every box and make a
        # routine server restart an SSH-round-trip to the whole fleet — so the
        # loop treats an unreachable server as weather, not as a fatal error.
        # The containers are deliberately left running: each worker talks to Weave
        # itself and retries on its own, so a blip must not destroy work in
        # flight. From Weave's side the host simply goes stale, which the board
        # already renders as offline.
        try:
            if not registered:
                register()
                registered = True
            reply = client.heartbeat_host(host_id, workers=running, seat=seat,
                                          seat_detail=seat_detail)
        except Exception as e:                      # noqa: BLE001 - any transport fault
            # A 404 means Weave no longer knows this host (its state was reset);
            # the machine re-introduces itself on the next round.
            if getattr(e, "code", None) == 404:
                registered = False
            if reachable:
                logger.warning(
                    f"devhost '{host_id}': Weave unreachable ({e}) — "
                    f"holding {len(running)} container(s) and retrying")
                reachable = False
            history.append({"control": "unreachable", "running": running,
                            "started": [], "stopped": [], "failed": []})
            if rounds is None or n < rounds:
                sleep(heartbeat_interval)
            continue

        if not reachable:
            logger.info(f"devhost '{host_id}': Weave is back")
            reachable = True
        control = reply.get("control", "run")

        # Onboarding: the workspace says what the project is, and it wins over
        # this machine's local flags. A host that was started with nothing but a
        # server URL learns the repo here.
        learned = reply.get("project") or {
            k: reply[k] for k in ("repo", "base_branch", "image") if k in reply}
        project.update({k: v for k, v in learned.items() if v})
        if not project.get("repo"):
            logger.warning(
                f"devhost '{host_id}': no project repository configured for "
                f"workspace '{workspace}' — set one with PUT /weave/project")

        # ── get the work off this machine ───────────────────────────────────
        # Before anything else this round, including a stop: a branch that only
        # exists in one clone on one box is not finished work, it is work nobody
        # else can reach. Publishing first means a machine being drained or shut
        # down still hands over what it built.
        # A container's credential expires within the day while the host's own
        # login keeps refreshing. Let the running containers ride along on it,
        # or they quietly lose their seat and stop being able to work at all.
        if seat_root and seat == "ok":
            try:
                reseated = refresh_seat_dirs(seat_root, running)
                if reseated:
                    logger.info(f"devhost '{host_id}': re-seated {reseated}")
            except Exception as e:                  # noqa: BLE001 - never fatal
                logger.warning(f"devhost '{host_id}': could not re-seat ({e})")

        if publish_branches is not None:
            try:
                pushed = publish_branches(
                    running, project.get("base_branch") or base_branch)
                if pushed:
                    logger.info(f"devhost '{host_id}': published {pushed}")
            except Exception as e:                  # noqa: BLE001 - never fatal
                logger.warning(f"devhost '{host_id}': could not publish branches ({e})")

        if control == "stop":
            # Terminal: take every container down and leave.
            result = reconciler.reconcile(0)
            history.append({"control": control, **result})
            logger.info(f"devhost '{host_id}' stopped by supervisor")
            break

        if control in ("pause", "drain"):
            # `pause` takes the machine down now; `drain` lets in-flight work
            # finish, so it stops nothing and simply starts nothing new.
            result = reconciler.reconcile(0) if control == "pause" else {
                "started": [], "stopped": [], "failed": [], "running": running}
            history.append({"control": control, **result})
        else:
            desired = int(reply.get("desired_workers", 0) or 0)
            if seat != "ok" and desired > 0:
                # Starting containers that cannot authenticate just burns them in
                # a crash loop; hold at zero and let the board show why.
                logger.warning(
                    f"devhost '{host_id}': {desired} developer(s) requested but the "
                    f"seat is '{seat}' — holding at zero")
                history.append({"control": control, "blocked": "seat", "running": running,
                                "started": [], "stopped": [], "failed": []})
            else:
                result = reconciler.reconcile(desired, held=reply.get("held_workers") or [])
                if result["started"] or result["stopped"] or result["failed"]:
                    logger.info(
                        f"devhost '{host_id}': started={result['started']} "
                        f"stopped={result['stopped']} failed={result['failed']}")
                history.append({"control": control, **result})

        if rounds is None or n < rounds:
            sleep(heartbeat_interval)

    return {"host": host_id, "seat": seat, "rounds": n,
            "running": runtime.running(), "history": history}


def _hostname() -> str:
    import socket
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover - defensive
        return ""


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - entrypoint
    import argparse

    from weave.team.worker import WeaveClient

    ap = argparse.ArgumentParser(description="Weave dev-host daemon")
    ap.add_argument("--server", required=True, help="Weave base URL")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--host-id", required=True, help="This machine's id in the fleet")
    ap.add_argument("--machine", default="", help="Display name; defaults to the hostname")
    ap.add_argument("--image", default="",
                    help="Container image; defaults to the workspace project's image")
    ap.add_argument("--repo", default="",
                    help="Override the workspace project's repository (rarely needed)")
    ap.add_argument("--clone", default="",
                    help="Where to keep this machine's clone (default: <worktrees>/../clone)")
    ap.add_argument("--worktrees", default="", help="Directory holding per-worker worktrees")
    ap.add_argument("--base-branch", default="main",
                    help="What each task branch starts from, so one PR shows one task")
    ap.add_argument("--max-workers", type=int, default=8,
                    help="Ceiling on containers here — they share one subscription seat")
    ap.add_argument("--seat-file", default="",
                    help=f"File holding the seat token, if not exported as {SEAT_TOKEN_VAR}")
    ap.add_argument("--token", default=os.environ.get("WEAVE_SERVER_TOKEN"))
    ap.add_argument("--api-key", default=os.environ.get("WEAVE_SERVER_API_KEY"))
    ap.add_argument("--interval", type=float, default=HEARTBEAT_INTERVAL)
    args = ap.parse_args(argv)

    client = WeaveClient(args.server, args.workspace, token=args.token, api_key=args.api_key)
    runtime = DockerRuntime(host_id=args.host_id)
    state_root = args.worktrees or os.path.expanduser("~/.weave")
    clone_root = args.clone or os.path.join(state_root, "clone")
    worktree_root = os.path.join(state_root, "worktrees")
    seat_root = os.path.join(state_root, "seats")
    result = run_devhost(
        client, host_id=args.host_id, runtime=runtime, server=args.server,
        workspace=args.workspace, image=args.image, repo_root=args.repo,
        worktree_root=worktree_root,
        machine=args.machine, base_branch=args.base_branch,
        seat_token=read_seat_token(seat_file=args.seat_file),
        cg_token=args.token or "", max_workers=args.max_workers,
        mount_root=state_root, seat_root=seat_root,
        heartbeat_interval=args.interval,
        prepare_worktree=_worktree_maker(clone_root, worktree_root),
        publish_branches=_branch_publisher(clone_root))
    logger.info(f"devhost finished: {result['host']} after {result['rounds']} rounds")
    return 0




if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
