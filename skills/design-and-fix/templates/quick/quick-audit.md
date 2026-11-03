# Quick Audit

> Scope: <folders / services audited>
> Context: <scale>, <key constraint>
> Format: 1.0.2
> Audit: plugin 1.0.2 · scanner schema 3 · rev <sha|unknown> · config <config_hash> · <timestamp>

## Change Since Last Audit

(re-run only — delete on first run; copy the Previous line and the Fixed/New bullets as printed by `compare_audits.py docs/architecture/history/<YYYY-MM-DD>[-N] docs/architecture --repo <root>`, using the folder you just archived into)

Previous: <Band> (<X.X> / 10) → Current: <Band> (<X.X> / 10)

- Fixed: <IDs>
- New: <IDs>

## Health Band

**<Band> (<X.X> / 10)**: <band line from the rubric>.
Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed

## Top Issues

| ID | Severity | Status | Source | Issue | Evidence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|
| <SEC-001> | <Critical> | <confirmed> | <scanner:hardcoded-secret> | <issue> | <path:line> | <fix> | <test or check> | <reason> |
| <REL-001> | <High> | <likely> | <agent> | <issue> | <path:line> | <fix> | <test or check> | <reason> |

- <SEC-001>: Observed: <what the code shows>. Inference: <what you conclude>. Limitations: <…>. (one line per High/Critical issue)

## Quick Wins

| Action (IDs) | Effort | Impact | Why |
|---|---|---|---|
| <SEC-001>: <action> | S | <Low/Medium/High> | <reason> |

## Next Steps

1. <IDs>: <action>. Why: <reason>
2. <IDs>: <action>. Why: <reason>
