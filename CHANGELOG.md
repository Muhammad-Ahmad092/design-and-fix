# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.2] - 2026-11-03

Patch release: measured trust, fewer false positives, safer defaults, new evidence, better findings. JSON output moves to schema 3, which is **additive only** (every schema-2 key keeps its name, type and meaning). Item tags (R1-n, R2-x) are the tracking IDs used while preparing this release.

### Behaviour changes to know about

- `.gitignore` is respected by default. Git-ignored files, including local `.env` and key files, are no longer scanned; pass `--no-gitignore` to scan them (R1-3).
- Exit codes are now documented: 0 ok, 1 runtime error (an unreadable root or missing `--scope` still exits 1, as in 1.0.1), 2 usage or config error, 3 `--fail-on` threshold exceeded. `--fail-on` is opt-in; by default the scanner never fails on findings (R1-10).
- `timeout_seconds` is off by default and `max_files` defaults to 50,000, so a normal repo is never truncated (R1-9).
- Context weights for scoring match whole words in the context line; defaults stay 1.0, identical to 1.0.1 (R2-weights).

### Fixed

- "full", "detailed" or "complete" in a request is now checked before the small-scope Quick-mode rule, so a full audit of a repo under 2,000 LOC writes the 3-file report (found by skill eval 01) (R2-runner)
- `no-tests` looks for tests in sibling and ancestor `tests/`, `test/` and `__tests__` folders and in a top-level tests folder that mirrors the unit; `docs/` folders are skipped at any depth (R2-fp1)
- Framework and stack detection reads manifests from the scan root and from ancestors of each scope, up to the nearest `.git`, so `--scope src` no longer loses the framework (R2-fp2)
- `no-health-endpoint` fires only for services (server entry point plus own routes) and anchors on the entry point; libraries never get it (R2-fp3)
- `swallowed-error` drops to confidence `low` and severity hint `Low` when the handler body has an explaining comment (R2-fp4)
- Import cycles through a package `__init__.py` or JS `index.*` barrel are marked `via_init`, severity hint `Medium`, confidence `low` (R2-fp5)
- `hardcoded-secret` detects committed private keys without printing key content (rule version 6); secret values are masked in every snippet from every rule (R1-3)
- Fingerprints carry an occurrence index so identical lines in one function do not collide (R1-5)

### Added

