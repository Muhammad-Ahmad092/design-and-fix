# System Design

> Scope: Food-delivery platform (customer app, restaurant dashboard, courier app, backend)
> Context: 50k users, team of 4, Node.js preferred, cost-sensitive, single launch city
> Format: 1.0.2
> Audit: plugin 1.1.0 · scanner not run · rev unknown · 2026-11-05T18:30:00Z

## Overview

A food-delivery platform where customers order from nearby restaurants, restaurants accept and prepare orders, and couriers deliver them with live tracking. A team of 4 must build and run it, so operational simplicity outranks independent scaling.

### Requirements

| Requirement | Value | Source | Effect on design |
|---|---|---|---|
| Goal | Customers order from nearby restaurants; couriers deliver with live tracking | user | One product in one launch city: one deployable covers it |
| Functional | Menus, ordering and payment, restaurant accept and prepare, courier dispatch, live tracking | user | Modules identity, catalog, ordering, payments, dispatch and tracking |
| Non-functional | p95 < 300 ms for API reads and writes, excluding the provider call | assumption (non-interactive) | Slow provider calls run in the worker, off the request path |
| Roles | Customer, restaurant staff, courier, admin | assumption (non-interactive) | Role check per route; three client apps over one API |
| Traffic and data estimate | 50k users, 10k DAU, about 30 peak QPS plus 20 courier pings/s | assumption (non-interactive) | One PostgreSQL primary without sharding; pings go to Redis |
| Availability and recovery target | 99.9%; RPO 1 h, RTO 4 h (skill default) | assumption (non-interactive) | Managed PostgreSQL with standby and point-in-time recovery meets it without a second region |
| Compliance | No special compliance; card data stays with the payment provider | assumption (non-interactive) | Hosted provider with tokenised cards keeps full PCI DSS out of scope |
| Budget | Cost-sensitive | assumption (non-interactive) | Managed services in one region, about 300–600 USD/month |
| Deployment constraint | One cloud region, single launch city | assumption (non-interactive) | No multi-region replication or global load balancing |
| Team capacity | Team of 4 (stated); mid-level, TypeScript (assumed) | assumption | Modular monolith on NestJS; no service mesh or self-run clusters |

## System Context

```mermaid
flowchart LR
    customer["Person: Customer"]
    staff["Person: Restaurant staff"]
    courier["Person: Courier"]
    system["System: Food Delivery Platform"]
    pay["External: Payment provider"]
    maps["External: Maps and routing API"]
    notify["External: Push and SMS provider"]
    customer -->|"browses, orders, tracks, HTTPS"| system
    staff -->|"manages menu, accepts orders, HTTPS"| system
    courier -->|"accepts jobs, sends location, HTTPS/WSS"| system
    system -->|"charges cards, HTTPS"| pay
    system -->|"geocodes, estimates ETA, HTTPS"| maps
    system -->|"sends push and OTP SMS, HTTPS"| notify
```

## Core Components

```mermaid
flowchart LR
    customer["Person: Customer"]
    subgraph sys["System: Food Delivery Platform"]
        capp["Container: Customer app (React Native)"]
        rweb["Container: Restaurant dashboard (React)"]
        kapp["Container: Courier app (React Native)"]
        api["Container: API (Node.js, NestJS)"]
        worker["Container: Worker (Node.js, BullMQ)"]
        db[("Container: Primary DB (PostgreSQL)")]
        redis[("Container: Cache, queue, locations (Redis)")]
    end
    pay["External: Payment provider"]
    notify["External: Push and SMS provider"]
    customer -->|"uses, HTTPS"| capp
    capp -->|"JSON, HTTPS"| api
    rweb -->|"JSON, HTTPS"| api
    kapp -->|"JSON/WSS"| api
    api -->|"reads/writes, SQL"| db
    api -->|"cache, jobs, pub/sub"| redis
    redis -->|"jobs"| worker
    worker -->|"reads/writes, SQL"| db
    api -->|"charges, HTTPS"| pay
    worker -->|"notifies, HTTPS"| notify
```

