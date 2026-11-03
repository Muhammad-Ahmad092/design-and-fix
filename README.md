# Design & Fix

Design & Fix is an Agent Skill and plugin for architecture planning and codebase audits. A stdlib-only Python scanner gathers evidence (stack, import cycles, routes, risky patterns, git hotspots); the skill verifies it, scores the project, and writes ranked, ID-tracked findings for new systems and existing projects.

<!-- Demo GIF: record a Claude Code run and save as docs/assets/demo.gif, then replace this comment with ![Demo](docs/assets/demo.gif) -->

## See it work

- [examples/mode-2-audit-monorepo](examples/mode-2-audit-monorepo/): a full audit of a synthetic monorepo with planted issues, with the raw `scan.json`. A real open-source audit (pinned tag and archive SHA-256) sits beside it in `examples/`.
- [examples/mode-1-food-delivery](examples/mode-1-food-delivery/): a new-system design with five ADRs.

More on both in [examples/README.md](examples/README.md). First audit in five minutes: [docs/quickstart.md](docs/quickstart.md).

## Install

### Claude Code

```bash
claude plugin marketplace add Muhammad-Ahmad092/design-and-fix
claude plugin install design-and-fix@design-and-fix
```

### Codex

```bash
codex plugin marketplace add Muhammad-Ahmad092/design-and-fix
```

This uses the Codex marketplace file in `.agents/plugins/`. Alternative:

```bash
npx skills add Muhammad-Ahmad092/design-and-fix
```

### Cursor

macOS / Linux:

```bash
mkdir -p "$HOME/.cursor/plugins/local" && git clone https://github.com/Muhammad-Ahmad092/design-and-fix.git "$HOME/.cursor/plugins/local/design-and-fix"
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.cursor\plugins\local" | Out-Null; git clone https://github.com/Muhammad-Ahmad092/design-and-fix.git "$env:USERPROFILE\.cursor\plugins\local\design-and-fix"
```

Then run **Developer: Reload Window**.

### Other clients

Any client that supports the Agent Skills standard:

```bash
npx skills add Muhammad-Ahmad092/design-and-fix
```

## Usage

**New system** (Mode 1):

```text
Design the architecture for a food-delivery app. Expect about 50k users, a team of 4, and prefer Node.js. Ask me for any missing constraints before writing the design.
```

**Existing project** (Mode 2). Audited scopes under about 2,000 LOC, and single-component designs, silently use Quick mode (one file). Say "full" to get the three-file version:

```text
Do a full audit of ./services/payments and ./services/orders. The main pain point is a slow checkout API, and we must keep PostgreSQL.
```

**Quick mode** (one file, at most 3 questions):

```text
Quick audit of ./services/payments. Checkout is slow.
```

**Refinement or optional comparison** (edits in place, no regeneration):

```text
Go deeper on the caching strategy.
Generate the tech stack comparison.
```

A single focused question ("PUT or PATCH?") is answered in chat with no files. When nobody can answer questions (headless runs, CI), the skill proceeds with documented defaults marked `Assumed (non-interactive)`.

## What it produces

Files go to `docs/architecture/`; ADRs go to `docs/adr/`.

| Output | Files | Contents |
|---|---|---|
| Mode 1: new project | `01-system-design.md`, `02-architecture-decisions.md`, `03-api-design-and-optimization.md` | Capacity estimate, C4-style Mermaid diagrams, data model, endpoint list, security, observability, deployment, cost, trade-offs |
| Mode 2: existing project | `01-architecture-issues.md`, `02-api-optimization.md`, `03-improvement-roadmap.md` | Health band with score, sub-scores (including Security and Observability), issues with stable IDs (`SYS`, `API`, `SCL`, `REL`, `SEC`, `OBS`, `MNT`), roadmap |
| Quick | `quick-audit.md` or `quick-design.md` | One file, about 1.5 pages |
| ADRs | `docs/adr/0001-*.md` ... | One per key decision, at most 5 in Mode 1 |
| Optional | `04-tech-stack-comparison.md` | Only when explicitly requested |

