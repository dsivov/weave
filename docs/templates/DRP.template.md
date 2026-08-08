<!-- TEMPLATE: DRP — Detailed Requirements & Plan. Stage 3. The "what/why" the RFC summarizes
     and the work plan builds from. Markdown; illustrate with mermaid. Replace {{PLACEHOLDERS}}. -->

# {{NAME}} — Detailed Requirements & Plan (DRP)

- **Project:** {{PROJECT}}
- **Status:** {{draft | review | accepted}}
- **Date:** {{YYYY-MM-DD}}
- **Owner:** {{name}}
- **Sources:** [BLOG_{{TOPIC}}.html](BLOG_{{TOPIC}}.html) · [{{NAME}}_RFC.html]({{NAME}}_RFC.html)

## 1 · Problem & goal

{{One paragraph: the problem this solves and the outcome that means "done".}}

**In scope**
- {{…}}

**Out of scope (non-goals)**
- {{…}}  ← be explicit; non-goals prevent scope creep.

## 2 · Context

```mermaid
flowchart LR
  U[{{actor}}] -->|{{action}}| S[{{system}}]
  S -->|{{reads/writes}}| D[({{data / external}})]
  S -->|{{output}}| R[{{result}}]
  classDef a fill:#1b2740,stroke:#5b8def,color:#e7ebf3;
  classDef b fill:#12302a,stroke:#19b89a,color:#e7ebf3;
  class U,R a; class S,D b;
```

{{What exists today, what it touches, and where this fits.}}

## 3 · Requirements

| # | Requirement | Priority | Rationale |
|---|-------------|:--------:|-----------|
| R1 | {{must do …}} | must | {{why}} |
| R2 | {{should do …}} | should | {{why}} |
| R3 | {{could do …}} | could | {{why}} |

## 4 · Constraints & assumptions

- **Constraint:** {{tech / compliance / latency / budget …}}
- **Assumption:** {{what we take as given — flag if unverified}}

## 5 · Acceptance criteria

The feature is accepted when **all** of these hold (these become the milestone test gates):

- [ ] {{observable, testable criterion}}
- [ ] {{observable, testable criterion}}
- [ ] {{a measured claim + its harness, per methodology R2}}

## 6 · Data & interfaces

```mermaid
classDiagram
  class {{Entity1}} {
    +{{field}}: {{type}}
    +{{field}}: {{type}}
  }
  class {{Entity2}} {
    +{{field}}: {{type}}
  }
  {{Entity1}} --> {{Entity2}} : {{relation}}
```

**Interfaces / endpoints**
- `{{METHOD}} {{/path}}` — {{purpose}} → {{shape}}

## 7 · Code layout & dependencies

<!-- Mandatory (methodology R10). Mark what already exists vs what this plan adds. -->

**Dependency manager:** {{conda (default) | uv | poetry | pip+venv | npm/pnpm | …}} — manifest
`{{environment.yml | pyproject.toml | requirements.txt | package.json}}`.
<!-- Python? This was ASKED, not assumed — record the answer in DECISIONS.md. -->

**File-system layout**

```
{{project}}/
├── {{src_or_pkg}}/
│   ├── {{module_a}}/        # {{what it owns}}            [new]
│   └── {{module_b}}.py      # {{what it owns}}            [exists]
├── tests/
│   └── test_{{…}}.py        # {{gate for M{{n}}}}          [new]
├── scripts/                 # measurement harnesses (R2)  [exists]
└── {{manifest}}             # {{pinned deps}}
```

**External libraries**

| Library | Version | Purpose | Reused / New | Why this over the alternative |
|---------|---------|---------|:------------:|-------------------------------|
| {{lib}} | {{x.y}} | {{…}} | reused | already in the repo — no second tool for this job |
| {{lib}} | {{x.y}} | {{…}} | new | {{alternative rejected because …}} |

**Existing code we build on** <!-- delete if greenfield -->

| What's already there | Where | How this plan reuses it |
|----------------------|-------|-------------------------|
| {{layout / module}} | `{{path}}` | {{extended, not replaced}} |
| {{library actually imported}} | `{{manifest}}` | {{kept as the one tool for {{job}}}} |
| {{database / integration}} | {{host / service}} | {{reused — no new store introduced}} |

{{If anything here is being **replaced**: say which incumbent, why, and the work-plan task
that removes it. Leaving both in place is not an option (R10).}}

## 8 · Risks & open questions

| Risk / question | Impact | Plan |
|-----------------|:------:|------|
| {{…}} | {{high/med}} | {{mitigation or who decides}} |

## 9 · Plan summary

Phases and milestones live in [{{NAME}}_WORK_PLAN.md]({{NAME}}_WORK_PLAN.md). At a glance:

```mermaid
flowchart LR
  P0[P0 · foundations] --> P1[P1 · {{theme}} → M1] --> P2[P2 · {{theme}} → M2] --> P3[P3 · {{theme}} → M3]
  classDef p fill:#1b2740,stroke:#5b8def,color:#e7ebf3;
  class P0,P1,P2,P3 p;
```
