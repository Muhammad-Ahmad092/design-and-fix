from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import List

from helpers import FIXTURES, REPO, SCRIPTS

import lint_output
import rubric

EXAMPLES = os.path.join(REPO, "examples")
MODE2 = os.path.join(EXAMPLES, "mode-2-audit-monorepo")
MODE1 = os.path.join(EXAMPLES, "mode-1-food-delivery")
MONOREPO = os.path.join(FIXTURES, "monorepo")
LINT = os.path.join(SCRIPTS, "lint_output.py")


def read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def copy(self, src: str, name: str = "docs") -> str:
        dst = os.path.join(self.tmp, name)
        shutil.copytree(src, dst)
        return dst

    def mutate(self, folder: str, rel: str, old: str, new: str, count: int = 1) -> None:
        path = os.path.join(folder, *rel.split("/"))
        text = read(path)
        self.assertIn(old, text, "mutation target missing: %r" % old)
        write(path, text.replace(old, new, count))

    def messages(self, findings) -> List[str]:
        return ["%s:%d: %s" % (f.file, f.line, f.message) for f in findings]

    def assertFinding(self, findings, *needles: str):
        for m in self.messages(findings):
            if all(n.lower() in m.lower() for n in needles):
                return
        self.fail("no finding containing %r in:\n%s" % (needles, "\n".join(self.messages(findings))))


class RubricTests(unittest.TestCase):
    def test_subscore_table(self):
        self.assertEqual(rubric.subscore([]), 10)
        self.assertEqual(rubric.subscore(["Low"]), 9)
        self.assertEqual(rubric.subscore(["Medium"] * 3), 7)
        self.assertEqual(rubric.subscore(["High", "Medium"]), 6)
        self.assertEqual(rubric.subscore(["High"] + ["Medium"] * 3), 5)
        self.assertEqual(rubric.subscore(["High", "High"]), 4)
        self.assertEqual(rubric.subscore(["Critical"] * 2), 2)
        self.assertEqual(rubric.subscore(["Critical"] * 4), 1)

    def test_overall_half_up_and_band(self):
        self.assertEqual(rubric.overall({"a": 5, "b": 6, "c": 6, "d": 6}), 5.8)  # 5.75
        self.assertEqual(rubric.overall({"a": 8}), 8.0)
        self.assertEqual(rubric.band(8.0), "Healthy")
        self.assertEqual(rubric.band(7.9), "Stable with risks")
        self.assertEqual(rubric.band(4.0), "Fragile")
        self.assertEqual(rubric.band(3.9), "Unsafe")

    def test_weighted_overall(self):
        subs = {"System Design": 8, "API Design": 6, "Scalability": 6, "Reliability": 3,
                "Security": 1, "Observability": 8, "Maintainability": 6}
        self.assertEqual(rubric.overall(subs), 5.4)
        weights = rubric.weights_for("payments platform, 50 req/s")
        self.assertEqual(weights["Security"], 2.0)
        self.assertEqual(rubric.overall(subs, weights), 4.9)  # 39 / 8 = 4.875

    def test_weights_for_probe_lines(self):
        none = ("Health check endpoints, shipping", "Mobile app, unhealthy", "Hippie pii-free")
        for line in none:
            self.assertEqual(set(rubric.weights_for(line).values()), {1.0}, line)
        self.assertEqual(
            rubric.weights_for("Handles payment data via payment provider")["Security"], 2.0)
        w = rubric.weights_for("realtime bidding")
        self.assertEqual((w["Scalability"], w["Reliability"], w["Security"]), (1.5, 1.5, 1.0))
        self.assertEqual(rubric.weights_for("Stores PHI and health data")["Security"], 2.0)
        self.assertEqual(rubric.weights_for("real time chat")["Reliability"], 1.5)

    def test_weights_for_keywords(self):
        self.assertEqual(set(rubric.weights_for("anything").values()), {1.0})
        w = rubric.weights_for("Real-time bidding, handles PII")
        self.assertEqual((w["Scalability"], w["Reliability"], w["Security"]), (1.5, 1.5, 2.0))
        self.assertEqual(rubric.weights_for("healthcare")["Security"], 2.0)
        self.assertEqual(rubric.weights_for("latency critical")["Reliability"], 1.5)

    def test_weights_for_ignores_negated_keywords(self):
        # I4: stating an absence must not raise a weight
        for line in ("internal admin tool, no PII, no payments", "not real-time",
                     "without payment data", "free of PHI", "no PII or payments",
                     "non PII batch jobs"):
            self.assertEqual(set(rubric.weights_for(line).values()), {1.0}, line)
        # negation stops at the clause boundary; a later plain mention still counts
        self.assertEqual(rubric.weights_for("no PII, payments via Stripe")["Security"], 2.0)
        self.assertEqual(rubric.weights_for("payments platform with no PII")["Security"], 2.0)
        self.assertEqual(rubric.weights_for("no PII here; stores PII later")["Security"], 2.0)

    def test_weights_for_compliance_keywords(self):
        # Minor 10: the compliance words context-collection.md asks about raise Security
        for line in ("PCI scope", "HIPAA covered", "GDPR applies", "fintech app",
                     "stores cardholder data"):
            self.assertEqual(rubric.weights_for(line)["Security"], 2.0, line)
        self.assertEqual(set(rubric.weights_for("card component library").values()), {1.0})


