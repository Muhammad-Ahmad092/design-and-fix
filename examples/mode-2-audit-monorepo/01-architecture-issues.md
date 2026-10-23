# Architecture Issues

> Scope: services/payments, services/orders, services/catalog in `tests/fixtures/monorepo` (synthetic fixture; all paths relative to it). App/ (.NET stub) not assessed.
> Context: about 50 req/s peak, must keep PostgreSQL, pain point: checkout (charge) is slow and sometimes double-charges

## Architecture Health Score: Fragile (5.4 / 10)

Fragile: structural fixes needed before growth.
Evidence: 11 files read, 20 scanner signals reviewed, 18 confirmed

| Category | Score | Band | Main driver (IDs) |
|---|---|---|---|
| System Design | 8 | Good | Import cycle db.ts ↔ util.ts (SYS-001 Medium) |
| API Design | 6 | Mixed | Unvalidated bodies, mixed error formats, POST /items never inserts (API-001 High, API-002 Medium, API-003 Medium, API-004 Low) |
| Scalability | 6 | Mixed | Insert per item in checkout, unbounded order list (SCL-001 High, SCL-002 Medium) |
| Reliability | 3 | Weak | No idempotency on charge, no timeouts, unchecked provider errors (REL-001 Critical, REL-002 High, REL-003 High, REL-004 High, REL-005 Medium) |
| Security | 1 | Severe | Open charge route and header-presence-only auth, SQL injection, committed password, open write routes (SEC-001 Critical, SEC-002 Critical, SEC-003 Critical, SEC-004 Critical) |
| Observability | 8 | Good | Logs without request IDs, no health route (OBS-002 Medium, OBS-001 Medium) |
| Maintainability | 6 | Mixed | Charge route untested, no tests in orders/catalog, 30 copied unused functions (MNT-002 High, MNT-001 Medium, MNT-003 Low) |

- Arithmetic: (8 + 6 + 6 + 3 + 1 + 8 + 6) / 7 = 5.43 → 5.4 → Fragile. `API-*` issues are defined in `02-api-optimization.md`.
- Rejected signals: `unbounded-query` at `services/catalog/app.py:21` (equality filter; the real defect is SEC-002) and at `services/payments/src/handlers/webhook.ts:8` (primary-key lookup).
- No git history (`--no-git`), so hotspots were not used to order reading.

## Critical Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| REL-001 | `POST /payments/charge` is not idempotent (double-charge pain point) | `services/payments/src/handlers/charge.ts:15`, `services/payments/src/handlers/charge.ts:17` | Handler reads only `items` and `customerId`, sends no idempotency key to the provider, writes no charge row first; `charge_items` rows (line 21) carry no charge ID | A retried request charges the card again and leaves no record to detect it; client retry behaviour Not verified (no client in scope) | Require an `Idempotency-Key` header, insert a `charges` row with `UNIQUE (idempotency_key)` in PostgreSQL before the provider call, forward the key, replay the stored response | Makes retries safe and stays on PostgreSQL |
| SEC-001 | `POST /payments/charge` has no authentication; refund's check is presence-only | `services/payments/src/handlers/charge.ts:14`, `services/payments/src/handlers/refund.ts:8` | The only auth helper, `requireAuth` (`refund.ts:7`-13), checks that an `Authorization` header exists and never verifies it; charge does not use it at all | Anyone can charge any `customerId`; anyone sending any `Authorization` value can refund any charge | Replace `requireAuth` with real token verification (JWT signature + expiry) in `src/lib/auth.ts` and apply it to every payments write route | Both money-moving routes are effectively open |
| SEC-002 | SQL injection in `POST /items` | `services/catalog/app.py:21` | Request field `name` is concatenated into the SQL string | Attacker can read or alter any catalog table | `cursor.execute("SELECT * FROM items WHERE name = %s", (name,))` | Parameter binding removes the injection in one line |
| SEC-003 | Production database password committed in source | `services/catalog/app.py:9` | `DB_PASSWORD` is a literal named for prod | Every repo reader holds the catalog DB password | Read it from an environment variable injected by a secret store; rotate the current value | A committed secret stays in history until rotated |
| SEC-004 | `POST /orders` and `POST /items` accept unauthenticated writes | `services/orders/src/orders.go:44`, `services/catalog/app.py:16` | No auth middleware: orders uses bare `gin.Default()` (line 43), catalog has no `before_request` check | Unauthenticated today: the injectable query at `app.py:21` and any write logic added to these handlers later are open to anyone | Add the same JWT verification (signature + expiry) as middleware on the Gin router and a Flask `before_request` hook | Same policy on every write route |

