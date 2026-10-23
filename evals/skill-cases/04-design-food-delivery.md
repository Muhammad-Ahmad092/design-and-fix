# 04: Mode 1 design for a food-delivery app

## Prompt

```text
Design the architecture for a food-delivery app. Expect about 50k users, a team of 4, and prefer Node.js. Ask me for any missing constraints before writing the design.
```

When the agent asks its questions, reply: `You decide.`

## Setup

- Empty folder, plugin installed.
- Reference output: `examples/mode-1-food-delivery/`.

## Must

- [ ] Asks one batched message of at most 6 questions before writing files, without re-asking scale, team or stack
- [ ] After "You decide.", records every default under Assumptions & Constraints
- [ ] Writes `docs/architecture/01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md` with headers in the standard order
- [ ] C4 Context and Container diagrams are Mermaid `flowchart` with C4 labels; Data Flow is a `sequenceDiagram`; max about 12 nodes each
- [ ] Capacity Estimates table shows a formula and a value per metric, built on the 50k users given
- [ ] Data Model has an entities table and an `erDiagram` with at most 8 entities
- [ ] Endpoints table has at most 12 rows; the OpenAPI 3.1 skeleton covers 3–5 endpoints
- [ ] Security (STRIDE-lite), Observability (with alert thresholds) and Deployment & Cost (monthly range with assumptions) are filled
- [ ] Pattern Choice makes one pick; a modular monolith is expected for a team of 4, and any other pick says why the guidance was overridden
- [ ] One ADR per Key Architectural Decisions row, at most 5, in `docs/adr/` numbered from `0001`, each with Context, Decision, Consequences
- [ ] Node.js is the backend runtime

## Must not

- [ ] Writes files before the questions are answered
- [ ] Gives "it depends" without a pick on any decision
- [ ] Writes `04-tech-stack-comparison.md`
- [ ] Writes more than 5 ADRs, or an ADR whose decision differs from its Key Architectural Decisions row
