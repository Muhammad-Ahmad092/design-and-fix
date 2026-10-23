# Architecture Decisions

> Scope: <project name>
> Context: <scale> users, team of <size>, <key constraint>

## Pattern Choice

**Chosen: <Monolith / Modular Monolith / Microservices>**

<2–3 lines justifying the pick against the collected context>

## Scaling Strategy

| Layer | Approach | Why |
|---|---|---|
| Compute | <horizontal / vertical> | <reason> |
| Caching | <what is cached, where> | <reason> |
| Load balancing | <approach> | <reason> |
| Async work | <queue / none> | <reason> |

## Data Storage

| Store | Choice | Why |
|---|---|---|
| Primary DB | <e.g. PostgreSQL> | <reason> |
| <Cache / object store / search, only if needed> | <choice> | <reason> |
| <Replication or sharding, only if relevant> | <approach> | <reason> |

## Data Model

| Entity | Key fields | Relationships | Key indexes (why) |
|---|---|---|---|
| <Entity A> | <id, field, field> | <has many Entity B> | <(field, created_at): list by owner> |
| <Entity B> | <id, a_id, field> | <belongs to Entity A> | <(a_id): join to A> |

```mermaid
erDiagram
    ENTITY_A ||--o{ ENTITY_B : "<has>"
    ENTITY_A {
        uuid id PK
        string field
    }
    ENTITY_B {
        uuid id PK
        uuid a_id FK
        string field
    }
```

## Failure Modes

| Scenario | Consequence | Mitigation | Why |
|---|---|---|---|
| <e.g. DB primary goes down> | <consequence> | <mitigation> | <reason> |
| <e.g. downstream provider timeout> | <consequence> | <mitigation> | <reason> |

## Security

| Threat (STRIDE) | Asset | Mitigation | Why |
|---|---|---|---|
| <e.g. Spoofing: stolen session token> | <user accounts> | <short-lived JWT + refresh rotation> | <reason> |
| <e.g. Tampering: SQL injection> | <primary DB> | <parameterized queries only> | <reason> |
| <e.g. Information disclosure: secrets in repo> | <provider keys> | <secret manager, env injection> | <reason> |

## Observability

| Signal | What | Alert threshold | Why |
|---|---|---|---|
| Logs | <structured JSON, request ID, user ID> | — | <reason> |
| Metrics (RED) | <rate, errors, duration per endpoint> | <e.g. 5xx > 1% for 5 min; p95 > 300 ms> | <reason> |
| Metrics (USE) | <CPU, memory, DB connections, queue depth> | <e.g. DB connections > 80%> | <reason> |
| Traces | <OpenTelemetry across API → DB → provider> | — | <reason> |

## Deployment & Cost

| Component | Runs on | Size / count | Monthly cost (USD) |
|---|---|---|---|
| <API> | <e.g. managed containers> | <e.g. 2 × 1 vCPU> | <range> |
| <Database> | <e.g. managed PostgreSQL> | <e.g. 2 vCPU, 8 GB + replica> | <range> |
| <Cache / queue> | <service> | <size> | <range> |

Total: about <low>–<high> USD/month. Assumptions: <region, traffic from Capacity Estimates, pricing tier>.

## Trade-offs Accepted

- <what is sacrificed> for <what gain>
- <what is sacrificed> for <what gain>
