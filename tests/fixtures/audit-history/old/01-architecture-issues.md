# Architecture Issues

> Scope: src (tiny fixture repo, old run)
> Audit: plugin 1.0.2 · scanner schema 3 · rev unknown · config 0a1b2c3d4e5f · 2026-09-01T09:00:00Z

## Architecture Health Score: Unsafe (3.9 / 10)

Unsafe: address Critical issues immediately.

## Critical Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| REL-001 | Provider call has no timeout | `src/pay.py:5` | No timeout argument | Hangs | Add timeout | Bounds latency |

## High Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| SEC-001 | SQL built by concatenation | `src/db.py:2` | String concat | Injection | Bind parameters | Removes injection |
| REL-002 | Blocking sleep in settle | `src/pay.py:10` | `time.sleep(5)` | Worker blocked | Remove it | Frees workers |

## Medium / Low Issues

| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|---|
| SCL-001 | Medium | Unbounded list query | `src/api.py:2` | No LIMIT | Memory growth | Add LIMIT | Bounded cost |
| MNT-001 | Low | Ad hoc cache dict in util | `src/util.py:2` | Local dict | No reuse | Use shared cache | One cache |
| OBS-001 | Low | Noisy print in gone.py | `src/gone.py:1`, `src/gone.py:1` | print | Noise | Use logger | Clean logs |
