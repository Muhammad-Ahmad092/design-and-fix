# Examples

Real output produced by following the `design-and-fix` skill as written. Use them to judge the format before running the skill on your own project.

| Folder | Mode | Input |
|---|---|---|
| `mode-2-audit-monorepo/` | Mode 2: audit | `tests/fixtures/monorepo`, a **synthetic fixture with planted issues** (it is test data, not a real product) |
| `mode-1-food-delivery/` | Mode 1: new design | The food-delivery prompt from the main README |

## Mode 2: audit of the synthetic monorepo

Prompt:

```text
Do a full audit of services/payments, services/orders and services/catalog. Checkout (charge) is slow and sometimes double-charges, we must keep PostgreSQL, and peak traffic is about 50 req/s.
```

- The prompt names the scope, pain point, constraint and scale, so the skill asks no questions. It says "full" because the fixture is under 2,000 LOC, which would otherwise select Quick mode.
- Scanner command: `python skills/design-and-fix/scripts/scan_repo.py tests/fixtures/monorepo --no-git > examples/mode-2-audit-monorepo/scan.json`; `root` was then rewritten to `tests/fixtures/monorepo` so no local path ships.
- Every signal was checked against the fixture source; 18 of 20 were confirmed. Every `path:line` is relative to `tests/fixtures/monorepo` and is checked by `tests/test_examples.py`.
- `App/` is a six-line .NET stub and was not assessed.

Files: `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md`, plus the raw `scan.json`.

## Mode 1: food-delivery design

Prompt (from the main README):

```text
Design the architecture for a food-delivery app. Expect about 50k users, a team of 4, and prefer Node.js. Ask me for any missing constraints before writing the design.
```

- The example was generated in a headless run, so the skill could not ask its questions; every missing item (DAU share, latency, availability, compliance, cost) uses the skill's defaults and is marked `Assumed (non-interactive)` under Assumptions & Constraints.
- In a real project the files go to `docs/architecture/` and the ADRs to `docs/adr/`; here the ADRs sit in `mode-1-food-delivery/adr/`.

Files: `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md`, `adr/0001`–`0005`.
