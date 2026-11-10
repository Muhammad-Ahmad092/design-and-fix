# API Optimization

> Scope: `hagopj13/node-express-boilerplate` at tag `v1.7.0` (https://github.com/hagopj13/node-express-boilerplate), whole repository; all paths relative to the repo root. Archive checksum in `evals/corpus.json`.
> Context: Express + MongoDB starter used as the base of a small REST API, about 2k users, team of 2, must keep MongoDB; Assumed (non-interactive)
> Format: 1.0.2
> Audit: plugin 1.1.0 · scanner schema 3 · rev unknown · config 3d96e5d5ea6d · 2026-11-05T16:53:20Z

## API Health Snapshot

| Endpoint group | Worst severity | Issue IDs | Note |
|---|---|---|---|
| `/v1/auth` | Medium | API-002, SEC-001, SEC-002, REL-001 | Validated bodies and a production-only rate limit; enumeration and mail-in-request issues |
| `/v1/users` | Medium | SCL-001 | Auth and ownership checks are correct; page size is unbounded |
| Error responses (all routes) | Medium | API-001 | One `{code, message}` shape, but the wrong status for some errors |
| `/v1/docs` | — | — | Registered only when `NODE_ENV=development` |

## Endpoint Inventory

| Method | Path | File | Auth detected |
|---|---|---|---|
| POST | /v1/auth/register | `src/routes/v1/auth.route.js:9` | no |
| POST | /v1/auth/login | `src/routes/v1/auth.route.js:10` | no |
| POST | /v1/auth/logout | `src/routes/v1/auth.route.js:11` | no |
| POST | /v1/auth/refresh-tokens | `src/routes/v1/auth.route.js:12` | no |
| POST | /v1/auth/forgot-password | `src/routes/v1/auth.route.js:13` | no |
| POST | /v1/auth/reset-password | `src/routes/v1/auth.route.js:14` | no |
| POST | /v1/auth/send-verification-email | `src/routes/v1/auth.route.js:15` | yes |
| POST | /v1/auth/verify-email | `src/routes/v1/auth.route.js:16` | no |
| GET | /v1/docs | `src/routes/v1/docs.route.js:14` | no |
| POST | /v1/users | `src/routes/v1/user.route.js:11` | yes |
| GET | /v1/users | `src/routes/v1/user.route.js:12` | yes |
| GET | /v1/users/{userId} | `src/routes/v1/user.route.js:16` | yes |
| PATCH | /v1/users/{userId} | `src/routes/v1/user.route.js:17` | yes |
| DELETE | /v1/users/{userId} | `src/routes/v1/user.route.js:18` | yes |

Routes from the scanner in the audited scope (first 9 rows), plus the 5 user routes read from `user.route.js` because the scanner does not follow `router.route().post()` chains. The unauthenticated auth routes are public by design (register, login, token flows).

## Issues Found

| ID | Severity | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|---|
| API-001 | Medium | confirmed | agent | Errors that carry a `statusCode` are returned as 400 | `src/middlewares/error.js:11` | The ternary in `statusCode = error.statusCode OR (error instanceof mongoose.Error) ? 400 : 500` binds as `(a OR b) ? 400 : 500` (the source writes the logical-or operator), so any error with a truthy `statusCode` becomes 400 | A body over the size limit (413) or another framework error reaches clients as 400 instead of its own status | Parenthesise the ternary: `error.statusCode` first, then `mongoose.Error ? 400 : 500` | Test: POST a JSON body above 100 kb to `/v1/auth/login` and expect 413 | Clients and monitors see the real status |
| API-002 | Medium | confirmed | agent | Rate limiting covers only `/v1/auth`, and only when `NODE_ENV` is `production` | `src/app.js:49`, `src/middlewares/rateLimiter.js:3` | One limiter (20 failed requests per 15 minutes, per IP) is mounted inside an environment check; `trust proxy` is not set, so behind a proxy every client shares one IP | `/v1/users` has no limit; staging and test environments are unprotected; a shared proxy IP can lock every user out | Mount a general limiter on `/v1`, keep the stricter one on `/v1/auth`, set `app.set('trust proxy', 1)` behind one proxy | Test: 21 failed logins from one IP return 429 and two IPs behind the proxy are counted separately | Brute force and scraping are bounded in every environment |

Only `API-*` issues are defined here; other categories are referenced by ID from `01-architecture-issues.md`.

## Caching Opportunities

| Target (IDs) | Cache what, where | Invalidation | Why |
|---|---|---|---|
| GET /v1/users/{userId} (SCL-001) | None now: at about 2k users MongoDB serves this from its working set | — | A cache adds invalidation cost without a measured need; revisit when `GET /v1/users` p95 exceeds 300 ms after SCL-001 |

## Quick Wins vs Structural Fixes

| Recommendation (IDs) | Category | Impact | Effort | Risk | Verification | Why |
|---|---|---|---|---|---|---|
| API-001: add the missing parentheses in `errorConverter` | correctness | Medium | S | Low | 413 test on `/v1/auth/login` | One-line change with a visible status fix |
| SCL-001: cap `limit` at 100 and allow-list `sortBy` | performance | Medium | S | Low | `limit=101` returns 400; Hypothesis: time `GET /v1/users?limit=100` p95 with k6 before/after | Bounds work per request |
| API-002: general limiter plus `trust proxy` | security | Medium | M | Medium | 21 failed logins from one IP return 429 | Limits abuse in every environment; a wrong proxy setting blocks real users |
| REL-001: send the reset mail off the request path | performance | Medium | M | Medium | `forgot-password` answers in under 1 s with SMTP down | Removes SMTP latency from the request |

Small fixes (S/M effort) first; no L/XL structural fix is needed for the API surface.