class ExampleLintTests(TempCase):
    def test_examples_are_lint_clean(self):
        self.assertEqual(self.messages(lint_output.lint(MODE2, repo=MONOREPO)), [])
        self.assertEqual(self.messages(lint_output.lint(MODE1)), [])

    def test_single_file_lint(self):
        one = os.path.join(MODE2, "01-architecture-issues.md")
        self.assertEqual(self.messages(lint_output.lint(one, repo=MONOREPO)), [])

    def test_wrong_header_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "03-improvement-roadmap.md", "## Medium-term (weeks)", "## Mid-term")
        self.assertFinding(lint_output.lint(d), "03-improvement-roadmap.md", "header")

    def test_omitted_section_allowed_wrong_order_detected(self):
        d = self.copy(MODE2)
        path = os.path.join(d, "02-api-optimization.md")
        text = read(path)
        start = text.index("## Caching Opportunities")
        end = text.index("## Quick Wins vs Structural Fixes")
        write(path, text[:start] + text[end:])
        self.assertEqual(self.messages(lint_output.lint(d, repo=MONOREPO)), [])
        self.mutate(d, "02-api-optimization.md", "## Endpoint Inventory", "## TMP")
        self.mutate(d, "02-api-optimization.md", "## API Health Snapshot", "## Endpoint Inventory")
        self.mutate(d, "02-api-optimization.md", "## TMP", "## API Health Snapshot")
        self.assertFinding(lint_output.lint(d), "02-api-optimization.md", "out of order")

    def test_required_header_missing_detected(self):
        d = self.copy(MODE2)
        path = os.path.join(d, "01-architecture-issues.md")
        text = read(path)
        start = text.index("## Architecture Health Score")
        end = text.index("## Critical Issues")
        write(path, text[:start] + text[end:])
        self.assertFinding(lint_output.lint(d), "01-architecture-issues.md",
                           "required header 'Architecture Health Score' is missing")

    def test_missing_scope_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "02-api-optimization.md", "> Scope:", "> Scop:")
        self.assertFinding(lint_output.lint(d), "Scope")

    def test_bad_severity_vocabulary(self):
        d = self.copy(MODE2)
        self.mutate(d, "02-api-optimization.md", "| API-004 | Low |", "| API-004 | Minor |")
        self.assertFinding(lint_output.lint(d), "API-004", "severity")

    def test_wrong_subscore_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "| Security | 1 | Severe |",
                    "| Security | 2 | Severe |")
        self.assertFinding(lint_output.lint(d), "Security", "sub-score")

    def test_bad_band_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "Fragile (4.9 / 10)", "Healthy (4.9 / 10)")
        self.assertFinding(lint_output.lint(d), "band")

    def test_bad_overall_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "Fragile (4.9 / 10)", "Fragile (5.1 / 10)")
        self.assertFinding(lint_output.lint(d), "overall")

    def test_duplicate_id_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "| SCL-002 | Medium |", "| SCL-001 | Medium |")
        self.assertFinding(lint_output.lint(d), "SCL-001", "more than once")

    def test_malformed_id_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "| MNT-003 | Low |", "| MNT-3 | Low |")
        self.assertFinding(lint_output.lint(d), "MNT-3", "malformed")

    def test_api_id_in_wrong_file_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "| SYS-001 | Medium |", "| API-009 | Medium |")
        self.assertFinding(lint_output.lint(d), "API-009", "02-api-optimization.md")

    def test_id_missing_from_roadmap_detected(self):
        d = self.copy(MODE2)
        path = os.path.join(d, "03-improvement-roadmap.md")
        lines = [ln for ln in read(path).split("\n") if not ln.startswith("| SYS-001:")]
        write(path, "\n".join(lines))
        self.assertFinding(lint_output.lint(d), "SYS-001", "roadmap")

    def test_id_in_two_roadmap_rows_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "03-improvement-roadmap.md",
                    "| OBS-002: structured JSON logs", "| SYS-001: structured JSON logs")
        self.assertFinding(lint_output.lint(d), "SYS-001", "2 roadmap rows")

    def test_wrong_horizon_placement_detected(self):
        d = self.copy(MODE2)
        self.mutate(d, "03-improvement-roadmap.md", "and rotate it | S |", "and rotate it | L |")
        self.assertFinding(lint_output.lint(d), "SEC-003", "Quick Wins")

    def test_evidence_line_out_of_range(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md",
                    "| `services/catalog/app.py:9` |", "| `services/catalog/app.py:9999` |")
        self.assertEqual([m for m in self.messages(lint_output.lint(d)) if "9999" in m], [])
        self.assertFinding(lint_output.lint(d, repo=MONOREPO), "app.py:9999", "lines")

    def test_evidence_missing_file_and_bad_syntax(self):
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md",
                    "| `services/catalog/app.py:9` |", "| `services/nope/app.py:9` |")
        self.assertFinding(lint_output.lint(d, repo=MONOREPO), "services/nope/app.py", "exist")
        # Absence evidence: a bare folder or file path is accepted when it exists.
        self.mutate(d, "01-architecture-issues.md",
                    "`services/nope/app.py:9`", "`services/catalog/`")
        self.assertEqual([m for m in self.messages(lint_output.lint(d, repo=MONOREPO))
                          if "SEC-003" in m and "evidence" in m], [])
        self.mutate(d, "01-architecture-issues.md", "`services/catalog/`", "`services/ghost`")
        self.assertFinding(lint_output.lint(d, repo=MONOREPO), "services/ghost", "exist")
        # Bare line numbers continue the previous path; out-of-range ones are still caught.
        self.mutate(d, "01-architecture-issues.md", "`services/ghost`",
                    "`services/catalog/app.py:9`, `3-4`")
        self.assertEqual([m for m in self.messages(lint_output.lint(d, repo=MONOREPO))
                          if "SEC-003" in m and "evidence" in m], [])
        self.mutate(d, "01-architecture-issues.md", "`3-4`", "`9000`")
        self.assertFinding(lint_output.lint(d, repo=MONOREPO), "app.py:9000", "lines")
        self.mutate(d, "01-architecture-issues.md", "`services/catalog/app.py:9`, `9000`",
                    "`see the app file`")
        self.assertFinding(lint_output.lint(d), "SEC-003", "evidence")

    def test_mermaid_unbalanced_detected(self):
        d = self.copy(MODE1)
        self.mutate(d, "01-system-design.md",
                    'capp["Container: Customer app (React Native)"]',
                    'capp["Container: Customer app (React Native)"')
        self.assertFinding(lint_output.lint(d), "mermaid", "unbalanced")

    def test_escaped_pipe_detected(self):
        d = self.copy(MODE1)
        self.mutate(d, "01-system-design.md", 'api -->|"reads/writes, SQL"| db',
                    'api -->\\|"reads/writes, SQL"\\| db')
        self.assertFinding(lint_output.lint(d), "mermaid", "escaped pipe")

    def test_mermaid_unknown_header_subgraph_and_node_count(self):
        d = self.copy(MODE1)
        self.mutate(d, "01-system-design.md", "sequenceDiagram\n", "sequenceDiagrams\n")
        self.assertFinding(lint_output.lint(d), "mermaid", "diagram")
        d = self.copy(MODE1, "docs2")
        self.mutate(d, "01-system-design.md", "    pay[\"External: Payment provider\"]\n    notify",
                    "    pay[\"External: Payment provider\"]\n    end\n    notify")
        self.assertFinding(lint_output.lint(d), "mermaid", "subgraph")
        d = self.copy(MODE1, "docs3")
        extra = "".join("    n%d[\"Container: Worker\"]\n    n%d --> worker\n" % (i, i)
                        for i in range(4))
        self.mutate(d, "01-system-design.md", "    pay[\"External: Payment provider\"]\n    notify",
                    extra + "    pay[\"External: Payment provider\"]\n    notify")
        self.assertFinding(lint_output.lint(d), "mermaid", "nodes")

    def test_mermaid_message_text_and_multiline_label_allowed(self):
        d = self.copy(MODE1)
        self.mutate(d, "01-system-design.md", "A->>D: insert order (status=pending) + items",
                    "A->>D: charge (retry :) unbalanced")
        self.mutate(d, "01-system-design.md", 'api["Container: API (Node.js, NestJS)"]',
                    'api["Container: API<br/>(Node.js,\n        NestJS)"]')
        self.assertEqual(self.messages(lint_output.lint(d)), [])

    def test_mermaid_unclosed_quote_across_block_detected(self):
        d = self.copy(MODE1)
        self.mutate(d, "01-system-design.md", 'api["Container: API (Node.js, NestJS)"]',
                    'api["Container: API (Node.js, NestJS)]')
        self.assertFinding(lint_output.lint(d), "mermaid", "quotes")

    def test_container_label_missing_from_components(self):
        d = self.copy(MODE1)
        self.mutate(d, "01-system-design.md", 'worker["Container: Worker (Node.js, BullMQ)"]',
                    'worker["Container: Ghost service (Rust)"]')
        self.assertFinding(lint_output.lint(d), "Ghost service", "Core Components")

    def test_weighted_overall(self):
        d = self.copy(MODE2)
        path = os.path.join(d, "01-architecture-issues.md")
        good = read(path)
        # The example is weighted (Context says "payments", Security 2.0) and lints clean.
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        # The unweighted headline (5.4) is wrong; the weighted one (4.9) is right.
        write(path, good.replace("Fragile (4.9 / 10)", "Fragile (5.4 / 10)"))
        self.assertFinding(lint_output.lint(d), "overall", "4.9")
        # A Weight the Context line does not justify is rejected.
        write(path, good.replace("| Reliability | 3 | Weak | 1.0 |", "| Reliability | 3 | Weak | 3.0 |"))
        self.assertFinding(lint_output.lint(d), "Reliability", "weight")


