# Evals

Two kinds of checks. Run both before each release.

| Kind | What | How |
|---|---|---|
| Scanner evals | `scan_repo.py` finds every expected signal on the fixtures and nothing on `clean-service` (`scanner-expected.json`) | Automated, `unittest` |
| Skill cases | The agent follows the skill end to end on five prompts (`skill-cases/`) | Manual or headless agent run, graded by checklist |

## Scanner evals

From the repo root:

```bash
python -m unittest discover -s tests -v
```

Use `python3` where `python` is missing. The suite also checks the skill docs, templates and `examples/`.

## Skill cases

Each file in `skill-cases/` has four sections: `## Prompt`, `## Setup`, `## Must`, `## Must not`.

1. Make a fresh copy of the folder named in `## Setup` (usually `tests/fixtures/monorepo`) outside the repo, with the plugin installed for your agent.
2. Run the prompt there, either interactively or headless, for example `claude -p "<prompt>"` from inside the copy.
3. Grade the transcript and the files written against the checklists.

| Case | Covers |
|---|---|
| `01-audit-monorepo.md` | Full Mode 2 audit: scanner first, verified evidence, scoring, roadmap |
| `02-quick-question.md` | Step 0: a single question is answered in chat |
| `03-non-interactive.md` | Headless run: no questions, defaults recorded |
| `04-design-food-delivery.md` | Mode 1: capacity, data model, endpoints, ADRs |
| `05-rerun-diff.md` | Re-run: archive, Change Since Last Audit, stable IDs |

## Grading

- A case passes when **every** `Must` item is true and **no** `Must not` item is true.
- A release ships only when all five cases pass. Record failures with the transcript; they become fixes or new checklist items.
- Grade by hand, or give the checklist, transcript and output files to an LLM judge and spot-check its verdict.