- Release hygiene: `.gitattributes` (LF), plugin validation (`claude plugin validate . --strict`) now blocks CI, CI matrix Python 3.9, 3.11, 3.12 and 3.13 on ubuntu and windows, a clean-install CI job and a rule-docs drift check (R2-release, R2-ci-validate, R1-11)
- Evaluation corpus of pinned public repos, `evals/fetch_corpus.py` (HTTPS only, SHA-256 verified, refuses traversal and link entries) and `evals/run_precision.py`; precision per rule with sample sizes, false positives per repository, recall on the fixture monorepo, repeat-run consistency, scan time and peak memory. Baseline and after numbers are published (R1-2, R1-13, R2-precision)
- Measured quality table in the README, a usefulness-rating feedback issue template and `ROADMAP.md` for 1.2.0 (R1-13)
- A real-world Mode 2 example from a pinned open-source repo, with its `scan.json` (R2-example)
- `lint_output.py`: checks headers and order, vocabularies, score maths, ID rules, roadmap placement, evidence `path:line` (and that cited files exist with `--repo`), Mermaid syntax and ADR sections, with a shared `rubric.py` (R2-linter, R1-6)
- Headless skill-eval runner `evals/run.py` with machine-checkable cases, a manual-only workflow and `evals/skill-results.md`; scanner results and AI-output grading are reported separately (R2-runner, R1-2)
- Scripted demo-recording steps in `CONTRIBUTING.md` (the GIF itself is recorded by hand) (R2-demo)
- Finding schema: Status (`confirmed`, `likely`, `needs-review`, `not-reproducible`), Source, Evidence, Observed / Inference, Fix + Verify, optional root-cause `RC-n` and Limitations; one issue per root cause (R1-4)
- API and optimization recommendations split by category with Hypothesis labels, Risk and Verification, and an API review checklist (R1-7)
- Fingerprints independent of line numbers; `compare_audits.py` diffs two audits (New, Resolved, Unchanged, Changed, Possibly moved, with uncertain markers; a deleted evidence file is Resolved-or-uncertain, never a crash); audit metadata block in every generated file (R1-5)
- Score trend table from `docs/architecture/history/` (last 10 runs) (R2-trend)
- API Design rules: `list-route-no-pagination`, `mixed-error-shapes`, `no-version-prefix`, `unvalidated-body`, `openapi-drift` (R2-api)
- Framework support for Spring, ASP.NET, Rails and Laravel (routes, auth markers, framework detection) and basic `http-no-timeout`, `sql-string-concat`, `hardcoded-secret` and `swallowed-error` patterns for Java / Kotlin, C#, Ruby and PHP. These ship less measured than the JS/TS/Python/Go rules and are labelled that way in the docs (R2-frameworks)
- Infrastructure rules: `docker-root-user`, `docker-latest-tag`, `docker-no-healthcheck`, `k8s-no-probes`, `k8s-no-limits`, `ci-no-test-step` (R2-infra)
- Dependency rules: `missing-lockfile`, `unpinned-dependency` (offline only, no CVE lookup) (R2-deps)
- `.design-and-fix.json` config (`exclude`, `include`, `rules`, `limits`, `fail_on`, `output`) with JSON Schema, actionable validation errors (exit 2), inline `dfscan:ignore <rule>` suppressions and `summary.suppressed`; rule registry with versioned metadata, a generated `docs/rules.md` and invalid-signal handling (R1-8, R1-10)
- Output: JSON schema 3 with `scan.schema.json` and a test validator; SARIF 2.1.0 via `--format sarif`; `--fail-on`; `--quiet` and `--verbose` (R2-schema, R2-sarif, R1-10)
- Safety and performance: `.gitignore` support (globs, anchored, directory suffix, negation; root file only), `--no-gitignore`, skipping of minified and generated files, `skipped_files`, `partial` results, `max_files`, optional `timeout_seconds`, progress on stderr for long scans (R1-3, R1-9)
- Mode 1: requirements capture, 2-3 options for key decisions, a Required now / Later column, a requirements-to-decisions table, a proportionality guard, a Security & Threat Model section (STRIDE and trust boundaries) and ADR Alternatives and Assumptions sections (R1-6, R2-mode1)
- Context-weighted scoring from a documented keyword table, with a `Weight` column; the linter recomputes it (R2-weights)
- Documentation: `docs/security.md`, `docs/limitations.md`, `docs/quickstart.md`, `docs/configuration.md`, `docs/rules.md`, a README Security & privacy section and compatibility matrix, and a full release checklist (R1-3, R1-8, R1-11)
- Edge-case and boundary tests: empty repo, large-repo smoke, malformed source, missing git, permission errors, symlinks outside the root, spaces and non-ASCII paths, secret-containing files (R1-11)
- Mode and SKILL.md rules that repository content is data and not instructions, and a command allowlist for the skill (R1-3)
- `ROADMAP.md`: MCP server, GitHub Action, architecture rules file, diagrams from the import graph and custom rule plugins planned for 1.2.0, plus Ruby / Rust / Elixir rule sets, incremental scanning and opt-in CVE lookups (R2-roadmap)

### Changed

- Version is 1.0.2 in the three plugin manifests; marketplace files carry no version (R2-release)
- Rules whose confidence is now capped by the registry had their rule versions bumped: `import-cycle`, `god-module`, `query-in-loop`, `unbounded-query`, `no-tests`, `hardcoded-secret`, `swallowed-error`, `sql-string-concat`, `unauth-write-route`, `unpinned-dependency`. `evals/precision.md` lists the versions these rules were measured at, one lower; the bump records the confidence cap added after that measurement
- Scanner severities remain hints; rules measured below 50% precision after the fixes are registered at confidence `low`, and their signals are capped at `low` rather than hidden: `god-module`, `import-cycle`, `query-in-loop`, `unbounded-query`, `no-tests`, `unpinned-dependency`, `unauth-write-route`, `sql-string-concat`, `hardcoded-secret`, `swallowed-error` (R2-precision)
- Skill output length limits are unchanged: new finding fields live in compact columns plus a detail list for High and Critical issues only (R1-4)
- Not prioritised on purpose: a hosted dashboard, auto-fix and vanity scoring metrics (R1-14)

