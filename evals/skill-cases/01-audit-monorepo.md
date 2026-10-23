# 01: Full audit of the monorepo fixture

## Prompt

```text
Do a full audit of services/payments, services/orders and services/catalog. Checkout (charge) is slow and sometimes double-charges, we must keep PostgreSQL, and peak traffic is about 50 req/s.
```

## Setup

- Fresh copy of `tests/fixtures/monorepo` as the working folder, plugin installed, no `docs/` folder.
- Reference output: `examples/mode-2-audit-monorepo/` (wording may differ; IDs and severities may differ where the skill allows judgement).

## Must

- [ ] Runs `scan_repo.py` with `--scope` for the three named services before writing anything
- [ ] Asks no questions (scope, pain point, constraint and scale are all given)
- [ ] Writes exactly `docs/architecture/01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md` (full mode, because the prompt says "full")
- [ ] Headers match `references/formatting-rules.md` in order; Scope and Context lines at the top of each file
- [ ] Headline is `## Architecture Health Score: <Band> (<X.X> / 10)`; every sub-score matches the rubric lookup table for its issues' severities
- [ ] Evidence line `Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed` is present and consistent
- [ ] Finds SQL string concatenation at `services/catalog/app.py:21` and the committed password at `services/catalog/app.py:9` as Critical
- [ ] Finds unauthenticated `POST /payments/charge` (`charge.ts:14`) and notes that `requireAuth` exists only in `refund.ts` and only checks that an `Authorization` header is present (`refund.ts:8`)
- [ ] Traces the double-charge pain point to the missing idempotency key in `services/payments/src/handlers/charge.ts`, or marks it `Not verified` with a reason
- [ ] Traces the slow-checkout pain point to the untimed provider `fetch` (`charge.ts:17`) and the insert-per-item loop (`charge.ts:21`)
- [ ] Every fix keeps PostgreSQL
- [ ] Every issue cites a `path:line` that exists in the fixture; `API-*` issues only in `02`, all others only in `01`
- [ ] Every defined ID appears in exactly one `Action (IDs)` row of the three roadmap horizon tables
- [ ] States that `App/` was not assessed (or leaves it out of scope)

## Must not

- [ ] Asks about stack, frameworks, database or folder layout
- [ ] Reports the `unbounded-query` signal at `services/payments/src/handlers/webhook.ts:8` (primary-key lookup) as an issue
- [ ] Cites a file or line that does not exist in the fixture
- [ ] Recommends replacing PostgreSQL
- [ ] Writes `04-tech-stack-comparison.md` or ADRs
- [ ] Uses severity, effort or impact words outside the fixed vocabulary
