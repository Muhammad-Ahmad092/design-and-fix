# 07: Huge repository

## Prompt

```text
Audit this repo. Don't ask me anything.
```

## Setup

- `tests/fixtures/monorepo` plus 20,000 generated Python files under `generated/` (created by the runner at setup time, never committed).

## Must

- [ ] Finishes inside the case timeout (900 s) instead of scanning everything
- [ ] Writes `docs/architecture/quick-audit.md` whose `> Scope:` line names what was audited

## Must not

- [ ] Hangs, or runs the full analyzers on the whole root
