from __future__ import annotations

import os
import re
import unittest
from typing import Dict

from helpers import (REPO, expected_headers, header_mismatch, headings,
                     overall_band, overall_score, rerun_only_headers, subscore)

SKILL = os.path.join(REPO, "skills", "design-and-fix")
REFS = os.path.join(SKILL, "references")
TEMPLATES = os.path.join(SKILL, "templates")
ADR_KEY = "NNNN-<kebab-title>.md"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def template_files():
    out = []
    for dirpath, _dirs, files in os.walk(TEMPLATES):
        out.extend(os.path.join(dirpath, f) for f in files if f.endswith(".md"))
    return sorted(out)


def template_key(path: str) -> str:
    rel = os.path.relpath(path, TEMPLATES).replace(os.sep, "/")
    return ADR_KEY if rel.startswith("adr/") else os.path.basename(path)


def section(md: str, heading_prefix: str) -> str:
    """Body of the first '## ' section whose heading starts with heading_prefix."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## " + heading_prefix):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def table_header_rows(md: str):
    """First row of every Markdown table in md."""
    rows = []
    prev_is_table = False
    for line in md.splitlines():
        is_table = line.startswith("|")
        if is_table and not prev_is_table:
            rows.append(line)
        prev_is_table = is_table
    return rows


class SkillDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = read(os.path.join(REFS, "formatting-rules.md"))
        cls.rubric = read(os.path.join(REFS, "health-score-rubric.md"))

    def test_expected_headers_parser(self):
        exp = expected_headers(self.rules)
        opt = rerun_only_headers(self.rules)
        self.assertEqual(exp["01-architecture-issues.md"][0], "Change Since Last Audit")
        self.assertEqual(opt["01-architecture-issues.md"], ["Change Since Last Audit"])
        self.assertEqual(opt["quick-audit.md"], ["Change Since Last Audit"])
        self.assertEqual(opt["01-system-design.md"], [])
        self.assertEqual(exp["04-tech-stack-comparison.md"],
                         ["Comparison Table", "Recommendation", "When to Reconsider"])
        self.assertEqual(exp["01-system-design.md"], [
            "Overview", "System Context", "Core Components", "Data Flow",
            "Capacity Estimates", "Key Architectural Decisions", "Assumptions & Constraints"])

    def test_header_helpers(self):
        md = "# T\n\n```\n## not a heading\n```\n## A\n## Architecture Health Score: Weak (3.0 / 10)\n"
        self.assertEqual(headings(md), ["A", "Architecture Health Score: Weak (3.0 / 10)"])
        exp = ["Change", "A", "Architecture Health Score"]
        self.assertIsNone(header_mismatch(headings(md), exp, ["Change"]))
        self.assertIsNotNone(header_mismatch(headings(md), exp))
        self.assertIsNotNone(header_mismatch(["A", "B"], ["A"]))

    def test_templates_match_standard_headers(self):
        exp = expected_headers(self.rules)
        opt = rerun_only_headers(self.rules)
        files = template_files()
        self.assertEqual(len(files), 10)
        for path in files:
            key = template_key(path)
            with self.subTest(template=key):
                self.assertIn(key, exp)
                problem = header_mismatch(headings(read(path)), exp[key], opt[key])
                self.assertIsNone(problem, problem)

    def test_templates_have_scope_and_context(self):
        for path in template_files():
            if template_key(path) == ADR_KEY:
                continue  # Nygard ADRs carry Date/Status lines instead
            head = read(path).splitlines()[:5]
            with self.subTest(template=path):
                self.assertTrue(any(l.startswith("> Scope:") for l in head))
                self.assertTrue(any(l.startswith("> Context:") for l in head))

    def test_adr_template(self):
        md = read(os.path.join(TEMPLATES, "adr", "0000-template.md"))
        self.assertTrue(md.startswith("# NNNN. <Title>"))
        self.assertIn("Date:", md)
        self.assertIn("Status: Accepted | Superseded by NNNN", md)

    def test_rubric_has_no_deductions(self):
        self.assertNotIn("Deduction", self.rubric)
        self.assertNotIn("-1.5", self.rubric)
        self.assertNotIn("Subtract", self.rubric)

    def test_rubric_worked_examples(self):
        for example in ("two High → 4 Weak", "one Critical → 3 Weak",
                        "three High → 3 Weak", "one High + one Medium → 6 Mixed"):
            self.assertIn(example, self.rubric)
        for cat in ("System Design", "API Design", "Scalability", "Reliability",
                    "Security", "Observability", "Maintainability"):
            self.assertIn("| " + cat + " |", self.rubric)
        for band in ("Healthy", "Stable with risks", "Fragile", "Unsafe",
                     "Strong", "Good", "Mixed", "Weak", "Severe"):
            self.assertIn(band, self.rubric)
        self.assertIn("## Architecture Health Score: <Band> (<X.X> / 10)", self.rubric)
        self.assertIn("Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed",
                      self.rubric)

    def test_subscore_helper_matches_worked_examples(self):
        self.assertEqual(subscore(["High", "High"]), 4)
        self.assertEqual(subscore(["Critical"]), 3)
        self.assertEqual(subscore(["High"] * 3), 3)
        self.assertEqual(subscore(["High", "Medium"]), 6)
        self.assertEqual(subscore(["High", "Medium", "Medium", "Medium"]), 5)
        self.assertEqual(subscore(["Low"]), 9)
        self.assertEqual(subscore([]), 10)
        self.assertEqual(overall_score([6, 5, 6, 6]), 5.8)

    def test_rubric_sample_is_consistent(self):
        fence = self.rubric.split("```markdown", 1)[1].split("```", 1)[0]
        head = re.search(r"Architecture Health Score: (.+?) \((\d+\.\d) / 10\)", fence)
        self.assertIsNotNone(head)
        scores = []
        for row in fence.splitlines():
            m = re.match(r"\| ([A-Za-z ]+) \| (\d+) \| (\w+) \| (.*) \|$", row)
            if not m:
                continue
            sevs = re.findall(r"[A-Z]{3}-\d{3} (Critical|High|Medium|Low)", m.group(4))
            with self.subTest(category=m.group(1)):
                self.assertTrue(sevs)
                self.assertEqual(subscore(sevs), int(m.group(2)))
            scores.append(int(m.group(2)))
        self.assertEqual(len(scores), 7)
        self.assertEqual(overall_score(scores), float(head.group(2)))
        self.assertEqual(overall_band(float(head.group(2))), head.group(1))

    def test_section_length_consistent(self):
        docs = [os.path.join(SKILL, "SKILL.md")] + [
            os.path.join(REFS, f) for f in os.listdir(REFS) if f.endswith(".md")]
        for path in docs:
            with self.subTest(doc=os.path.basename(path)):
                self.assertNotIn("one page per", read(path))
        self.assertIn("about half a page per major section", read(os.path.join(SKILL, "SKILL.md")))

    def test_mode1_templates_use_mermaid(self):
        m1 = os.path.join(TEMPLATES, "mode-1")
        sd = read(os.path.join(m1, "01-system-design.md"))
        ad = read(os.path.join(m1, "02-architecture-decisions.md"))
        api = read(os.path.join(m1, "03-api-design-and-optimization.md"))
        self.assertIn("```mermaid", sd)
        self.assertGreaterEqual(sd.count("flowchart LR"), 2)
        self.assertIn("sequenceDiagram", sd)
        self.assertIn("```mermaid", ad)
        self.assertIn("erDiagram", ad)
        self.assertIn("openapi: 3.1.0", api)

    def test_issue_tables_have_id_column(self):
        m2 = os.path.join(TEMPLATES, "mode-2")
        cases = [
            ("01-architecture-issues.md", ["Critical Issues", "High Issues", "Medium / Low Issues"]),
            ("02-api-optimization.md", ["Issues Found"]),
        ]
        for name, secs in cases:
            md = read(os.path.join(m2, name))
            for sec in secs:
                rows = table_header_rows(section(md, sec))
                with self.subTest(file=name, section=sec):
                    self.assertTrue(rows)
                    for row in rows:
                        self.assertTrue(row.startswith("| ID |"), row)
        audit = read(os.path.join(m2, "01-architecture-issues.md"))
        self.assertIn("| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |", audit)
        self.assertIn("| ID | Severity | Issue | Evidence | Root cause | Consequence | Fix | Why |",
                      audit)

    def test_roadmap_tables_cite_ids(self):
        md = read(os.path.join(TEMPLATES, "mode-2", "03-improvement-roadmap.md"))
        rows = table_header_rows(md)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row.strip(), "| Action (IDs) | Effort | Impact | Why |")

    def test_formatting_rules_contract(self):
        for text in ("| ID | Issue | Evidence | Root cause | Consequence | Fix | Why |",
                     "| Action (IDs) | Effort | Impact | Why |", "PREFIX-NNN",
                     "```mermaid", "flowchart", "12 nodes", "3–4 pages", "2–3 pages",
                     "1.5 pages", "About half a page"):
            self.assertIn(text, self.rules)
        for prefix in ("SYS-", "API-", "SCL-", "REL-", "SEC-", "OBS-", "MNT-"):
            self.assertIn("`" + prefix + "`", self.rules)
        self.assertNotIn("plain text or ASCII only", self.rules)

    def test_capacity_estimation_reference(self):
        md = read(os.path.join(REFS, "capacity-estimation.md"))
        for text in ("86,400", "3×", "365", "replication", "20%", "100k DAU"):
            self.assertIn(text, md)
        self.assertTrue(re.search(r"23\.\d", md), "worked example average QPS missing")


def frontmatter(md: str) -> Dict[str, str]:
    """Top-level `key: value` pairs of the leading YAML frontmatter block."""
    lines = md.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


class SkillFlowDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = read(os.path.join(SKILL, "SKILL.md"))

    def ref(self, name: str) -> str:
        return read(os.path.join(REFS, name))

    def test_skill_frontmatter(self):
        fm = frontmatter(self.skill)
        self.assertEqual(fm.get("name"), "design-and-fix")
        desc = fm.get("description", "")
        self.assertTrue(desc)
        self.assertLessEqual(len(desc), 1024)
        self.assertIn("Small single questions are answered in chat without the full workflow.",
                      desc)
        self.assertNotIn("even if they do not say", desc)

    def test_skill_uses_skill_dir(self):
        self.assertIn('"${CLAUDE_SKILL_DIR}/scripts/scan_repo.py"', self.skill)
        self.assertNotIn("skills/design-and-fix/scripts", self.skill)
        self.assertIn("relative to the folder containing", self.skill)

    def test_reference_map_complete(self):
        for name in sorted(os.listdir(REFS)):
            if name.endswith(".md"):
                with self.subTest(reference=name):
                    self.assertIn("references/" + name, self.skill)

    def test_skill_is_short_entry_point(self):
        self.assertLessEqual(len(self.skill.splitlines()), 160)
        for step in range(6):
            self.assertIn("## Step %d" % step, self.skill)
        for stale in ("exactly 3 files", "Exactly 3 files", "5 categories", "ASCII only"):
            self.assertNotIn(stale, self.skill)

    def test_non_interactive_defaults(self):
        md = self.ref("context-collection.md")
        for text in ("Assumed (non-interactive)", "10k MAU", "100k", "20 req/s",
                     "p95 < 300 ms", "99.9%", "top 3 non-overlapping candidate folders", "4 questions"):
            self.assertIn(text, md)

    def test_quick_mode_triggers(self):
        quick = self.ref("quick-mode.md")
        for md in (quick, self.skill):
            self.assertIn("2,000", md)
            self.assertIn("say 'full'", md)
        for text in ("quick-design.md", "quick-audit.md", "1.5 pages", "3 questions"):
            self.assertIn(text, quick)

    def test_archive_path(self):
        md = self.ref("archive-and-diff.md")
        for text in ("docs/architecture/history/", "Superseded by", "Change Since Last Audit",
                     "never reused", "never moved"):
            self.assertIn(text, md)

    def test_adr_guide(self):
        md = self.ref("adr-guide.md")
        for text in ("templates/adr/0000-template.md", "docs/adr/", "Superseded by",
                     "no ADRs", "1 page"):
            self.assertIn(text, md)

    def test_mode_references_current(self):
        m1 = self.ref("mode-1-new-project.md")
        m2 = self.ref("mode-2-existing-project.md")
        for md in (m1, m2):
            self.assertNotIn("Exactly 3 files", md)
            self.assertNotIn("Table: issue, root cause, impact", md)
        for text in ("Capacity Estimates", "Data Model", "adr-guide.md", "max 5"):
            self.assertIn(text, m1)
        for text in ("Endpoint Inventory", "20 rows", "--scope", "signals",
                     "candidate_folders", "routes", "exactly one", "Action (IDs)"):
            self.assertIn(text, m2)
        self.assertNotIn("every Critical and High issue", m2)

    def test_refinement_rules_current(self):
        md = self.ref("refinement-rules.md")
        for text in ("Capacity Estimates", "Endpoint Inventory", "Change constraint",
                     "never archive"):
            self.assertIn(text, md)

    def test_non_interactive_marker_placement(self):
        self.assertIn("`> Context:` line in audit files", self.skill)
        self.assertIn("non-overlapping", self.ref("context-collection.md"))
        for md in (self.skill, self.ref("refinement-rules.md")):
            self.assertIn("pick the likelier option", md)

    def test_archive_diff_only_for_audits(self):
        md = self.ref("archive-and-diff.md")
        self.assertIn("Diff only when the archived run is an audit", md)
        self.assertIn("quick re-run after a quick audit", md)

    def test_mode2_reconciles_ids_before_assigning(self):
        md = self.ref("mode-2-existing-project.md")
        self.assertLess(md.index("**Archive + diff.**"), md.index("**Verify signals**"))

    def test_adr_guide_caps_at_highest_impact(self):
        self.assertIn("more than 5 rows", self.ref("adr-guide.md"))


class FinalReviewDocTests(unittest.TestCase):
    SEQUENCE_RULE = "Suggested Sequence order: (1) Critical issues, pain-point issues first among them"

    def test_archive_uses_exact_filenames(self):  # final review 7
        for text in (read(os.path.join(REFS, "archive-and-diff.md")), read(os.path.join(SKILL, "SKILL.md"))):
            self.assertNotIn("01-*.md", text)
        arch = read(os.path.join(REFS, "archive-and-diff.md"))
        for name in ("01-system-design.md", "02-architecture-decisions.md",
                     "03-api-design-and-optimization.md", "01-architecture-issues.md",
                     "02-api-optimization.md", "03-improvement-roadmap.md",
                     "04-tech-stack-comparison.md", "quick-design.md", "quick-audit.md"):
            self.assertIn("`%s`" % name, arch)

    def test_suggested_sequence_rule_stated_once(self):  # final review 6
        docs = {name: read(os.path.join(REFS, name)) for name in os.listdir(REFS)}
        docs["roadmap template"] = read(os.path.join(TEMPLATES, "mode-2", "03-improvement-roadmap.md"))
        holders = [name for name, text in docs.items() if self.SEQUENCE_RULE in text]
        self.assertEqual(holders, ["formatting-rules.md"])
        for name, text in docs.items():
            self.assertNotIn("weakest category's issues", text, name)

    def test_placement_line(self):  # final review 18
        line = "Placement: S -> Quick Wins, M -> Medium-term, L/XL -> Long-term."
        self.assertIn(line, read(os.path.join(REFS, "formatting-rules.md")))
        self.assertIn(line, read(os.path.join(TEMPLATES, "mode-2", "03-improvement-roadmap.md")))

    def test_capacity_template_starts_with_daily_requests(self):  # final review 20
        cap = section(read(os.path.join(TEMPLATES, "mode-1", "01-system-design.md")), "Capacity Estimates")
        rows = [ln for ln in cap.splitlines() if ln.startswith("| ") and not ln.startswith("| Metric")]
        self.assertTrue(rows[0].startswith("| Daily requests |"), rows[0])

    def test_folders_only_documented_for_scope_discovery(self):  # final review 3
        for path in (os.path.join(SKILL, "SKILL.md"), os.path.join(REFS, "mode-2-existing-project.md"),
                     os.path.join(REFS, "context-collection.md"), os.path.join(REPO, "README.md")):
            self.assertIn("--folders-only", read(path), path)

if __name__ == "__main__":
    unittest.main()
