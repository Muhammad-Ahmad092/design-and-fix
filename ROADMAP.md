# Roadmap

These are plans, not promises, and the order may change with feedback (use the [audit feedback template](.github/ISSUE_TEMPLATE/audit_feedback.md)). The direction for the next two releases: **1.1.0 makes what the plugin already detects reliable; 2.0.0 adds new capabilities on top of that.**

## 1.1.0: accuracy and trust (shipped 2026-11-05)

Release gate: no high-confidence rule regresses, every behaviour change has positive and negative fixtures, and precision is re-measured with sample sizes, with no rule below its recorded floor. Details and numbers: [CHANGELOG.md](CHANGELOG.md) and [evals/precision.md](evals/precision.md).

### Shipped in 1.1.0

- [x] **`unauth-write-route`**: framework auth adapters (Express, NestJS, Flask, FastAPI, Django/DRF, Gin/Echo/chi, Spring Security, ASP.NET Core, Rails, Laravel); unresolved inherited auth is reported as `needs-review` through `uncertain_reason`. 0% to 33% (n=19).
- [x] **`query-in-loop` and `unbounded-query`**: real database and ORM calls only, eager loading, prefetching and batching recognised; the snippet names the call and the loop.
- [x] **`sql-string-concat`**: source-to-sink evidence from request input to the query call; concatenation alone no longer flags.
- [x] **`hardcoded-secret`**: known credential formats first, entropy as supporting evidence, placeholders and documentation context recognised.
- [x] **`swallowed-error`, `god-module`, `import-cycle`, `no-tests`, `unpinned-dependency`** reworked.
- [x] Signal confidence derived from measured precision; a rule measured at 0% ships off by default (`openapi-drift`).
- [x] Corpus of 18 repos, known misses, recall and per-language and per-framework results.
- [x] Versioned labelling guide and a blind second labeller with agreement and kappa.
- [x] Precision floor per rule and a regression check in the release job.
- [x] Precision after the skill's verification step and report quality, measured separately from scanner precision.
- [x] Skill evals for failure cases (no tests, very large repo, prompt injection, unsupported language), on a schedule and with more than one model.
- [x] Finding fields for related findings and first detection, an `informational` status, the linter result in the delivery message and a score-change line on re-runs. Provenance is the existing Source column; "last detected" was dropped as redundant with the audit date.
- [x] Mode 1: Requirements table and a trade-off table for every key decision.
- [x] CLI: `--list-rules`, `--explain-rule`, `--version`, `--max-findings`, `--output-dir` and the six configuration profiles.
- [x] Faster scan: each file parsed once per scan; django 72 s to 23.7 s.
- [x] Eval runner support for Codex and Cursor with recorded client versions (end-to-end results in [evals/skill-results.md](evals/skill-results.md)).
- [x] Docs: five-minute start, before-and-after example, "What this is not" and an architecture overview. The demo recording is still to come.

### Follow-ups found during 1.1.0

- **`openapi-drift` mount resolution**: resolve Express `app.use('/prefix', router)` mounts (and the same pattern in other frameworks) so routes are compared with their full path; the rule stays off by default until it measures well.
- **Labelling guide: demo apps for `unauth-write-route`**: add an explicit rule for unprotected write routes in demo apps with no user model. The two labellers split on exactly these (kappa 0.27), so the rule's precision reads anywhere from 33% to about 83% until the guide decides.
- **Leetspeak placeholders in `hardcoded-secret`**: values such as `S0ME_SECRET` still pass the placeholder check and fire at Medium.
- **Known-format credentials as their own rule**: split known-format credential detection (provider tokens, PEM private keys, key files) out of `hardcoded-secret` into its own measured rule, so it can report `high` confidence. Today the shared `low` ceiling, set by the heuristic `key = value` matches, caps provider tokens at `low` too.
- **Denied script commands in skill evals**: in the 1.1.0 skill evals the script allow-list denied the agent's lint command (Opus, cases 04 and 08), so it delivered unlinted output; other runs report denied scan commands too. (a) Done in 1.1.0: `SKILL.md` tells the agent to call each script as one plain absolute-path command, with no `cd`, `&&` or shell variables. (b) Follow-up: run the Claude cases with a structured output format so the runner can read the denied commands and fail a case whose transcript shows a denied plugin-script command (the runner reads plain text today).
- **Mount and `APIRouter` prefixes in the public-path check**: the check for public auth names (register, token, health and similar) uses the route's local path only. A prefix applied in another file (an Express `app.use('/devices', router)` mount, `APIRouter(prefix=...)`) is not joined first, so `post('/register')` in a router mounted at `/devices` is still treated as public.
- **SARIF `enabled` field**: only default-off rules carry `defaultConfiguration.enabled: false`; add it to every rule's entry so consumers need not rely on the SARIF default.

