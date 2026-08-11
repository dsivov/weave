# Running your first fleet

Organised by the job you are doing, not by which subsystem does it (R51).

Every step below is a command you can copy. That is not a style choice: a test
(`tests/test_cli_covers_docs.py`) fails if a step in this guide names a command
that does not exist, because a documented step with no command is how an
onboarding guide rots between releases while still reading correctly.

**What you are aiming at:** a machine that carries autonomous developers, a
workspace whose governance is signed, and a board that shows work moving.

---

## 1 · Check the machine before you configure it

```
weave doctor
```

Every Weave role — human and agent — is an ordinary Claude Code session, so a
machine with no subscription seat cannot do any work at all. `doctor` separates
the three failures that otherwise look identical: no seat, a seat with no
subscription, and a metered variable exported in your shell.

It **reports** rather than repairs. If it names `ANTHROPIC_API_KEY`, that
variable stays where it is — you exported it for a reason, and Weave does not
use it. A worker scrubs it from what reaches `claude` and refuses to start if a
metered *backend* is forced outright.

Expect: `subscription seat: ✓ ok`, `metered vars: ✓ none`.

---

## 2 · Start the server

```
weave init --working-dir ./weave_storage
source ./weave_storage/weave.env
weave up --host 0.0.0.0 --port 9800
```

`init` generates `WEAVE_TOKEN_SECRET` and writes it, mode `0600`, to `weave.env`.
It is not optional: the server refuses to start on the published default, because
every token it issues carries the role RBAC is enforced against. Re-running
`init` leaves an existing secret alone — regenerating one logs everybody out.

Bind `0.0.0.0` if anyone will reach this from another machine.

`up` runs in the foreground; Ctrl-C stops it. It is the same server as
`python3 -m weave.server.app --host 0.0.0.0 --port 9800`, which is what to use
under a process manager.

Then open `http://<host>:9800/` — the root redirects to the UI.

---

## 3 · Create the first administrator

```
weave user add alice --role admin --workspaces team
```

The HTTP bootstrap window closes on the first user, so the *local* command is the
one that always works — running it already requires access to the machine and its
storage, which is more authority than any network caller has.

A role is not access. `--workspaces` is the grant, and without it `alice` is an
admin of nothing.

---

## 4 · Give the workspace its vocabulary

```
weave roles install --workspace team --approver alice
```

That installs the standard four-role model — manager, architect, developer,
integrator — with its ontology, actions, lifecycle, rules and RBAC policy.
`weave roles list` shows what you get.

To choose a different shape instead, open **Team vocabulary** in the UI, pick the
one that matches how your team works, and read the diff before signing it.

Either way, what you sign becomes a **version** in the ledger, not a config file.
That is why there is no file to edit here and no restart afterwards: the runtime
enforces what was signed, from the moment it is signed. If you get it wrong, roll
the version back and the previous behaviour returns. `--approver` is who signed
it, and it is recorded — "who took away my access" is a question this has to be
able to answer.

---

## 5 · Tell the workspace what it is building

```
weave project register --workspace team --repo <clone-url> --test-command 'python3 -m pytest -q'
```

Or set it in the UI's project panel — the same state either way. Dev hosts are
generic until they know what to build, and every machine that registers picks
this up on its next heartbeat, so there is nothing to configure on the machines
themselves.

Set `--test-command` deliberately. A developer that cannot run it **halts and
says so** rather than recording "the tests failed" as a finding, so a wrong
command is a visible stall instead of a quiet stream of false learnings.

---

## 6 · Attach a machine that carries developers

On the machine that will run developer containers:

```
claude auth login
weave doctor
python3 -m weave.devhost --server http://<server>:9800 --workspace team
```

The machine names itself after its own hostname; pass `--host-id` when two hosts
share one.

Or run it as a container, which is the same daemon:

```
docker compose -f deploy/compose.devhost.yml up -d
```

The daemon **registers itself and heartbeats**; the server never dials it. That
is what lets this machine sit behind NAT, on someone's desk, or in a private VPC
with no inbound access at all. Nothing on this machine listens on a port, and
nothing needs to.

---

## 7 · Put developers to work

```
weave agents list  --workspace team
weave agents up    --workspace team --host <host-id> --count 3
```

Or from the board: **Dispatch**.

Nothing starts immediately, and the screen says so. Dispatch records how many
developers each machine should run; each host reads that on its **next
heartbeat** and starts or stops containers itself. "Run three developers in
Berlin" is a piece of state the machine pulls, not a command pushed to it.

Watch `desired vs running` per host — `weave agents list` shows it. A gap that
never closes means that machine's daemon is not heartbeating.

To wind a machine down: `weave agents down --workspace team --host <host-id>`,
or `--control drain` to let in-flight tasks finish first.

---

## 8 · If you are upgrading an existing install

```
weave migrate reviews --workspace team --dry-run
weave migrate reviews --workspace team
weave migrate reviews --workspace team --verify
```

Reviews and learnings used to be list fields on a task record. They are nodes
now, so they can be traversed and cited. Run the dry run first — the number it
prints is the number a real run will create. Running it twice is safe.

---

## Where to look when something is wrong

| Symptom | First thing to check |
|---|---|
| The board is empty and nothing moves | `weave doctor` on the dev host — a machine with no seat cannot work |
| A host shows `desired 3 · running 0` | the daemon is not heartbeating; check it can reach the server |
| A change to permissions did nothing | it is signed into the ledger, so check Studio history for the version |
| The UI is blank at the server root | the UI was not built; the server falls back to `/docs` |
| Every task "fails its tests" | the test command may not exist on that host — the worker halts rather than recording a false finding |
| A permissions change did nothing, and Studio shows no version | it was written outside the ledger — that is a bug, not a setting; see D-032/D-034 |
| `weave up` refuses to start | `weave init` has not run, or `weave.env` was never sourced |
