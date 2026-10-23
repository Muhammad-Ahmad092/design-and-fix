# 0004. Use BullMQ for background jobs

Date: 2026-10-22
Status: Accepted

## Context

Notifications, payment webhooks and dispatch retries must not run on the request path, and must retry on failure. Volume is thousands of jobs per day. Redis is already part of the design (cache, locations, rate limits).

## Decision

We will use BullMQ on the existing Redis, consumed by one Node.js worker, with exponential backoff (5 tries) and a failed-job alert. Alternatives considered: Kafka (rejected because a cluster to run, plus event replay nobody needs yet, is too much for thousands of jobs per day and a team of 4).

## Consequences

- Positive: no new infrastructure; jobs, retries and delays in TypeScript.
- Negative: no long-term event log; Redis loss can drop queued jobs between AOF syncs.
- Revisit when: several consumers need the same events, or job volume reaches millions per day.