FORMAT_102 = """# Architecture Issues

> Format: 1.0.2
> Scope: services/payments
> Context: about 50 req/s peak
> Audit: plugin 1.0.2 · scanner schema 3 · rev unknown · config 0a1b2c3d4e5f · 2026-11-02T10:00:00Z

## Architecture Health Score: Stable with risks (6.0 / 10)

Evidence: 1 files read, 1 scanner signals reviewed, 1 confirmed

| Category | Score | Band | Main driver (IDs) |
|---|---|---|---|
| System Design | Not assessed | - | not in scope |
| API Design | Not assessed | - | not in scope |
| Scalability | Not assessed | - | not in scope |
| Reliability | 6 | Mixed | No timeout (REL-001 High) |
| Security | Not assessed | - | not in scope |
| Observability | Not assessed | - | not in scope |
| Maintainability | Not assessed | - | not in scope |

## Critical Issues

None.

## High Issues

| ID | Issue | Status | Source | Evidence | Root cause | Consequence | Fix | Verify | Why |
|---|---|---|---|---|---|---|---|---|---|
| REL-001 | No timeout | confirmed | scanner:no-timeout | `a.ts:3` | RC-1 | Hangs | Add timeout | Test aborts after 5 s | Bounds latency |

- REL-001: Observed: fetch without a signal. Inference: a slow provider hangs the request. Limitations: none.

- RC-1: provider client has no default timeout.

## Medium / Low Issues

None.
"""

DETAIL = ("- REL-001: Observed: fetch without a signal. Inference: a slow provider hangs the "
          "request. Limitations: none.")

ROADMAP_EMPTY = ("# Improvement Roadmap\n\n> Format: 1.0.2\n> Scope: s\n> Context: c\n"
                 "> Audit: plugin 1.0.2 · scanner schema 3 · rev unknown\n\n"
                 "## Suggested Sequence\n\n1. Nothing open.\n")

ADR_BODY = ("## Context\n\nc\n\n## Decision\n\nd\n\n## Alternatives\n\na\n\n"
            "## Assumptions\n\nb\n\n## Consequences\n\n- ok\n")


