# Architecture Health Score Rubric

Used in Mode 2 only. Produces an overall score (1-10) plus sub-scores, so the user sees where the weakness is.

## Sub-Categories and Weights

| Sub-category | Weight | Measures |
|---|---|---|
| System Design | 20% | Boundaries, coupling, pattern fit for scale |
| API Design | 20% | Consistency, versioning, contracts, pagination, error handling |
| Scalability | 20% | Statelessness, bottlenecks, async handling, data growth |
| Reliability | 20% | Timeouts, retries, idempotency, failure isolation, observability |
| Maintainability | 20% | Modularity, tests, duplication, documentation |

If the user's focus excludes a category (for example no API in scope), drop it and re-weight the rest equally. State this in one line under the table.

## Score Bands (per sub-category)

| Score | Meaning |
|---|---|
| 9-10 | Strong: only Low issues, no structural concerns |
| 7-8 | Good: a few Medium issues, no High |
| 5-6 | Mixed: High issues present, fixable without redesign |
| 3-4 | Weak: multiple High issues or one Critical, needs structural work |
| 1-2 | Severe: multiple Critical issues, unsafe to scale or operate |

## Scoring Procedure

1. Start each sub-category at 10.
2. Subtract per issue found in that category:

| Severity | Deduction |
|---|---|
| Critical | -3 |
| High | -1.5 |
| Medium | -0.5 |
| Low | -0.1 |

3. Cap the result:
   - One or more Critical issues: sub-category cannot exceed 5.
   - Three or more High issues: sub-category cannot exceed 6.
4. Floor at 1.
5. Round each sub-score to the nearest whole number.
6. Overall = weighted average of sub-scores, rounded to one decimal.

## Evidence Rules

- Every deduction maps to a listed issue in `01-architecture-issues.md`. No unexplained numbers.
- A category with no verified evidence either way is marked `Not assessed` and excluded from the overall.
- Scores reflect the audited scope only, never the whole company or product.

## Output Format

```markdown
## Architecture Health Score: 6.2 / 10

| Sub-category | Score | Main driver |
|---|---|---|
| System Design | 7/10 | Circular dependency between orders and billing |
| API Design | 4/10 | No pagination, inconsistent error format |
| Scalability | 6/10 | Stateful sessions block horizontal scaling |
| Reliability | 6/10 | No timeouts on payment provider calls |
| Maintainability | 8/10 | Good test coverage, minor duplication |

Scope: `services/orders`, `services/payments`
```

## Interpretation Line

Add one line under the table:

| Overall | Line |
|---|---|
| 8.0-10 | Healthy: focus on Low/Medium cleanup |
| 6.0-7.9 | Stable but with real risks: fix High issues before scaling |
| 4.0-5.9 | Fragile: structural fixes needed before growth |
| Below 4.0 | Unsafe: address Critical issues immediately |

## Consistency Checks

- [ ] Sub-scores match the issue counts by severity
- [ ] Any Critical issue lowers its category to 5 or below
- [ ] Weakest sub-category is the one referenced first in the roadmap
- [ ] Overall score arithmetic is correct
