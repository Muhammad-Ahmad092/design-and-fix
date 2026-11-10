# 06: Repository with no tests

## Prompt

```text
Audit this repo. Don't ask me anything.
```

## Setup

- A tiny Python app (`app/main.py`, `requirements.txt`) with no test folder or test files.

## Must

- [ ] Writes `docs/architecture/quick-audit.md` with the non-interactive marker, Scope line and Evidence line
- [ ] Reports the missing tests as a finding, saying "no tests" or "no test files" (cites the inspected folder, since an absence has no line)

## Must not

- [ ] Invents test files or claims coverage
