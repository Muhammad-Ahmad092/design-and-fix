# Architecture Health Score Rubric

Used in Mode 2 and Quick audit. Scores come from a lookup table, never from subtraction, so the score and the band always agree.

## Categories

Seven categories, equal weight. An issue counts only in the category of its ID prefix.

| Category | ID prefix | Measures |
|---|---|---|
| System Design | `SYS-` | Boundaries, coupling, dependency direction, pattern fit for scale |
| API Design | `API-` | Consistency, versioning, contracts, pagination, error format |
| Scalability | `SCL-` | Statelessness, bottlenecks, unbounded queries, async handling, data growth |
| Reliability | `REL-` | Timeouts, retries, idempotency, failure isolation |
| Security | `SEC-` | Secrets, authn/authz, input handling, injection, data exposure |
| Observability | `OBS-` | Logs, metrics, traces, alerts, error reporting |
| Maintainability | `MNT-` | Modularity, tests, duplication, cycles, documentation |

- A category that is out of scope, or has no evidence either way, is `Not assessed` and excluded; the remaining categories are re-weighted equally.
- A category scored without any file read is not allowed: mark it `Not assessed`.

## Sub-score Lookup

Find the category's worst severity, then use its count. Critical decides when present, else High, else Medium.

| Worst issues in category | Score | Band |
|---|---|---|
| No issues | 10 | Strong |
| Only Low issues | 9 | Strong |
| 1–2 Medium (no High/Critical) | 8 | Good |
| 3+ Medium (no High/Critical) | 7 | Good |
| Exactly 1 High (no Critical), ≤2 Medium | 6 | Mixed |
| Exactly 1 High (no Critical), 3+ Medium | 5 | Mixed |
| 2 High (no Critical) | 4 | Weak |
| 3+ High (no Critical) or exactly 1 Critical | 3 | Weak |
| 2 Critical | 2 | Severe |
| 3+ Critical | 1 | Severe |

Issue count matters only within a band, so a bigger codebase does not drift down linearly.

### Worked Examples

- two High → 4 Weak (any number of Medium/Low)
- one Critical → 3 Weak (any number of High/Medium/Low)
- three High → 3 Weak
- one High + one Medium → 6 Mixed

## Overall Score and Band

Overall = mean of assessed sub-scores, rounded half up to one decimal (5.75 → 5.8). The band comes from the rounded value. **The band is the headline**; the number goes in parentheses.

| Overall | Band | Line |
|---|---|---|
| 8.0–10 | Healthy | focus on Low/Medium cleanup |
| 6.0–7.9 | Stable with risks | fix High issues before scaling |
| 4.0–5.9 | Fragile | structural fixes needed before growth |
| < 4.0 | Unsafe | address Critical issues immediately |

## Output Format

Headline: `## Architecture Health Score: <Band> (<X.X> / 10)`
Evidence line: `Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed`

```markdown
## Architecture Health Score: Fragile (5.3 / 10)

Fragile: structural fixes needed before growth.
Evidence: 31 files read, 14 scanner signals reviewed, 9 confirmed

| Category | Score | Band | Main driver (IDs) |
|---|---|---|---|
| System Design | 8 | Good | Import cycle orders ↔ billing (SYS-001 Medium) |
| API Design | 4 | Weak | No pagination, mixed error formats (API-001 High, API-002 High) |
| Scalability | 6 | Mixed | Query inside loop in checkout (SCL-001 High) |
| Reliability | 3 | Weak | No timeout on payment provider call (REL-001 Critical) |
| Security | 3 | Weak | Hardcoded secret in config (SEC-001 Critical) |
| Observability | 7 | Good | No request IDs, no error-rate alert (OBS-001 Medium, OBS-002 Medium, OBS-003 Medium) |
| Maintainability | 6 | Mixed | Untested charge flow, duplicated validation (MNT-001 High, MNT-002 Medium) |
```

The example lists every issue with its severity so each score can be checked against the lookup table. Arithmetic: (8 + 4 + 6 + 3 + 3 + 7 + 6) / 7 = 5.29 → 5.3 → Fragile. A `Not assessed` row shows `Not assessed | — | <reason>` and is left out of the mean.

- Quick audit shows only the headline band, the number and the Evidence line, no category table.

## Consistency Checks

- [ ] Every sub-score equals the lookup-table row for that category's issue counts
- [ ] Every issue counted is defined once with an ID whose prefix matches its category
- [ ] Overall equals the mean of assessed sub-scores, rounded half up to one decimal
- [ ] Headline band matches the overall band table
- [ ] Every assessed category has at least one file read; others say `Not assessed`
- [ ] Every defined ID sits in exactly one roadmap `Action (IDs)` row; Suggested Sequence follows the order in `references/formatting-rules.md` (Roadmap Table)
