# Context Collection

Step 2 in every mode. No output files before it is complete, but never stall: when questions cannot be asked, use the defaults below.

## Rules

1. **Batch** all questions into one message, never one at a time.
2. **Fill gaps only.** Read the prompt and attached docs first; do not re-ask what is answered.
3. **Keep it short.** Numbered list, one line per question, with an example answer where useful.
4. **Accept "you decide".** Pick the default from the table below and record it under Assumptions & Constraints.
5. **Limits.** Mode 1: max 6 questions. Mode 2: max 4 questions. Quick mode: max 3 (or none when non-interactive). Prioritize by impact on the architecture.
6. **One follow-up round maximum.** After that, proceed with stated assumptions.

## Non-interactive Fallback

If the agent cannot ask questions (headless, exec or CI run, or the user said "don't ask" / "assume defaults"), proceed immediately: use the defaults table for every unanswered item and mark each one `Assumed (non-interactive)` under Assumptions & Constraints. Audit files (Mode 2, `quick-audit.md`) have no such section: put the assumed defaults on the `> Context:` line with the same marker. Never wait for answers that cannot arrive.

## Defaults

| Mode | Item | Default |
|---|---|---|
| Mode 1 | Scale | 10k MAU at launch / 100k in 12 months, peak 20 req/s |
| Mode 1 | Team | team of 3 mid-level |
| Mode 1 | Stack | Whatever the request names, else TypeScript + PostgreSQL |
| Mode 1 | Latency | p95 < 300 ms |
| Mode 1 | Availability | 99.9% |
| Mode 1 | Compliance | No special compliance |
| Mode 1 | Cost | Cost-sensitive |
| Mode 2 | Scope | top 3 non-overlapping candidate folders by LOC (prefer depth-2 folders over their parent; `candidate_folders[].approx_loc` from the scanner) |
| Mode 2 | Pain points | None stated |
| Mode 2 | Constraints | None |
| Mode 2 | Scale | Unknown: Scalability scored on code signals only |

Example line in the output: `- Scale: 10k MAU at launch, 100k in 12 months, peak 20 req/s (Assumed (non-interactive))`.

## Mode 1: New Project Questions

| # | Topic | Example ask | Why it matters |
|---|---|---|---|
| 1 | Scale | Users, requests/sec, data volume at launch and in 12 months | Drives pattern, storage, caching and Capacity Estimates |
| 2 | Stack | Preferred or forbidden languages, frameworks, cloud | Avoids recommending unusable tech |
| 3 | Team | Size, seniority, existing skills | Sets the operational complexity the team can carry |
| 4 | Non-functional | Latency, availability, compliance, cost sensitivity | Drives failure-mode, security and scaling decisions |
| 5 | Timeline / budget | Launch date, infra budget | Decides build-vs-buy and Deployment & Cost |

## Mode 2: Scan First, Then Ask

Run the scanner before asking anything (commands in `SKILL.md`, Step 2): `--folders-only` when the scope is unknown (scope discovery), `--scope` for the evidence scan. **Never ask about stack, frameworks, database or folder layout**: `detected_stack`, `frameworks` and `candidate_folders` answer those. Ask only these, max 4 questions:

| # | Topic | Example ask | Why it matters |
|---|---|---|---|
| 1 | Focus | "The scan shows `services/payments` (600 LOC), `services/orders` (420 LOC), `App` (300 LOC); `services` (1,020 LOC) is their parent, so it is not offered separately. Audit which?" | Keeps analysis accurate and cheap; propose candidates from the scan |
| 2 | Pain points | Slow endpoints, outages, hard-to-change modules | Points the audit at real problems first |
| 3 | Constraints | Cannot change X, must stay on Y, frozen contracts | Prevents unusable recommendations |
| 4 | Scale | Current traffic and growth expectation | Calibrates Scalability scoring |

If the user already named the folders, skip question 1: run the `--scope` scan first (its output still carries whole-root `detected_stack` and `candidate_folders`) and decide Quick mode before asking anything, so Quick's 3-question limit holds. Non-interactive with no named scope: the default scope above, recorded on the `> Scope:` line, e.g. `> Scope: services/payments, services/orders, App (Assumed (non-interactive))`.

## Batched Message Template

```
Before I start, a few details (answer what you can, say "you decide" for the rest):

1. Scale: expected users / traffic / data volume?
2. Stack: any preferred or forbidden technologies?
3. Team: size and experience level?
4. Non-functional: latency, availability, compliance, cost limits?
5. Timeline / budget pressure?
```

For Mode 2, use the four Mode 2 questions, with the focus question listing the scanned candidates.

## After Answers

Record every answer, and every default assumed, as a short list. It becomes the **Assumptions & Constraints** section (Mode 1 `01-system-design.md`, `quick-design.md`) or the `> Context:` line plus Scope of the audit files (Mode 2, `quick-audit.md`).
