# ADR Guide

Architecture Decision Records keep the reasons behind each decision next to the code. Mode 1 writes them by default; Mode 2 and Quick mode write them only when the user asks.

## When to Write

| Mode | ADRs |
|---|---|
| Mode 1 | One per row of Key Architectural Decisions in `01-system-design.md`, max 5; when the table has more than 5 rows, write ADRs for the 5 with the highest impact. Opt-out: the user says "no ADRs" |
| Mode 2 | Only on request (for example for a structural fix the user accepts) |
| Quick | Only on request |

## Format

Nygard format, from `templates/adr/0000-template.md`: title line `# NNNN. <Title>`, then `Date:` and `Status:` lines, then the sections Context, Decision, Consequences. 1 page maximum each.

| Part | Content |
|---|---|
| Status | `Accepted`, or `Superseded by NNNN` once replaced |
| Context | 2–5 lines: the forces at play (scale, team, constraints) |
| Decision | Active voice, "We will use …", plus the alternatives considered and why they lost |
| Consequences | Positive, negative, and the condition that would make you revisit it |

The Decision must agree with the matching Key Architectural Decisions row (same choice, same alternative).

## Location and Numbering

- Path: `docs/adr/NNNN-<kebab-title>.md` (for example `docs/adr/0003-use-postgresql-for-orders.md`). Create `docs/adr/` if missing.
- `NNNN` is four digits. Numbering continues from the highest existing ADR in `docs/adr/` (existing `0001`–`0004` → next is `0005`); start at `0001` when none exist. Never reuse a number.
- ADRs are never archived or moved by `references/archive-and-diff.md`.

## Re-runs and Superseding

- Unchanged decisions get no new ADR.
- A changed decision gets a new ADR with the next number.
- The ADR it replaces gets exactly one edit: its Status line becomes `Superseded by NNNN` (the new number). Nothing else in it changes.

## Checklist

- [ ] One ADR per Key Architectural Decisions row, max 5 (Mode 1), unless "no ADRs"
- [ ] Nygard sections from the template; 1 page max
- [ ] Numbering continues from existing ADRs; no number reused
- [ ] Re-run: only changed decisions get a new ADR; replaced ADR's Status is `Superseded by NNNN`
