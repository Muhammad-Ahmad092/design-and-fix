# How Design & Fix works

Two parts with different jobs. The scanner collects evidence cheaply and deterministically. The skill reads code, decides, scores and writes. Three terms are kept apart in every document:

- **Scanner signal**: a lead from a static pattern. It can be wrong.
- **Verified finding**: a signal (or an agent discovery) that the skill confirmed by reading the cited code. It gets an ID, a severity and a Status.
- **Recommendation**: the Fix and Verify cells of a finding and the roadmap built from them. Nothing is applied; Design & Fix never edits your code.

## The flow

```text
scan_repo.py  ->  scan.json (signals)  ->  skill verifies  ->  findings + score  ->  docs/architecture/*.md
                                                |                                        |
                                           rejected signals                    lint_output.py checks structure
                                           (counted, not listed)               compare_audits.py diffs re-runs
```

### The scanner (`skills/design-and-fix/scripts/`)

Python 3.9+, standard library only, no network, read-only. It walks the repo (honouring the root `.gitignore`), builds the import graph and route list, runs the rules in [rules.md](rules.md) and writes schema 3 JSON: `signals`, `routes`, `import_graph`, `hotspots`, `summary`, `notes`. Each signal has a rule, `file`, `line`, `snippet` (secrets masked), `confidence` and `severity_hint`. A signal may add `uncertain_reason` when the scanner could not see something that decides the answer, such as auth configured outside `--scope`. Rules measured at 0% precision at release ship off by default; the scan lists them in `summary.default_off_rules`, and a config re-enables one with `"rules": {"<id>": {"enabled": true}}` ([configuration.md](configuration.md)).

### The skill (`skills/design-and-fix/`)

The skill runs the scanner first, asks only about pain points, constraints and scale, then reads the code behind each signal. For every signal it checks that the cited line says what the rule claims, that it is not a comment, test or generated file, and that nothing outside the file (middleware, a sibling test folder) changes the answer. Each outcome is one of:

| Outcome | Where it appears |
|---|---|
| Rejected | Counted on the "Rejected signal(s)" line with the reason; no ID |
| `confirmed` | Issue row; the defect is visible in the code |
| `likely` | Issue row; strong evidence that depends on runtime |
| `needs-review` | Issue row; the signal carries `uncertain_reason`, or the skill could not decide |
| `informational` | Issue row, not scored, not on the roadmap |
| `not-reproducible` | Re-run only: the old issue is gone; counts as fixed |

Scores come from a lookup table in the rubric, never from the agent's impression. `lint_output.py` checks structure, scores, IDs and that every cited `path:line` exists. On a re-run the previous output is archived and `compare_audits.py` prints Fixed, New, Still open and a score-change line. It cannot check that the diagnosis is right; see [limitations.md](limitations.md). A worked re-run is in [examples/before-after](../examples/before-after/).

## Example: a rejected signal

From [examples/mode-2-audit-monorepo](../examples/mode-2-audit-monorepo/01-architecture-issues.md). The scanner emitted:

```text
unbounded-query  services/catalog/app.py  line 21  confidence low  cursor.execute("SELECT * FROM items WHERE name = '" + name + "'")
```

The skill read the query: it filters on `name`, so it does not return the whole table. The signal was a false positive for its rule, so it became no issue and was counted on the line "Rejected signal (1 of 23)" with that reason. The real defect on that line, user input concatenated into SQL, is SEC-002 from `sql-string-concat`. Rejections work the same way for other rules: in the 1.0.2 run of the [express example](../examples/mode-2-audit-express-boilerplate/01-architecture-issues.md), three `no-tests` signals were rejected because the tests live in the repo-root `tests/` tree.

## Example: a confirmed finding

Same audit. The scanner emitted:

```text
no-health-endpoint  src/app.js  line 17  confidence medium  "no health endpoint in service src"
```

The skill read `src/app.js` and `src/routes/v1/index.js`, found no health route, and noted that `ecosystem.config.json` only restarts pm2 on crash. The signal became a verified finding:

| ID | Severity | Status | Source | Evidence |
|---|---|---|---|---|
| OBS-001 | Medium | confirmed | scanner:no-health-endpoint | `src/app.js:17` |

Its recommendation: add `GET /v1/health` that checks `mongoose.connection.readyState`. Its verification: the route returns 200, and 503 while the MongoDB connection is down. The Source value `scanner:<rule>` records that the lead came from the scanner; `agent` means the skill found it by reading, with no signal behind it.

## Evaluation

[evals/](../evals/README.md) measures both parts: scanner precision per rule against hand-labelled signals on pinned public repos, and skill cases run through a harness. The eval runner supports `claude`, `codex` and `cursor`; only Claude Code has been run end to end. Numbers are in the README under Measured quality.
