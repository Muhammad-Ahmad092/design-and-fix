# Mode 2: Existing Project

Trigger: user points to a codebase, folders, services, or existing docs and wants an audit.

## Workflow

```
Context Collection -> Scope -> Analyze -> Score -> Fill 3 templates -> Self-check -> Deliver
```

1. **Context Collection.** Follow `references/context-collection.md` (Mode 2 questions). Focus, pain points, constraints.
2. **Scope.** Audit only the folders or services the user pointed to. Never scan the whole repo unless asked. Optionally run `scripts/scan_repo.py` to detect stack and list candidate folders.
3. **Analyze** across the areas below. Read real files; do not guess.
4. **Score.** Read `references/health-score-rubric.md` and produce the Architecture Health Score.
5. **Fill templates** from `templates/mode-2/`. Write to `docs/architecture/`.
6. **Self-check** against the checklist below.
7. **Deliver.** List the 3 files created and the overall score, nothing more.

## Analysis Areas

| Area | Look for |
|---|---|
| System design & architecture | Unclear boundaries, circular dependencies, god modules, wrong pattern for scale, shared mutable state |
| API design & performance | Inconsistent naming, missing pagination, N+1 queries, chatty endpoints, no versioning, missing rate limits, uncached hot reads |
| Scalability | Single points of failure, stateful services, unbounded queries, synchronous work that should be async |
| Reliability | Missing timeouts, retries without backoff, no idempotency, weak error handling, no health checks |
| Maintainability | Duplicated logic, missing tests on critical paths, tangled layers, undocumented contracts |

## Evidence Rules

- Every issue cites a file path (and line or function when possible).
- If something could not be verified, label it `Not verified` rather than asserting it.
- Respect the user's constraints ("cannot change X"): recommend fixes that work within them.
- Prioritize the user's stated pain points first.

## Severity Definitions

| Severity | Meaning | Examples |
|---|---|---|
| Critical | Data loss, security breach, or outage risk now | Unauthenticated write endpoint, no backups, SQL injection |
| High | Will break or block growth soon | N+1 on hot path, no timeouts on external calls, single DB with no failover |
| Medium | Slows the team or degrades performance | Inconsistent error format, missing pagination on large lists |
| Low | Cleanup and polish | Naming inconsistencies, dead code |

## Output: Exactly 3 Files

### `01-architecture-issues.md`

| Section | Content |
|---|---|
| Architecture Health Score | Overall /10 + sub-scores table (see rubric) |
| Critical Issues | Table: issue, root cause, impact, fix, why |
| High Issues | Same table format |
| Medium / Low Issues | Same table format, condensed into one combined table |

### `02-api-optimization.md`

| Section | Content |
|---|---|
| API Health Snapshot | Score or status per major endpoint group |
| Issues Found | Table: endpoint/area, problem, recommended fix, severity, why |
| Caching Opportunities | Bullets: specific, concrete wins |
| Quick Wins vs Structural Fixes | Two lists so the user sees what is cheap vs what is a rewrite |

### `03-improvement-roadmap.md`

| Section | Content |
|---|---|
| Quick Wins (days) | Table: action, effort, impact |
| Medium-term (weeks) | Same table format |
| Long-term (months/quarters) | Same table format |
| Suggested Sequence | Short ordered list: what to do first and why |

## Ordering Rules

- Issues sorted by severity, then by impact within each severity.
- Every issue in `01` and `02` appears in the roadmap `03`, placed by effort.
- Sequence puts Critical fixes and low-effort/high-impact items first.

## Self-Check Before Delivering

- [ ] Scope matches what the user pointed to
- [ ] Every issue has file evidence or is marked `Not verified`
- [ ] Every fix has a one-line "why"
- [ ] Issues are ranked Critical > High > Medium > Low
- [ ] Health score sub-scores match the issues found (no unexplained numbers)
- [ ] Roadmap covers every Critical and High issue
- [ ] User constraints are respected in every fix
- [ ] Sections that do not apply are omitted (no filler)
- [ ] Exactly 3 files; no `04-*` unless requested
