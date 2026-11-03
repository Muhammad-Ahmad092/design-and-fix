# Roadmap

Planned for 1.2.0. These are plans, not promises, and the order may change with feedback (use the [audit feedback template](.github/ISSUE_TEMPLATE/audit_feedback.md)). Each item lists why it matters and the 1.0.2 foundation it builds on.

## Planned for 1.2.0

### MCP server

Tools `scan_folders`, `scan_scope` and `lint_output` exposed over MCP, so any MCP client can run the scanner and linter without shell access.

- Why: removes the "allowed to run Python" friction and works in clients with no skill support.
- Foundation: schema 3 JSON output, `--folders-only` discovery and stable exit codes.

### GitHub Action

Audit changed folders on pull requests and comment the score change and new issue IDs.

- Why: audits are most useful when they run on every change, not once a quarter.
- Foundation: `compare_audits.py` fingerprint diffs, SARIF output and `--fail-on`.

### Architecture rules file

Forbidden imports and layer rules, enforced in CI. It will extend the 1.0.2 `.design-and-fix.json` config rather than add a second file.

- Why: turns a one-off finding ("domain imports infrastructure") into a guard that stays fixed.
- Foundation: the import graph, the config loader and its schema validation.

### Diagrams from the import graph

Generate Mermaid component and dependency diagrams from the scanner's import graph, checked against the written components.

- Why: hand-drawn diagrams drift; the graph is the evidence.
- Foundation: `import_graph` in the scan output and the linter's Mermaid checks.

### Custom rule plugins

Load project-specific rules from `.design-and-fix/rules/` using the same interface as built-in rules.

- Why: every team has its own anti-patterns; they should not need a fork.
- Foundation: the 1.0.2 rule registry (id, version, category, severity, confidence, limitations, remediation) and Signal validation, which already document the interface. Third-party loading is not built in 1.0.2.

## Also planned

- **Ruby, Rust and Elixir rule sets.** Why: language coverage gaps. Foundation: per-language registry metadata and the framework detection added in 1.0.2.
- **Incremental scanning and caching**. Why: very large monorepos; django took 72 s in the 1.0.2 benchmark, over the 30 s target.
- **Framework-aware auth for `unauth-write-route`** (Laravel controller-constructor and route-group middleware, Rails CanCan `load_and_authorize_resource`, Spring Security chains, login/register routes) and the other false-positive patterns listed in `docs/limitations.md`. Why: those rules measured under 50% precision in 1.0.2. Foundation: the 1.0.2 benchmark in `evals/` and per-file fingerprints.
- **CVE lookups** as an opt-in, separate tool. Why: dependency hygiene is offline only today. Foundation: the manifest parsing behind `missing-lockfile` and `unpinned-dependency`. It stays separate so the scanner itself keeps making no network calls.

## Not planned

A hosted dashboard, auto-fix pull requests, and vanity scoring metrics. See [docs/limitations.md](docs/limitations.md) for what the tool deliberately does not claim.
