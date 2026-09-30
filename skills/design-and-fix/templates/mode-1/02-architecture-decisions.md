# Architecture Decisions

> Scope: <project name>
> Context: <scale> users, team of <size>, <key constraint>

## Pattern Choice

**Chosen: <Monolith / Modular Monolith / Microservices>**

<2-3 lines justifying the pick against the collected context>

## Scaling Strategy

| Layer | Approach | Why |
|---|---|---|
| Compute | <horizontal / vertical> | <reason> |
| Caching | <what is cached, where> | <reason> |
| Load Balancing | <approach> | <reason> |

## Data Storage

| Store | Choice | Why |
|---|---|---|
| Primary DB | <e.g. PostgreSQL> | <reason> |
| <Sharding / replication, only if relevant> | <approach> | <reason> |

## Failure Modes

| Scenario | Impact | Mitigation |
|---|---|---|
| <e.g. DB primary goes down> | <impact> | <mitigation> |
| <e.g. downstream service timeout> | <impact> | <mitigation> |

## Trade-offs Accepted

- <what is sacrificed> for <what gain>
- <what is sacrificed> for <what gain>