class Format102Tests(TempCase):
    def make(self, text: str) -> str:
        d = os.path.join(self.tmp, "docs")
        write(os.path.join(d, "01-architecture-issues.md"), text)
        return d

    def test_valid_102_file_is_clean(self):
        self.assertEqual(self.messages(lint_output.lint(self.make(FORMAT_102))), [])

    def test_101_file_skips_new_field_checks(self):
        text = (FORMAT_102.replace("> Format: 1.0.2\n", "").replace(DETAIL + "\n", "")
                .replace(" Status | Source |", "")
                .replace(" confirmed | scanner:no-timeout |", "")
                .replace(" Verify |", "")
                .replace(" Test aborts after 5 s |", "")
                .replace("|---|---|---|---|---|---|---|---|---|---|", "|---|---|---|---|---|---|---|---|"))
        # A 1.0.1 audit is accepted only as an archived run (history/<date>/) ...
        arch = os.path.join(self.tmp, "docs", "history", "2026-01-05")
        write(os.path.join(arch, "01-architecture-issues.md"), text)
        self.assertEqual(self.messages(lint_output.lint(arch)), [])
        # ... a current audit file without `> Format: 1.0.2` is flagged (I5)
        self.assertFinding(lint_output.lint(self.make(text)), "Format: 1.0.2")

    def test_bad_status_and_source(self):
        d = self.make(FORMAT_102.replace("| confirmed |", "| certain |"))
        self.assertFinding(lint_output.lint(d), "REL-001", "status")
        d = self.make(FORMAT_102.replace("| scanner:no-timeout |", "| robot |"))
        self.assertFinding(lint_output.lint(d), "REL-001", "source")

    def test_missing_status_column(self):
        d = self.make(FORMAT_102.replace(" Status | Source |", " Source |")
                      .replace(" confirmed | scanner:no-timeout |", " scanner |")
                      .replace("|---|---|---|---|---|---|---|---|---|---|",
                               "|---|---|---|---|---|---|---|---|---|"))
        self.assertFinding(lint_output.lint(d), "Status")

    def test_verify_required_for_high(self):
        d = self.make(FORMAT_102.replace("Test aborts after 5 s", ""))
        self.assertFinding(lint_output.lint(d), "REL-001", "verify")
        detail = FORMAT_102.replace("Test aborts after 5 s", "").replace(
            "Limitations: none.", "Limitations: none. Verify: unit test aborts after 5 s.")
        self.assertEqual(self.messages(lint_output.lint(self.make(detail))), [])

    def test_dash_verify_and_missing_detail_line_flagged_for_high(self):
        # I6: `—` is allowed only for Medium/Low; High/Critical need a detail line
        d = self.make(FORMAT_102.replace("Test aborts after 5 s", "—"))
        self.assertFinding(lint_output.lint(d), "REL-001", "verify")
        d = self.make(FORMAT_102.replace(DETAIL + "\n", ""))
        self.assertFinding(lint_output.lint(d), "REL-001", "detail line")
        d = self.make(FORMAT_102.replace("Inference:", "Guess:"))
        self.assertFinding(lint_output.lint(d), "REL-001", "detail line")

    def test_audit_line_required_and_checked(self):
        # I5: a 1.0.2 file needs a well-formed Audit line
        d = self.make(FORMAT_102.replace("> Audit: plugin 1.0.2", "> Audt: plugin 1.0.2"))
        self.assertFinding(lint_output.lint(d), "missing `> Audit:`")
        d = self.make(FORMAT_102.replace("· rev unknown ", ""))
        self.assertFinding(lint_output.lint(d), "`> Audit:` line must read")
        for good in ("> Audit: plugin 1.0.2 · scanner schema 3 · rev abc1234",
                     "> Audit: plugin 1.0.2 · scanner not run · rev unknown · 2026-11-02T10:00Z"):
            d = self.make(re.sub(r"> Audit: .*", good, FORMAT_102))
            self.assertEqual(self.messages(lint_output.lint(d)), [], good)

    def test_dropping_format_line_does_not_silence_checks(self):
        # I5: removing Format and Audit lines and breaking the new fields must not lint clean
        bad = re.sub(r"> Audit: .*\n", "", FORMAT_102.replace("> Format: 1.0.2\n", ""))
        bad = bad.replace("| confirmed |", "| bogus |")
        self.assertFinding(lint_output.lint(self.make(bad)), "Format: 1.0.2")

    def test_not_reproducible_is_kept_but_not_scored(self):
        # I9: a not-reproducible row: no score impact, no Verify/detail, stale evidence allowed
        text = (FORMAT_102.replace("| REL-001 | No timeout | confirmed |",
                                   "| REL-001 | No timeout | not-reproducible |")
                .replace("Test aborts after 5 s", "—")
                .replace(DETAIL + "\n", "")
                .replace("| Reliability | 6 | Mixed |", "| Reliability | 10 | Strong |")
                .replace("Stable with risks (6.0 / 10)", "Healthy (10.0 / 10)")
                .replace("`a.ts:3`", "`gone/a.ts:3`"))
        d = self.make(text)
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self.assertEqual(self.messages(lint_output.lint(d, repo=repo)), [])
        write(os.path.join(d, "03-improvement-roadmap.md"), ROADMAP_EMPTY)
        self.assertEqual(self.messages(lint_output.lint(d)), [])   # not required on the roadmap
        # the same row as `confirmed` is scored, needs Verify/detail and must be on the roadmap
        found = self.messages(lint_output.lint(self.make(text.replace("not-reproducible",
                                                                      "confirmed")), repo=repo))
        for needle in ("sub-score", "Verify", "detail line", "roadmap", "does not exist"):
            self.assertTrue(any(needle in m for m in found), (needle, found))

    def test_unresolved_rc_reference(self):
        d = self.make(FORMAT_102.replace("- RC-1: provider client has no default timeout.\n", ""))
        self.assertFinding(lint_output.lint(d), "RC-1", "defined")

    def test_weight_column_required_when_context_raises_weights(self):
        d = self.make(FORMAT_102.replace("50 req/s peak", "50 req/s peak, real-time"))
        self.assertFinding(lint_output.lint(d), "weight")

    def test_adr_alternatives_and_assumptions_required(self):
        head = "# 0001. Use X\n\n> Format: 1.0.2\n\nDate: 2026-10-22\nStatus: Accepted\n\n"
        d = os.path.join(self.tmp, "adr")
        write(os.path.join(d, "0001-use-x.md"), head + ADR_BODY)
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        write(os.path.join(d, "0001-use-x.md"),
              head + ADR_BODY.replace("## Alternatives\n\na\n\n", ""))
        self.assertFinding(lint_output.lint(d), "Alternatives")

    def test_adr_supersedes_pairs(self):
        def adr(title: str, status: str, extra: str = "") -> str:
            return ("# %s\n\n> Format: 1.0.2\n\nDate: 2026-10-22\nStatus: %s\n%s\n%s"
                    % (title, status, extra, ADR_BODY))

        d = os.path.join(self.tmp, "adr")
        write(os.path.join(d, "0001-old.md"), adr("0001. Old", "Superseded by 0002"))
        write(os.path.join(d, "0002-new.md"), adr("0002. New", "Accepted", "Supersedes: 0001\n"))
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        write(os.path.join(d, "0001-old.md"), adr("0001. Old", "Accepted"))
        self.assertFinding(lint_output.lint(d), "0002", "0001", "superseded")
        write(os.path.join(d, "0001-old.md"), adr("0001. Old", "Superseded by 0002"))
        write(os.path.join(d, "0002-new.md"), adr("0002. New", "Accepted"))
        self.assertFinding(lint_output.lint(d), "0001", "Supersedes")


