# API Optimization

> Scope: services/payments, services/orders, services/catalog in `tests/fixtures/monorepo` (synthetic fixture; all paths relative to it). App/ (.NET stub) not assessed.
> Context: about 50 req/s peak, must keep PostgreSQL, pain point: checkout (charge) is slow and sometimes double-charges

## API Health Snapshot

| Endpoint group | Worst severity | Issue IDs | Note |
|---|---|---|---|
| /payments/charge | Critical | REL-001, SEC-001, REL-002, SCL-001, REL-003, API-001 | Both pain points live here: unbounded provider wait and no idempotency |
| /payments/refund | Critical | SEC-001, API-002 | Auth check is presence-only (`refund.ts:8`); 5 s timeout already set (`refund.ts:18`) |
| /payments/:id | Medium | REL-005, API-002, API-004 | Hangs on a DB error; returns 200 `null` for a missing charge |
| /orders | Critical | SEC-004, API-003 | Handler is a stub that returns 201 without persisting (`orders.go:39`) |
| /items, /items/<id> | Critical | SEC-002, SEC-004, REL-004, REL-005, API-001, API-002, API-003 | Injection, no auth and an untimed pricing call |
| /healthz | — | — | Only health endpoint in scope (orders) |

## Endpoint Inventory

| Method | Path | File | Auth detected |
|---|---|---|---|
| POST | /payments/charge | `services/payments/src/handlers/charge.ts:14` | no |
| POST | /payments/refund | `services/payments/src/handlers/refund.ts:15` | yes (presence-only, SEC-001) |
| GET | /payments/:id | `services/payments/src/handlers/webhook.ts:6` | no |
| POST | /orders | `services/orders/src/orders.go:44` | no |
| GET | /healthz | `services/orders/src/orders.go:45` | no |
| POST | /items | `services/catalog/app.py:16` | no |
| GET | /items/<int:item_id> | `services/catalog/app.py:25` | no |

Routes from the scanner in the audited scope (7). `auth_detected` is the scanner's name-based signal; reading the files confirms the six `no` values and shows the one `yes` (`refund.ts:8`) only checks that an `Authorization` header is present.

## Issues Found

| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|---|
| API-001 | High | Write routes accept unvalidated bodies | `services/payments/src/handlers/charge.ts:15`, `services/catalog/app.py:20` | `req.body as {…}` is a compile-time cast only; `payload["name"]` assumes the key | A body without `items` throws in `totalOf` (line 33) inside an un-awaited async handler: the request hangs, and on Node 15+ the process exits (Node version Not verified) | Validate with a schema (zod in payments, pydantic in catalog) and return 400 | Bad input must never take down checkout |
| API-002 | Medium | Error responses differ per route | `services/payments/src/handlers/refund.ts:9`, `services/catalog/app.py:32`, `services/payments/src/handlers/webhook.ts:10` | `{ "error": … }` in refund, empty `{}` with 502 in catalog, 200 `null` for a missing charge | Clients need per-route error handling; "not found" looks like success | One `application/problem+json` (RFC 9457) envelope; 404 for missing resources | One client error path for all services |
| API-003 | Medium | `POST /items` and `POST /orders` return 201 but create nothing | `services/catalog/app.py:21`, `services/catalog/app.py:22`, `services/orders/src/orders.go:39` | `/items` runs a `SELECT` and returns its rows; `/orders` is a stub returning `{"ok": true}` | Callers believe items and orders were created | `INSERT … RETURNING *` and 201 with the created resource (409 on duplicate name) | The status code must match the effect |
| API-004 | Low | `GET /payments/:id` lives in `webhook.ts` | `services/payments/src/handlers/webhook.ts:6` | File name does not match its only route; no webhook route exists | Readers look for webhook handling that is not there | Rename to `charges.ts` | Files named after their routes |

Only `API-*` issues are defined here; other categories are referenced by ID from `01-architecture-issues.md`.

## Caching Opportunities

| Target (IDs) | Cache what, where | Invalidation | Why |
|---|---|---|---|
| POST /payments/charge (REL-001) | Final response per `Idempotency-Key`, in the PostgreSQL `charges` table | Keep 24 h, nightly purge | Retries replay the stored result without a second provider call; keeps PostgreSQL |
| GET /items/<int:item_id> (REL-004) | Pricing response per item, in-process TTL cache in catalog | TTL 60 s | Removes the external call from most reads; 50 req/s needs no shared cache |

- `GET /payments/:id` needs no cache: a primary-key lookup (`webhook.ts:8`) is already cheap.

## Quick Wins vs Structural Fixes

**Quick Wins**
- REL-002, REL-003: 5 s abort on the provider fetch and a status check before recording items. Why: cuts the slow-checkout tail in hours.
- API-003, API-004: make `POST /items` and `POST /orders` persist; rename `webhook.ts`. Why: contract matches behaviour at near-zero cost.

**Structural Fixes**
- REL-001, SCL-001: idempotent, transactional charge flow (key table, one multi-row insert). Why: removes double charges and N round trips together.
- API-001, API-002: shared request validation and one error envelope across the three services. Why: consistent contracts before more clients integrate.