## 2.0.0: new capabilities

Release gate: every new relationship and finding is traceable to evidence, nothing changes source code without explicit approval, and the default scanner stays offline and read-only.

### Wider regression gate

`evals/regression.py` checks report staleness, precision floors, fixture recall and fingerprints. Add schema and SARIF validity, redaction, and runtime and memory against the previous release as gated checks (unit tests cover schema, SARIF and redaction today; runtime and memory are not gated).

### MCP server

Tools `scan_folders`, `scan_scope` and `lint_output` exposed over MCP, so any MCP client can run the scanner and linter without shell access.

- Why: removes the "allowed to run Python" friction and works in clients with no skill support.
- Foundation: schema 3 JSON output, `--folders-only` discovery and stable exit codes.

### GitHub Action

Audit changed folders on pull requests, upload SARIF, compare against a baseline, fail only at configured thresholds and comment the score change and new issue IDs.

- Why: audits are most useful when they run on every change, not once a quarter.
- Foundation: `compare_audits.py` fingerprint diffs, SARIF output and `--fail-on`.

### Architecture rules file

Forbidden imports, layer rules and project policies (version prefix, health endpoint, non-root containers, service owners), enforced in CI. It extends `.design-and-fix.json` rather than adding a second file.

- Why: turns a one-off finding ("domain imports infrastructure") into a guard that stays fixed.
- Foundation: the import graph, the config loader and its schema validation.

### Diagrams from the import graph

Generate Mermaid C4, service and module dependency diagrams from the scanner's graph, with discovered facts and inferred relationships marked differently.

- Why: hand-drawn diagrams drift; the graph is the evidence.
- Foundation: `import_graph` in the scan output and the linter's Mermaid checks.

### Custom rule plugins

Load project-specific rules from `.design-and-fix/rules/`: declarative rules first, executable rules only behind a strict interface.

- Why: every team has its own anti-patterns; they should not need a fork.
- Foundation: the 1.0.2 rule registry (id, version, category, severity, confidence, limitations, remediation) and Signal validation.

### Deeper analysis engine

- An optional tree-sitter parsing backend, with the regex path kept as the dependency-free fallback.
- A cross-file data-flow engine for the security rules.
- A cross-file architecture graph (services, modules, routes, data access, jobs, calls, events, auth boundaries), a monorepo service map and a score per service.
- **Incremental scanning and caching** by content hash.
- **Ruby, Rust and Elixir rule sets.**

### New rules and evidence sources

- A focused security layer: ownership and IDOR, unsafe deserialization, path traversal, command injection, SSRF, file uploads, CORS and security headers, cookie and session flags, sensitive data in logs, permissive container and cloud permissions.
- Performance rules: blocking I/O in async handlers, unbounded concurrency, repeated external calls, over-fetching.
- API inventory per endpoint and OpenAPI or GraphQL breaking-change detection between two spec versions.
- Database schema and migrations, slow-query logs and APM exports, git co-change coupling, and Terraform, Helm and docker-compose files as evidence.
- **CVE and licence lookups** as an opt-in, separate tool, so the scanner itself keeps making no network calls.

### New modes

- **PR / diff audit**: "did this change make the architecture worse?"
- **Baselines and finding lifecycle**: accept today's findings, report only new, resolved and reopened ones.
- **Remediation proposals**: a proposed diff, test stubs and a verify step that re-runs the rules, applied only after the user approves. The default stays read-only.
- **ADR check**, **migration planner**, **threat-model mode**, **capacity and cost estimates** (labelled as estimates) and **load-test plans** for flagged endpoints.

### Integrations

Issue export to GitHub Issues or Jira (one per root cause, linked to the stable ID), an HTML report with trends, and confidence-weighted and target scores.

## Not planned

Dozens of shallow pattern rules, claims of full SAST coverage, automatic source rewriting by default, a hosted dashboard, network checks in the default scanner, an LLM as the only detector, and estimates presented as measurements. See [docs/limitations.md](docs/limitations.md) for what the tool deliberately does not claim.
