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
| Mode 1 | Workload (scale) | 10k MAU at launch / 100k in 12 months, peak 20 req/s |
| Mode 1 | Team size | team of 3 mid-level |
| Mode 1 | Stack | Whatever the request names, else TypeScript + PostgreSQL |
| Mode 1 | Deployment environment | One cloud region, managed containers + managed PostgreSQL |
| Mode 1 | Latency | p95 < 300 ms |
| Mode 1 | Availability | 99.9% |
| Mode 1 | Data sensitivity | Basic personal data (name, email); no payments or health data; no special compliance |
| Mode 1 | Budget | Cost-sensitive |
| Mode 1 | Operational constraints | No dedicated ops person: managed services, no self-run clusters |
| Mode 2 | Scope | top 3 non-overlapping candidate folders by LOC (prefer depth-2 folders over their parent; `candidate_folders[].approx_loc` from the scanner) |
| Mode 2 | Pain points | None stated |
| Mode 2 | Constraints | None |
| Mode 2 | Scale | Unknown: Scalability scored on code signals only |

Example line in the output: `- Scale: 10k MAU at launch, 100k in 12 months, peak 20 req/s (Assumed (non-interactive))`.

## Mode 1: New Project Questions

Requirements capture: the answers (or defaults) fill the Requirements table in `01-system-design.md` (`| Requirement | Value | Source | Effect on design |`), one row per item:

| Requirement row | Comes from | Default when unanswered |
|---|---|---|
| Goal | The request or PRD | none: always from the input |
| Functional | The request or PRD | none: always from the input |
| Non-functional | Question 4 (Latency, consistency) | Latency default |
| Roles | The request; question 5 when admin or partner access matters | end users plus one admin role |
| Traffic and data estimate | Question 1 (Workload) | Workload default |
| Availability and recovery target | Question 4 (Availability) | Availability default; RPO 1 h, RTO 4 h |
| Compliance | Question 5 (Data sensitivity) | Data sensitivity default |
| Budget | Question 6 | Budget default |
| Deployment constraint | Question 2 (Deployment environment) and question 6 (Operational constraints) | Deployment environment and Operational constraints defaults |
| Team capacity | Question 3 (Team size) | Team size default |

Source is `user` for a stated answer and `assumption` for a default (`assumption (non-interactive)` when no questions could be asked). Effect on design names the decision or component the value drives. Each row also feeds Requirements → Decisions, and each assumption is listed under Assumptions & Constraints.

| # | Topic | Example ask | Why it matters |
|---|---|---|---|
| 1 | Workload | Users, requests/sec, data volume at launch and in 12 months | Drives pattern, storage, caching and Capacity Estimates |
| 2 | Stack + deployment environment | Preferred or forbidden languages; cloud, on-prem or edge; managed services allowed? | Avoids recommending unusable tech or hosting |
| 3 | Team size | Size, seniority, who is on call | Sets the operational complexity the team can carry |
| 4 | Latency + availability | p95 target, uptime target, peak events | Drives failure-mode and scaling decisions |
| 5 | Data sensitivity | PII, payments, health data, compliance (GDPR, PCI, HIPAA) | Sets the depth of Security & Threat Model |
| 6 | Budget + operational constraints | Infra budget, launch date, ops limits (no Kubernetes, one region) | Decides build-vs-buy, Deployment & Cost and proportionality |

## Mode 2: Scan First, Then Ask

Run the scanner before asking anything (commands in `SKILL.md`, Step 2): `--folders-only` when the scope is unknown (scope discovery), `--scope` for the evidence scan. **Never ask about stack, frameworks, database or folder layout**: `detected_stack`, `frameworks` and `candidate_folders` answer those. Ask only these, max 4 questions:

| # | Topic | Example ask | Why it matters |
|---|---|---|---|
| 1 | Focus | "The scan shows `services/payments` (600 LOC), `services/orders` (420 LOC), `App` (300 LOC); `services` (1,020 LOC) is their parent, so it is not offered separately. Audit which?" | Keeps analysis accurate and cheap; propose candidates from the scan |
| 2 | Pain points | Slow endpoints, outages, hard-to-change modules | Points the audit at real problems first |
| 3 | Constraints | Cannot change X, must stay on Y, frozen contracts | Prevents unusable recommendations |
| 4 | Scale | Current traffic and growth expectation | Calibrates Scalability scoring |

Write data sensitivity and latency facts on the `> Context:` line with the rubric's keywords when they apply (`payments`, `PII`, `real-time`): they set category weights (`references/health-score-rubric.md`, Context Weights). Stating an absence is safe: `no PII` or `not real-time` raises nothing.

If the user already named the folders, skip question 1: run the `--scope` scan first (its output still carries whole-root `detected_stack` and `candidate_folders`) and decide Quick mode before asking anything, so Quick's 3-question limit holds. Non-interactive with no named scope: the default scope above, recorded on the `> Scope:` line, e.g. `> Scope: services/payments, services/orders, App (Assumed (non-interactive))`.

## Batched Message Template

```
Before I start, a few details (answer what you can, say "you decide" for the rest):

1. Workload: expected users / traffic / data volume, now and in 12 months?
2. Stack and hosting: preferred or forbidden technologies, cloud or on-prem?
3. Team: size, experience, who runs it in production?
4. Latency and availability targets?
5. Data sensitivity: personal, payment or health data? Any compliance?
6. Budget, launch date and operational limits?
```

For Mode 2, use the four Mode 2 questions, with the focus question listing the scanned candidates.

## After Answers

Record every answer, and every default assumed, as a short list. It becomes the **Assumptions & Constraints** section (Mode 1 `01-system-design.md`, `quick-design.md`) or the `> Context:` line plus Scope of the audit files (Mode 2, `quick-audit.md`).
