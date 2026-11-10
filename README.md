# Design & Fix

[![CI](https://github.com/Muhammad-Ahmad092/design-and-fix/actions/workflows/ci.yml/badge.svg)](https://github.com/Muhammad-Ahmad092/design-and-fix/actions/workflows/ci.yml)

Design & Fix is an Agent Skill and plugin for architecture planning and codebase audits. A stdlib-only Python scanner gathers evidence (stack, import cycles, routes, risky patterns, git hotspots); the skill verifies it, scores the project, and writes ranked, ID-tracked findings for new systems and existing projects.

## Five-minute start

1. Install for Claude Code (other harnesses are under [Install](#install)):

   ```bash
   claude plugin marketplace add Muhammad-Ahmad092/design-and-fix
   claude plugin install design-and-fix@design-and-fix
   ```

2. In your project, send one prompt:

   ```text
   Quick audit of ./services/payments. Checkout is slow.
   ```

3. Read `docs/architecture/quick-audit.md` (a full audit writes `01-architecture-issues.md`, `02-api-optimization.md` and `03-improvement-roadmap.md`). The top of a real full audit, from [examples/mode-2-audit-monorepo](examples/mode-2-audit-monorepo/01-architecture-issues.md):

   ```text
   ## Architecture Health Score: Fragile (4.9 / 10)

   Fragile: structural fixes needed before growth.
   Evidence: 11 files read, 23 scanner signals reviewed, 22 confirmed
   ```

A demo recording will be added to `docs/assets/`. Until then, see [examples/mode-2-audit-monorepo](examples/mode-2-audit-monorepo/) (full audit with the raw `scan.json`), [examples/before-after](examples/before-after/) (one issue fixed, re-audited, compared), and [examples/mode-1-food-delivery](examples/mode-1-food-delivery/) (new-system design with five ADRs). More in [examples/README.md](examples/README.md) and [docs/quickstart.md](docs/quickstart.md).

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

## Reports only, never edits code

The scanner never modifies your code: it prints to stdout and, only with `--output-dir`, writes `scan.json` there. The skill writes only `docs/architecture/` and `docs/adr/` (including `docs/architecture/.scan/scan.json`, which you should git-ignore). Fixes appear as recommendations in the report; applying them is your decision. Nothing in 1.1.0 edits your source code.

## Terms used in this project

- **Scanner signal**: a lead from a static pattern, with a rule, a file and a line. It can be wrong. A signal can carry `uncertain_reason` when the scanner could not see something that decides the answer (for example auth configured outside `--scope`); the skill then uses Status `needs-review`.
- **Verified finding**: a signal, or something the agent found itself, that the skill confirmed by reading the cited code. Each gets a stable ID, a severity and a Status: `confirmed`, `likely`, `needs-review`, `informational` or `not-reproducible`. Rejected signals are counted, not listed as issues.
- **Recommendation**: the Fix and Verify cells of a finding, and the roadmap built from them. A recommendation is advice, not an applied change.

More: [docs/architecture.md](docs/architecture.md).

## What this is not

- Not a vulnerability scanner: no CVE lookup, no runtime testing.
- Not a linter or a CI gate by default (`--fail-on` is opt-in).
- Not a code fixer: it never edits your code.
- Not a replacement for review: the agent can misread code, and scores compare runs of the same scope, not different projects. See [docs/limitations.md](docs/limitations.md).

## The scanner

`skills/design-and-fix/scripts/scan_repo.py` is Python 3.9+, stdlib only. The skill runs it before asking Mode 2 questions.

```bash
python skills/design-and-fix/scripts/scan_repo.py <path> [--scope DIR]... [--format json|markdown|sarif] [--fail-on LEVEL] [--config PATH] [--no-gitignore] [--no-git] [--folders-only] [--quiet] [--verbose] [--profile NAME] [--max-findings N] [--output-dir DIR]
python skills/design-and-fix/scripts/scan_repo.py --list-rules | --explain-rule ID | --version
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
| `--profile NAME` | Apply a built-in profile under the config file |
| `--max-findings N` | Keep the N most severe signals (summary counts and `--fail-on` still cover all) |
| `--output-dir DIR` | Also write the JSON result to `DIR/scan.json` |
| `--list-rules` / `--explain-rule ID` / `--version` | List rules with default confidence, explain one rule, print the version |

All flags: [docs/configuration.md](docs/configuration.md).

**Exit codes:** 0 ok; 1 runtime error (unreadable root, missing scope, unwritable `--output-dir`; printed as `{"error": "..."}`); 2 usage or config error (bad flag, invalid `.design-and-fix.json`, with a message naming the key and the fix); 3 `--fail-on` threshold exceeded, or `--fail-on` set and the scan partial (timeout or `max_files` reached: the gate fails closed).

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

**Signals are leads, not findings.** They come from static patterns and can be wrong. The skill reads the cited file to confirm each one before reporting it. Comments are ignored. Test files are ignored except by `hardcoded-secret` (known formats only there; like all its signals, at confidence `low`), which also scans `.env`, YAML, JSON, TOML, INI and properties files (not lockfiles or `.env.example`) and flags committed private keys without printing them. At most 200 signals per rule are listed. Suppress a line with `// dfscan:ignore <rule>` or `# dfscan:ignore <rule>`. Some rules are off by default because their measured precision was 0% at release; turn one back on with `"rules": {"<id>": {"enabled": true}}` in `.design-and-fix.json` (see [docs/configuration.md](docs/configuration.md)).

Spring, ASP.NET, Rails and Laravel support is newer and less measured than JS/TS/Python/Go. See [docs/limitations.md](docs/limitations.md).

## Measured quality

Numbers here come from real runs, each with its sample size. A scanner signal is a lead; precision is the share of sampled signals that were real problems when read in context (unsure labels excluded). The skill-eval pass rate is separate: it grades the agent's output (files, linter, required content), not the scanner.

### Scanner precision

<!-- precision:start -->
From [evals/precision.md](evals/precision.md): 18 public repos at pinned SHAs (the 14 from 1.0.2 plus example-voting-app, robot-shop, awesome-compose and todo-nodejs-mongo, added so more rules have signals), up to 20 hand-labelled signals per rule (seed 1002), 248 labels. A second labeller relabelled five rules blind; agreement and kappa are in the report. `Sample` is the number of signals drawn for the rule (unsure labels included), and precision = tp / (tp + fp). With samples this small, read each number as a band; the 95% intervals are in the report. `mixed-error-shapes`, `retry-no-backoff` and `sql-string-concat` had no corpus signals and are listed as unmeasured in [docs/rules.md](docs/rules.md).

| Rule | Sample | Precision (1.1.0) | 1.0.2 (14 repos) | Confidence |
|---|---|---|---|---|
| `ci-no-test-step` | 2 | 50% | not measured | medium |
| `console-logging-only` | 12 | 17% | 100% (n=2) | low |
| `docker-latest-tag` | 2 | 50% | not measured | medium |
| `docker-no-healthcheck` | 5 | 100% | 100% (n=4) | low |
| `docker-root-user` | 20 | 50% | 62% (n=8) | medium |
| `god-module` | 14 | 30% | 21% (n=20) | low |
| `hardcoded-secret` | 5 | 40% | 10% (n=20) | low |
| `http-no-timeout` | 20 | 45% | 100% (n=4) | low |
| `import-cycle` | 14 | 29% | 25% (n=12) | low |
| `k8s-no-limits` | 5 | 100% | not measured | medium |
| `k8s-no-probes` | 5 | 100% | not measured | medium |
| `list-route-no-pagination` | 2 | 100% | not measured | medium |
| `missing-lockfile` | 6 | 100% | 100% (n=2) | medium |
| `no-health-endpoint` | 20 | 90% | 100% (n=4) | medium |
| `no-tests` | 20 | 50% | 30% (n=20) | low |
| `no-version-prefix` | 8 | 38% | 100% (n=2) | low |
| `openapi-drift` | 14 | 0% | not measured | low, off by default |
| `query-in-loop` | 3 | 100% | 0% (n=13) | low |
| `swallowed-error` | 20 | 5% | 5% (n=20) | low |
| `unauth-write-route` | 19 | 33% | 0% (n=20) | low |
| `unbounded-query` | 9 | 11% | 0% (n=9) | low |
| `unpinned-dependency` | 18 | 100% | 0% (n=4) | low |
| `unvalidated-body` | 5 | 20% | not measured | low |

`openapi-drift` is **off by default** in 1.1.0: it measured 0% (0 of 14), all from Express routers mounted with `app.use('/prefix', router)`, whose prefix it does not resolve. Enable it with `"rules": {"openapi-drift": {"enabled": true}}` in `.design-and-fix.json`; every scan notes which rules are off by default.

False positives per repository, extrapolated from the per-rule rates: mean **10.1**, median **2.7** (18 repos; extrapolated from 242 decided labels over 23 rules). The mean is pulled up by django (about 52) and awesome-compose (about 50, a collection of about 40 sample stacks); the per-repo table is in [evals/precision.md](evals/precision.md#false-positives-per-repository-extrapolated).
<!-- precision:end -->

The Confidence column is what the scanner emits: every signal of a rule is capped at that value, and measured precision can only lower it, never raise it. Every rule under 50% is at `low`, and so, in 1.1.0, are `docker-no-healthcheck`, `no-tests`, `query-in-loop`, `unpinned-dependency` and the unmeasured `sql-string-concat` and `retry-no-backoff`, whose registry ceiling is `low`; `hardcoded-secret` reports known-format provider tokens at `low` too, the same as its heuristic matches. None are hidden except the default-off `openapi-drift`: the skill verifies each signal in the code before it becomes an issue. The weakest rules that run by default are `swallowed-error` (Django's documented `except X: pass` fallbacks) and `unbounded-query` (filtered single-row lookups). `unauth-write-route` is the least certain number: the two labellers disagreed on demo apps with no user model (kappa 0.27), so read it as 33% to about 83%; see [docs/limitations.md](docs/limitations.md). Scan time: median 0.51 s over 18 repos; the largest (django, 6,823 files) took 23.7 s, under the 30-second target. Recall on the fixture monorepo: 16 of 16 known issues. Reruns are identical on 18 of 18 repos.

### Skill evals

<!-- skill-evals:start -->
**16 / 18** skill eval runs pass (9 cases on Opus and Sonnet, plugin 1.1.0, Claude Code CLI 2.1.312, November 2026): full audit, quick question, non-interactive run, new-project design, re-run with diff, a repo with no tests, a 20,000-file repo, prompt-injection text in a README, and an unsupported language. Sonnet passes 9 of 9 and Opus 7 of 9; the two Opus failures happened because the eval's script allow-list denied the agent's lint command, so it delivered unlinted output (the prompt injection was not followed). Grading is deterministic: files, required strings, the transcript, the exit code, a write allow-list and a clean output linter. Per-case results are in [evals/skill-results.md](evals/skill-results.md); earlier runs and the skill fixes they found are in [evals/README.md](evals/README.md#skill-eval-history).
<!-- skill-evals:end -->

### Usefulness rating

Collecting. Tell us how useful an audit was with the [audit feedback template](.github/ISSUE_TEMPLATE/audit_feedback.md).

## Security & privacy

- The scanner never modifies your code and makes no network calls; it writes a file only with `--output-dir` (`scan.json`). The skill writes only `docs/architecture/` and `docs/adr/` (git-ignore `docs/architecture/.scan/`).
- It runs only read-only `git log` and `git rev-parse`; the skill runs only its own scripts.
- `.gitignore` is respected by default, so git-ignored `.env` and key files are not scanned (`--no-gitignore` changes that). Secret values are masked in every snippet.
- The harness you run it in sends the files the agent reads to its model provider. Scope narrowly or exclude folders to keep sensitive code out.
- Repository content is treated as data, never as instructions.

Details: [docs/security.md](docs/security.md).

## Compatibility

| Item | Status |
|---|---|
| Python | 3.9, 3.11, 3.12, 3.13 and 3.14 tested in CI |
| OS | Ubuntu and Windows tested in CI |
| Claude Code | Plugin validated with `claude plugin validate . --strict` in CI; end-to-end tested with the skill evals (`evals/skill-results.md`) |
| Codex | Manifest validated in CI; `evals/run.py --client codex` exists, not run end to end yet (no PASS row in `evals/skill-results.md`) |
| Cursor | Manifest validated in CI; `evals/run.py --client cursor` exists, not run end to end yet (no PASS row in `evals/skill-results.md`) |
| Other Agent Skills clients | Not tested |

Harness versions are listed only where verified.

## More documentation

[Quickstart](docs/quickstart.md) | [Configuration](docs/configuration.md) | [Rules](docs/rules.md) | [Limitations](docs/limitations.md) | [Architecture](docs/architecture.md) | [Troubleshooting](docs/troubleshooting.md) | [Compatibility](docs/compatibility.md) | [Security & privacy](docs/security.md) | [Roadmap](ROADMAP.md) | [Changelog](CHANGELOG.md)

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
