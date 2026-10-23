# 05: Re-run archives the previous audit and diffs it

## Prompt

```text
Re-run the full audit of services/payments, services/orders and services/catalog. Same context as before: checkout (charge) is slow and sometimes double-charges, we must keep PostgreSQL, peak is about 50 req/s.
```

## Setup

- Fresh copy of `tests/fixtures/monorepo` as the working folder, plugin installed.
- Copy `examples/mode-2-audit-monorepo/01-architecture-issues.md`, `02-api-optimization.md` and `03-improvement-roadmap.md` into `docs/architecture/` of the copy (the previous run).
- Fix one planted issue before running: in `services/catalog/app.py` replace line 21 with `cursor.execute("SELECT * FROM items WHERE name = %s", (name,))`.

## Must

- [ ] Moves the three previous files to `docs/architecture/history/<today>/` before writing new ones
- [ ] New `01-architecture-issues.md` starts with `## Change Since Last Audit`, before the health score
- [ ] Change Since Last Audit shows previous → current band and score, a per-category delta table, and Fixed / New / Still open IDs
- [ ] `SEC-002` (SQL injection) is listed as Fixed and is not reused for another issue
- [ ] Every surviving issue keeps its previous ID (for example `REL-001`, `SEC-001`, `SCL-001`)
- [ ] Any new issue gets the next unused number for its prefix
- [ ] Security sub-score and the overall score are recomputed from the remaining issues with the rubric
- [ ] Delivery line names the archive folder

## Must not

- [ ] Overwrites the previous files without archiving them
- [ ] Renumbers surviving issues
- [ ] Still reports SQL injection at `services/catalog/app.py:21`
- [ ] Moves or edits anything in `docs/adr/`
