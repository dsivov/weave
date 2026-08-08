<!-- TEMPLATE: CODE REVIEW. Stage 6 — run at the end of each milestone against its diff.
     Findings grouped by severity with stable IDs (C#/H#/M#/S#). Markdown; a mermaid
     severity summary helps at a glance. Verify before reporting; kill plausible-but-wrong. -->

# {{NAME}} — Code Review ({{milestone e.g. M1}}, {{YYYY-MM-DD}})

- **Scope:** {{branch / commit range / files reviewed}}
- **Reviewer:** {{name / agent}}  ·  **Result:** {{blocked | changes-requested | approved}}

## Summary

```mermaid
pie showData
  title Findings by severity
  "Critical" : {{n}}
  "High" : {{n}}
  "Medium" : {{n}}
  "Security" : {{n}}
```

{{One paragraph: overall health, and the gating verdict — what must be fixed before the next milestone.}}

## Critical
> Ship-blockers: wrong results, crashes, data loss.

### C1 — {{one-line claim}}
- **Where:** `{{file}}:{{line}}`
- **Failure:** {{concrete inputs/state → wrong output/crash}}
- **Fix:** {{the change}}

## High
> Serious but not ship-blocking.

### H1 — {{one-line claim}}
- **Where:** `{{file}}:{{line}}`  ·  **Failure:** {{…}}  ·  **Fix:** {{…}}

## Medium
> Correctness/robustness worth fixing soon.

### M1 — {{one-line claim}}
- **Where:** `{{file}}:{{line}}`  ·  **Note:** {{…}}

## Security
> Isolation, secrets, injection, timing, disclosure.

### S1 — {{one-line claim}}
- **Where:** `{{file}}:{{line}}`  ·  **Risk:** {{…}}  ·  **Fix:** {{…}}

## Contract check (methodology R11)
> Walk `CONSTRAINTS.md` against this milestone's diff. Every constraint gets a verdict — a
> silent omission reads as "held". An unreported drift is a **Critical** finding on its own.

| ID | Verdict | Evidence |
|----|---------|----------|
| {{A1}} | {{held \| drifted \| n/a}} | {{`file:line` — what the diff does}} |
| {{A2}} | {{held}} | {{…}} |

- **Any drift reported to the human before it landed?** {{yes — D-NN \| no → C# finding}}
- **Contract amended this milestone?** {{no \| yes → v{{n}}, amendment row + `D-NN` logged}}
- **Non-goals still respected?** {{yes \| no — {{which}}}}

## Layout & dependency drift (methodology R10)
- **Layout matches the doc?** {{yes | no — `{{path}}` isn't in the architecture layout}}
- **Manifest matches the declared library table?** {{yes | no — `{{lib}}` added off-plan}}
- **Any duplicate functionality introduced?** {{none | `{{lib_a}}` overlaps `{{lib_b}}` — one must go}}
- Resolution: {{update the doc, or move the code — never leave the drift silent}}

## Non-issues confirmed (checked, clean)
- {{thing that looked suspicious but is correct — say why, so it isn't re-flagged}}

## Verdict
- [ ] All **Critical** fixed → milestone gate can pass.
- [ ] **High** fixed or logged as open findings in the next checkpoint.
- [ ] Layout & dependencies match the design docs (or the docs were updated).
- [ ] **Every constraint in `CONSTRAINTS.md` still holds** (or was amended with approval).
- Decisions arising: log in `DECISIONS.md`.
