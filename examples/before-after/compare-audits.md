# Compare output

Produced by:

```bash
python skills/design-and-fix/scripts/compare_audits.py examples/before-after/before examples/before-after/after \
  --repo <after-copy> --old-repo <before-copy> --date 2026-11-05
```

```text
## Change Since Last Audit

Previous: Stable with risks (6.3 / 10) → Current: Stable with risks (6.6 / 10)
Revision: unknown → unknown

- Fixed: REL-002
- Still open: API-001, MNT-001, OBS-001, REL-001, REL-003, SCL-001, SEC-001, SYS-001

Score +0.3 (6.3 → 6.6): fixed REL-002 (High).
```