| Component | Responsibility | Required now / Later | Why it exists |
|---|---|---|---|
| Customer app | Browse restaurants, order, pay, track | Required now | Primary revenue channel |
| Restaurant dashboard | Menu management, accept and ready orders | Required now | Restaurants work from a tablet browser, no store install |
| Courier app | Job offers, status updates, location pings | Required now | Needs background GPS, so native |
| API (modular monolith) | Modules: identity, catalog, ordering, payments, dispatch, tracking | Required now | One deployable for a team of 4; modules keep boundaries for a later split |
| Worker | Notifications, payment webhooks, dispatch retries | Required now | Keeps slow external calls off the request path |
| PostgreSQL | Users, restaurants, menus, orders, payments, deliveries | Required now | Transactions for order and payment state |
| Redis | Menu cache, BullMQ jobs, latest courier positions, rate limits | Required now | One extra store covers four needs |
| Search service | Full-text restaurant and dish search | Later: when restaurants exceed about 2,000 or PostgreSQL search p95 passes 300 ms | A single launch city is small enough for PostgreSQL and PostGIS filters |

`Later` components stay out of the diagram until their trigger is met.

## Data Flow

Main request: place order

```mermaid
sequenceDiagram
    actor U as Customer app
    participant A as API
    participant D as PostgreSQL
    participant P as Payment provider
    participant Q as Redis (BullMQ)
    U->>A: POST /v1/orders (Idempotency-Key)
    A->>D: insert order (status=pending) + items, one transaction
    D-->>A: order id
    A->>P: create payment (idempotency key = order id, 5 s timeout)
    P-->>A: authorized
    A->>D: update order status=placed, insert payment (authorized)
    A->>Q: enqueue notify-restaurant, start-dispatch
    A-->>U: 201 + order id, status=placed
```

- The `Idempotency-Key` is stored with a unique index on `orders`, so a retried request returns the first order instead of a second charge.
- Card authorization only; capture happens when the restaurant accepts, so a rejected order needs no refund.
- A failed notify or dispatch job retries with exponential backoff (5 tries), then alerts on-call; the order stays `placed`.

## Capacity Estimates

Formulas in `references/capacity-estimation.md`.

| Metric | Formula | Value |
|---|---|---|
| Daily requests | 10,000 DAU × 30 + 200 couriers × 2,880 location pings | 876,000 |
| Average QPS | 876,000 / 86,400 | ≈ 10.1 |
| Peak QPS | 3× average | ≈ 30 |
| Read / write split | 240,000 reads / 636,000 writes per day (27% / 73%) of peak QPS | ≈ 8 / 22 |
| Storage per year | 60,000 persisted writes/day × 1 KB × 365 × 3 | ≈ 66 GB |
| Bandwidth (peak) | 30 × 10 KB | ≈ 0.3 MB/s (≈ 2.4 Mbit/s) |
| Cache size (hot set) | 20% × 240,000 reads/day × 2 KB | ≈ 96 MB |

Location pings are 90% of writes but go to Redis, not PostgreSQL: one API pair, one PostgreSQL primary with a standby and one small Redis carry this load without sharding.

## Key Architectural Decisions

| Decision | Chosen | Alternative considered | Why |
|---|---|---|---|
| Architecture pattern | Modular monolith (Node.js, NestJS) | Microservices | Team of 4 at 30 peak QPS cannot carry per-service ops |
| Primary database | PostgreSQL | MongoDB | Orders and payments need multi-row transactions |
| Live courier tracking | Redis latest-position keys + pub/sub over WebSocket | Polling positions stored in PostgreSQL | About 90% of writes are pings; keep them off the primary DB |
| Background work | BullMQ job queue on Redis | Kafka | Reuses Redis; job volume is thousands per day, not millions |
| Payments | Hosted payment provider with tokenised cards and idempotency keys | Storing and charging cards in-house | Keeps the system out of full PCI DSS scope |

Each decision compares its options on the same ten columns (Low / Medium / High plus the reason that matters here).

### Trade-offs: Architecture pattern

| Option | Complexity | Scalability | Reliability | Security | Operations | Cost | Familiarity | Lock-in | Migration path |
|---|---|---|---|---|---|---|---|---|---|
| Modular monolith (chosen) | Low: one deployable | Medium: modules scale together | Medium: one bad release hits every module | Medium: one boundary to secure | Low: one pipeline | Low | High: NestJS known | Low | Split a module into a service along its existing boundary |
| Microservices | High: contracts and tracing | High: per domain | Medium: partial failures to handle | Medium: many service boundaries | High: per-service ops for a team of 4 | High | Low | Medium: platform tooling | Merging services back is costly |
| Serverless functions | Medium | High | Medium: cold starts on checkout | Medium | Low | Low at this volume | Medium | High: provider runtime | Rewrite handlers to leave; WebSocket tracking is awkward |

### Trade-offs: Primary database

