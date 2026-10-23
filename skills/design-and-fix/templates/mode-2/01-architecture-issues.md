# Architecture Issues

> Scope: <folders / services audited>
> Context: <scale>, <key constraint>

## Change Since Last Audit

(re-run only — delete on first run)

Previous: <Band> (<X.X> / 10) on <YYYY-MM-DD> → Current: <Band> (<X.X> / 10). Archived run: `docs/architecture/history/<YYYY-MM-DD>/`

| Category | Previous | Current | Δ |
|---|---|---|---|
| <category> | <N> | <N> | <+/-N> |

- Fixed: <IDs>
- New: <IDs>
- Still open: <IDs>

If the archived run was a `quick-audit.md`, compare overall band/score and IDs only; drop the Δ table.

## Architecture Health Score: <Band> (<X.X> / 10)

<Band>: <band line from the rubric>.
Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed

| Category | Score | Band | Main driver (IDs) |
|---|---|---|---|
| System Design | <N> | <Band> | <main driver> (<IDs>) |
| API Design | <N> | <Band> | <main driver> (<IDs>) |
| Scalability | <N> | <Band> | <main driver> (<IDs>) |
| Reliability | <N> | <Band> | <main driver> (<IDs>) |
| Security | <N> | <Band> | <main driver> (<IDs>) |
| Observability | <N> | <Band> | <main driver> (<IDs>) |
| Maintainability | <N> | <Band> | <main driver> (<IDs>) |

Not assessed categories show `Not assessed | — | <reason>` and are left out of the mean. `API-*` issues are defined in `02-api-optimization.md`.

## Critical Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| <SEC-001> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <reason> |

## High Issues

| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|
| <REL-001> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <reason> |

## Medium / Low Issues

| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why |
|---|---|---|---|---|---|---|---|
| <MNT-001> | Medium | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <reason> |
| <OBS-001> | Low | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <reason> |
