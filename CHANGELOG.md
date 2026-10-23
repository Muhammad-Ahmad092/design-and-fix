# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
