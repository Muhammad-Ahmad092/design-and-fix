# Examples

Real output produced by following the `design-and-fix` skill as written (Format 1.0.2). Use them to judge the format before running the skill on your own project.

| Folder | Mode | Input |
|---|---|---|
| `mode-2-audit-express-boilerplate/` | Mode 2: audit | **A real open-source service**: `hagopj13/node-express-boilerplate` at tag `v1.7.0` (MIT), whole repository |
| `mode-2-audit-monorepo/` | Mode 2: audit | `tests/fixtures/monorepo`, a **synthetic fixture with planted issues** (it is test data, not a real product) |
| `mode-1-food-delivery/` | Mode 1: new design | The food-delivery prompt from the main README |

## Mode 2: audit of a real service

Prompt:

```text
Audit this Express API starter (node-express-boilerplate v1.7.0). Don't ask questions, assume defaults.
```

- Scope names the repository, the pinned tag and URL. The repo is fetched by `evals/fetch_corpus.py` (pinned in `evals/corpus.json`) into `../design-and-fix-corpus/`, outside this repository; it is not copied here.
- Scanner command: `python skills/design-and-fix/scripts/scan_repo.py <corpus>/node-express-boilerplate/node-express-boilerplate-1.7.0 --no-git > examples/mode-2-audit-express-boilerplate/scan.json`; `root` was then rewritten to `node-express-boilerplate@v1.7.0` so no local path ships. The archive has no git history, so the Audit line says `rev unknown`.
- It is non-interactive, so the Context line is an assumption (marked `Assumed (non-interactive)`). The code is about 3.7k LOC, above the Quick-mode threshold, so the full workflow ran.
- 34 files were read, 2 scanner signals were opened and both confirmed. The 1.0.2 scanner also raised two `hardcoded-secret` hits here (a token-type enum and a sample JWT in Swagger docs) and three `no-tests` hits (the tests live in the repo-root `tests/` folder); 1.1.0 no longer reports them. The scanner still misses the chained `router.route().post()` user routes; the Endpoint Inventory says which rows were read by hand.
- Every `path:line` is relative to the repo root. The test suite checks the evidence format always, and checks the files too when the corpus has been fetched. `lint_output.py --scan examples/mode-2-audit-express-boilerplate/scan.json` runs here without `--repo`, because the repo is not part of this checkout.
- Result: a healthy codebase (Healthy, 8.1 / 10) with Medium and Low issues only, so there are no Critical or High sections.

Files: `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md`, plus the raw `scan.json`.

## Mode 2: audit of the synthetic monorepo

Prompt:

```text
Do a full audit of services/payments, services/orders and services/catalog. Checkout (charge) is slow and sometimes double-charges, we must keep PostgreSQL, and peak traffic is about 50 req/s.
```

- The prompt names the scope, pain point, constraint and scale, so the skill asks no questions. It says "full" because the fixture is under 2,000 LOC, which would otherwise select Quick mode.
- Scanner command: `python skills/design-and-fix/scripts/scan_repo.py tests/fixtures/monorepo --no-git --scope services/payments --scope services/orders --scope services/catalog > examples/mode-2-audit-monorepo/scan.json`; `root` was then rewritten to `tests/fixtures/monorepo`.
- Every signal was checked against the fixture source: 1 of 23 was rejected and 18 back `confirmed` issues; the rest back `likely` issues and SEC-001, which stays `needs-review` because its signal carries `uncertain_reason` (the router mount is outside the scanned files). Every `path:line` is relative to `tests/fixtures/monorepo` and is checked by `tests/test_examples.py`; `lint_output.py --repo tests/fixtures/monorepo --scan examples/mode-2-audit-monorepo/scan.json` runs clean.
- The Context line says "payments", so Security counts double in the score (weights table in `references/health-score-rubric.md`): 4.9 weighted instead of 5.4 unweighted.
- `App/` is a six-line .NET stub and was not assessed.

Files: `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md`, plus the raw `scan.json`.

## Mode 1: food-delivery design

Prompt (from the main README):

```text
Design the architecture for a food-delivery app. Expect about 50k users, a team of 4, and prefer Node.js. Ask me for any missing constraints before writing the design.
```

- The example was generated in a headless run, so the skill could not ask its questions; every missing item (DAU share, latency, availability, compliance, cost) uses the skill's defaults and is marked `Assumed (non-interactive)` under Assumptions & Constraints.
- Format 1.0.2 and plugin 1.1.0 additions to look for: the Requirements table in Overview (ten rows, each with its Source: `user` for what the prompt stated, `assumption (non-interactive)` for defaults), one trade-off table per decision (five, one per Key Architectural Decisions row), Requirements → Decisions, Security & Threat Model (trust boundaries plus STRIDE with a Boundary column), a `Required now / Later` column, and ADRs with Alternatives and Assumptions sections.
- In a real project the files go to `docs/architecture/` and the ADRs to `docs/adr/`; here the ADRs sit in `mode-1-food-delivery/adr/`.

Files: `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md`, `adr/0001`–`0005`.
