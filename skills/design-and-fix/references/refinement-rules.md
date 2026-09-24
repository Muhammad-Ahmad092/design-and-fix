# Refinement Rules

Applies after the 3 output files exist. The user can go deeper on, or revisit, one topic without regenerating everything.

## Principle

**Expand only what was asked.** Never regenerate all files. Touch the smallest section that answers the request.

## Trigger Phrases

| User says | Action |
|---|---|
| "Go deeper on X" | Expand the section covering X |
| "Revisit X" / "reconsider X" | Re-evaluate the decision on X and update it |
| "More detail on X" | Same as "go deeper" |
| "Why did you choose X?" | Answer in chat; edit files only if the user then asks for a change |
| "Generate the tech stack comparison" | Create `04-tech-stack-comparison.md` from `templates/optional/` |
| "Change constraint Y" (scale, stack, budget) | Update Assumptions, then re-check only sections that depend on Y |

## Topic-to-Section Map

### Mode 1

| Topic | File | Section |
|---|---|---|
| Components, data flow | `01-system-design.md` | Core Components / Data Flow |
| Architectural choices | `01-system-design.md` | Key Architectural Decisions |
| Monolith vs microservices | `02-architecture-decisions.md` | Pattern Choice |
| Scaling, caching layers, load balancing | `02-architecture-decisions.md` | Scaling Strategy |
| Database choice | `02-architecture-decisions.md` | Data Storage |
| Failures, resilience | `02-architecture-decisions.md` | Failure Modes |
| REST/GraphQL/gRPC | `03-api-design-and-optimization.md` | API Style |
| Versioning, naming | `03-api-design-and-optimization.md` | Endpoint Conventions |
| Auth, rate limits | `03-api-design-and-optimization.md` | Auth & Rate Limiting |
| API caching | `03-api-design-and-optimization.md` | Caching Strategy |
| Pagination, N+1, batching | `03-api-design-and-optimization.md` | Performance Notes |

### Mode 2

| Topic | File | Section |
|---|---|---|
| Score, sub-scores | `01-architecture-issues.md` | Architecture Health Score |
| A specific issue | `01-architecture-issues.md` | Matching severity table row |
| Endpoint problems | `02-api-optimization.md` | Issues Found |
| Caching wins | `02-api-optimization.md` | Caching Opportunities |
| Cheap vs expensive fixes | `02-api-optimization.md` | Quick Wins vs Structural Fixes |
| Order of work, timeline | `03-improvement-roadmap.md` | Matching horizon table / Suggested Sequence |

## Procedure

```
User request
     |
     v
Map topic -> file + section (tables above)
     |
     +--> Ambiguous? Ask ONE short question, then continue
     |
     v
Read only that section (and the Assumptions / scope if needed)
     |
     v
Expand or revise that section in place
     |
     v
Dependency check: does this change affect other sections?
     |
     +--> No  -> done
     +--> Yes -> list them in one line, offer to update, do not auto-edit
```

## Rules

1. **Edit in place.** Modify the target section inside the existing file; do not create a new file.
2. **Keep the format.** Same tables, same headers, same one-line "why" on every recommendation.
3. **Go deeper means more specific, not longer prose.** Add concrete numbers, configs, examples, trade-offs, in tables or bullets.
4. **Stay within limits.** A refined section may grow, but still no walls of prose. Split into sub-tables if needed.
5. **New context wins.** If the user gives new facts (traffic doubled, team shrank), update Assumptions & Constraints first, then revise the affected section.
6. **Mode 2 consistency.** If a refinement changes an issue's severity or adds/removes an issue, update the health score and the roadmap row, then tell the user in one line.
7. **Report changes briefly.** Reply with: file, section changed, one-line summary of the change, and any impacted sections.
8. **Do not repeat unchanged content** in the reply.

## Reply Format

```
Updated: 02-architecture-decisions.md > Data Storage
Change: added read-replica plan and failover threshold
Impacted (not changed): 02 > Failure Modes, 03 > Caching Strategy
```

## Limits

- Never regenerate all 3 files unless the user explicitly asks to redo everything.
- Never create `04-tech-stack-comparison.md` unless explicitly requested.
- If a refinement request is outside the original scope (new service, new module), say so in one line and ask whether to extend the scope before editing.
