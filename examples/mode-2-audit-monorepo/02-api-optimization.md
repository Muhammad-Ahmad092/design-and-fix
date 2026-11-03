# API Optimization

> Scope: services/payments, services/orders, services/catalog in `tests/fixtures/monorepo` (synthetic fixture; all paths relative to it). App/ (.NET stub) not assessed.
> Context: payments checkout, about 50 req/s peak, must keep PostgreSQL, pain point: checkout (charge) is slow and sometimes double-charges
> Format: 1.0.2
> Audit: plugin 1.0.2 · scanner schema 3 · rev unknown · config 3d96e5d5ea6d · 2026-11-03T11:54:08Z

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

| ID | Severity | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|---|
| API-001 | High | confirmed | scanner:unvalidated-body | Write routes accept unvalidated bodies | `services/payments/src/handlers/charge.ts:15`, `services/catalog/app.py:20` | `req.body as {…}` is a compile-time cast only; `payload["name"]` assumes the key | A body without `items` throws in `totalOf` (line 33) inside an un-awaited async handler: the request hangs, and on Node 15+ the process exits (Node version Not verified) | Validate with a schema (zod in payments, pydantic in catalog) and return 400 | Test: `POST /payments/charge` with `{}` returns 400 and the process stays up | Bad input must never take down checkout |
| API-002 | Medium | confirmed | agent | Error responses differ per route | `services/payments/src/handlers/refund.ts:9`, `services/catalog/app.py:32`, `services/payments/src/handlers/webhook.ts:10` | `{ "error": … }` in refund, empty `{}` with 502 in catalog, 200 `null` for a missing charge | Clients need per-route error handling; "not found" looks like success | One `application/problem+json` (RFC 9457) envelope; 404 for missing resources | Test: a missing charge ID returns 404 with the shared envelope | One client error path for all services |
| API-003 | Medium | confirmed | agent | `POST /items` and `POST /orders` return 201 but create nothing | `services/catalog/app.py:21`, `services/catalog/app.py:22`, `services/orders/src/orders.go:39` | `/items` runs a `SELECT` and returns its rows; `/orders` is a stub returning `{"ok": true}` | Callers believe items and orders were created | `INSERT … RETURNING *` and 201 with the created resource (409 on duplicate name) | Test: a created item can be fetched by `GET /items/<id>` | The status code must match the effect |
| API-004 | Low | confirmed | agent | `GET /payments/:id` lives in `webhook.ts` | `services/payments/src/handlers/webhook.ts:6` | File name does not match its only route; no webhook route exists | Readers look for webhook handling that is not there | Rename to `charges.ts` | — | Files named after their routes |

- API-001: Observed: bodies are cast, never checked, before use. Inference: malformed input crashes or hangs the handler. Limitations: an upstream validation layer is Not verified (none in the repo).

Only `API-*` issues are defined here; other categories are referenced by ID from `01-architecture-issues.md`.

## Caching Opportunities

| Target (IDs) | Cache what, where | Invalidation | Why |
|---|---|---|---|
| POST /payments/charge (REL-001) | Final response per `Idempotency-Key`, in the PostgreSQL `charges` table | Keep 24 h, nightly purge | Retries replay the stored result without a second provider call; keeps PostgreSQL |
| GET /items/<int:item_id> (REL-004) | Pricing response per item, in-process TTL cache in catalog | TTL 60 s | Removes the external call from most reads; 50 req/s needs no shared cache |

- `GET /payments/:id` needs no cache: a primary-key lookup (`webhook.ts:8`) is already cheap.

## Quick Wins vs Structural Fixes

| Recommendation (IDs) | Category | Impact | Effort | Risk | Verification | Why |
|---|---|---|---|---|---|---|
| API-003, API-004: make `POST /items` and `POST /orders` persist; rename `webhook.ts` | correctness | Medium | S | Low | Created item is returned by `GET /items/<id>` | Contract matches behaviour at near-zero cost |
| API-001: shared request validation, `400` on malformed bodies | correctness | High | M | Low | `{}` to `POST /payments/charge` returns 400 | Malformed input stops hanging or crashing the service |
| API-002: one error envelope across the three services | maintainability | Medium | M | Medium | Missing charge returns 404 with the shared envelope | Consistent contracts before more clients integrate |
| REL-001, SCL-001 (see `01`): idempotent, transactional charge flow | performance | High | M | Medium | Hypothesis: time `POST /payments/charge` p95 with k6 at 50 req/s before/after | Removes double charges and N round trips together |

Small fixes (S/M effort) first, structural fixes (L/XL) after; no L/XL item exists in the API surface.
