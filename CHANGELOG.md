# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] - 2026-11-05

Accuracy and trust release: the weak rules were reworked and re-measured on a larger corpus, rules now say when they are unsure, the scan is about three times faster on large repos, and the evals that back these numbers are stronger. JSON output stays at schema 3 and every change to it is **additive**. The report format stays `Format: 1.0.2`; the new finding fields are optional.

### Behaviour changes to know about

- `openapi-drift` is **off by default**. It measured 0% (0 of 14) on the corpus, all from Express routers mounted with `app.use('/prefix', router)`, whose prefix it does not resolve. Enable it with `"rules": {"openapi-drift": {"enabled": true}}` in `.design-and-fix.json`. Every scan lists the rules that are off by default in `notes` and in the new `summary.default_off_rules`; a config that sets `"enabled"` for such a rule (true or false) is respected and is not noted twice.
- Measured precision now caps signal confidence (`dfscan/measured.json`): `high` only from 80% with at least 10 decided labels, `low` under 50%, and the registry value when fewer than 5 labels were decided. Measured precision can only lower a rule's confidence, so some signals carry a lower confidence than in 1.0.2.
- `--fail-on` fails closed on a partial scan (timeout or `max_files` reached): when no threshold is hit but `summary.partial` is true, the exit code is 3 and a note says so. Without `--fail-on` the exit code is unchanged.
- SARIF marks default-off rules (such as `openapi-drift`) with `defaultConfiguration.enabled: false`, and a disabled `openapi-drift` no longer runs or writes notes.
- Signals whose evidence the scanner could not follow carry `uncertain_reason` (for example "router ... is exported but not mounted in the scanned files"). The skill reports such findings as `needs-review`, never as `confirmed`, and `lint_output.py --scan <scan.json>` enforces it. SARIF results carry it as `properties.uncertainReason`.
- `hardcoded-secret` reports known credential formats at Critical and generic `key = "value"` matches at Medium at most.
- RSpec spec files (`*_spec.rb`) are now treated as test files, so rules that skip test code skip them and `summary.test_files` counts them. The rules whose output can change on RSpec repos had their rule versions bumped.
- Most rules have new rule versions (listed in `meta.rule_versions` and `docs/rules.md`). Fingerprints still hash the source line, so issues keep matching across re-runs even where a snippet text changed (`query-in-loop` now reads `<call> inside <loop> at line N`, `sql-string-concat` reads `<variable> from line N reaches SQL at line M`).

### Rules reworked, with measured precision

Precision = tp / (tp + fp) on hand-labelled corpus samples, seed 1002, up to 20 signals per rule. 1.0.2 was measured on 14 repos, 1.1.0 on 18 (four added so more rules have signals). With samples this small, read each number as a band; `evals/precision.md` has the 95% intervals.

| Rule | 1.0.2 | 1.1.0 | What changed |
|---|---|---|---|
| `unauth-write-route` | 0% (n=20) | 33% (n=19) | Framework auth adapters (below). The two labellers disagreed on demo apps with no user model (kappa 0.27); with the second labeller's labels it is about 83%. |
| `query-in-loop` | 0% (n=13) | 100% (n=3) | Only real database and ORM calls (receiver plus method, per ORM family), the innermost data loop, and skips for eager loading and prefetching, batched `IN`/`__in=` calls, schema and catalog loops, small fixed loops, per-item transactions and migrations. |
| `unbounded-query` | 0% (n=9) | 11% (n=9) | Docstrings ignored; runtime table names, PostGIS metadata views and catalog CTEs skipped; `objects.all()` only in request handlers or route files. |
| `sql-string-concat` | 0% (n=2) | no corpus signals | Source-to-sink check: request input must reach the SQL call (below). Concatenation alone no longer flags; the three 1.0.2 corpus signals were all false positives and are gone. |
| `hardcoded-secret` | 10% (n=20) | 40% (n=5) | Known provider formats first (AWS, Stripe, GitHub, GitLab, Slack, Google, Anthropic, OpenAI, private keys); generic values need entropy and no placeholder words; OpenAPI examples, docstrings, doctests and CI database-service passwords skipped; key files in test and fixture folders reported at low confidence. Corpus signals 36 to 5. |
| `swallowed-error` | 5% (n=20) | 5% (n=20) | Handlers with an explaining comment, optional imports, best-effort cleanup calls, `try/except/else` and Python fallback patterns are dropped. Django's documented `except X: pass` fallbacks remain the main false positive. |
| `god-module` | 21% (n=20) | 30% (n=14) | Needs fan-in, size and a structural indicator (several import clusters or many mixed top-level definitions); cohesive same-kind collections and test fixtures never qualify. |
| `import-cycle` | 25% (n=12) | 29% (n=14) | Imports inside multi-line function signatures and bracketed or triple-quoted continuations are read correctly, which removes false cycles such as Flask's blueprints-to-templating one. |
| `no-tests` | 30% (n=20) | 50% (n=20) | Root test trees (`tests/`, `spec/`, pytest `testpaths`) cover deeper units when the project runs that tree as its suite (integration folders, runner configs, CI test steps); colocated layouts are still checked per folder. |
| `unpinned-dependency` | 0% (n=4) | 100% (n=18) | `requirements.txt` files for the docs of a Python library are skipped. |

