# Mode 1: New Project

Trigger: user provides an idea, PRD, doc, or rough description of a project that is not built yet.

## Workflow

```
Read input -> Context Collection -> Decide -> Fill 3 templates -> Self-check -> Deliver
```

1. **Read input.** Extract what the system does, who uses it, and any stated constraints.
2. **Context Collection.** Follow `references/context-collection.md` (Mode 1 questions). Fill gaps only.
3. **Decide.** Make one clear choice per decision. No "it depends" without a pick.
4. **Fill templates** from `templates/mode-1/`. Write to `docs/architecture/`.
5. **Self-check** against the checklist below.
6. **Deliver.** List the 3 files created, nothing more.

## Output: Exactly 3 Files

### `01-system-design.md`

| Section | Content |
|---|---|
| Overview | 2-3 lines: what the system does, who it is for |
| Core Components | Table: component, responsibility, why it exists |
| Data Flow | Text flow (A -> B -> C) with a one-line note per hop |
| Key Architectural Decisions | Table: decision, chosen option, alternative considered, why |
| Assumptions & Constraints | Bullets from context collection, including assumed defaults |

### `02-architecture-decisions.md`

| Section | Content |
|---|---|
| Pattern Choice | Monolith, modular monolith, or microservices: pick one, justify in 2-3 lines |
| Scaling Strategy | Table: horizontal/vertical, caching layers, load balancing |
| Data Storage | DB choice(s), why, sharding/replication notes only if relevant |
| Failure Modes | Table: scenario, impact, mitigation |
| Trade-offs Accepted | Bullets: what is sacrificed for what gain |

### `03-api-design-and-optimization.md`

| Section | Content |
|---|---|
| API Style | REST, GraphQL, or gRPC: one choice, one-line reason |
| Endpoint Conventions | Naming, versioning strategy, status code rules |
| Auth & Rate Limiting | Approach + why |
| Caching Strategy | What is cached, where, invalidation approach |
| Performance Notes | Pagination, batching, N+1 avoidance as bullets |

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

These are defaults. Override them whenever the collected context says otherwise, and say why.

## Self-Check Before Delivering

- [ ] Every recommendation has a one-line "why"
- [ ] Every decision is a single pick, not a menu
- [ ] Assumptions from context collection are listed
- [ ] Sections that do not apply are omitted (no filler)
- [ ] No section exceeds roughly half a page of prose
- [ ] Tables are used wherever there are more than 2 comparable items
- [ ] Exactly 3 files; no `04-*` unless requested
