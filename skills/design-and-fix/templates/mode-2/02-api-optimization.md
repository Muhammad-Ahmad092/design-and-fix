# API Optimization

> Scope: <folders / services audited>
> Context: <scale>, <key constraint>

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

| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|---|
| <API-001> | <High> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <reason> |
| <API-002> | <Medium> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <reason> |

Only `API-*` issues are defined here; other categories are referenced by ID from `01-architecture-issues.md`.

## Caching Opportunities

| Target (IDs) | Cache what, where | Invalidation | Why |
|---|---|---|---|
| <GET /orders/{id} (API-002)> | <order summary, Redis> | <TTL 30 s + delete on update> | <reason> |

## Quick Wins vs Structural Fixes

**Quick Wins**
- <IDs>: <cheap, fast change>. Why: <reason>

**Structural Fixes**
- <IDs>: <change that needs redesign>. Why: <reason>
