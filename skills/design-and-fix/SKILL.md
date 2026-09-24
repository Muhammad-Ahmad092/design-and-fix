---
name: design-and-fix
description: Produces ranked, actionable system-design, architecture, and API-optimization guidance, either by designing a new project from scratch or auditing an existing codebase. Always collects constraints (scale, stack, team, non-functional requirements) before generating anything. Use this skill whenever the user asks to design a system, plan an architecture, choose between monolith and microservices, design or review an API, optimize API performance, audit a codebase for scalability, reliability, or maintainability issues, score architecture health, or build an improvement roadmap, even if they do not say "architecture" explicitly.
---

# design-and-fix

Ranked, context-grounded architecture and API guidance. Two modes: **New Project** (design) and **Existing Project** (audit and fix).

## Core Principle

**No output without context.** Never generate files before the Context Collection phase is complete. Advice without constraints (scale, team, budget, stack) is generic noise.

## Step 1: Detect Mode

| Signal | Mode | Read next |
|---|---|---|
| User gives an idea, PRD, doc, or rough description of something not yet built | Mode 1: New Project | `references/mode-1-new-project.md` |
| User points to a codebase, folders, services, or existing docs and wants an audit | Mode 2: Existing Project | `references/mode-2-existing-project.md` |
| Unclear | Ask one question: "New design or audit of an existing project?" | none until answered |

## Step 2: Context Collection (mandatory)

Read `references/context-collection.md` and follow it.

- Ask all missing questions in one batched message, never one at a time.
- Skip anything the user already answered in their prompt or attached docs.
- Do not proceed to Step 3 until answers arrive, unless the user explicitly says to assume defaults.

## Step 3: Generate Output

Follow the mode reference file. Each mode produces **exactly 3 files**, using the matching folder in `templates/`.

| Mode | Files |
|---|---|
| Mode 1 | `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md` |
| Mode 2 | `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md` |

Write files to `docs/architecture/` in the project (create the folder if missing). If the agent cannot write files, output them in chat in the same order.

Mode 2 additionally requires an **Architecture Health Score (1-10)** with sub-scores. Read `references/health-score-rubric.md` before scoring.

## Step 4: Progressive Refinement

After the 3 files exist, the user may say "go deeper on X" or "revisit Y". Read `references/refinement-rules.md`. Expand only the requested section. Never regenerate all files.

## Optional File

`04-tech-stack-comparison.md` (template in `templates/optional/`) is created **only** if the user explicitly asks for it.

## Cross-Cutting Rules

Full details in `references/formatting-rules.md`. Summary:

1. Discipline over verbosity: about one page per major section, tables and bullets over prose.
2. Every recommendation carries a one-line "why".
3. No filler: omit sections that do not apply.
4. Consistent headers across modes and files.
5. Never invent facts about the user's codebase; cite file paths or say "not verified".

## Reference Map

| File | Read when |
|---|---|
| `references/context-collection.md` | Always, before generating |
| `references/mode-1-new-project.md` | Mode 1 |
| `references/mode-2-existing-project.md` | Mode 2 |
| `references/health-score-rubric.md` | Mode 2 scoring |
| `references/refinement-rules.md` | User asks to go deeper or revisit |
| `references/formatting-rules.md` | Before writing any output file |
| `scripts/scan_repo.py` | Mode 2, to detect stack and list candidate folders (optional) |
