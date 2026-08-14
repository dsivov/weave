# Execution log — what was run while writing the guide

**The guide's rule is that every claimed step is executed before it is written.** These are the
transcripts, kept so the claim is checkable rather than asserted.

Captured 2026-08-14 against a clean install on the machine that wrote the guide — a fresh
`weave_storage`, a new administrator, and governance installed from nothing.

| file | step |
|---|---|
| `01.txt` | `weave doctor` on an empty directory |
| `02.txt` | `weave init` — what it writes, and where |
| `03-up.txt` | `weave up` — the first screen a reader meets |
| `04.txt` | `weave user add` — the first administrator, with no `--working-dir` anywhere |
| `05.txt` | `weave roles install` — five signed layers |
| `07-agents.txt` | `weave agents --help` |
| `08-fleet.txt` | the fleet, before any machine has joined |

**Three of these captures are the reason for defects.** `03-up.txt` is the first screen *after*
W25–W26 fixed it — the version before showed a blocking `yes/no` prompt, another product's tagline,
and a workers default the event-bus constraint refuses. `04.txt` runs with no `--working-dir` because
W27 gave the CLI and the server one default; before that they wrote to different directories and the
account was invisible to the server.

**Not captured here:** the dev-host join (§8), which was executed live and produced W32 — the
published command omitted `--token` and looped on `401`. The guide carries the invocation that ran.
