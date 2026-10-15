# Design & Fix

Design & Fix is an Agent Skill and plugin for architecture planning and codebase audits. It produces ranked, evidence-based guidance for new systems and existing projects, with a focus on system design, APIs, scalability, reliability, and maintainability.

**Core principle:** collect constraints before making recommendations. The workflow asks for missing context such as scale, stack, team, budget, and non-functional requirements before producing deliverables.

## Install

This repository includes plugin packaging for Claude Code, Codex, and Cursor. Their installation flows differ: Claude Code installs from a plugin marketplace, Codex can install the skill directly (the native plugin manifest needs an update; see below), and Cursor supports local plugin imports.

### Claude Code

Run these commands in a terminal:

```bash
claude plugin marketplace add Muhammad-Ahmad092/design-and-fix
claude plugin install design-and-fix@design-and-fix
```

The second command installs for the current user by default. To enable the plugin for a repository, use `--scope project`:

```bash
claude plugin install design-and-fix@design-and-fix --scope project
```

The equivalent commands inside a Claude Code session are `/plugin marketplace add Muhammad-Ahmad092/design-and-fix` and `/plugin install design-and-fix@design-and-fix`. See the [Claude Code plugin installation guide](https://code.claude.com/docs/en/plugins/install).

### Codex

Install the portable skill with the Agent Skills CLI:

```bash
npx skills add Muhammad-Ahmad092/design-and-fix
```

This installs the `design-and-fix` skill for a compatible agent; it does not install the repository as a Codex plugin.

**Native Codex plugin status:** the current [.codex-plugin/plugin.json](.codex-plugin/plugin.json) does not match the documented Codex manifest format: its `skills` value is an array of objects, while Codex expects a skill-directory path such as `"./skills/"`, and the manifest is missing required interface metadata. Use the skill installation command above until the manifest is corrected. See [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins).

### Cursor

The repository includes a Cursor Plugin manifest. To test it locally, clone the repository into Cursor's local plugin directory.

**macOS / Linux:**

```bash
mkdir -p "$HOME/.cursor/plugins/local"
git clone https://github.com/Muhammad-Ahmad092/design-and-fix.git "$HOME/.cursor/plugins/local/design-and-fix"
```

**Windows PowerShell:**

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.cursor\plugins\local" | Out-Null
git clone https://github.com/Muhammad-Ahmad092/design-and-fix.git "$env:USERPROFILE\.cursor\plugins\local\design-and-fix"
```

Restart Cursor or run **Developer: Reload Window**, then open **Customize** and confirm the plugin is available. Local plugin imports may be disabled by team policy. Public Cursor Marketplace listing requires submitting the plugin for review; this repository is not presented as an already-published listing. See [Cursor Plugins](https://cursor.com/docs/plugins).

### Other Agent Skills-compatible clients

For a client that supports the Agent Skills standard, the same CLI can install the skill:

```bash
npx skills add Muhammad-Ahmad092/design-and-fix
```

This installs the skill component, not each platform's native plugin features. See the [Skills CLI reference](https://skills.sh/docs/cli).

## What It Produces

### Mode 1: New Project

Use this mode when you provide an idea, PRD, or description of a system that has not been built yet. It creates exactly three Markdown files in `docs/architecture/`:

| File | Contents |
|---|---|
| `01-system-design.md` | Overview, components, data flow, key decisions, assumptions, and constraints |
| `02-architecture-decisions.md` | Architecture pattern, scaling, storage, failure modes, and trade-offs |
| `03-api-design-and-optimization.md` | API style, conventions, authentication, rate limiting, caching, and performance |

### Mode 2: Existing Project

Use this mode when you identify a codebase, folders, services, or documents to audit. It creates exactly three Markdown files in `docs/architecture/`:

| File | Contents |
|---|---|
| `01-architecture-issues.md` | Architecture Health Score (1-10), sub-scores, and issues ranked by severity |
| `02-api-optimization.md` | API findings, caching opportunities, quick wins, and structural fixes |
| `03-improvement-roadmap.md` | Prioritized quick wins, medium-term work, and long-term work |

Mode 2 audits only the scope you identify. Findings cite source files where possible; unverified claims are labeled rather than presented as facts. The optional `scripts/scan_repo.py` helper can identify likely stack signals and candidate folders, but does not analyze source contents.

### Optional Comparison

`04-tech-stack-comparison.md` is generated only when explicitly requested. It is not included in either default three-file output.

## Workflow

1. Detect whether the request is a new design, an existing-project audit, or unclear. Ask one short question if the mode is unclear.
2. Read the request and provided materials, then ask one batched set of questions for missing constraints. Ask no more than six questions and allow sensible assumptions when requested.
3. Follow the matching mode instructions and templates. Do not generate output files until context collection is complete.
4. For audits, inspect the requested scope, cite evidence, score only assessed areas, and connect findings to the roadmap.
5. After the three files exist, refine only the section the user asks to revisit. Do not regenerate the full set.

## Usage

**New system:**

```text
Design the architecture for a food-delivery app. Expect about 50k users, a team of 4, and prefer Node.js. Ask me for any missing constraints before writing the design.
```

**Existing project:**

```text
Audit ./services/payments and ./services/orders. The main pain point is a slow checkout API, and we must keep PostgreSQL. Ask for any missing context before starting.
```

**Refinement or optional comparison:**

```text
Go deeper on the caching strategy.
Generate the tech stack comparison.
```

## Output Conventions

- Prefer concise tables and bullets over long prose.
- Include a brief reason with every recommendation or fix.
- Omit sections that do not apply instead of filling them with generic advice.
- Do not invent facts about a codebase; cite paths or mark claims as not verified.
- Keep assumptions and constraints visible in the generated documents.

## Repository Layout

```text
design-and-fix/
├── .claude-plugin/
│   ├── marketplace.json
│   └── plugin.json
├── .codex-plugin/
│   └── plugin.json
├── .cursor-plugin/
│   └── plugin.json
├── skills/
│   └── design-and-fix/
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       ├── references/
│       ├── scripts/scan_repo.py
│       └── templates/
├── LICENSE
└── README.md
```

## License

MIT. See [LICENSE](LICENSE).
