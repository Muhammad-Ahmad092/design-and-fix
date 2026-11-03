# 0004. Use BullMQ for background jobs

Date: 2026-10-22
Status: Accepted
> Format: 1.0.2

## Context

Notifications, payment webhooks and dispatch retries must not run on the request path, and must retry on failure. Volume is thousands of jobs per day. Redis is already part of the design (cache, locations, rate limits).

## Alternatives

| Option | Strength | Weakness | Why not chosen |
|---|---|---|---|
| Kafka | Durable log, replay, many consumers | A cluster to run and tune | Too much for thousands of jobs per day and a team of 4 |

## Decision

We will use BullMQ on the existing Redis, consumed by one Node.js worker, with exponential backoff (5 tries) and a failed-job alert.

## Consequences

- Positive: no new infrastructure; jobs, retries and delays in TypeScript.
- Negative: no long-term event log; Redis loss can drop queued jobs between AOF syncs.
- Revisit when: several consumers need the same events, or job volume reaches millions per day.

## Assumptions

- Job volume stays in the thousands per day.
- Redis runs with AOF persistence (a few seconds of queued jobs may be lost on failure).
