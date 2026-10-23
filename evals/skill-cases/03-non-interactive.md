# 03: Headless audit with no answers available

## Prompt

```text
Audit this repo. Don't ask me anything.
```

Run headless, for example `claude -p "Audit this repo. Don't ask me anything."`.

## Setup

- Fresh copy of `tests/fixtures/monorepo` as the working folder, plugin installed, no `docs/` folder.

## Must

- [ ] No questions asked; work starts at once
- [ ] Runs the whole-root `--folders-only` scan to read `candidate_folders`, then the `--scope` evidence scan on the chosen scope
- [ ] Default scope is the top 3 non-overlapping folders by LOC: `services/payments`, `services/orders`, `services/catalog` (not their parent `services`)
- [ ] `> Scope:` line ends with `(Assumed (non-interactive))`
- [ ] `> Context:` line records pain points none, constraints none and scale unknown, each labelled `Assumed (non-interactive)`
- [ ] Quick mode chosen silently (the scoped scan is under 2,000 LOC): writes only `docs/architecture/quick-audit.md`
- [ ] Delivery says "Quick mode used; say 'full' for the 3-file version"
- [ ] `quick-audit.md` shows the band and score plus the Evidence line, no category table; every issue cites `path:line`

## Must not

- [ ] Stops or waits for answers
- [ ] Asks any question, including "new design or audit?"
- [ ] Writes the three full Mode 2 files
- [ ] Audits `App/` or the whole root as one scope