AUDIT = "> Audit: plugin 1.0.2 · scanner schema 3 · rev unknown · config 0a1b2c3d4e5f\n"


class ReviewFixTests(TempCase):
    """Regression tests for the 1.0.2 release review (area C)."""

    def make(self, text: str, name: str = "01-architecture-issues.md") -> str:
        d = os.path.join(self.tmp, "docs")
        write(os.path.join(d, name), text)
        return d

    def test_history_runs_are_skipped_by_default(self):
        # C1: a stale archived run must not break the lint of the current run
        d = self.copy(MODE2)
        arch = os.path.join(d, "history", "2026-10-01")
        shutil.copytree(MODE2, arch)
        self.mutate(arch, "01-architecture-issues.md", "`services/catalog/app.py:9`",
                    "`services/moved-away/app.py:9`")
        self.assertEqual(self.messages(lint_output.lint(d, repo=MONOREPO)), [])
        found = lint_output.lint(d, repo=MONOREPO, include_history=True)
        self.assertFinding(found, "history/2026-10-01/01-architecture-issues.md", "moved-away")
        code = subprocess.run([sys.executable, LINT, d, "--repo", MONOREPO, "--include-history"],
                              capture_output=True).returncode
        self.assertEqual(code, 1)

    def test_evidence_accepts_real_world_paths(self):
        # I7: Next.js dynamic segments, route groups, Remix `$id`, spaces, non-ASCII
        paths = ["app/[id]/page.tsx", "src/(auth)/login.ts", "routes/$id.tsx", "my folder/a.py",
                 "src/caf\u00e9.py", "Dockerfile"]
        repo = os.path.join(self.tmp, "repo")
        for rel in paths:
            write(os.path.join(repo, *rel.split("/")), "line1\nline2\nline3\n")
        cell = ", ".join("`%s:2`" % p for p in paths)
        d = self.make(FORMAT_102.replace("`a.ts:3`", cell))
        self.assertEqual([m for m in self.messages(lint_output.lint(d, repo=repo))
                          if "evidence" in m], [])
        d = self.make(FORMAT_102.replace("`a.ts:3`", "`app/[id]/page.tsx:9`"))
        self.assertFinding(lint_output.lint(d, repo=repo), "page.tsx:9", "lines")

    def test_evidence_outside_repo_rejected(self):
        # Minor 6: `..` and absolute paths never reach the file system
        for bad in ("../../etc/hosts:1", "/etc/hosts:1", "C:/Windows/win.ini:1", "a/../../b.py:2"):
            d = self.make(FORMAT_102.replace("`a.ts:3`", "`%s`" % bad))
            self.assertFinding(lint_output.lint(d, repo=self.tmp), "REL-001", "repo root")

    def test_foreign_adrs_are_not_rewritten(self):
        # I8: a MADR-style ADR without `> Format: 1.0.2` gets only the supersede pairing check
        d = os.path.join(self.tmp, "adr")
        madr = ("# Use PostgreSQL\n\n* Status: accepted\n* Date: 2020-01-01\n\n"
                "## Context and Problem Statement\n\nx\n\n## Decision Outcome\n\ny\n")
        write(os.path.join(d, "0001-use-postgresql.md"), madr)
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        new = ("# 0002. Use CockroachDB\n\n> Format: 1.0.2\n\nDate: 2026-10-22\nStatus: Accepted\n"
               "Supersedes: 0001\n\n" + ADR_BODY)
        write(os.path.join(d, "0002-use-cockroachdb.md"), new)
        self.assertFinding(lint_output.lint(d), "0001", "Superseded by 0002")
        write(os.path.join(d, "0001-use-postgresql.md"),
              madr.replace("* Status: accepted\n", "Status: Superseded by 0002\n"))
        self.assertEqual(self.messages(lint_output.lint(d)), [])

    def test_mermaid_headers(self):
        # Minor 4: C4Context is forbidden; gantt/journey and frontmatter are valid Mermaid
        def doc(block: str) -> str:
            return FORMAT_102 + "\n```mermaid\n" + block + "```\n"
        d = self.make(doc("C4Context\n    title x\n"))
        self.assertFinding(lint_output.lint(d), "mermaid", "C4")
        for ok in ("gantt\n    title Plan\n", "journey\n    title Checkout\n",
                   "---\ntitle: Flow\n---\nflowchart LR\n    a --> b\n"):
            d = self.make(doc(ok))
            self.assertEqual(self.messages(lint_output.lint(d)), [], ok)

    def test_bad_severity_reported_once(self):
        # Minor 15: vocabulary pass and issue pass used to report the same cell twice
        d = self.copy(MODE2)
        self.mutate(d, "02-api-optimization.md", "| API-004 | Low |", "| API-004 | Minor |")
        hits = [m for m in self.messages(lint_output.lint(d)) if "API-004" in m and "severity" in m]
        self.assertEqual(len(hits), 1, hits)

    def test_weight_column_before_score_still_checks_overall(self):
        # Minor 16: the overall-mean check finds Score by header name, so a Weight column
        # placed before Score (with Not assessed rows) no longer skips it silently
        text = FORMAT_102.replace("| Category | Score |", "| Category | Weight | Score |").replace(
            "|---|---|---|---|\n| System", "|---|---|---|---|---|\n| System")
        text = re.sub(r"(?m)^\| (%s) \|" % "|".join(rubric.CATEGORIES), r"| \1 | 1.0 |", text)
        d = self.make(text)
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        d = self.make(text.replace("Stable with risks (6.0 / 10)", "Stable with risks (7.0 / 10)"))
        self.assertFinding(lint_output.lint(d), "overall", "6.0")


