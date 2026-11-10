# Archive and Diff

Step 3, before writing any output file. Keeps earlier runs instead of overwriting them, and turns a re-audit into a progress report.

## When It Applies

`docs/architecture/` already contains plugin output, which is only these exact filenames: `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md`, `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md`, `04-tech-stack-comparison.md`, `quick-design.md`, `quick-audit.md`. Refinements ("go deeper on X", `references/refinement-rules.md`) edit in place and never archive.

## Steps

1. **Archive.** Move those files to `docs/architecture/history/<YYYY-MM-DD>/` (today's date). If that folder exists, use `<YYYY-MM-DD>-2`, then `-3`, and so on. Move only those plugin filenames; leave every other file in `docs/architecture/` alone (no globs: a user's own `01-notes.md` stays). ADRs in `docs/adr/` are never moved.
2. **Diff (Mode 2).** Diff only when the archived run is an audit (`01-architecture-issues.md` or `quick-audit.md`); otherwise (Mode 1 files or `quick-design.md` archived) archive and omit Change Since Last Audit. Read the archived issues before assigning IDs (step 3). After the new files are written, run the comparison script instead of diffing by hand:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/compare_audits.py" docs/architecture/history/<YYYY-MM-DD>[-N] docs/architecture --repo <root>
   ```

   The first argument is the folder you just archived into in step 1 (with its `-N` suffix on a same-day re-run).

   It matches issues by fingerprint (computed from each ID prefix and Evidence cell; you never hash) and prints the whole **Change Since Last Audit** section: Previous → Current band and score, Fixed / New / Still open / Changed / Possibly moved IDs, and `### Score Trend`: one line explaining the score change, then the last 10 runs from `docs/architecture/history/`. The line names what moved the score, for example `Score +0.8 (6.1 → 6.9): fixed SEC-001 (Critical), SEC-002 (High); new MNT-004 (Low).`; repeat it in the delivery message. Paste it at the top of the new `01-architecture-issues.md` (template in `templates/mode-2/`) unchanged, keeping every `(uncertain)` marker: an uncertain match is never presented as definitive. Then add the per-category Δ table yourself from the archived and current score tables. Trend dates are the dates the runs were made, read from each run's `> Audit:` timestamp, not the archive folder name. Without a snapshot of the old code (`--old-repo <dir>`, which the skill cannot make), a moved file is detected only when the issue kept its ID, and the move is marked uncertain.
3. **ID stability.** An issue that still exists keeps its previous ID. Assign IDs by reading the archived issues (same file + same problem); a `Keep old IDs: <new> -> <old>` line from the script means you may have renumbered a surviving issue. If the pair's match is certain, restore the old ID in all files, run the script again, and drop that line. If it is marked `(uncertain)`, read both issues first and restore the old ID only when they are the same problem. New issues get the next unused number per prefix. IDs of fixed issues are never reused, so `SEC-002` always means one problem across every run. You can either drop a fixed issue or keep its row for this one run with Status `not-reproducible`. Either way the script reports it as Fixed, and a kept row is not scored and needs no roadmap row.
4. **Tell the user.** The delivery line says the previous run was archived and where: `Previous run archived to docs/architecture/history/2026-10-22/`.
5. **Quick ↔ full diffs.** If the archived run is a `quick-audit.md`, the diff compares overall band/score and issue IDs only (no per-category deltas). A quick re-run after a full audit, or a quick re-run after a quick audit, adds the same 4-line Change Since Last Audit note (Previous line, Fixed and New bullets, Score line, taken from the script's output) at the top of `quick-audit.md`.

Finding the next unused number: take the highest number ever used for that prefix across the current run and every folder under `docs/architecture/history/`, then add one.

## Mode 1 Re-runs

Archive the same way (step 1), but produce no diff section. ADRs:

- Existing ADRs stay in `docs/adr/`.
- A new ADR is written only for a decision that changed; numbering continues (`references/adr-guide.md`).
- The ADR it replaces gets exactly one edit: its Status line becomes `Superseded by NNNN`.
- Unchanged decisions get no new ADR.

## Example Layout

```
docs/
├── adr/
│   ├── 0001-use-postgresql.md          (never moved)
│   └── 0002-modular-monolith.md
└── architecture/
    ├── 01-architecture-issues.md        (current run, starts with Change Since Last Audit)
    ├── 02-api-optimization.md
    ├── 03-improvement-roadmap.md
    └── history/
        ├── 2026-09-01/                  (archived 2026-09-01)
        └── 2026-10-22/                  (archived 2026-10-22, just before the current run)
```

## Checklist

- [ ] Previous plugin output moved to `docs/architecture/history/<YYYY-MM-DD>[-N]/`; ADRs untouched
- [ ] Mode 2: Change Since Last Audit from `compare_audits.py` at the top of `01` (or the 4-line note in `quick-audit.md`); uncertain markers kept
- [ ] Surviving issues keep their IDs; fixed IDs never reused
- [ ] Delivery line names the archive folder and repeats the `Score ...` line
