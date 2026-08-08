<!-- TEMPLATE: CHANGE REQUEST. Stage 4b. A scoped change on top of an EXISTING architecture.
     Reference the architecture section(s) it touches. Markdown; illustrate with mermaid. -->

# {{NAME}} — Change Request (CR-{{NNN}})

- **Project:** {{PROJECT}}  ·  **Date:** {{YYYY-MM-DD}}  ·  **Status:** {{proposed | approved | done}}
- **Affects:** [{{SYSTEM}}_ARCHITECTURE.html]({{SYSTEM}}_ARCHITECTURE.html) §{{section}}
- **Requested by:** {{name}}

## 1 · What & why

{{One paragraph: the change and the reason. What breaks or is missing without it?}}

## 2 · Before → after

```mermaid
flowchart LR
  subgraph Before
    A1[{{current}}] --> A2[{{current}}]
  end
  subgraph After
    B1[{{new}}]:::new --> B2[{{current}}]
  end
  classDef new fill:#231b3a,stroke:#a974f0,color:#e7ebf3;
```

{{What specifically changes — components, data, contracts.}}

## 3 · Scope

**Changes**
- {{file / component}} — {{what changes}}

**Unchanged (explicitly)**
- {{what this CR does NOT touch}}

## 4 · Layout & dependency delta

<!-- Mandatory (methodology R10). "None" is a valid answer — say it explicitly. -->

**Files / directories**

| Path | Added / moved / deleted | Owns |
|------|-------------------------|------|
| `{{path}}` | {{added}} | {{…}} |

**Dependencies**

| Library | Version | New / reused | Why nothing already installed covers it |
|---------|---------|:------------:|------------------------------------------|
| {{lib}} | {{x.y}} | new | {{…}} |

{{Reuse first: existing modules, libraries, databases and integrations stay the one tool for
their job. If this CR replaces an incumbent, name it here and add the removal task in §7.}}

## 5 · Impact & risk

| Area | Impact | Risk | Mitigation |
|------|--------|:----:|------------|
| {{data / API / perf / migration}} | {{…}} | {{low/med/high}} | {{…}} |

- **Backward compatibility:** {{yes / no — migration note}}
- **Rollback:** {{how to revert}}

## 6 · Acceptance criteria (test gate)

- [ ] {{observable, testable}}
- [ ] {{regression: existing behavior still holds}}
- [ ] {{measured claim + harness, if applicable}}

## 7 · Tasks

- [ ] `{{path}}` — {{…}}
- [ ] `test_{{…}}.py` — {{…}}

**Review:** on completion, code review of the CR diff; log the decision in `DECISIONS.md`.
