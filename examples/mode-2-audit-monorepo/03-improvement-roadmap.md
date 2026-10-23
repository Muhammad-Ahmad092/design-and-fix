# Improvement Roadmap

> Scope: services/payments, services/orders, services/catalog in `tests/fixtures/monorepo` (synthetic fixture; all paths relative to it). App/ (.NET stub) not assessed.
> Context: about 50 req/s peak, must keep PostgreSQL, pain point: checkout (charge) is slow and sometimes double-charges

Every issue ID from `01` and `02` appears in exactly one `Action (IDs)` row of the three horizon tables; Suggested Sequence may cite IDs again.

## Quick Wins (days)

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| SEC-003: load the catalog DB password from a secret-store env var and rotate it | S | High | The committed value is already exposed |
| SEC-002: bind `name` as a query parameter in `POST /items` | S | High | Removes SQL injection with a one-line change |
| REL-002: abort the provider fetch after 5 s and return 504 | S | High | Caps checkout latency and the retries behind double charges |
| REL-003: check the provider status before recording items; return 402/502 | S | High | Stops reporting failed charges as success |
| REL-004: `timeout=3` on the pricing call, 5 s `http.Client` in orders | S | Medium | One slow dependency can no longer pin workers |
| REL-005: log and return 500/502 in the two empty error handlers | S | Medium | Ends hung lookups and silent pricing failures |
| API-003: make `POST /items` and `POST /orders` persist and return the created row | S | Medium | Status code matches the effect |
| MNT-003: delete the 30 unused normalizer copies | S | Low | Removes about 540 lines of copy-paste |
| OBS-001: add `GET /healthz` with a PostgreSQL ping to payments and catalog | S | Medium | Lets the platform restart hung instances |
| SCL-002: add `LIMIT` and a keyset cursor to `listOrders` | S | Medium | Bounds memory before the query gets a route |
| SYS-001: move `formatRow` into `db.ts`, drop the `db` import from `util.ts` | S | Low | Breaks the cycle with no behaviour change |
| API-004: rename `webhook.ts` to `charges.ts` | S | Low | File name matches its route |

## Medium-term (weeks)

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| SEC-001: replace the presence-only `requireAuth` with JWT signature + expiry verification in `src/lib/auth.ts`, applied to charge and refund | M | High | Both money-moving routes are effectively open today |
| REL-001: `Idempotency-Key` header, `charges` table with a unique key, key forwarded to the provider, stored response replayed | M | High | Removes the double-charge pain point on PostgreSQL |
| SCL-001: write charge items with one multi-row insert inside the charge transaction | M | High | One round trip per checkout, no partial rows |
| MNT-002: Supertest suite for the charge route with a mocked provider and a repeated-key case | M | High | Proves a retry charges exactly once |
| API-001: schema validation on write routes plus an async error middleware in payments | M | High | Malformed input returns 400 instead of hanging or crashing |
| SEC-004: the same JWT verification as middleware on the orders (Gin) and catalog (Flask) write routes | M | High | Same auth policy on every write route |
| API-002: one `application/problem+json` error envelope and 404 for missing resources | M | Medium | Clients handle errors one way |
| MNT-001: handler tests for orders and catalog | M | Medium | Safety net for the SEC-004 and API-001 changes |

## Long-term (months/quarters)

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| OBS-002: structured JSON logs and OpenTelemetry traces carrying request, charge and idempotency IDs in all three services | L | Medium | Traces one checkout end to end across three stacks |

## Suggested Sequence

1. REL-001: ship the idempotent charge flow. Why: Critical and the double-charge pain point, so it leads the Critical group.
2. SEC-003, SEC-002, SEC-001, SEC-004: fix the other Critical issues, all in Security (weakest category, score 1). Why: active breach risk; SEC-003 and SEC-002 take hours.
3. REL-002: abort the provider call after 5 s. Why: the slow-checkout pain point, and fewer retries feeding REL-001.
4. REL-003, then SCL-001, MNT-002, API-001: check the provider result, batch the item inserts, test the charge route, validate bodies. Why: High impact; Security has no High issues, so Impact and Effort set the order.
5. Remaining quick wins, then API-002, MNT-001, OBS-002. Why: cleanup and visibility once the money path is safe.
