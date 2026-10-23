# Quick Mode

One short file for small asks. Same rigor as the full workflow (evidence, IDs, fixed vocabulary), a fraction of the length.

## When Quick Mode Is Chosen

- (a) the user says "quick", or
- (b) Mode 2 and the scanner reports < 2,000 approx LOC (`summary.approx_loc` of the `--scope` run) in the audited scope, or
- (c) Mode 1 and the request is a single component/feature rather than a whole system.

Chosen silently (no confirmation question). The delivery line says "Quick mode used; say 'full' for the 3-file version". "full" or "detailed" in the request always forces the full workflow.

A single focused question ("PUT or PATCH?") is not Quick mode: answer it in chat with no file (SKILL.md, Step 0).

## Output

| Request | File | Template |
|---|---|---|
| Design (Mode 1 shape) | `docs/architecture/quick-design.md` | `templates/quick/quick-design.md` |
| Audit (Mode 2 shape) | `docs/architecture/quick-audit.md` | `templates/quick/quick-audit.md` |

- One file, max about 1.5 pages. Headers, in order, from `references/formatting-rules.md` (Standard Headers, Quick).
- No ADRs and no `04-*` unless the user asks.

## Context

At most 3 questions, batched in one message (or none when non-interactive: use the defaults in `references/context-collection.md`, marked `Assumed (non-interactive)`). Quick audit never asks about stack, frameworks, database or folder layout.

## Quick Audit Rules

- Still runs the scanner (`--scope` on the audited folders), still verifies every signal by reading the file, still cites `path:line`, still uses issue IDs (`PREFIX-NNN`).
- Shows the overall band only, with the score in parentheses: `**<Band> (<X.X> / 10)**` plus the Evidence line. No sub-scores, no category table. Compute it with `references/health-score-rubric.md`.
- Every issue is defined in Top Issues; Quick Wins and Next Steps together reference each ID exactly once.

## Re-runs

Previous output is archived first (`references/archive-and-diff.md`). A quick re-run after a full or quick audit adds the 3-line Change Since Last Audit note (band change, fixed IDs, new IDs) at the top of `quick-audit.md`. A full audit after a quick one compares overall band/score and IDs only.

## Self-Check

- [ ] One file, about 1.5 pages, standard Quick headers in order
- [ ] At most 3 questions asked (none when non-interactive, defaults marked)
- [ ] Quick audit: scanner run, signals verified, `path:line` evidence, IDs, band plus score in parentheses only
- [ ] Delivery line includes "Quick mode used; say 'full' for the 3-file version"
