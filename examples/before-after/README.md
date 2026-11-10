# Before and after: one issue fixed

This example shows the audit loop on a copy of the synthetic fixture `tests/fixtures/monorepo`, scope `services/payments`. One real fix was applied, the scanner was re-run, and a second audit was compared with the first.

## The fix

One line in `services/payments/src/handlers/charge.ts` (line 17): the provider call gets a timeout.

```diff
-  const providerResponse = await fetch(PROVIDER_URL, { method: "POST", body });
+  const providerResponse = await fetch(PROVIDER_URL, { method: "POST", body, signal: AbortSignal.timeout(5000) });
```

This is the fix proposed for REL-002 in [examples/mode-2-audit-monorepo](../mode-2-audit-monorepo/01-architecture-issues.md). The skill never makes this edit itself; it was applied by hand to a temporary copy, and the fixture in `tests/fixtures/monorepo` is unchanged.

## What was run

1. Copy the fixture twice and apply the fix to one copy.
2. `python skills/design-and-fix/scripts/scan_repo.py <copy> --scope services/payments --no-git` on both copies. The scanner signal `http-no-timeout` at `charge.ts:17` is present before and gone after; the other eight signals are identical.
3. Write a quick audit for each state ([before](before/quick-audit.md), [after](after/quick-audit.md)). Each signal was read in the code. Seven of the nine are confirmed findings, the import cycle is `likely`, and SEC-001 is `needs-review` because the scanner signal carries `uncertain_reason` (it could not see where the router is mounted). The Fix column holds the recommendation.
4. `lint_output.py <dir> --repo <copy> --scan <dir>/scan.json` on both folders: no findings (each audit's Audit and Scope lines match its own `scan.json`).
5. `compare_audits.py` on the two folders. Full output: [compare-audits.md](compare-audits.md).

```text
- Fixed: REL-002
- Still open: API-001, MNT-001, OBS-001, REL-001, REL-003, SCL-001, SEC-001, SYS-001

Score +0.3 (6.3 → 6.6): fixed REL-002 (High).
```

Reliability goes from two High issues (sub-score 4) to one (sub-score 6), which moves the overall score from 6.3 to 6.6. The band stays Stable with risks because eight issues are still open.

The after audit is a re-run, so it shows what a re-run writes:

- The Change Since Last Audit note at the top: the Previous line, the Fixed bullet and the Score line copied from the `compare_audits.py` output (`references/quick-mode.md`, Re-runs).
- REL-002 stays in Top Issues for this one run with Status `not-reproducible`: not scored, no Quick Wins or Next Steps entry, and `compare_audits.py` counts it as Fixed.
- Detail lines carry the optional fields `Related: REL-001` (SEC-001 and REL-001 are fixed in the same handler) and `First seen: 2026-11-05`, the date of the before run's Audit timestamp.

## Files

| Path | Contents |
|---|---|
| `before/scan.json`, `after/scan.json` | Scanner output (schema 3), `root` rewritten to a relative label so no local path is stored |
| `before/quick-audit.md`, `after/quick-audit.md` | The two audits |
| `compare-audits.md` | The `compare_audits.py` output |

The code copies are not committed. To reproduce, copy the fixture, apply the diff above, and run the commands in step 2 to 5.
