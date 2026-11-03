# Architecture Issues

> Scope: src (tiny fixture repo, new run)
> Revision: abc1234

## Architecture Health Score: Stable with risks (6.3 / 10)

Stable with risks: fix High issues before scaling.

## Critical Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why | Status | Source |
|---|---|---|---|---|---|---|---|---|
| REL-001 | Provider call has no timeout | `src/pay.py:7` | No timeout argument | Hangs | Add timeout | Bounds latency | confirmed | scanner:no-timeout |
| SEC-001 | SQL built by concatenation | `src/db.py:2` | String concat | Injection | Bind parameters | Removes injection | confirmed | agent |

## Medium / Low Issues

| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why | Status | Source |
|---|---|---|---|---|---|---|---|---|---|
| SCL-001 | Medium | Unbounded list query | `src/api.py:2` | No LIMIT | Memory growth | Add LIMIT | Bounded cost | likely | scanner:unbounded-query |
| SCL-002 | Medium | Unbounded export query | `src/api.py:7` | No LIMIT | Memory growth | Add LIMIT | Bounded cost | likely | scanner:unbounded-query |
| MNT-001 | Low | Ad hoc cache dict in util | `src/lib/util.py:2` | Local dict | No reuse | Use shared cache | One cache | confirmed | agent |
