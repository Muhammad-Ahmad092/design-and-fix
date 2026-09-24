# Context Collection

Mandatory first phase in both modes. No output files before this is complete.

## Rules

1. **Batch** all questions into one message.
2. **Fill gaps only.** Read the user's prompt and attached docs first; do not re-ask what is already answered.
3. **Keep it short.** Numbered list, one line per question, with an example answer where useful.
4. **Accept "you decide".** For any unanswered item the user delegates, pick a sensible default and record it under "Assumptions & Constraints" in the output.
5. **Do not interrogate.** Maximum 6 questions per batch. If more gaps exist, prioritize by impact on architecture.
6. **One follow-up round maximum.** After that, proceed with stated assumptions.

## Mode 1: New Project Questions

| # | Topic | Example ask | Why it matters |
|---|---|---|---|
| 1 | Scale | Users, requests/sec, data volume at launch and in 12 months | Drives pattern, storage, and caching choices |
| 2 | Stack | Preferred or forbidden languages, frameworks, cloud | Avoids recommending unusable tech |
| 3 | Team | Size, seniority, existing skills | Determines operational complexity the team can carry |
| 4 | Non-functional | Latency target, availability target, compliance, cost sensitivity | Drives failure-mode and scaling decisions |
| 5 | Timeline / budget | Launch date, infra budget | Decides build-vs-buy and monolith-vs-microservices |

## Mode 2: Existing Project Questions

| # | Topic | Example ask | Why it matters |
|---|---|---|---|
| 1 | Focus | Which folders or services to audit (avoid whole-repo scans) | Keeps analysis accurate and cheap |
| 2 | Pain points | Slow endpoints, outages, hard-to-change modules | Points the audit at real problems first |
| 3 | Constraints | Cannot change X, must stay on Y, frozen contracts | Prevents unusable recommendations |
| 4 | Scale (optional) | Current traffic and growth expectation | Calibrates scalability scoring |

## Batched Message Template

```
Before I start, I need a few details (answer what you can, say "you decide" for the rest):

1. Scale: expected users / traffic / data volume?
2. Stack: any preferred or forbidden technologies?
3. Team: size and experience level?
4. Non-functional: latency, availability, compliance, cost limits?
5. Timeline / budget pressure?
```

For Mode 2, swap in the Mode 2 questions.

## After Answers

Record every answer, and every default you assumed, in a short list. This list becomes the **Assumptions & Constraints** section (Mode 1, `01-system-design.md`) or the header context of `01-architecture-issues.md` (Mode 2).
