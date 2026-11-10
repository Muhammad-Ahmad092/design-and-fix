# Quick Audit

> Scope: services/payments in `tests/fixtures/monorepo` (synthetic fixture; paths relative to it)
> Context: checkout API, about 50 req/s peak, must keep PostgreSQL, pain point: checkout is slow
> Format: 1.0.2
> Audit: plugin 1.1.0 · scanner schema 3 · rev unknown · config 3d96e5d5ea6d · 2026-11-05T16:54:09Z

## Health Band

**Stable with risks (6.3 / 10)**: fix High issues before scaling.
Evidence: 6 files read, 9 scanner signals reviewed, 7 confirmed

## Top Issues

| ID | Severity | Status | Source | Issue | Evidence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|
| SEC-001 | Critical | needs-review | scanner:unauth-write-route | `POST /payments/charge` has no authentication | `services/payments/src/handlers/charge.ts:14` | Verify a token in middleware and apply it to every payments write route | Test: a charge without a token returns 401 | The route moves money for any `customerId` |
| REL-001 | High | confirmed | agent | `POST /payments/charge` is not idempotent | `services/payments/src/handlers/charge.ts:15` | Require an `Idempotency-Key` header and store it with a unique constraint before calling the provider | Test: two requests with one key produce one provider call | A retried request would charge the card twice |
| REL-002 | High | confirmed | scanner:http-no-timeout | Provider call in checkout has no timeout | `services/payments/src/handlers/charge.ts:17` | `signal: AbortSignal.timeout(5000)`; map the abort to 504 | Test: a provider stub that sleeps 10 s yields 504 within 6 s | A slow provider holds every checkout open |
| SCL-001 | High | confirmed | scanner:query-in-loop | One awaited `INSERT` per item in checkout | `services/payments/src/handlers/charge.ts:21` | One multi-row insert in the same transaction as the charge | Test: a 5-item checkout issues one insert statement | Fewer round trips and no partial rows |
| API-001 | High | confirmed | scanner:unvalidated-body | Request bodies are cast, never validated | `services/payments/src/handlers/charge.ts:15`, `services/payments/src/handlers/refund.ts:15` | Validate `items` and `customerId` with a schema before use | Test: a body without `items` returns 400 | Malformed input reaches the provider and the database |
| SYS-001 | Medium | likely | scanner:import-cycle | Import cycle `db.ts` and `util.ts` | `services/payments/src/lib/db.ts:1` | Move the shared helper into its own module | Check: the scanner reports no cycle | Cycles make load order fragile |
| REL-003 | Medium | confirmed | scanner:swallowed-error | Empty `catch` in the webhook handler | `services/payments/src/handlers/webhook.ts:11` | Log the error and return 500 | Test: a failing DB returns 500 and logs it | Failures become visible |
| OBS-001 | Low | confirmed | scanner:console-logging-only | Console-only logging without request IDs | `services/payments/src/lib/util.ts:5` | Structured logs carrying a request ID | Check: one charge yields log lines sharing a request ID | Duplicate charges can be traced |
| MNT-001 | Low | confirmed | scanner:missing-lockfile | No lockfile for payments | `services/payments/package.json:1` | Commit `package-lock.json` | Check: CI installs with `npm ci` | Reproducible builds |

- SEC-001: Observed: the charge handler reads the body with no auth step. Inference: anyone can charge. Limitations: the scanner could not see where the router is mounted (`uncertain_reason`), so auth outside the scanned files is Not verified. Related: REL-001.
- REL-001: Observed: no idempotency key is read or sent. Inference: a retry charges again. Limitations: the provider's own dedupe is Not verified.
- REL-002: Observed: bare `fetch` to the provider. Inference: checkout latency equals provider latency with no ceiling. Limitations: the provider's real latency is Not verified.
- SCL-001: Observed: `await db.query` inside a `for` loop. Inference: latency grows with item count. Limitations: typical items per checkout is Assumed.
- API-001: Observed: `req.body` is cast with `as`. Inference: shapes are never checked. Limitations: validation in a gateway is Not verified.

## Quick Wins

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| REL-002: add a 5 s timeout to the provider call | S | High | Bounds checkout latency |
| MNT-001: commit the lockfile | S | Low | Reproducible builds |
| REL-003: log and return 500 in the webhook | S | Medium | Failures stop hanging |
| OBS-001: add request IDs to logs | S | Low | Traceable charges |

## Next Steps

1. SEC-001, REL-001: add authentication and an idempotency key to the charge route. Why: they close the money-moving risks.
2. SCL-001, API-001: batch the inserts and validate bodies. Why: both touch the same handler.
3. SYS-001: break the import cycle. Why: low risk once the handler is stable.
