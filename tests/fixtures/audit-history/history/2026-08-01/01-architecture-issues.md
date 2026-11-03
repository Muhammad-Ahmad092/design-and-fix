# Architecture Issues

## Architecture Health Score: Unsafe (2.0 / 10)

## Critical Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| REL-001 | Provider call has no timeout | `src/pay.py:5` | x | x | x | x |
| SEC-001 | SQL built by concatenation | `src/db.py:2` | x | x | x | x |
