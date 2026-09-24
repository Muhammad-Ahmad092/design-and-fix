# Formatting Rules

Read before writing any output file. Applies to both modes and all files (`01`, `02`, `03`, and optional `04`).

## Core Rules

| # | Rule | Why |
|---|---|---|
| 1 | Discipline over verbosity: about one page per major section, half a page of prose maximum | Keeps files reviewable in minutes |
| 2 | Tables wherever there are more than 2 comparable items | Comparisons are scannable in tables, not in paragraphs |
| 3 | Bullets for lists; no walls of prose | Faster to read and diff |
| 4 | Every recommendation or fix includes a one-line "why" | No unexplained prescriptions |
| 5 | No filler: omit sections that do not apply | Padding hides the useful content |
| 6 | Consistent headers across modes and files | Files read like one system, not ad hoc reports |
| 7 | Flows and diagrams in plain text or ASCII only | Renders in any tool, terminal, or diff |

## The "Why" Line

Every row or bullet that recommends something ends with a short reason.

| Bad | Good |
|---|---|
| Use Redis for caching. | Use Redis for caching. Why: hot reads dominate traffic and PostgreSQL is the bottleneck. |
| Add pagination. | Add cursor pagination to `/orders`. Why: unbounded lists cause slow responses as data grows. |

- One line maximum, under about 20 words.
- In tables, use a dedicated `Why` column.

## Standard Headers

Use these exact names so files stay consistent.

### Mode 1

| File | Headers (in order) |
|---|---|
| `01-system-design.md` | Overview, Core Components, Data Flow, Key Architectural Decisions, Assumptions & Constraints |
| `02-architecture-decisions.md` | Pattern Choice, Scaling Strategy, Data Storage, Failure Modes, Trade-offs Accepted |
| `03-api-design-and-optimization.md` | API Style, Endpoint Conventions, Auth & Rate Limiting, Caching Strategy, Performance Notes |

### Mode 2

| File | Headers (in order) |
|---|---|
| `01-architecture-issues.md` | Architecture Health Score, Critical Issues, High Issues, Medium / Low Issues |
| `02-api-optimization.md` | API Health Snapshot, Issues Found, Caching Opportunities, Quick Wins vs Structural Fixes |
| `03-improvement-roadmap.md` | Quick Wins (days), Medium-term (weeks), Long-term (months/quarters), Suggested Sequence |

### Optional

| File | Headers (in order) |
|---|---|
| `04-tech-stack-comparison.md` | Comparison Table, Recommendation, When to Reconsider |

Omit a header only when the section does not apply. Never rename headers.

## File Skeleton

```markdown
# <File Title>

> Scope: <one line: project or folders covered>
> Context: <one line: scale, team, key constraints>

## <Section 1>
...

## <Section 2>
...
```

- One `#` title per file, `##` for sections, `###` only if a section needs sub-parts.
- The Scope and Context lines are mandatory at the top of every file.

## Table Standards

| Rule | Detail |
|---|---|
| Header row | Always present, short column names |
| Cell length | One line per cell; move detail to bullets under the table |
| Column order | Subject first, decision or fix next, `Why` last |
| Severity column | Use exactly: Critical, High, Medium, Low |
| Effort column | Use exactly: S (hours), M (days), L (weeks), XL (months) |
| Impact column | Use exactly: Low, Medium, High |

### Issue Table Template

| Issue | Root cause | Impact | Fix | Why |
|---|---|---|---|---|

### Decision Table Template

| Decision | Chosen | Alternative considered | Why |
|---|---|---|---|

### Roadmap Table Template

| Action | Effort | Impact | Why |
|---|---|---|---|

## ASCII Flow Style

```
Client -> API Gateway -> Auth Service -> Orders Service -> PostgreSQL
                                              |
                                              v
                                        Message Queue -> Worker
```

- Left to right for request flow, top to bottom for async/side effects.
- One line of note per hop when it matters (`-> [validates JWT]`).
- Maximum roughly 12 nodes; split into two diagrams beyond that.

## Wording Rules

- Direct and specific: name the component, endpoint, file, or number.
- Make one choice per decision. Avoid "it depends" without a pick.
- Use numbers over adjectives (`p95 < 300 ms`, not `fast`).
- Cite file paths in Mode 2 issues; mark unverified claims `Not verified`.
- No marketing language, no preamble, no closing summary.

## Length Limits

| Element | Limit |
|---|---|
| Overview | 2-3 lines |
| Prose block | 4 lines maximum |
| "Why" line | 1 line |
| Section | About half a page |
| File | About 2-3 pages total |

## Pre-Write Checklist

- [ ] Standard headers used, in the right order
- [ ] Scope and Context lines at the top
- [ ] Tables used for every comparison of 3+ items
- [ ] Every recommendation has a "why"
- [ ] Severity, Effort, and Impact use the fixed vocabulary
- [ ] No filler sections, no generic boilerplate
- [ ] Diagrams are plain text