class FindingFieldsTests(TempCase):
    """1.1.0: informational status, uncertain scanner signals, Related / First seen."""

    def make(self, text: str) -> str:
        d = os.path.join(self.tmp, "docs")
        write(os.path.join(d, "01-architecture-issues.md"), text)
        return d

    # the scan FORMAT_102's Audit and Scope lines were copied from
    META = {"timestamp": "2026-11-02T10:00:00Z", "config_hash": "0a1b2c3d4e5f",
            "scope": ["services/payments"]}

    def scan_file(self, signals, meta=None) -> str:
        path = os.path.join(self.tmp, "scan-%d.json" % len(os.listdir(self.tmp)))
        data = {"schema_version": 3, "signals": signals}
        if meta is not False:
            data["meta"] = dict(self.META) if meta is None else meta
        write(path, json.dumps(data))
        return path

    def test_informational_status_accepted_not_scored(self):
        text = (FORMAT_102.replace("| REL-001 | No timeout | confirmed |",
                                   "| REL-001 | No timeout | informational |")
                .replace("Test aborts after 5 s", "—")
                .replace(DETAIL + "\n", "")
                .replace("| Reliability | 6 | Mixed |", "| Reliability | 10 | Strong |")
                .replace("Stable with risks (6.0 / 10)", "Healthy (10.0 / 10)"))
        d = self.make(text)
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        write(os.path.join(d, "03-improvement-roadmap.md"), ROADMAP_EMPTY)
        self.assertEqual(self.messages(lint_output.lint(d)), [])   # no roadmap row needed
        # unlike not-reproducible, its Evidence must still exist
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self.assertFinding(lint_output.lint(d, repo=repo), "REL-001", "does not exist")
        # scoring it as if it were a defect is an arithmetic error
        scored = text.replace("| Reliability | 10 | Strong |", "| Reliability | 6 | Mixed |")
        scored = scored.replace("Healthy (10.0 / 10)", "Stable with risks (6.0 / 10)")
        self.assertFinding(lint_output.lint(self.make(scored)), "Reliability", "sub-score")

    def test_needs_review_required_when_signal_uncertain(self):
        uncertain = {"rule": "no-timeout", "file": "a.ts", "line": 3,
                     "uncertain_reason": "auth config outside --scope"}
        scan = self.scan_file([uncertain])
        d = self.make(FORMAT_102)
        found = lint_output.lint(d, scan=scan)
        self.assertFinding(found, "REL-001", "needs-review", "no-timeout")
        ok = self.make(FORMAT_102.replace("| confirmed |", "| needs-review |"))
        self.assertEqual(self.messages(lint_output.lint(ok, scan=scan)), [])
        # a certain signal, another line, another rule, or an agent-sourced issue: no finding
        for sig in (dict(uncertain, uncertain_reason=""), dict(uncertain, line=4),
                    dict(uncertain, rule="other-rule"),
                    {"rule": "no-timeout", "file": "a.ts", "line": 3}):
            self.assertEqual(self.messages(lint_output.lint(d, scan=self.scan_file([sig]))), [],
                             sig)
        agent = self.make(FORMAT_102.replace("| scanner:no-timeout |", "| agent |"))
        self.assertEqual(self.messages(lint_output.lint(agent, scan=scan)), [])
        # a signal inside an Evidence range matches too
        ranged = self.make(FORMAT_102.replace("`a.ts:3`", "`a.ts:2-5`"))
        self.assertFinding(lint_output.lint(ranged, scan=scan), "REL-001", "needs-review")
        # CLI: --scan is optional; a missing or broken scan file is a usage error (exit 2)
        proc = subprocess.run([sys.executable, LINT, d, "--scan", scan], capture_output=True,
                              text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("needs-review", proc.stdout)
        bad = os.path.join(self.tmp, "bad.json")
        write(bad, "{not json")
        for path in (bad, os.path.join(self.tmp, "missing.json")):
            proc = subprocess.run([sys.executable, LINT, d, "--scan", path], capture_output=True,
                                  text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 2, path)
            self.assertNotIn("Traceback", proc.stderr)

    def test_scanner_agent_source_does_not_escape_uncertain_gate(self):
        # final review C I-1: relabelling Source to scanner+agent must not turn the gate off
        uncertain = {"rule": "no-timeout", "file": "a.ts", "line": 3,
                     "uncertain_reason": "client configured outside --scope"}
        scan = self.scan_file([uncertain])
        for source in ("scanner+agent:no-timeout", "scanner+agent",
                       "scanner+agent:other-rule, no-timeout"):
            d = self.make(FORMAT_102.replace("| scanner:no-timeout |", "| %s |" % source))
            self.assertFinding(lint_output.lint(d, scan=scan), "REL-001", "needs-review",
                               "Uncertainty resolved")
        # another rule only: the signal is not this issue's
        other = self.make(FORMAT_102.replace("| scanner:no-timeout |",
                                             "| scanner+agent:other-rule |"))
        self.assertEqual(self.messages(lint_output.lint(other, scan=scan)), [])
        # resolved: the detail line names the file that resolved it, another file
        base = FORMAT_102.replace("| scanner:no-timeout |", "| scanner+agent:no-timeout |")
        resolved = base.replace("Limitations: none.", "Limitations: none. Uncertainty "
                                "resolved: `config/http.ts:4`.")
        self.assertEqual(self.messages(lint_output.lint(self.make(resolved), scan=scan)), [])
        bare = base.replace("Limitations: none.",
                            "Limitations: none. Uncertainty resolved: config/http.ts:4.")
        self.assertEqual(self.messages(lint_output.lint(self.make(bare), scan=scan)), [])
        # the signal's own file does not resolve anything
        own = base.replace("Limitations: none.",
                           "Limitations: none. Uncertainty resolved: `a.ts:9`.")
        self.assertFinding(lint_output.lint(self.make(own), scan=scan), "REL-001",
                           "needs-review", "other than a.ts")
        # with --repo, the resolving file must exist and hold that line
        repo = os.path.join(self.tmp, "repo")
        write(os.path.join(repo, "a.ts"), "x\n" * 5)
        d = self.make(resolved)
        self.assertFinding(lint_output.lint(d, repo=repo, scan=scan), "REL-001",
                           "config/http.ts:4")
        write(os.path.join(repo, "config", "http.ts"), "x\n" * 5)
        self.assertEqual(self.messages(lint_output.lint(d, repo=repo, scan=scan)), [])
        # a scanner:<rule> source is held to needs-review even with a resolution line
        plain = resolved.replace("| scanner+agent:no-timeout |", "| scanner:no-timeout |")
        self.assertFinding(lint_output.lint(self.make(plain), scan=scan), "REL-001",
                           "needs-review")

    def test_scan_meta_must_match_audit_and_scope_lines(self):
        # final review C I-2: --scan must be the scan this audit was built from
        d = self.make(FORMAT_102)
        self.assertEqual(self.messages(lint_output.lint(d, scan=self.scan_file([]))), [])
        cases = (
            (dict(self.META, timestamp="2026-11-03T09:00:00Z"), "2026-11-03T09:00:00Z"),
            (dict(self.META, config_hash="ffffffffffff"), "ffffffffffff"),
            (dict(self.META, scope=["services/orders"]), "services/orders"),
            (dict(self.META, scope=["services/payments", "services/catalog"]),
             "services/catalog"),
            ({}, "meta"),
        )
        for meta, needle in cases:
            found = lint_output.lint(d, scan=self.scan_file([], meta=meta))
            self.assertFinding(found, "not the scan this audit was built from", needle)
        self.assertFinding(lint_output.lint(d, scan=self.scan_file([], meta=False)),
                           "not the scan this audit was built from", "meta")
        # the Audit line lost its timestamp or config hash, or says no scan ran
        for old, new in ((" · 2026-11-02T10:00:00Z", ""),
                         (" · config 0a1b2c3d4e5f", ""),
                         ("scanner schema 3", "scanner not run")):
            bad = self.make(FORMAT_102.replace(old, new))
            self.assertFinding(lint_output.lint(bad, scan=self.scan_file([])),
                               "not the scan this audit was built from")
        # lenient on format, strict on value: same instant written differently is fine
        for stamp in ("2026-11-02 10:00:00 UTC", "2026-11-02T12:00:00+02:00",
                      "2026-11-02T10:00Z", "`2026-11-02T10:00:00Z` (claude-code)"):
            ok = self.make(FORMAT_102.replace("2026-11-02T10:00:00Z", stamp)
                           .replace("config 0a1b2c3d4e5f", "config 0A1B2C3D4E5F"))
            self.assertEqual(self.messages(lint_output.lint(ok, scan=self.scan_file([]))), [],
                             stamp)
        late = self.make(FORMAT_102.replace("2026-11-02T10:00:00Z", "2026-11-02T10:00:01Z"))
        self.assertFinding(lint_output.lint(late, scan=self.scan_file([])), "timestamp")
        # a whole-root scan (empty scope) names no folder to check; archived runs are skipped
        root = dict(self.META, scope=[])
        d = self.make(FORMAT_102)   # make() rewrites the one docs folder
        self.assertEqual(self.messages(lint_output.lint(d, scan=self.scan_file([], root))), [])
        # the same check runs on every audit file in the folder
        write(os.path.join(d, "03-improvement-roadmap.md"), ROADMAP_EMPTY.replace(
            "rev unknown", "rev unknown · config 0a1b2c3d4e5f · 2026-11-01T00:00:00Z").replace(
            "> Scope: s", "> Scope: services/payments"))
        self.assertFinding(lint_output.lint(d, scan=self.scan_file([])),
                           "03-improvement-roadmap.md", "not the scan")

    def test_quick_audit_unscored_issue_needs_no_next_step(self):
        head = ("# Quick Audit\n\n> Scope: s\n> Context: c\n> Format: 1.0.2\n" + AUDIT
                + "\n## Health Band\n\n**Healthy (9.0 / 10)**: fine.\n"
                "Evidence: 1 files read, 0 scanner signals reviewed, 0 confirmed\n\n"
                "## Top Issues\n\n"
                "| ID | Severity | Status | Source | Issue | Evidence | Fix | Verify | Why |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| MNT-001 | Low | confirmed | agent | Dead code | `a.py:1` | Remove | \u2014 | r |\n"
                "| SYS-001 | High | %s | agent | Sync call by design | `a.py:2` | Keep | \u2014 | r |\n"
                "\n## Next Steps\n\n1. MNT-001: remove. Why: r\n")
        d = os.path.join(self.tmp, "q")
        for status in ("informational", "not-reproducible"):
            write(os.path.join(d, "quick-audit.md"), head % status)
            self.assertEqual(self.messages(lint_output.lint(d)), [], status)
            write(os.path.join(d, "quick-audit.md"),
                  (head % status) + "2. SYS-001: watch. Why: r\n")
            self.assertEqual(self.messages(lint_output.lint(d)), [], status)   # once is fine
            write(os.path.join(d, "quick-audit.md"), (head % status)
                  + "2. SYS-001: watch. Why: r\n3. SYS-001: again. Why: r\n")
            self.assertFinding(lint_output.lint(d), "SYS-001", "at most 1")
        write(os.path.join(d, "quick-audit.md"), head % "confirmed")
        self.assertFinding(lint_output.lint(d), "SYS-001", "expected exactly 1")

    def test_related_and_first_seen_fields(self):
        good = FORMAT_102.replace("Limitations: none.",
                                  "Limitations: none. Related: REL-001. First seen: 2026-09-01.")
        self.assertEqual(self.messages(lint_output.lint(self.make(good))), [])
        version = good.replace("First seen: 2026-09-01", "First seen: 1.0.2")
        self.assertEqual(self.messages(lint_output.lint(self.make(version))), [])
        undefined = good.replace("Related: REL-001", "Related: SEC-009")
        self.assertFinding(lint_output.lint(self.make(undefined)), "REL-001", "Related", "SEC-009")
        # final review C M-2: an archive folder name (<date>-N) is not a run date
        archive = good.replace("First seen: 2026-09-01", "First seen: 2026-09-01-2")
        self.assertFinding(lint_output.lint(self.make(archive)), "REL-001", "First seen",
                           "Audit timestamp")
        bad_date = good.replace("First seen: 2026-09-01", "First seen: last spring")
        self.assertFinding(lint_output.lint(self.make(bad_date)), "REL-001", "First seen")


class CliTests(TempCase):
    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, LINT] + list(args), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", cwd=REPO)
        return proc.returncode, proc.stdout, proc.stderr

    def test_lint_cli_exit_codes(self):
        code, out, _ = self.run_cli(MODE2, "--repo", MONOREPO)
        self.assertEqual((code, out.strip()), (0, ""))
        d = self.copy(MODE2)
        self.mutate(d, "01-architecture-issues.md", "Fragile (4.9 / 10)", "Healthy (4.9 / 10)")
        code, out, _ = self.run_cli(d)
        self.assertEqual(code, 1)
        self.assertRegex(out.splitlines()[0], r"^01-architecture-issues\.md:\d+: .*band")
        code, out, _ = self.run_cli(d, "--format", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(sorted(data[0]), ["file", "line", "message"])
        code, _, err = self.run_cli(os.path.join(self.tmp, "missing"))
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())
        code, _, _ = self.run_cli()
        self.assertEqual(code, 2)



