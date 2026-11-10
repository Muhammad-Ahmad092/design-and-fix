# API Optimization

> Scope: <folders / services audited>
> Context: <scale>, <key constraint>
> Format: 1.0.2
> Audit: plugin 1.1.0 · scanner schema 3 · rev <sha|unknown> · config <config_hash> · <timestamp>

## API Health Snapshot

| Endpoint group | Worst severity | Issue IDs | Note |
|---|---|---|---|
| <e.g. /payments> | <Critical/High/Medium/Low/—> | <API-001, REL-001> | <one-line note> |
| <e.g. /orders> | <Critical/High/Medium/Low/—> | <IDs or —> | <one-line note> |

## Endpoint Inventory

| Method | Path | File | Auth detected |
|---|---|---|---|
| <POST> | </charge> | <path:line> | <yes / no / unknown> |
| <GET> | </orders/{id}> | <path:line> | <yes / no / unknown> |

Routes from the scanner in the audited scope, max 20 rows. <+N more not shown, or delete this line>

## Issues Found

| ID | Severity | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|---|
| <API-001> | <High> | <confirmed> | <scanner:list-route-no-pagination> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <test or check> | <reason> |
| <API-002> | <Medium> | <likely> | <agent> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <check or —> | <reason> |

- <API-001>: Observed: <what the code shows>. Inference: <what you conclude>. Limitations: <counter-evidence, or none>. (High/Critical only)

Only `API-*` issues are defined here; other categories are referenced by ID from `01-architecture-issues.md`.

## Caching Opportunities

| Target (IDs) | Cache what, where | Invalidation | Why |
|---|---|---|---|
| <GET /orders/{id} (API-002)> | <order summary, Redis> | <TTL 30 s + delete on update> | <reason> |

## Quick Wins vs Structural Fixes

| Recommendation (IDs) | Category | Impact | Effort | Risk | Verification | Why |
|---|---|---|---|---|---|---|
| <API-001>: <cheap, fast change> | <correctness/security/maintainability/performance/cost> | <Low/Medium/High> | <S> | <Low/Medium/High> | <test or check> | <reason> |
| <API-002>: <change that needs redesign> | <performance> | <Low/Medium/High> | <L> | <Low/Medium/High> | <Hypothesis: time GET /orders p95 with k6 at 50 req/s before/after> | <reason> |

Small fixes (S/M effort) first, structural fixes (L/XL) after; the roadmap still places S in Quick Wins and M in Medium-term. Performance claims from code patterns are a Hypothesis until measured.
