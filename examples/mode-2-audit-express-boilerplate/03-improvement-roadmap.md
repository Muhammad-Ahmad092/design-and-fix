# Improvement Roadmap

> Scope: `hagopj13/node-express-boilerplate` at tag `v1.7.0` (https://github.com/hagopj13/node-express-boilerplate), whole repository; all paths relative to the repo root. Archive checksum in `evals/corpus.json`.
> Context: Express + MongoDB starter used as the base of a small REST API, about 2k users, team of 2, must keep MongoDB; Assumed (non-interactive)
> Format: 1.0.2
> Audit: plugin 1.1.0 · scanner schema 3 · rev unknown · config 3d96e5d5ea6d · 2026-11-05T16:53:20Z

Every issue ID from `01` and `02` appears in exactly one `Action (IDs)` row of the three horizon tables; Suggested Sequence may cite IDs again.
Placement: S -> Quick Wins, M -> Medium-term, L/XL -> Long-term.

## Quick Wins (days)

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| SEC-003: require a 32+ character `JWT_SECRET` and reject the sample value in production | S | High | A forged admin token is the worst outcome in this codebase |
| SEC-001: return 204 from `forgot-password` for unknown emails | S | Medium | Stops the account probe |
| API-001: fix the precedence in `errorConverter` | S | Medium | Correct status codes for clients and monitors |
| SCL-001: cap `limit` and allow-list `sortBy` | S | Medium | Bounds the cost of the list route |
| OBS-001, OBS-002: add `GET /v1/health` and a Docker `HEALTHCHECK` | S | Medium | One shared cause (RC-1), one small change |
| SYS-001: move token reads and deletes into `token.service.js` | S | Low | Prepares the SEC-002 change |
| MNT-001: fix the `.env.example` key | S | Low | The documented setting starts working |
| MNT-002: pin the LTS Node version in CI and the Dockerfile | S | Low | Tests match production |

## Medium-term (weeks)

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| SEC-002: store hashed tokens | M | Medium | A database read no longer yields live tokens |
| API-002: general rate limit and `trust proxy` | M | Medium | Protection in every environment |
| REL-001: queue the reset mail with retry | M | Medium | SMTP outages stop failing requests |
| OBS-003: request IDs and metrics | M | Medium | Incidents become traceable and alertable |

## Long-term (months/quarters)

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| MNT-003: upgrade mongoose 5 and passport, replace `xss-clean` | L | Medium | Keeps the stack on maintained majors |

## Suggested Sequence

Order: the Suggested Sequence rule in `references/formatting-rules.md` (Roadmap Table).

1. SEC-003, SEC-001: harden secret validation and the reset endpoint. Why: Security is the weakest category and both are one-day changes.
2. SYS-001, SEC-002: centralise token storage, then hash tokens. Why: the refactor makes the hashing change a single-file edit.
3. API-001, SCL-001, API-002: fix status codes, cap page size, widen rate limiting. Why: confirmed issues with Medium impact and small changes first.
4. OBS-001, OBS-002, OBS-003, REL-001: health endpoint, healthcheck, request IDs, queued mail. Why: operability before the user base grows.
5. MNT-001, MNT-002, MNT-003: typo, Node pin, dependency upgrades. Why: cleanup last, the upgrade is the largest step.
