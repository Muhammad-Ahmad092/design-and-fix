# 0002. Use PostgreSQL as the primary database

Date: 2026-10-22
Status: Accepted

## Context

Orders, order items and payments change together and must never disagree (a paid order without items, or a charge without an order). Persisted writes are about 60,000 per day (≈ 66 GB per year with replication), and "restaurants near me" needs geo queries.

## Decision

We will use managed PostgreSQL 16 with a hot standby as the store of record, with PostGIS for nearby search. Alternatives considered: MongoDB (rejected because order and payment state needs multi-row transactions and relational constraints, and the team would still need a separate geo index strategy).

## Consequences

- Positive: ACID transactions, unique constraints for idempotency keys, PostGIS in the same database.
- Negative: vertical scaling first; schema migrations need care during meal peaks.
- Revisit when: sustained write load exceeds what one primary handles (well above the estimated 22 peak writes/s), or storage passes about 1 TB.
