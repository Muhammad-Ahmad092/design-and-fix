---
name: design-and-fix
description: Designs new systems and audits existing codebases, producing ranked, evidence-backed architecture and API guidance written to docs/architecture/. Use when the user asks to design a system, service or feature, plan an architecture, choose between monolith and microservices, estimate capacity, design or review an API, write architecture decision records, audit a codebase or service for system design, API, scalability, reliability, security, observability or maintainability issues, score architecture health, re-run an audit to see what changed, or build an improvement roadmap. Audits run a bundled static scanner first and verify every signal by reading the cited file. Small single questions are answered in chat without the full workflow.
---

# design-and-fix

Ranked, context-grounded architecture and API guidance. Modes: **Mode 1** (design a new project), **Mode 2** (audit an existing project) and **Quick** (one short file for small asks).

**No output without context, no finding without evidence.** Collect constraints before writing files (Step 2); in audits, every issue cites a file you actually read.

## Step 0: Size the request

| Request | Do |
|---|---|
| A single focused question ("PUT or PATCH?", "is Redis right for sessions?") | Answer directly in chat: no questionnaire, no files, no scanner. Stop here. |
| A real design or audit request | Continue with Step 1. |

**Quick mode** (one file, details in `references/quick-mode.md`) is chosen when:

- (a) the user says "quick", or
- (b) Mode 2 and the scanner reports < 2,000 approx LOC (`summary.approx_loc`) in the audited scope, or
- (c) Mode 1 and the request is a single component/feature rather than a whole system.

Quick mode is chosen silently (no confirmation question); the delivery line says "Quick mode used; say 'full' for the 3-file version". "full" or "detailed" in the request always forces the full workflow.

## Step 1: Detect mode

| Signal | Mode | Read next |
|---|---|---|
| An idea, PRD, doc or rough description of something not built yet | Mode 1: New Project | `references/mode-1-new-project.md` |
| A codebase, folders, services or existing docs to audit | Mode 2: Existing Project | `references/mode-2-existing-project.md` |
| Unclear | Ask one question: "New design or audit of an existing project?" (non-interactive: pick the likelier option and state it on the Context line) | none until answered |

## Step 2: Context

Follow `references/context-collection.md`.

- Ask all missing questions in one batched message; skip what the prompt or docs already answer.
- Mode 2 scans first and never asks about stack, frameworks, database or folder layout (max 4 questions). When the scope is already known, run the `--scope` scan and decide Quick mode before asking anything. Quick mode asks at most 3.
- If you cannot ask (headless/exec/CI run, or the user said "don't ask" / "assume defaults"), proceed at once with the defaults table and mark each default `Assumed (non-interactive)` (under Assumptions & Constraints in design files, on the `> Context:` line in audit files).

### The scanner (Mode 2 and Quick audit)

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/scan_repo.py" <root> --folders-only   # scope unknown: discover scope
python3 "${CLAUDE_SKILL_DIR}/scripts/scan_repo.py" <root> --scope <path>   # evidence scan
```

Other harnesses: resolve `scripts/` relative to the folder containing this SKILL.md; on Windows use `python` if `python3` is missing. Use `--format markdown` for a quick human read and the default JSON for evidence. When the scope is unknown, run `--folders-only` (fast: stack, frameworks and `candidate_folders` only) to propose scope; never run the full analyzers on the whole root unless asked.

**Signals are leads.** Open each cited file, confirm or reject the signal, and only then turn it into an issue. Rejected signals are not reported (optionally counted in one line: "12 scanner signals reviewed, 9 confirmed").

## Step 3: Archive previous output

Before writing any output file, if `docs/architecture/` already holds plugin output (the exact filenames listed in `references/archive-and-diff.md`), archive it and, in audits, diff against it. Follow `references/archive-and-diff.md`. ADRs are never moved. Refinements (Step 5) never archive.

## Step 4: Generate

| Mode | Files in `docs/architecture/` | Templates | Reference |
|---|---|---|---|
| Mode 1 | `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md`, plus up to 5 ADRs in `docs/adr/` | `templates/mode-1/`, `templates/adr/` | `references/mode-1-new-project.md`, `references/adr-guide.md` |
| Mode 2 | `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md` (ADRs only on request) | `templates/mode-2/` | `references/mode-2-existing-project.md` |
| Quick | `quick-design.md` or `quick-audit.md` | `templates/quick/` | `references/quick-mode.md` |

- Create the folders if missing. If the agent cannot write files, output them in chat in the same order.
- Audits score with `references/health-score-rubric.md` (seven categories, lookup table, band as headline).
- `04-tech-stack-comparison.md` (`templates/optional/`) is created **only** when the user explicitly asks for it.
- Deliver in a few lines: files written, the health band and score (audits), whether a previous run was archived and where, and the Quick-mode line when it applies.

## Step 5: Refinement

After the files exist, "go deeper on X", "revisit Y" or "change constraint Z" edits only the affected section in place. Follow `references/refinement-rules.md`. Never regenerate all files unless asked.

## Cross-cutting rules

Full details in `references/formatting-rules.md`. Summary:

1. Discipline over verbosity: about half a page per major section, tables and bullets over prose.
2. Every recommendation carries a one-line "Why".
3. No filler: omit sections that do not apply.
4. Standard headers, issue IDs (`PREFIX-NNN`) and fixed Severity / Effort / Impact vocabulary.
5. Diagrams in Mermaid by default.
6. Never invent facts about the user's codebase; cite `path:line` or mark `Not verified`.

## Reference map

| File | Read when |
|---|---|
| `references/context-collection.md` | Step 2, always before generating |
| `references/quick-mode.md` | Quick mode is chosen |
| `references/archive-and-diff.md` | Step 3, previous output exists |
| `references/mode-1-new-project.md` | Mode 1 |
| `references/capacity-estimation.md` | Mode 1 Capacity Estimates |
| `references/adr-guide.md` | Mode 1 ADRs, or ADRs requested in Mode 2 |
| `references/mode-2-existing-project.md` | Mode 2 |
| `references/health-score-rubric.md` | Mode 2 and Quick audit scoring |
| `references/formatting-rules.md` | Before writing any output file |
| `references/refinement-rules.md` | Step 5, user asks to go deeper, revisit or change a constraint |
| `scripts/scan_repo.py` | Mode 2 and Quick audit, before asking questions |
