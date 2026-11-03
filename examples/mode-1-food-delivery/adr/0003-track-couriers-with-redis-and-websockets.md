# 0003. Track couriers with Redis and WebSockets

Date: 2026-10-22
Status: Accepted
> Format: 1.0.2

## Context

About 200 couriers ping their position every 10 s: 576,000 writes per day, about 66% of all requests and 90% of all writes. Customers want a live map, but only the latest position matters; no product feature needs the full track.

## Alternatives

| Option | Strength | Weakness | Why not chosen |
|---|---|---|---|
| Pings in PostgreSQL, clients poll | One store, full history | 90% of writes and constant polling reads hit the primary | Would dominate load for a feature that needs only the latest point |

## Decision

We will store each courier's latest position in a Redis key (expires after 5 min) and push updates to customers over a WebSocket fed by Redis pub/sub.

## Consequences

- Positive: PostgreSQL write load drops by about 90%; updates reach customers in under a second.
- Negative: no location history for disputes; WebSocket connections need reconnect logic on deploys.
- Revisit when: disputes or analytics need stored tracks (then sample one point per minute into a separate table).

## Assumptions

- About 200 couriers on shift, one ping per 10 s (Assumed (non-interactive)).
- No product feature needs stored tracks at launch.