## High Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| REL-002 | Provider call in checkout has no timeout (slow-checkout pain point) | `services/payments/src/handlers/charge.ts:17` | `fetch(PROVIDER_URL, …)` without an `AbortSignal`; the default client waits minutes | A slow provider holds every checkout open, clients time out and retry, which feeds REL-001 | `signal: AbortSignal.timeout(5000)`; map the abort to 504 | Bounds checkout latency and the retry storm |
| SCL-001 | One awaited `INSERT` per item in checkout | `services/payments/src/handlers/charge.ts:21` | `await db.query` inside the `for` loop (lines 20–26), no transaction | N sequential round trips per checkout; partial rows when one insert fails; at 50 req/s × 5 items (items per checkout Assumed) ≈ 250 inserts/s | One multi-row `INSERT … SELECT * FROM unnest(…)` in the same transaction as the charge row | One round trip and atomic writes, still PostgreSQL |
| REL-003 | Provider failures reported as successful charges | `services/payments/src/handlers/charge.ts:18`, `services/payments/src/handlers/charge.ts:29` | `providerResponse.ok` is never checked; network errors reject an async handler Express 4 does not await | Declined charge returns 201 and items are recorded; a rejected fetch leaves the request hanging | Check status, return 402/502 before writing items, wrap the handler in an async error middleware | Client sees the true outcome of the charge |
| MNT-002 | Charge route has no test; the only provider test hits the network | `services/payments/src/handlers/charge.test.ts:5`, `services/payments/src/handlers/charge.test.ts:9` | Tests cover `totalOf` only; "calls provider" fetches a live URL | Double-charge and error paths can regress unseen; suite fails offline | Supertest cases for `POST /payments/charge` with a mocked provider, incl. a repeated `Idempotency-Key` | Locks in the REL-001 fix |
| REL-004 | Outbound HTTP without timeouts in catalog and orders | `services/catalog/app.py:28`, `services/orders/src/orders.go:18` | `requests.get` without `timeout=`; `http.Get` uses `http.DefaultClient` (no `Timeout`); `fetchRates` has no caller in the fixture | `GET /items/<id>` blocks a worker for as long as pricing hangs | `requests.get(url, timeout=3)`; package-level `http.Client{Timeout: 5 * time.Second}` | Stops one slow dependency from exhausting workers |

## Medium / Low Issues

| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|---|
| OBS-002 | Medium | Console-only logging without request or charge IDs | `services/payments/src/lib/util.ts:5`, `services/payments/src/handlers/charge.ts:28`, `services/payments/src/handlers/webhook.ts:9` | `logger.info` wraps `console.log`; the charge log line holds only `customerId` | Duplicate charges cannot be traced to the requests that caused them | Structured JSON logs and OpenTelemetry traces carrying request, charge and idempotency IDs in all three services | Makes double-charge reports investigable |
| REL-005 | Medium | Errors swallowed in two handlers | `services/payments/src/handlers/webhook.ts:11`, `services/catalog/app.py:30` | Empty `catch` sends no response; `except Exception: pass` never uses `log` (line 7) | `GET /payments/:id` hangs until the client gives up; pricing failures leave no trace | Log the error and return 500 / 502 | Failures become visible and bounded |
| SYS-001 | Medium | Import cycle between `db.ts` and `util.ts` | `services/payments/src/lib/db.ts:1`, `services/payments/src/lib/util.ts:1` | `util.ts` imports `db` only for a `typeof db` check (line 10); Medium, not High, because the cycle is one check inside one service's lib | Load order decides whether `db` is defined; every handler depends on both | Move `formatRow` into `db.ts` and drop the `db` import from `util.ts` | One-way dependency, no init-order surprises |
| OBS-001 | Medium | No health endpoint in payments or catalog | `services/payments/src/handlers/charge.ts:14`, `services/catalog/app.py:16` | Route lists contain no health route; orders has one (`orders.go:45`) | Orchestrator cannot tell a hung process from a healthy one | Add `GET /healthz` that pings PostgreSQL | Enables restarts and load-balancer checks |
| SCL-002 | Medium | `listOrders` reads all of a user's orders | `services/orders/src/orders.go:22` | No `LIMIT`; rows appended to one slice (lines 27–34); not yet wired to a route | Memory and latency grow with order history | `LIMIT $2` plus keyset cursor `AND id < $3` | Bounded cost per call |
| MNT-001 | Medium | No tests in orders or catalog | `services/orders/src/orders.go:38`, `services/catalog/app.py:17` | No test files under either service | Handler changes ship unverified | `httptest` cases for orders, Flask test client for catalog | Covers the write routes before SEC-004 changes them |
| MNT-003 | Low | 30 identical, unused normalizer functions | `services/payments/src/handlers/charge.ts:43`, `services/payments/src/handlers/refund.ts:31`, `services/payments/src/handlers/webhook.ts:21` | `normalizeCharge1…10`, `normalizeRefund1…10`, `normalizeWebhook1…10` copy one body; no callers | About 540 of 704 payments LOC is copy-paste; a fix to one copy misses 29 | Delete them; keep one `normalizeRecord` in `src/lib` if a caller appears | Smaller handlers, one place to fix |
