# System Design

> Scope: <project name>
> Context: <scale> users, team of <size>, <key constraint>

## Overview

<2–3 lines: what the system does, who it is for, the one constraint that shapes it most>

## System Context

```mermaid
flowchart LR
    user["Person: <primary user role>"]
    admin["Person: <secondary role>"]
    system["System: <system name>"]
    ext1["External: <external system, e.g. payment provider>"]
    ext2["External: <external system, e.g. email service>"]
    user -->|"<uses>, HTTPS"| system
    admin -->|"<manages>, HTTPS"| system
    system -->|"<charges cards>, HTTPS"| ext1
    system -->|"<sends email>, HTTPS"| ext2
```

## Core Components

```mermaid
flowchart LR
    user["Person: <primary user role>"]
    subgraph sys["System: <system name>"]
        web["Container: <web or mobile app> (<tech>)"]
        api["Container: <API> (<tech>)"]
        worker["Container: <worker> (<tech>)"]
        queue[("Container: <queue> (<tech>)")]
        db[("Container: <database> (<tech>)")]
    end
    ext1["External: <external system>"]
    user -->|"<uses>, HTTPS"| web
    web -->|"JSON, HTTPS"| api
    api -->|"SQL"| db
    api -->|"<publishes events>"| queue
    queue --> worker
    worker -->|"<calls>, HTTPS"| ext1
```

| Component | Responsibility | Why it exists |
|---|---|---|
| <name> | <what it does> | <reason it is a separate component> |
| <name> | <what it does> | <reason it is a separate component> |

## Data Flow

Main request: <name of the request, e.g. "place order">

```mermaid
sequenceDiagram
    actor U as <User>
    participant A as <API>
    participant D as <Database>
    participant Q as <Queue>
    U->>A: <request, e.g. POST /v1/orders>
    A->>D: <write, e.g. insert order (status=pending)>
    D-->>A: <result>
    A->>Q: <publish event>
    A-->>U: <response, e.g. 201 + order id>
```

- <step that needs a note: validation, idempotency key, transaction boundary>
- <async side effect and what happens if it fails>

## Capacity Estimates

Formulas in `references/capacity-estimation.md`.

| Metric | Formula | Value |
|---|---|---|
| Daily requests | <DAU> × <requests per user per day> | <value> |
| Average QPS | <daily requests> / 86,400 | <value> |
| Peak QPS | 3× average | <value> |
| Read / write split | <read %> / <write %> of peak QPS | <value> / <value> |
| Storage per year | <writes/day> × <record size> × 365 × <replication> | <value> |
| Bandwidth (peak) | <peak QPS> × <avg response size> | <value> |
| Cache size (hot set) | 20% × <daily reads> × <object size> | <value> |

<one line: what these numbers mean for the design>

## Key Architectural Decisions

| Decision | Chosen | Alternative considered | Why |
|---|---|---|---|
| <decision area> | <chosen option> | <alternative> | <reason> |
| <decision area> | <chosen option> | <alternative> | <reason> |

ADRs: one per row (max 5) in `docs/adr/`, unless the user opted out.

## Assumptions & Constraints

- <assumption or constraint from context collection>
- <default assumed where the user said "you decide", or `Assumed (non-interactive)`>
