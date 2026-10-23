# 0003. Track couriers with Redis and WebSockets

Date: 2026-10-22
Status: Accepted

## Context

About 200 couriers ping their position every 10 s: 576,000 writes per day, about 66% of all requests and 90% of all writes. Customers want a live map, but only the latest position matters; no product feature needs the full track.

## Decision

We will store each courier's latest position in a Redis key (expires after 5 min) and push updates to customers over a WebSocket fed by Redis pub/sub. Alternatives considered: writing pings to PostgreSQL and letting clients poll (rejected because it puts 90% of writes and constant polling reads on the primary database).

## Consequences

- Positive: PostgreSQL write load drops by about 90%; updates reach customers in under a second.
- Negative: no location history for disputes; WebSocket connections need reconnect logic on deploys.
- Revisit when: disputes or analytics need stored tracks (then sample one point per minute into a separate table).
