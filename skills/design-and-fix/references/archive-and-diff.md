# Archive and Diff

Step 3, before writing any output file. Keeps earlier runs instead of overwriting them, and turns a re-audit into a progress report.

## When It Applies

`docs/architecture/` already contains plugin output, which is only these exact filenames: `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md`, `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md`, `04-tech-stack-comparison.md`, `quick-design.md`, `quick-audit.md`. Refinements ("go deeper on X", `references/refinement-rules.md`) edit in place and never archive.

## Steps

1. **Archive.** Move those files to `docs/architecture/history/<YYYY-MM-DD>/` (today's date). If that folder exists, use `<YYYY-MM-DD>-2`, then `-3`, and so on. Move only those plugin filenames; leave every other file in `docs/architecture/` alone (no globs: a user's own `01-notes.md` stays). ADRs in `docs/adr/` are never moved.
2. **Diff (Mode 2).** Diff only when the archived run is an audit (`01-architecture-issues.md` or `quick-audit.md`); otherwise (Mode 1 files or `quick-design.md` archived) archive and omit Change Since Last Audit. Read the archived `01-architecture-issues.md` and `02-api-optimization.md` and write **Change Since Last Audit** at the top of the new `01-architecture-issues.md` (template in `templates/mode-2/`):
   - previous → current overall score and band; the "Previous … on <date>" value comes from the archived files' own date (their Scope/Context lines, or the archive folder name if absent),
   - per-category deltas,
   - issues fixed (IDs), new issues (IDs), still open (IDs).
3. **ID stability.** An issue that still exists keeps its previous ID (match by same file + same problem). New issues get the next unused number per prefix. IDs of fixed issues are never reused, so `SEC-002` always means one problem across every run.
4. **Tell the user.** The delivery line says the previous run was archived and where: `Previous run archived to docs/architecture/history/2026-10-22/`.
5. **Quick ↔ full diffs.** If the archived run is a `quick-audit.md`, the diff compares overall band/score and issue IDs only (no per-category deltas). A quick re-run after a full audit, or a quick re-run after a quick audit, adds the same 3-line Change Since Last Audit note (band change, fixed IDs, new IDs) at the top of `quick-audit.md`.

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
- [ ] Mode 2: Change Since Last Audit at the top of `01` (or the 3-line note in `quick-audit.md`)
- [ ] Surviving issues keep their IDs; fixed IDs never reused
- [ ] Delivery line names the archive folder
