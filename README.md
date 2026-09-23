# design-and-fix

A skill for coding agents (Claude Code, Codex, Cursor) that produces ranked, actionable system-design, architecture, and API-optimization guidance, either for a new project or by auditing an existing one.

**Core principle:** no output without context. The skill always collects your constraints (scale, stack, team, non-functional requirements) before generating anything.

---

## Install

The repository owner is [Muhammad-Ahmad092](https://github.com/Muhammad-Ahmad092).

### Claude Code

```
/plugin marketplace add Muhammad-Ahmad092/design-and-fix
/plugin install design-and-fix@design-and-fix
```

### Codex

Copy or symlink the skill into a Codex skills location:

```bash
git clone https://github.com/Muhammad-Ahmad092/design-and-fix.git
cp -r design-and-fix/skills/design-and-fix .agents/skills/          # project-level
# or
cp -r design-and-fix/skills/design-and-fix ~/.agents/skills/        # user-level
```

### Cursor

1. Open **Cursor Settings → Rules**
2. Click **Add Rule** → **Remote Rule (Github)**
3. Paste `https://github.com/Muhammad-Ahmad092/design-and-fix`

### Any agent (skills CLI)

```bash
npx skills add Muhammad-Ahmad092/design-and-fix
```

---

## Modes

### Mode 1: New Project

**Trigger:** you give an idea, PRD, or rough description of something not yet built.

Output (3 files):

| File | Purpose |
|---|---|
| `01-system-design.md` | Architecture, components, data flow, key decisions |
| `02-architecture-decisions.md` | Patterns, scaling strategy, storage, failure modes |
| `03-api-design-and-optimization.md` | API style, conventions, auth, caching, performance |

### Mode 2: Existing Project

**Trigger:** you point to a codebase, folders, or docs and ask for an audit.

Output (3 files):

| File | Purpose |
|---|---|
| `01-architecture-issues.md` | Architecture Health Score (1-10) + issues ranked by severity |
| `02-api-optimization.md` | API issues, caching opportunities, quick wins vs structural fixes |
| `03-improvement-roadmap.md` | Quick wins, medium-term, long-term action plan |

### Optional

`04-tech-stack-comparison.md` is generated only when explicitly requested.

---

## Workflow

```
User request
     |
     v
+--------------------+
| Context Collection |  (mandatory, batched questions, skips what is already answered)
+--------------------+
     |
     +--------------------+
     |                    |
     v                    v
 Mode 1: New         Mode 2: Existing
     |                    |
     |                    v
     |             Analysis -> Health Score
     |                    |
     +---------+----------+
               |
               v
        3 output files
               |
               v
   Progressive refinement
   ("go deeper on API caching")
```

---

## Usage Examples

```
Design the architecture for a food-delivery app. ~50k users, team of 4, Node.js preferred.
```

```
Audit ./services/payments and ./services/orders. Pain point: slow checkout API. Must stay on PostgreSQL.
```

```
Go deeper on the caching strategy.
```

```
Generate the tech stack comparison.
```

---

## Rules

- Tables and bullets over prose; roughly one page per major section.
- Every recommendation includes a one-line "why".
- Sections that do not apply are omitted, not padded.
- Refinement expands only the requested section; nothing is regenerated.

---

## Repo Structure

```
design-and-fix/
├── README.md
├── LICENSE
├── .claude-plugin/
├── .codex-plugin/
├── .cursor-plugin/
└── skills/
    └── design-and-fix/
        ├── SKILL.md
        ├── agents/
        ├── references/
        ├── templates/
        └── scripts/
```

---

## License

MIT. See [LICENSE](LICENSE).
