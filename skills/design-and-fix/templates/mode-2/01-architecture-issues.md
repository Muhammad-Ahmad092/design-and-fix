# Architecture Issues

> Scope: <folders / services audited>
> Context: <scale>, <key constraint>
> Format: 1.0.2
> Audit: plugin 1.0.2 · scanner schema 3 · rev <sha|unknown> · config <config_hash> · <timestamp>

## Change Since Last Audit

(re-run only — delete on first run. Replace this section with the output of `compare_audits.py docs/architecture/history/<YYYY-MM-DD>[-N] docs/architecture --repo <root>` (the folder you just archived into) as printed, including any Revision, Changed and Possibly moved lines and `### Score Trend`; uncertain matches keep their "(uncertain)" marker. Add the Δ table yourself from the two score tables.)

Previous: <Band> (<X.X> / 10) → Current: <Band> (<X.X> / 10)

| Category | Previous | Current | Δ |
|---|---|---|---|
| <category> | <N> | <N> | <+/-N> |

- Fixed: <IDs>
- New: <IDs>
- Still open: <IDs>

### Score Trend

| Date | Overall | Band | Fixed | New |
|---|---|---|---|---|
| <YYYY-MM-DD> | <X.X> | <Band> | <IDs or —> | <IDs or —> |

If the archived run was a `quick-audit.md`, drop the Δ table.

## Architecture Health Score: <Band> (<X.X> / 10)

<Band>: <band line from the rubric>.
Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed

| Category | Score | Band | Weight | Main driver (IDs) |
|---|---|---|---|---|
| System Design | <N> | <Band> | 1.0 | <main driver> (<IDs>) |
| API Design | <N> | <Band> | 1.0 | <main driver> (<IDs>) |
| Scalability | <N> | <Band> | <1.0 / 1.5> | <main driver> (<IDs>) |
| Reliability | <N> | <Band> | <1.0 / 1.5> | <main driver> (<IDs>) |
| Security | <N> | <Band> | <1.0 / 2.0> | <main driver> (<IDs>) |
| Observability | <N> | <Band> | 1.0 | <main driver> (<IDs>) |
| Maintainability | <N> | <Band> | 1.0 | <main driver> (<IDs>) |

Weights come from the Context line via the rubric's weights table (default 1.0). Not assessed categories show `Not assessed | — | <weight> | <reason>` and are left out of the mean. `API-*` issues are defined in `02-api-optimization.md`.

## Critical Issues

| ID | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|
| <SEC-001> | <confirmed> | <scanner:rule / agent / scanner+agent> | <issue> | <path:line> | <root cause or RC-1> | <consequence> | <fix> | <test or check proving the fix> | <reason> |

- <SEC-001>: Observed: <what the code shows>. Inference: <what you conclude>. Limitations: <counter-evidence, or none>.

## High Issues

| ID | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|
| <REL-001> | <likely> | <scanner:rule> | <issue> | <path:line> | <root cause or RC-1> | <consequence> | <fix> | <test or check> | <reason> |

- <REL-001>: Observed: <…>. Inference: <…>. Limitations: <…>.

## Medium / Low Issues

| ID | Severity | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|---|
| <MNT-001> | Medium | <confirmed> | <agent> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <check or —> | <reason> |
| <OBS-001> | Low | <needs-review> | <scanner:rule> | <issue> | <path:line> | <root cause> | <consequence> | <fix> | <check or —> | <reason> |

- RC-1: <root cause shared by 2+ issues> (<IDs>) — delete when no cause is shared
