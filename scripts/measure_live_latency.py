#!/usr/bin/env python
"""How long an event takes to cross from one session to another (R2, M3 gate).

The gate is a number, not a green tick: **an action in one session appears in
another in under 1s at p95 over 100 trials**, and the number is published in the
milestone review. So this harness measures rather than asserts, prints the whole
distribution rather than the one figure that passes, and is reproducible by
someone who does not trust the result.

    python scripts/measure_live_latency.py --url http://127.0.0.1:9800 \
        --user alice --password … --workspace alpha --trials 100

**What is actually measured:** the full path a live update takes —
`POST` → handler → event bus → SSE filter → socket → client parse. Two
authenticated SSE clients are connected, so the figure is *cross-session*: the
publishing request and the receiving connection are different HTTP conversations,
which is what the gate is about.

**What drives it, stated plainly:** `POST /live/presence`, because it is the
event-publishing endpoint this build mounts. A task claim travels the identical
transport — the same bus, the same stream, the same filter — but the team routes
that would emit one are not mounted here, so the claim-specific leg is *not*
covered by this number. Reporting presence latency as claim latency would be
overclaiming; reporting the transport latency it genuinely measures is not.

Exit codes: **0** p95 under the threshold · **1** over it · **2** could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

#: The gate's threshold, in seconds.
P95_THRESHOLD = 1.0


async def _login(client, url: str, user: str, password: str) -> str:
    response = await client.post(
        f"{url}/login",
        data={"username": user, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    token = response.json().get("access_token", "")
    if not token:
        raise RuntimeError("login returned no access token")
    return token


class _Listener:
    """One SSE client, recording arrival times by marker."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.arrivals: Dict[str, float] = {}
        self.ready = asyncio.Event()
        #: Why this listener never became ready, in the caller's words. A
        #: refusal and a dead transport look identical from the outside, and
        #: "never became ready" sent the M3 reviewer hunting for an unmounted
        #: router when the real answer was a 403 (M3 review, M1).
        self.failure: Optional[str] = None
        self._stop = asyncio.Event()

    async def run(self, client, url: str, token: str, workspace: str) -> None:
        headers = {
            "Authorization": f"Bearer {token}",
            "WEAVE-WORKSPACE": workspace,
            "Accept": "text/event-stream",
        }
        async with client.stream("GET", f"{url}/live/stream", headers=headers) as r:
            if r.is_error:
                body = (await r.aread()).decode("utf-8", "replace").strip()
                self.failure = f"HTTP {r.status_code} from {url}/live/stream: {body[:300]}"
                if r.status_code in (401, 403):
                    self.failure += (
                        "\n    A 401/403 here is usually the measuring user having no "
                        "membership of this workspace — /live/stream refuses a "
                        "non-member by design. Create it with, for example:\n"
                        f"        weave user add <name> --role admin --workspaces {workspace}\n"
                        "    A role alone is not access; the grant is separate."
                    )
                # Released so the caller stops waiting and reports the cause,
                # rather than timing out with nothing to go on.
                self.ready.set()
                return
            async for line in r.aiter_lines():
                if self._stop.is_set():
                    return
                if line.startswith(":"):
                    # The open comment tells us LISTEN is live; without waiting
                    # for it the first trials would time the connection setup.
                    self.ready.set()
                    continue
                if not line.startswith("data: "):
                    continue
                received = time.perf_counter()
                try:
                    payload = json.loads(line[6:]).get("payload") or {}
                except ValueError:
                    continue
                marker = payload.get("editing", "")
                if marker:
                    self.arrivals.setdefault(marker, received)

    def stop(self) -> None:
        self._stop.set()


