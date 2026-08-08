# Weave

**The team is the product.** Weave is a multi-user, multi-role system for running an
AI development team: humans and autonomous agents planning, building and reviewing on
one governed graph, where every action passes governance and every answer resolves to
a real document.

- **Vision** — [docs/BLOG_THE_TEAM_IS_THE_PRODUCT.html](docs/BLOG_THE_TEAM_IS_THE_PRODUCT.html)
- **Proposal** — [docs/WEAVE_RFC.html](docs/WEAVE_RFC.html) · **Requirements** — [docs/WEAVE_DRP.md](docs/WEAVE_DRP.md)
- **Architecture** — [docs/WEAVE_ARCHITECTURE.html](docs/WEAVE_ARCHITECTURE.html)
- **The contract** — [docs/CONSTRAINTS.md](docs/CONSTRAINTS.md) · **Plan** — [docs/WEAVE_WORK_PLAN.md](docs/WEAVE_WORK_PLAN.md)
- **Where the code came from** — [PROVENANCE.md](PROVENANCE.md)

> **Status: P0 complete, M0 under review.** The engine and product layers are copied,
> rebranded and green; the surfaces the plan adds — the user store, the answer
> surface, the live layer, the wizards, the senior seat and the CLI — land in P1–P6.

## Shape

Three deployables, and no fourth (A1): the **Weave server** (which also serves the
built UI as static assets), the **dev-host daemon**, and the **dev-agent container
image**.

```
weave_core/     the engine — the graph, governance verdicts, the signed ledger,
                the event port, the model connectors. Knows nothing about HTTP
                and imports nothing from weave/ (A2).
weave/          the product — the team model, the identities, and every byte of
                HTTP: server, routers, MCP, dev-host daemon.
weave-ui/       React 19 · Vite 7 · Tailwind 4 · zustand 5, built into the server.
tests/          the carried suites plus one gate suite per milestone.
scripts/        the guards and the measurement harnesses.
deploy/         compose bundles and the dev-agent image.
```

## Running it

```bash
conda env create -f environment.yml
conda activate weave
pip install -e . --no-deps

pytest                      # the Python suite
scripts/nameguard.sh        # the rebrand is enforced, not remembered
```

The default storage path is file-based and needs nothing installed. **It is
single-operator only** — every write is a whole-file read-modify-write, so
concurrent writers lose each other's work. PostgreSQL is the multi-user path;
Neo4j is the optional dedicated graph engine. Those three, and no fourth (A4).

## Two things that are easy to break and expensive to notice

**The subscription boundary (A13).** Every Claude Code client — a human's seat and
every dev container — authenticates by subscription seat only. The `anthropic` SDK
is not a dependency, and no API key, auth token or base-URL override reaches a
Claude Code process. Server-side model use is the only place a credential exists.
Note the asymmetry: `CLAUDE_CODE_OAUTH_TOKEN` is deliberately *not* scrubbed —
scrubbing removes metered auth, and the seat is the opposite of metered auth.

**Outbound-only fleets (A15).** The server never dials a dev host. Hosts register
and heartbeat; supervisory acts are state the host reads back (`desired_workers`),
never commands pushed at it. That is what lets a dev host sit behind NAT or in a
private VPC — and anything needing an inbound connection to a host breaks remote
fleets.

## Working on it

This project runs on the house methodology: docs first, every claim measured,
every milestone gated and reviewed. `docs/CONSTRAINTS.md` holds the agreed
architecture as fifteen falsifiable sentences and is loaded into every session —
**if a change would make one of them false, it stops, and the contract is amended
before the code lands.** See [CLAUDE.md](CLAUDE.md).
