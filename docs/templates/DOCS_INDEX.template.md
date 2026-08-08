<!-- TEMPLATE: DOCS INDEX — the map of docs/. Keep current as artifacts are added.
     Ordered by pipeline stage so a newcomer can read top-to-bottom. -->

# {{PROJECT}} — Documentation Index

The map of `docs/`. Artifacts follow the pipeline: **BLOG → RFC → DRP → ARCHITECTURE/CR → WORK PLAN → reviews.**
See [../ONBOARDING/METHODOLOGY.md](../ONBOARDING/METHODOLOGY.md) for the method.

```mermaid
flowchart LR
  BLOG --> RFC
  RFC <--> DRP
  RFC --> CON[CONSTRAINTS.md]
  DRP --> CON
  CON --> ARCH[ARCHITECTURE]
  ARCH --> WP[WORK PLAN] --> REV[reviews]
  CON -.checked by.-> WP
  CON -.checked by.-> REV
  DEC[DECISIONS.md] -.-> RFC
  DEC -.-> ARCH
  classDef n fill:#1b2740,stroke:#5b8def,color:#e7ebf3;
  classDef c fill:#2a2114,stroke:#f0a73c,color:#ffce86;
  class BLOG,RFC,DRP,ARCH,WP,REV,DEC n; class CON c;
```
_RFC & DRP are co-authored (approach ↔ detail); see METHODOLOGY.md §1._

## Vision
- [BLOG_{{TOPIC}}.html](BLOG_{{TOPIC}}.html) — {{one line}}

## Proposal & requirements
- [{{NAME}}_RFC.html]({{NAME}}_RFC.html) — {{one line}}
- [{{NAME}}_DRP.md]({{NAME}}_DRP.md) — {{one line}}

## The contract
- [CONSTRAINTS.md](CONSTRAINTS.md) — the agreed top-level architecture in ~15 falsifiable
  sentences. Loaded every session; drift from it stops the build (R11).

## Design
- [{{NAME}}_ARCHITECTURE.html]({{NAME}}_ARCHITECTURE.html) — {{one line}}
- [{{…}}_CHANGE_REQUEST.md]({{…}}_CHANGE_REQUEST.md) — {{one line}}

## Plan & progress
- [{{NAME}}_WORK_PLAN.md]({{NAME}}_WORK_PLAN.md) — phases, milestones, tasks, gates
- [{{NAME}}_CODE_REVIEW.md]({{NAME}}_CODE_REVIEW.md) — milestone reviews
- [PROJECT_REVIEW_{{DATE}}.md](PROJECT_REVIEW_{{DATE}}.md) — checkpoints

## Reference
- [DECISIONS.md](DECISIONS.md) — decision log
- [{{other reference docs}}]({{…}})
