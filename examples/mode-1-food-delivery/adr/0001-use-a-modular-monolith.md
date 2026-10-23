# 0001. Use a modular monolith

Date: 2026-10-22
Status: Accepted

## Context

The platform serves about 50k users (≈ 30 peak QPS) and is built and run by a team of 4 who prefer Node.js. Domains (catalog, ordering, payments, dispatch, tracking) are known, but their load profiles are not yet measured. The team cannot staff on-call for many deployables.

## Decision

We will use a modular monolith: one NestJS API and one worker from one repository, with one module per domain and no cross-module table access (enforced by a lint rule). Alternatives considered: microservices (rejected because per-service deploys, tracing and contracts cost more than a team of 4 can carry at 30 peak QPS).

## Consequences

- Positive: one build, one deploy, in-process calls and plain transactions across modules.
- Negative: one bad release affects every domain; modules scale together.
- Revisit when: one module needs more than 4× the instances of the rest, or the team grows past about 10 engineers.