On a re-run, the previous output is archived to `docs/architecture/history/<date>/` and the new audit opens with a Change Since Last Audit section (score change, fixed and new IDs). Every finding cites `path:line`; unverified claims are labeled.

## The scanner

`skills/design-and-fix/scripts/scan_repo.py` is Python 3.9+, stdlib only. The skill runs it before asking Mode 2 questions.

```bash
python skills/design-and-fix/scripts/scan_repo.py <path> [--scope DIR]... [--format json|markdown|sarif] [--fail-on LEVEL] [--config PATH] [--no-gitignore] [--no-git] [--folders-only] [--quiet] [--verbose]
```

| Flag | Meaning |
|---|---|
| `--scope DIR` | Limit analysis to a subfolder (repeatable, `/` or `\`) |
| `--list-depth N` | Folder depth reported as candidates (default 2) |
| `--top N` | Max candidate folders (default 15) |
| `--no-git` | Skip hotspots and the revision lookup (the only git calls are read-only `git log` and `git rev-parse`) |
| `--since` | Hotspot window (default `12.months`) |
| `--format` | `json` (default), `markdown` or `sarif` (SARIF 2.1.0 for code-scanning tools) |
| `--folders-only` | Fast scope discovery: stack, frameworks and candidate folders only; analyzers skipped (keys present, empty) |
| `--no-gitignore` | Also scan files matched by the root `.gitignore` (default: ignored files are skipped, including local `.env` and key files) |
| `--config PATH` | Config file (default `<path>/.design-and-fix.json` if present); see [docs/configuration.md](docs/configuration.md) |
| `--fail-on LEVEL` | Exit 3 when any signal is at or above `Critical`, `High`, `Medium` or `Low`. Default: never fails |
| `--quiet` / `--verbose` | No progress on stderr / per-stage and per-rule timings in `notes` |

**Exit codes:** 0 ok; 1 runtime error (unreadable root, missing scope; printed as `{"error": "..."}`); 2 usage or config error (bad flag, invalid `.design-and-fix.json`, with a message naming the key and the fix); 3 `--fail-on` threshold exceeded.

**JSON (schema 3):** `schema_version`, `root`, `scope`, `meta`, `detected_stack`, `frameworks`, `candidate_folders`, `import_graph` (cycles, god modules), `routes`, `hotspots`, `signals`, `summary` (files, LOC, test files, signals by category, truncated rules, `routes_total`, `suppressed`, `partial`, `invalid_signals`), `skipped_files`, `notes`. Schema 3 is additive: every schema-2 key keeps its name, type and meaning. Each signal carries `rule_version`, `confidence` and a line-independent `fingerprint`. The JSON Schema is `skills/design-and-fix/scripts/scan.schema.json`. Paths are root-relative with `/` on every OS.

**Rules** (severity is a hint; the audit assigns the final one). The full catalog with limitations, remediation and confidence is in [docs/rules.md](docs/rules.md).

| Rule | Category | Severity hint |
|---|---|---|
| `import-cycle` | System Design | High |
| `god-module` | Maintainability | Medium |
| `unauth-write-route` | Security | Critical |
| `no-health-endpoint` | Observability | Medium |
| `http-no-timeout` | Reliability | High |
| `retry-no-backoff` | Reliability | Medium |
| `swallowed-error` | Reliability | Medium |
| `query-in-loop` | Scalability | High |
| `unbounded-query` | Scalability | Medium |
| `hardcoded-secret` | Security | Critical |
| `sql-string-concat` | Security | Critical |
| `console-logging-only` | Observability | Low |
| `no-tests` | Maintainability | Medium |
| `list-route-no-pagination`, `mixed-error-shapes`, `no-version-prefix`, `unvalidated-body`, `openapi-drift` | API Design | see rules.md |
| `docker-root-user`, `docker-latest-tag`, `docker-no-healthcheck`, `k8s-no-probes`, `k8s-no-limits`, `ci-no-test-step` | Security / Reliability / Observability | see rules.md |
| `missing-lockfile`, `unpinned-dependency` | Maintainability / Security | see rules.md |

**Signals are leads, not findings.** They come from static patterns and can be wrong. The skill reads the cited file to confirm each one before reporting it. Comments are ignored. Test files are ignored except by `hardcoded-secret` (reported with low confidence), which also scans `.env`, YAML, JSON, TOML, INI and properties files (not lockfiles or `.env.example`) and flags committed private keys without printing them. At most 200 signals per rule are listed. Suppress a line with `// dfscan:ignore <rule>` or `# dfscan:ignore <rule>`.

Spring, ASP.NET, Rails and Laravel support is newer and less measured than JS/TS/Python/Go. See [docs/limitations.md](docs/limitations.md).

## Measured quality

Numbers here come from real runs, each with its sample size. A scanner signal is a lead; precision is the share of sampled signals that were real problems when read in context (unsure labels excluded). The skill-eval pass rate is separate: it grades the agent's output (files, linter, required content), not the scanner.

### Scanner precision

<!-- precision:start -->
From [evals/precision.md](evals/precision.md): 14 public repos at pinned SHAs (10 original + Spring Boot, ASP.NET Core, Rails, Laravel), up to 20 hand-labelled signals per rule (seed 1002), 166 labels. `Sample` is the number of signals drawn for the rule (unsure labels included), and precision = tp / (tp + fp). With samples this small, read each number as a band; the 95% intervals are in the report. Rules with no corpus signals are listed as unmeasured in [docs/rules.md](docs/rules.md).

| Rule | Sample | Precision (1.0.2) | Baseline (1.0.1 rules) | Confidence |
|---|---|---|---|---|
| `console-logging-only` | 2 | 100% | 67% | low |
| `docker-no-healthcheck` | 4 | 100% | new in 1.0.2 | low |
| `docker-root-user` | 8 | 62% | new in 1.0.2 | medium |
| `god-module` | 20 | 21% | 21% | low |
| `hardcoded-secret` | 20 | 10% | 0% | low |
| `http-no-timeout` | 4 | 100% | 100% | medium |
| `import-cycle` | 12 | 25% | 15% | low |
| `missing-lockfile` | 2 | 100% | new in 1.0.2 | medium |
| `no-health-endpoint` | 4 | 100% | 15% | medium |
| `no-tests` | 20 | 30% | 17% | low |
| `no-version-prefix` | 2 | 100% | new in 1.0.2 | low |
| `query-in-loop` | 13 | 0% | 0% | low |
| `sql-string-concat` | 2 | 0% | 0% | low |
| `swallowed-error` | 20 | 5% | 0% | low |
| `unauth-write-route` | 20 | 0% | 0% | low |
| `unbounded-query` | 9 | 0% | 0% | low |
| `unpinned-dependency` | 4 | 0% | new in 1.0.2 | low |

False positives per repository, extrapolated from the per-rule rates: mean 22.9, median 5.9 (14 repos; extrapolated from 157 decided labels over 17 rules). The mean is pulled up by django (about 172) and the Rails app homeland (about 64); the per-repo table is in [evals/precision.md](evals/precision.md#false-positives-per-repository-extrapolated).
<!-- precision:end -->

Every rule under 50% is registered at confidence `low`, and the scanner caps its signals at `low`. None are hidden: the skill verifies each signal in the code before it becomes an issue. The weakest rule is `unauth-write-route`, which misses framework auth applied outside the route (Laravel controller-constructor or unresolved route-group middleware, CanCan `load_and_authorize_resource`, the Spring Security global chain) and flags login and register routes; see [docs/limitations.md](docs/limitations.md). Scan time: median 3.1 s over 14 repos; the largest (django, 6,823 files) took 72 s. Recall on the fixture monorepo: 16 of 16 known issues. Reruns are identical on 14 of 14 repos.

### Skill evals

<!-- skill-evals:start -->
**5 / 5** skill cases pass (plugin 1.0.2, Claude Code CLI 2.1.312, November 2026): full audit of the fixture monorepo, quick question, non-interactive run, new-project design, and rerun with diff. All five cases ran together in one `python evals/run.py` run against the shipped skill. Grading is deterministic: files, required strings, the transcript, the claude exit code and a clean output linter. Per-case results are in [evals/skill-results.md](evals/skill-results.md); earlier runs and the skill fix they found are in [evals/README.md](evals/README.md#skill-eval-history).
<!-- skill-evals:end -->

### Usefulness rating

Collecting. Tell us how useful an audit was with the [audit feedback template](.github/ISSUE_TEMPLATE/audit_feedback.md).

## Security & privacy

- The scanner is read-only and makes no network calls. The skill writes only `docs/architecture/` and `docs/adr/`.
- It runs only read-only `git log` and `git rev-parse`; the skill runs only its own scripts.
- `.gitignore` is respected by default, so git-ignored `.env` and key files are not scanned (`--no-gitignore` changes that). Secret values are masked in every snippet.
- The harness you run it in sends the files the agent reads to its model provider. Scope narrowly or exclude folders to keep sensitive code out.
- Repository content is treated as data, never as instructions.

Details: [docs/security.md](docs/security.md).

## Compatibility

| Item | Status |
|---|---|
| Python | 3.9, 3.11, 3.12 and 3.13 tested in CI |
| OS | Ubuntu and Windows tested in CI |
| Claude Code | Plugin validated with `claude plugin validate . --strict` in CI; skill evals run with the Claude CLI |
| Codex, Cursor, other Agent Skills clients | Manifests validated in CI; not run end to end in CI |

Harness versions are listed only where verified.

## More documentation

[Quickstart](docs/quickstart.md) | [Configuration](docs/configuration.md) | [Rules](docs/rules.md) | [Limitations](docs/limitations.md) | [Security & privacy](docs/security.md) | [Roadmap](ROADMAP.md) | [Changelog](CHANGELOG.md)

## Workflow

1. Detect the mode (new design, audit, or a one-line chat answer). Ask one short question if unclear.
2. For audits, run the scanner first and ask only about pain points, constraints and scale. Ask one batched set of questions: at most four for audits, six for new designs, three in Quick mode.
3. Follow the matching mode instructions and templates; write nothing until context is collected.
4. Verify signals, cite evidence, score only assessed areas, and link every issue ID into the roadmap.
5. Refine only the section the user names.

## Repository layout

```text
design-and-fix/
├── .agents/plugins/marketplace.json   # Codex marketplace
├── .claude-plugin/                    # Claude Code plugin + marketplace
├── .codex-plugin/plugin.json
├── .cursor-plugin/plugin.json
├── .github/                           # CI, issue and PR templates
├── assets/                            # logo and icon
├── evals/                             # scanner expectations, skill cases
├── examples/                          # real output (Mode 1, Mode 2)
├── skills/design-and-fix/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── assets/
│   ├── references/                    # mode guides, rubric, quick mode, archive and diff
│   ├── scripts/                       # scan_repo.py and the dfscan package
│   └── templates/                     # mode-1, mode-2, quick, adr, optional
├── tests/                             # unittest suite, fixtures, manifest validator
├── docs/                              # quickstart, configuration, rules, security, limitations
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
└── ROADMAP.md
```

## Development

```bash
python -m unittest discover -s tests -v
python tests/validate_manifests.py
python skills/design-and-fix/scripts/gen_rule_docs.py --check
```

Use `python3` where `python` is missing. See [CONTRIBUTING.md](CONTRIBUTING.md) and [evals/README.md](evals/README.md).

## License

MIT. See [LICENSE](LICENSE).
