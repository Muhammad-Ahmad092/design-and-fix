# Mode 1: New Project

Trigger: the user provides an idea, PRD, doc or rough description of a project that is not built yet. A single component or feature uses Quick mode instead (`references/quick-mode.md`) unless the user says "full" or "detailed".

## Workflow

```
Read input -> Context -> Archive -> Decide -> Fill 3 templates + ADRs -> Self-check -> Deliver
```

1. **Read input.** Extract what the system does, who uses it and any stated constraints.
2. **Context.** Follow `references/context-collection.md` (Mode 1 questions or defaults).
3. **Archive.** If earlier output exists in `docs/architecture/`, follow `references/archive-and-diff.md` (Mode 1 re-runs produce no diff section).
4. **Decide.** One clear choice per decision. No "it depends" without a pick.
5. **Fill templates** from `templates/mode-1/` into `docs/architecture/`, then write ADRs (below).
6. **Self-check** against the checklist below.
7. **Deliver.** List the files and ADRs written, plus the archive location on a re-run.

## Output

Three core files. Headers, in order, are defined in `references/formatting-rules.md` (Standard Headers, Mode 1) and laid out in the templates; this table says what each new or non-obvious section must contain.

| File | Section | Content |
|---|---|---|
| `01-system-design.md` | System Context | C4 Context diagram (Mermaid `flowchart`, C4 labels): your system as one box, people, external systems |
| `01-system-design.md` | Core Components | C4 Container diagram plus component table (component, responsibility, why it exists) |
| `01-system-design.md` | Data Flow | Mermaid `sequenceDiagram` for the main request, plus notes on the hops that need them |
| `01-system-design.md` | Capacity Estimates | Table: metric, formula, value (average and peak QPS, read/write split, storage per year, bandwidth, cache size). Formulas and worked example in `references/capacity-estimation.md`; peak = 3× average unless stated |
| `01-system-design.md` | Key Architectural Decisions | Decision table; each row becomes an ADR |
| `02-architecture-decisions.md` | Data Model | Entities table (entity, key fields, relationships, key indexes + why) plus a Mermaid `erDiagram`, max 8 entities |
| `02-architecture-decisions.md` | Security | STRIDE-lite table: threat, asset, mitigation |
| `02-architecture-decisions.md` | Observability | Logs, metrics (RED/USE), traces, alerts with thresholds |
| `02-architecture-decisions.md` | Deployment & Cost | Deployment shape table and a rough monthly cost range with stated assumptions |
| `03-api-design-and-optimization.md` | Endpoints | Key endpoints table (method, path, purpose, auth, request → response), ≤12 rows, plus an OpenAPI 3.1 skeleton for the top 3–5 endpoints only |

Every other section follows its template. Length: about 3–4 pages per file, about half a page per section.

## ADRs

- Write one ADR per row of Key Architectural Decisions, max 5, to `docs/adr/NNNN-<kebab-title>.md` using `templates/adr/0000-template.md`. Rules (numbering, format, one page max) in `references/adr-guide.md`.
- Numbering continues from existing ADRs in `docs/adr/`.
- Opt-out: if the user says "no ADRs", write none and drop the ADR line under Key Architectural Decisions.
- **Re-run:** existing ADRs stay in `docs/adr/` and are never moved or archived. Write a new ADR only for a decision that changed; the ADR it replaces gets exactly one edit, its Status line becomes `Superseded by NNNN`. Unchanged decisions get no new ADR.

## Decision Guidance

| Situation | Lean toward | Why |
|---|---|---|
| Team under 8, unclear domain boundaries | Modular monolith | Lowest operational cost, easy to split later |
| Independent scaling needs per domain, team 10+ | Microservices | Only when team and ops maturity can carry it |
| Read-heavy, simple queries | Cache + read replicas | Cheaper than premature sharding |
| Event fan-out, async workflows | Queue / event-driven | Decouples producers from consumers |
| Public API for third parties | REST + versioning | Widest tooling and client support |
| Internal service-to-service, high throughput | gRPC | Typed contracts, lower overhead |
| Highly varied client data needs | GraphQL | Clients fetch only what they need |

These are defaults. Override them whenever the collected context or the Capacity Estimates say otherwise, and say why.

## Self-Check Before Delivering

- [ ] Standard headers in order (`references/formatting-rules.md`); Scope and Context lines at the top
- [ ] Every recommendation has a one-line "Why"; every decision is a single pick
- [ ] C4 Context and Container diagrams, Data Flow `sequenceDiagram` and Data Model `erDiagram` are Mermaid, max about 12 nodes (8 entities)
- [ ] Capacity Estimates show formula and value per metric; inputs listed under Assumptions & Constraints
- [ ] Endpoints table ≤12 rows; OpenAPI skeleton covers 3–5 endpoints only
- [ ] Security, Observability (with alert thresholds) and Deployment & Cost (with cost range and assumptions) filled
- [ ] Assumptions listed, defaults marked `Assumed (non-interactive)` when no questions could be asked
- [ ] ADRs: one per Key Architectural Decision row, max 5, numbering continued, unless the user said "no ADRs"; on a re-run only changed decisions get a new ADR
- [ ] Sections that do not apply are omitted; about half a page per section, about 3–4 pages per file
- [ ] Three core files; no `04-*` unless requested