class Mode1RigorTests(TempCase):  # Task 15: Requirements and trade-off tables (plugin 1.1.0 files)
    SD = "01-system-design.md"
    TRADEOFF = ("| Option | Complexity | Scalability | Reliability | Security | Operations | Cost "
                "| Familiarity | Lock-in | Migration path |")

    def test_example_is_110_and_clean(self):
        md = read(os.path.join(MODE1, self.SD))
        self.assertIn("### Requirements", md)
        self.assertIn("| Requirement | Value | Source | Effect on design |", md)
        self.assertGreaterEqual(md.count(self.TRADEOFF), 5)
        self.assertEqual(self.messages(lint_output.lint(MODE1)), [])

    def test_lint_flags_missing_tradeoff_table(self):
        d = self.copy(MODE1)
        text = read(os.path.join(d, self.SD))
        start = text.index("### Trade-offs: Background work")
        end = text.index("### Trade-offs: Payments")
        write(os.path.join(d, self.SD), text[:start] + text[end:])
        self.assertFinding(lint_output.lint(d), self.SD, "5 decision", "4 trade-off table")

    def test_lint_flags_old_option_table_columns(self):
        d = self.copy(MODE1)
        self.mutate(d, self.SD, self.TRADEOFF, "| Option | Strength | Weakness | Verdict |")
        self.assertFinding(lint_output.lint(d), self.SD, "trade-off table", "columns")

    def test_lint_flags_tradeoff_with_one_option(self):
        d = self.copy(MODE1)
        text = read(os.path.join(d, self.SD))
        i = text.index("### Trade-offs: Payments")
        rows = [ln for ln in text[i:].splitlines() if ln.startswith("| ")][1:]  # option rows
        write(os.path.join(d, self.SD), text[:i] + text[i:].replace(rows[1] + "\n", "", 1))
        self.assertFinding(lint_output.lint(d), self.SD, "at least 2 options")

    def test_lint_flags_missing_requirements_table(self):
        d = self.copy(MODE1)
        self.mutate(d, self.SD, "| Requirement | Value | Source | Effect on design |",
                    "| Requirement | Value | Notes |")
        self.assertFinding(lint_output.lint(d), self.SD, "Requirements table")

    def test_lint_flags_bad_requirement_rows(self):
        d = self.copy(MODE1)
        text = read(os.path.join(d, self.SD))
        budget = next(ln for ln in text.splitlines() if ln.startswith("| Budget |"))
        text = text.replace(budget + "\n", "")
        goal = next(ln for ln in text.splitlines() if ln.startswith("| Goal |"))
        cells = goal.split("|")
        cells[3] = " maybe "
        text = text.replace(goal, "|".join(cells))
        write(os.path.join(d, self.SD), text)
        found = lint_output.lint(d)
        self.assertFinding(found, self.SD, "Budget", "missing")
        self.assertFinding(found, self.SD, "Goal", "source", "user", "assumption")

    def test_requirement_value_none_is_valid_but_effect_needs_reason(self):  # fix round 1
        d = self.copy(MODE1)
        text = read(os.path.join(d, self.SD))
        comp = next(ln for ln in text.splitlines() if ln.startswith("| Compliance |"))
        budget = next(ln for ln in text.splitlines() if ln.startswith("| Budget |"))
        text = text.replace(comp, "| Compliance | none | user | none: no regulated data |")
        text = text.replace(budget, "| Budget | n/a | assumption | none: no budget limit given |")
        write(os.path.join(d, self.SD), text)
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        self.mutate(d, self.SD, "| Compliance | none | user | none: no regulated data |",
                    "| Compliance | none | user | none |")
        found = lint_output.lint(d)
        self.assertFinding(found, self.SD, "Compliance", "effect on design is empty")
        self.assertFalse([m for m in self.messages(found) if "value is empty" in m])

    def test_plugin_110_audit_line_switches_checks_on(self):
        d = self.copy(MODE1)
        text = read(os.path.join(d, self.SD)).replace("> Audit: plugin 1.0.2", "> Audit: plugin 1.1.0")
        write(os.path.join(d, self.SD), text.replace("### Requirements\n", "").replace(
            "### Trade-offs: ", "#### Options: "))
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        self.mutate(d, self.SD, "| Requirement | Value | Source | Effect on design |",
                    "| Requirement | Value | Notes |")
        self.assertFinding(lint_output.lint(d), self.SD, "Requirements table")

    def test_older_mode1_files_are_exempt(self):
        d = self.copy(MODE1)
        # An older (1.0.2) file: no 1.1.0 headings and an older plugin on the Audit line.
        text = read(os.path.join(d, self.SD)).replace("### Requirements\n", "")
        text = text.replace("plugin 1.1.0", "plugin 1.0.2")
        text = text.replace("### Trade-offs: ", "#### Options: ")
        text = text.replace(self.TRADEOFF, "| Option | Strength | Weakness | Verdict |")
        text = text.replace("| Requirement | Value | Source | Effect on design |",
                            "| Requirement | Value | Notes |")
        write(os.path.join(d, self.SD), text)
        self.assertEqual(self.messages(lint_output.lint(d)), [])

    def test_scan_folder_is_not_linted(self):
        d = self.copy(MODE1)
        write(os.path.join(d, ".scan", self.SD), "# broken\n\n## Nonsense\n")
        write(os.path.join(d, ".scan", "scan.json"), '{"signals": []}')
        self.assertEqual(self.messages(lint_output.lint(d)), [])
        self.assertEqual(self.messages(lint_output.lint(d, scan=os.path.join(
            d, ".scan", "scan.json"))), [])


if __name__ == "__main__":
    unittest.main()