async def _run(args: argparse.Namespace) -> int:
    try:
        import httpx
    except ImportError:  # pragma: no cover
        print("httpx is required", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            token = await _login(client, args.url, args.user, args.password)
        except Exception as e:  # noqa: BLE001
            print(f"could not authenticate against {args.url}: {e}", file=sys.stderr)
            return 2

        listeners = [_Listener("a"), _Listener("b")]
        tasks = [
            asyncio.create_task(
                listener.run(client, args.url, token, args.workspace)
            )
            for listener in listeners
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(l.ready.wait() for l in listeners)), timeout=15
            )
        except asyncio.TimeoutError:
            pass

        # A listener may be "ready" because it gave up. Report *why* — R2 means
        # someone who does not already know the trick can reproduce the claim,
        # and "never became ready" is not something anyone can act on.
        refused = [l for l in listeners if l.failure]
        unready = [l for l in listeners if not l.ready.is_set()]
        if refused or unready:
            for listener in refused:
                print(f"SSE client '{listener.name}' was refused: {listener.failure}",
                      file=sys.stderr)
            for listener in unready:
                print(
                    f"SSE client '{listener.name}' never became ready and the "
                    f"connection did not fail — is {args.url} reachable, and is "
                    "/live/stream mounted?",
                    file=sys.stderr,
                )
            for t in tasks:
                t.cancel()
            return 2

        headers = {
            "Authorization": f"Bearer {token}",
            "WEAVE-WORKSPACE": args.workspace,
        }
        samples: Dict[str, List[float]] = {l.name: [] for l in listeners}
        missed = 0

        for _ in range(args.trials):
            marker = uuid.uuid4().hex
            published = time.perf_counter()
            response = await client.post(
                f"{args.url}/live/presence",
                json={"board": "latency", "editing": marker},
                headers=headers,
            )
            response.raise_for_status()

            deadline = published + args.timeout
            while time.perf_counter() < deadline:
                if all(marker in l.arrivals for l in listeners):
                    break
                await asyncio.sleep(0.002)

            for listener in listeners:
                arrival = listener.arrivals.pop(marker, None)
                if arrival is None:
                    missed += 1
                else:
                    samples[listener.name].append(arrival - published)

        for listener in listeners:
            listener.stop()
        for t in tasks:
            t.cancel()

    every = [s for values in samples.values() for s in values]
    if not every:
        print("no events were received at all", file=sys.stderr)
        return 2

    def pct(values: List[float], p: float) -> float:
        ordered = sorted(values)
        k = max(0, min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1)))))
        return ordered[k]

    report: Dict[str, Any] = {
        "url": args.url,
        "workspace": args.workspace,
        "trials": args.trials,
        "clients": len(listeners),
        "samples": len(every),
        "missed": missed,
        "p50_ms": round(statistics.median(every) * 1000, 2),
        "p95_ms": round(pct(every, 95) * 1000, 2),
        "p99_ms": round(pct(every, 99) * 1000, 2),
        "max_ms": round(max(every) * 1000, 2),
        "mean_ms": round(statistics.fmean(every) * 1000, 2),
        "threshold_ms": P95_THRESHOLD * 1000,
        "driver": "POST /live/presence (transport identical to a task claim)",
    }
    report["pass"] = report["p95_ms"] <= report["threshold_ms"] and missed == 0

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"cross-session live latency — {args.trials} trials, "
              f"{len(listeners)} SSE clients, {len(every)} samples\n")
        print(f"  p50   {report['p50_ms']:>8.2f} ms")
        print(f"  p95   {report['p95_ms']:>8.2f} ms   (gate: ≤ {P95_THRESHOLD * 1000:.0f} ms)")
        print(f"  p99   {report['p99_ms']:>8.2f} ms")
        print(f"  max   {report['max_ms']:>8.2f} ms")
        print(f"  mean  {report['mean_ms']:>8.2f} ms")
        if missed:
            print(f"\n  {missed} event(s) never arrived — a miss is a failure, "
                  "not a slow sample.")
        print()

    if missed:
        print("events were lost; the gate is about delivery as well as speed",
              file=sys.stderr)
        return 1
    if not report["pass"]:
        print(f"p95 {report['p95_ms']}ms exceeds the {P95_THRESHOLD * 1000:.0f}ms gate",
              file=sys.stderr)
        return 1
    print(f"p95 {report['p95_ms']}ms — within the "
          f"{P95_THRESHOLD * 1000:.0f}ms gate.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/measure_live_latency.py",
        description="Measure cross-session live-update latency (R2, M3 gate).",
    )
    parser.add_argument("--url", default="http://127.0.0.1:9800")
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="seconds to wait for one event before counting a miss")
    parser.add_argument("--json", action="store_true")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