| Option | Complexity | Scalability | Reliability | Security | Operations | Cost | Familiarity | Lock-in | Migration path |
|---|---|---|---|---|---|---|---|---|---|
| PostgreSQL (chosen) | Low | Medium: one primary plus replicas | High: transactions and managed standby | High: row-level constraints | Low: managed service | Low | High | Low: standard SQL | Read replicas first, then partition orders by city |
| MongoDB | Low | High: sharding built in | Medium: multi-document transactions are newer | Medium | Low: managed service | Medium | Medium | Medium: query API | Reshape documents into tables to leave |

### Trade-offs: Live courier tracking

| Option | Complexity | Scalability | Reliability | Security | Operations | Cost | Familiarity | Lock-in | Migration path |
|---|---|---|---|---|---|---|---|---|---|
| Redis keys + pub/sub over WebSocket (chosen) | Medium: socket gateway | High: pings stay off PostgreSQL | Medium: positions lost on a Redis restart (acceptable) | Medium: socket auth needed | Medium: one more managed service | Low | Medium | Low | Move to a managed pub/sub service behind the same gateway |
| Polling positions stored in PostgreSQL | Low | Low: 20 writes/s plus polling reads on the primary | Medium | High: no new surface | Low | Low | High | Low | Add Redis later when the primary saturates |

### Trade-offs: Background work

| Option | Complexity | Scalability | Reliability | Security | Operations | Cost | Familiarity | Lock-in | Migration path |
|---|---|---|---|---|---|---|---|---|---|
| BullMQ on Redis (chosen) | Low | Medium: thousands of jobs/day | Medium: retries and backoff built in | Medium | Low: reuses Redis | Low | High: Node.js library | Low | Swap the queue adapter for a managed queue |
| Kafka | High | High: millions of events/day | High: replayable log | Medium | High: cluster to run | High | Low | Medium | Event replay rarely needed at this volume |

### Trade-offs: Payments

| Option | Complexity | Scalability | Reliability | Security | Operations | Cost | Familiarity | Lock-in | Migration path |
|---|---|---|---|---|---|---|---|---|---|
| Hosted provider, tokenised cards (chosen) | Low | High: provider scales | High: provider SLA plus idempotency keys | High: card data never stored | Low | Medium: per-transaction fee | Medium | Medium: provider API | Second provider behind the same payments module |
| Storing and charging cards in-house | High | Medium | Medium | Low: full PCI DSS scope | High: audits and key management | High | Low | Low | Hard to undo once card data is stored |

ADRs: one per row in `docs/adr/` (shipped in this example under `adr/`).

## Requirements → Decisions

| Requirement | Decision(s) | How it is met |
|---|---|---|
| 50k users, about 30 peak QPS | Architecture pattern, Primary database | One API pair and one PostgreSQL primary carry the load without sharding |
| Team of 4, Node.js preferred | Architecture pattern, Background work | One NestJS deployable and one worker, no service mesh |
| Live courier tracking | Live courier tracking | Latest positions in Redis, pushed over WebSocket |
| No double charge on retries | Payments, Primary database | Provider idempotency key and a unique `idempotency_key` index on orders |
| Card data kept out of scope | Payments | Hosted provider with tokenised cards |
| p95 < 300 ms, 99.9% availability (Assumed) | Primary database, Background work | Managed PostgreSQL with standby; slow external calls run in the worker |
| Cost-sensitive, single launch city | Architecture pattern, Background work | About 300–600 USD/month in one region (see `02`, Deployment & Cost) |

## Assumptions & Constraints

- Users: 50k registered customers (stated); 20% daily active = 10k DAU, 30 requests per DAU (Assumed (non-interactive))
- Couriers: 200 on shift at once, one location ping every 10 s over 8 h (Assumed (non-interactive))
- Team: 4 (stated); mid-level, comfortable with TypeScript (Assumed (non-interactive))
- Roles: customer, restaurant staff, courier, admin (Assumed (non-interactive))
- Stack: Node.js (stated) with TypeScript and PostgreSQL (Assumed (non-interactive), skill default)
- Latency: p95 < 300 ms for API reads and writes, excluding the provider call (Assumed (non-interactive))
- Availability: 99.9%; recovery: RPO 1 h, RTO 4 h, the skill default (Assumed (non-interactive))
- Compliance: no special compliance; card data stays with the payment provider (Assumed (non-interactive))
- Cost: cost-sensitive; one cloud region, single launch city (Assumed (non-interactive))
- Sizing inputs: record 1 KB, response 10 KB, menu object 2 KB, peak = 3× average, replication 3 (Assumed (non-interactive))