Rules that were measured on a handful of signals in 1.0.2 now have larger samples, and some measured lower: `console-logging-only` 17% (n=12, was 100% at n=2), `http-no-timeout` 45% (n=20, was 100% at n=4), `no-version-prefix` 38% (n=8, was 100% at n=2). They run at confidence `low`. Rules with no corpus signals (`mixed-error-shapes`, `retry-no-backoff`, `sql-string-concat`) are listed as unmeasured with the reason in `docs/rules.md`.

### Added

- **Auth adapters for `unauth-write-route`**: Express (app, router and mount middleware, followed through `require`/`import` and `module.exports = (app) => {...}` callers), NestJS global guards and `@Public()`-style decorators, Flask `before_request` hooks, FastAPI router, include and app dependencies, Django/DRF mixins, decorators, permission classes and settings defaults, Gin/Echo/chi groups and middleware, Spring Security filter chains (Java and Kotlin DSL, ordered matchers), ASP.NET Core fallback policies, global filters and `[AllowAnonymous]`, Rails controller filters and parents, and Laravel controller and route-group middleware. Intentionally public routes (login, registration, token and password-reset flows, health, signed webhooks) are skipped. When inherited auth cannot be resolved, the signal is `low` with `uncertain_reason` instead of a confident finding.
- **Auth resolver scoping and public paths** (`unauth-write-route`):
  - Express middleware applies by receiver, line and mount path: `app.use('/admin', auth)` covers only `/admin` routes, and a path the scan cannot read is `low` with `uncertain_reason`. Named routers and `.route(path).get().post()` chains are followed.
  - A login or auth router mounted with `app.use`, and CORS middleware (Express and Gin), no longer count as auth for other routes.
  - Spring Security: a `permitAll()` that covers every request (`anyRequest()`, `/**`, `/*`, a method-only matcher) is reported as unprotected. A `<prefix>/**` permit is public only when its last segment is public, open, anonymous, anon, guest, static or assets; other prefixes are `low` with `uncertain_reason`.
  - Auth in one service or app no longer protects another: NestJS guards, Flask `before_request` hooks and Django settings apply only within the route's own app, and a route whose app cannot be told is `low` with `uncertain_reason`. A Flask hook that only loads the user is not auth unless it rejects the request.
  - DRF views use `DEFAULT_PERMISSION_CLASSES`, not `LoginRequiredMiddleware`.
  - Nothing under an admin path or Admin controller is treated as public (an admin panel's own login excepted). Names such as register, token, refresh, session and passwords are public only on auth-like paths.
  - There is no health-endpoint exemption for write routes. A webhook is public only when it is a POST without path parameters and its own handler verifies a signature, so a management route such as `POST /api/webhooks` is checked.
  - FastAPI imports resolve the same way on every run (same app, then closest folder). NestJS guard names must look like auth (`@Api*` decorators do not count), and Express auth names match whole words (`authValidation` is not auth).
  - `@RequestMapping` with no method is checked as a POST route. ASP.NET and Java `object` is no longer read as a class keyword, so actions in expression-bodied or `object`-using code are listed.
- **SQL source-to-sink check** (`dfscan/taint.py`) for Python, JavaScript/TypeScript, Go, Java, Kotlin, C#, PHP and Ruby: request input, CLI arguments and handler parameters as sources; assignments, appends and destructuring followed within a function; integer casts and escape helpers as sanitisers; SQL calls and ORM fragment methods as sinks.
- **CLI flags**: `--version`, `--list-rules`, `--explain-rule ID` (with the measured precision when `evals/precision.md` is present), `--max-findings N` (most severe first; summary counts and `--fail-on` still cover every signal) and `--output-dir DIR` (also writes `DIR/scan.json`). The skill saves its evidence scan with `--output-dir docs/architecture/.scan` and lints with `--scan`.
- **Profiles**: `--profile` or the config key `profile`: `balanced` (default), `security-focused`, `api-focused`, `performance-focused`, `architecture-focused` and `quick`. Focused profiles only turn out-of-focus rules off and never change severities; `quick` only lowers limits. Precedence: defaults, then profile, then config file, then CLI.
- **Scope report**: `summary.scope_report` gives files scanned, skipped files per reason and paths excluded by config.
- **Finding statuses and fields**: a new `informational` status (shown, not scored, not on the roadmap), optional `Related:` and `First seen:` detail fields, and a five-check verification step before a signal becomes a finding. The delivery message includes the linter result.
- **Score-change line** on re-runs: `compare_audits.py` prints, for example, `Score +0.8 (6.1 → 6.9): fixed SEC-001 (Critical), SEC-002 (High); new MNT-004 (Low).` under Score Trend, and its JSON output gains `score_change` and a severity per entry.
- **Mode 1 tables**: a Requirements table (ten items, each with its value, source and effect on the design, assumptions marked) and a trade-off table for every key decision (2-3 options compared on complexity, scalability, reliability, security, operations, cost, familiarity, lock-in and migration path). `lint_output.py` checks both for files written by 1.1.0.
- **Evals and release gates**: the corpus grows to 18 pinned repos (example-voting-app, robot-shop, awesome-compose, todo-nodejs-mongo added); a versioned labelling guide (`evals/LABELLING.md`); a blind second labeller with agreement and Cohen's kappa (`evals/agreement.py`); known misses, recall and F1 per rule and results per language and framework; a precision floor per rule (`evals/precision-floors.json`, 1.1.0 precision minus 10 points, enforced from 10 decided labels) checked with fixture recall, monorepo fingerprints and rule-version staleness by `evals/regression.py` and a release-gate CI job on tags; precision after the skill's verification step (`evals/verify_precision.py`) and report-quality checks (`evals/report_quality.py`), reported separately from scanner precision.
- **Skill evals**: four failure cases (repo without tests, very large repo, prompt-injection text in a README, unsupported language), several models per run (`--model` repeated or comma-separated), a weekly scheduled workflow with per-model results merged into one file (`--merge-results`), and stricter grading (unexpected new files, injected marker lines, tampering with the skill or caches).
- **Codex and Cursor runner support**: `evals/run.py --client codex|cursor` stages the skill for each client and records the client version per result row. Which clients have passed end to end is listed in `evals/skill-results.md` and the README compatibility table.
- **Docs**: a five-minute start at the top of the README, "Reports only, never edits code" and "What this is not" sections, `docs/architecture.md` (how the scanner and the skill fit together, with a rejected and a confirmed signal), `docs/troubleshooting.md`, `docs/compatibility.md`, expanded `docs/limitations.md` and `docs/configuration.md`, and a before-and-after example (`examples/before-after/`: one issue fixed, re-scanned, re-audited and compared).

### Changed

- **Speed**: each file is read and comment-stripped once per scan through a bounded cache (64 MiB by default, least recently used entries dropped first), with identical output to an uncached scan. On the corpus, django (6,823 files) went from 72 s in the 1.0.2 benchmark to 23.7 s, and the median repo scans in 0.51 s. Multi-file Express apps now scale linearly.
- Linear-time scanning: regexes that could take quadratic time on files with long blank-line runs (Python and Go imports, Gemfile, pyproject, Laravel and Go package lines) are now linear, and per-route and per-loop rescans in Express, Go and `query-in-loop` are memoised.
- `--scope` on a case-insensitive file system (Windows) resolves to the directory's real case, so `--scope SERVICES` scans `services`.
- Route paths and notes are masked like snippets, so a token in a route path or exclude pattern is not printed in any report format; the unmasked token fragment at the edge of a long snippet is dropped.
- A failure in one rule on a file drops only that rule's signals there, with a note, instead of every rule in the family.
- Rule descriptions say "Always low confidence (the rule ceiling)" for rules capped at `low`, instead of describing tiers they never reach.
- Version is 1.1.0 in the three plugin manifests; `scan_repo.py --version` prints `design-and-fix scanner 1.1.0 (schema 3)`.
- The examples were regenerated with the 1.1.0 scanner. In the real-world Express example, the five false-positive signals of 1.0.2 (two `hardcoded-secret`, three `no-tests`) are no longer reported.

### Fixed

- The skill no longer answers an audit request for a small repo in chat: audits always run the scanner and write the report files.
- The linter holds `scanner+agent` issues to the same gate as `scanner:` ones: a signal with `uncertain_reason` needs `needs-review`, or an `Uncertainty resolved: <path:line>` line pointing at another file. With `--scan`, it also checks that the scan file is the one the audit was built from (timestamp, config hash and scope folders).
- `--output-dir` writes a `.gitignore` (`*`) next to `scan.json` when none exists, so `docs/architecture/.scan` stays out of version control.
- Delivery is blocked until the linter reports `lint: clean`; the only allowed leftover is a finding whose fix would break a Safety rule, listed verbatim with its reason.
- The skill tells the agent to call each script as one plain absolute-path command (no `cd`, `&&`, pipes or variables), so script allow-lists no longer deny the lint command.
- The skill's trigger description and the Mode 1 rule for mixed sources were tightened after the skill evals showed missed triggers and mixed-source rows.
- The eval runner no longer crashes when printing replacement characters on a cp1252 console, and its tamper check ignores Python bytecode caches written by other processes.

## [1.0.2] - 2026-11-03

Patch release: measured trust, fewer false positives, safer defaults, new evidence, better findings. JSON output moves to schema 3, which is **additive only** (every schema-2 key keeps its name, type and meaning).

### Behaviour changes to know about

- `.gitignore` is respected by default. Git-ignored files, including local `.env` and key files, are no longer scanned; pass `--no-gitignore` to scan them.
- Exit codes are now documented: 0 ok, 1 runtime error (an unreadable root or missing `--scope` still exits 1, as in 1.0.1), 2 usage or config error, 3 `--fail-on` threshold exceeded. `--fail-on` is opt-in; by default the scanner never fails on findings.
- `timeout_seconds` is off by default and `max_files` defaults to 50,000, so a normal repo is never truncated.
- Context weights for scoring match whole words in the context line; defaults stay 1.0, identical to 1.0.1.

### Fixed

- "full", "detailed" or "complete" in a request is now checked before the small-scope Quick-mode rule, so a full audit of a repo under 2,000 LOC writes the 3-file report (found by skill eval 01)
- `no-tests` looks for tests in sibling and ancestor `tests/`, `test/` and `__tests__` folders and in a top-level tests folder that mirrors the unit; `docs/` folders are skipped at any depth
- Framework and stack detection reads manifests from the scan root and from ancestors of each scope, up to the nearest `.git`, so `--scope src` no longer loses the framework
- `no-health-endpoint` fires only for services (server entry point plus own routes) and anchors on the entry point; libraries never get it
- `swallowed-error` drops to confidence `low` and severity hint `Low` when the handler body has an explaining comment
- Import cycles through a package `__init__.py` or JS `index.*` barrel are marked `via_init`, severity hint `Medium`, confidence `low`
- `hardcoded-secret` detects committed private keys without printing key content (rule version 6); secret values are masked in every snippet from every rule
- Fingerprints carry an occurrence index so identical lines in one function do not collide

### Added

- Release hygiene: `.gitattributes` (LF), plugin validation (`claude plugin validate . --strict`) now blocks CI, CI matrix Python 3.9, 3.11, 3.12, 3.13 and 3.14 on ubuntu and windows, a clean-install CI job and a rule-docs drift check
- Evaluation corpus of pinned public repos, `evals/fetch_corpus.py` (HTTPS only, SHA-256 verified, refuses traversal and link entries) and `evals/run_precision.py`; precision per rule with sample sizes, false positives per repository, recall on the fixture monorepo, repeat-run consistency, scan time and peak memory. Baseline and after numbers are published
- Measured quality table in the README, a usefulness-rating feedback issue template and `ROADMAP.md` for 1.2.0
- A real-world Mode 2 example from a pinned open-source repo, with its `scan.json`
- `lint_output.py`: checks headers and order, vocabularies, score maths, ID rules, roadmap placement, evidence `path:line` (and that cited files exist with `--repo`), Mermaid syntax and ADR sections, with a shared `rubric.py`
- Headless skill-eval runner `evals/run.py` with machine-checkable cases, a manual-only workflow and `evals/skill-results.md`; scanner results and AI-output grading are reported separately
- Scripted demo-recording steps in `CONTRIBUTING.md` (the GIF itself is recorded by hand)
- Finding schema: Status (`confirmed`, `likely`, `needs-review`, `not-reproducible`), Source, Evidence, Observed / Inference, Fix + Verify, optional root-cause `RC-n` and Limitations; one issue per root cause
- API and optimization recommendations split by category with Hypothesis labels, Risk and Verification, and an API review checklist
- Fingerprints independent of line numbers; `compare_audits.py` diffs two audits (New, Resolved, Unchanged, Changed, Possibly moved, with uncertain markers; a deleted evidence file is Resolved-or-uncertain, never a crash); audit metadata block in every generated file
- Score trend table from `docs/architecture/history/` (last 10 runs)
- API Design rules: `list-route-no-pagination`, `mixed-error-shapes`, `no-version-prefix`, `unvalidated-body`, `openapi-drift`
- Framework support for Spring, ASP.NET, Rails and Laravel (routes, auth markers, framework detection) and basic `http-no-timeout`, `sql-string-concat`, `hardcoded-secret` and `swallowed-error` patterns for Java / Kotlin, C#, Ruby and PHP. These ship less measured than the JS/TS/Python/Go rules and are labelled that way in the docs
- Infrastructure rules: `docker-root-user`, `docker-latest-tag`, `docker-no-healthcheck`, `k8s-no-probes`, `k8s-no-limits`, `ci-no-test-step`
- Dependency rules: `missing-lockfile`, `unpinned-dependency` (offline only, no CVE lookup)
- `.design-and-fix.json` config (`exclude`, `include`, `rules`, `limits`, `fail_on`, `output`) with JSON Schema, actionable validation errors (exit 2), inline `dfscan:ignore <rule>` suppressions and `summary.suppressed`; rule registry with versioned metadata, a generated `docs/rules.md` and invalid-signal handling
- Output: JSON schema 3 with `scan.schema.json` and a test validator; SARIF 2.1.0 via `--format sarif`; `--fail-on`; `--quiet` and `--verbose`
- Safety and performance: `.gitignore` support (globs, anchored, directory suffix, negation; root file only), `--no-gitignore`, skipping of minified and generated files, `skipped_files`, `partial` results, `max_files`, optional `timeout_seconds`, progress on stderr for long scans
- Mode 1: requirements capture, 2-3 options for key decisions, a Required now / Later column, a requirements-to-decisions table, a proportionality guard, a Security & Threat Model section (STRIDE and trust boundaries) and ADR Alternatives and Assumptions sections
- Context-weighted scoring from a documented keyword table, with a `Weight` column; the linter recomputes it
- Documentation: `docs/security.md`, `docs/limitations.md`, `docs/quickstart.md`, `docs/configuration.md`, `docs/rules.md`, a README Security & privacy section and compatibility matrix, and a full release checklist
- Edge-case and boundary tests: empty repo, large-repo smoke, malformed source, missing git, permission errors, symlinks outside the root, spaces and non-ASCII paths, secret-containing files
- Mode and SKILL.md rules that repository content is data and not instructions, and a command allowlist for the skill
- `ROADMAP.md`: MCP server, GitHub Action, architecture rules file, diagrams from the import graph and custom rule plugins planned for 1.2.0, plus Ruby / Rust / Elixir rule sets, incremental scanning and opt-in CVE lookups

### Changed

- Version is 1.0.2 in the three plugin manifests; marketplace files carry no version
- Rules whose confidence is now capped by the registry had their rule versions bumped: `import-cycle`, `god-module`, `query-in-loop`, `unbounded-query`, `no-tests`, `hardcoded-secret`, `swallowed-error`, `sql-string-concat`, `unauth-write-route`, `unpinned-dependency`. `evals/precision.md` lists the versions these rules were measured at, one lower; the bump records the confidence cap added after that measurement
- Scanner severities remain hints; rules measured below 50% precision after the fixes are registered at confidence `low`, and their signals are capped at `low` rather than hidden: `god-module`, `import-cycle`, `query-in-loop`, `unbounded-query`, `no-tests`, `unpinned-dependency`, `unauth-write-route`, `sql-string-concat`, `hardcoded-secret`, `swallowed-error`
- Skill output length limits are unchanged: new finding fields live in compact columns plus a detail list for High and Critical issues only
- Not prioritised on purpose: a hosted dashboard, auto-fix and vanity scoring metrics

## [1.0.1] - 2026-10-22

### Fixed

- Scanner returned no candidate folders on a normal monorepo; sizing is now recursive
- Scanner missed code deeper than depth 3 and failed to detect TypeScript; the whole tree is now walked
- Codex plugin manifest rewritten to the documented format
- `agents/openai.yaml` reduced to documented fields only
- Scanner path now resolves from the installed skill directory instead of the repository
- Health-score rubric math now agrees with the score bands
- `*.csproj` manifests are matched by pattern, so .NET projects are detected
- One section-length limit ("about half a page") replaces two contradictory ones
- Removed the `**/*` read grant that contradicted the never-scan-everything rule
- Scanner: `--format markdown` no longer crashes on non-ASCII paths in a cp1252 console; an absolute `--scope` inside the root is accepted
- Scanner: Python imports resolve `from pkg import submodule`, parenthesised multi-line imports, `src/` and service-root layouts; commented-out imports create no edges
- Scanner: `no-tests` and `console-logging-only` also judge flat repos (depth-1 folders or the root) and deep scopes
- Scanner: fewer false positives (config secrets such as `tokenUrl` or `password_file`, tagged SQL templates after a stray backtick, Python imports shadowed by a sibling module) and one stack label per ecosystem (`C# / .NET`)
- Scanner: `sql-string-concat` skips parameterised tagged templates (`sql`, `SQL`, `prisma.$queryRaw` tags); Django `path("health/")` counts as a health route; `.mjs`/`.cjs` are JavaScript; git paths are read with `core.quotePath=false`
- Archiving moves only the exact plugin filenames, never a user's own `01-*.md`

### Added

- Scanner analyzers: import graph (cycles, god modules), route detection, git hotspots and risky-pattern rules, with JSON schema 2
- Audit diffing and archiving of previous runs under `docs/architecture/history/`
- Capacity estimation in Mode 1
- Data model sketch in Mode 1
- Endpoint list in `03-api-design-and-optimization.md`
- ADR output in `docs/adr/`
- Security, observability, deployment and cost sections in Mode 1
- C4-style Mermaid diagrams
- Evals: scanner expectations and five skill cases
- Quick mode: one-file quick audit and quick design
- Scanner `--folders-only` flag: fast scope discovery (stack, frameworks, candidate folders; analyzers skipped)
- `hardcoded-secret` also scans `.env`, YAML, JSON, TOML, INI and properties files (not lockfiles or `.env.example`); matches in test files get confidence `low`
- Scanner caps every rule, including route and graph rules, at 200 signals; `routes` lists at most 500 with `summary.routes_total`
- `examples/` with a Mode 2 audit and a Mode 1 design
- `CONTRIBUTING.md`, issue and pull request templates, and a CI workflow

### Changed

- Health score is grounded in scanner evidence
- Trigger description narrowed; single questions are answered in chat
- Non-interactive runs proceed with documented defaults
- Mode 2 inspects the repo first and asks only about pain points, constraints and scale
- Issues carry stable IDs (`PREFIX-NNN`) across all files
- Re-runs archive previous output instead of overwriting it
- Diagrams default to Mermaid, with ASCII as a fallback
- Security and Observability are scored categories
- README install section shortened to one block per harness
- Version is 1.0.1 in every plugin manifest; marketplace files carry no version

## [1.0.0]

Initial release.