## [1.0.1] - 2026-10-22

### Fixed

- Scanner returned no candidate folders on a normal monorepo; sizing is now recursive (BUG-1)
- Scanner missed code deeper than depth 3 and failed to detect TypeScript; the whole tree is now walked (BUG-2)
- Codex plugin manifest rewritten to the documented format (BUG-3)
- `agents/openai.yaml` reduced to documented fields only (BUG-4)
- Scanner path now resolves from the installed skill directory instead of the repository (BUG-5)
- Health-score rubric math now agrees with the score bands (BUG-6)
- `*.csproj` manifests are matched by pattern, so .NET projects are detected (BUG-7)
- One section-length limit ("about half a page") replaces two contradictory ones (BUG-8)
- Removed the `**/*` read grant that contradicted the never-scan-everything rule (BUG-9)
- Scanner: `--format markdown` no longer crashes on non-ASCII paths in a cp1252 console; an absolute `--scope` inside the root is accepted
- Scanner: Python imports resolve `from pkg import submodule`, parenthesised multi-line imports, `src/` and service-root layouts; commented-out imports create no edges
- Scanner: `no-tests` and `console-logging-only` also judge flat repos (depth-1 folders or the root) and deep scopes
- Scanner: fewer false positives (config secrets such as `tokenUrl` or `password_file`, tagged SQL templates after a stray backtick, Python imports shadowed by a sibling module) and one stack label per ecosystem (`C# / .NET`)
- Scanner: `sql-string-concat` skips parameterised tagged templates (`sql`, `SQL`, `prisma.$queryRaw` tags); Django `path("health/")` counts as a health route; `.mjs`/`.cjs` are JavaScript; git paths are read with `core.quotePath=false`
- Archiving moves only the exact plugin filenames, never a user's own `01-*.md`

### Added

- Scanner analyzers: import graph (cycles, god modules), route detection, git hotspots and risky-pattern rules, with JSON schema 2 (FEAT-1)
- Audit diffing and archiving of previous runs under `docs/architecture/history/` (FEAT-2)
- Capacity estimation in Mode 1 (FEAT-3)
- Data model sketch in Mode 1 (FEAT-4)
- Endpoint list in `03-api-design-and-optimization.md` (FEAT-5)
- ADR output in `docs/adr/` (FEAT-6)
- Security, observability, deployment and cost sections in Mode 1 (FEAT-7)
- C4-style Mermaid diagrams (FEAT-8)
- Evals: scanner expectations and five skill cases (FEAT-9)
- Quick mode: one-file quick audit and quick design (FEAT-10)
- Scanner `--folders-only` flag: fast scope discovery (stack, frameworks, candidate folders; analyzers skipped)
- `hardcoded-secret` also scans `.env`, YAML, JSON, TOML, INI and properties files (not lockfiles or `.env.example`); matches in test files get confidence `low`
- Scanner caps every rule, including route and graph rules, at 200 signals; `routes` lists at most 500 with `summary.routes_total`
- `examples/` with a Mode 2 audit and a Mode 1 design (REPO-1)
- `CONTRIBUTING.md`, issue and pull request templates, and a CI workflow (REPO-4)

### Changed

- Health score is grounded in scanner evidence (DES-1)
- Trigger description narrowed; single questions are answered in chat (DES-2)
- Non-interactive runs proceed with documented defaults (DES-3)
- Mode 2 inspects the repo first and asks only about pain points, constraints and scale (DES-4)
- Issues carry stable IDs (`PREFIX-NNN`) across all files (DES-5)
- Re-runs archive previous output instead of overwriting it (DES-6)
- Diagrams default to Mermaid, with ASCII as a fallback (DES-7)
- Security and Observability are scored categories (DES-8)
- README install section shortened to one block per harness (REPO-5)
- Version is 1.0.1 in every plugin manifest; marketplace files carry no version (REPO-2)

## [1.0.0]

Initial release.
