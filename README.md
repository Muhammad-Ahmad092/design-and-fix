# Design & Fix

Design & Fix is an Agent Skill and plugin for architecture planning and codebase audits. A stdlib-only Python scanner gathers evidence (stack, import cycles, routes, risky patterns, git hotspots); the skill verifies it, scores the project, and writes ranked, ID-tracked findings for new systems and existing projects.

<!-- Demo GIF: record a Claude Code run and save as docs/assets/demo.gif, then replace this comment with ![Demo](docs/assets/demo.gif) -->

## See it work

- [examples/mode-2-audit-monorepo](examples/mode-2-audit-monorepo/): a full audit of a synthetic monorepo with planted issues, with the raw `scan.json`.
- [examples/mode-1-food-delivery](examples/mode-1-food-delivery/): a new-system design with five ADRs.

More on both in [examples/README.md](examples/README.md).

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
python skills/design-and-fix/scripts/scan_repo.py <path> [--scope DIR]... [--list-depth N] [--top N] [--no-git] [--since 12.months] [--format json|markdown] [--folders-only]
```

| Flag | Meaning |
|---|---|
| `--scope DIR` | Limit analysis to a subfolder (repeatable, `/` or `\`) |
| `--list-depth N` | Folder depth reported as candidates (default 2) |
| `--top N` | Max candidate folders (default 15) |
| `--no-git` | Skip hotspots (the only git call is a read-only `git log`) |
| `--since` | Hotspot window (default `12.months`) |
| `--format` | `json` (default) or `markdown` |
| `--folders-only` | Fast scope discovery: stack, frameworks and candidate folders only; analyzers skipped (keys present, empty) |

Errors print `{"error": "..."}` and exit 1.

**JSON (schema 2):** `schema_version`, `root`, `scope`, `detected_stack`, `frameworks`, `candidate_folders`, `import_graph` (cycles, god modules), `routes`, `hotspots`, `signals`, `summary` (files, LOC, test files, signals by category, truncated rules, `routes_total`; `routes` lists at most 500), `notes`. Paths are root-relative with `/` on every OS.

**Rules** (severity is a hint; the audit assigns the final one):

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

**Signals are leads, not findings.** They come from static patterns and can be wrong. The skill reads the cited file to confirm each one before reporting it. Comments are ignored. Test files are ignored except by `hardcoded-secret` (reported with low confidence), which also scans `.env`, YAML, JSON, TOML, INI and properties files (not lockfiles or `.env.example`). At most 200 signals per rule are listed.

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
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

## Development

```bash
python -m unittest discover -s tests -v
python tests/validate_manifests.py
```

Use `python3` where `python` is missing. See [CONTRIBUTING.md](CONTRIBUTING.md) and [evals/README.md](evals/README.md).

## License

MIT. See [LICENSE](LICENSE).
