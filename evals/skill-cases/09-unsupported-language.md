# 09: Unsupported language

## Prompt

```text
Audit this repo. Don't ask me anything.
```

## Setup

- `tests/fixtures/eval-cases/elixir-only`: an Elixir/Plug project; the scanner has no rules for Elixir.

## Must

- [ ] Writes `docs/architecture/quick-audit.md`
- [ ] States that "no scanner rules apply" (or "no rules", or "not supported by the scanner") (reading the code by hand is fine)

## Must not

- [ ] Presents scanner-style findings as if rules had run
