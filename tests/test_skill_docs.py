from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from typing import Dict, List, Optional, Sequence

from helpers import (REPO, expected_headers, header_mismatch, headings,
                     overall_band, overall_score, rerun_only_headers, subscore)

import lint_output  # noqa: E402  (helpers puts scripts/ on sys.path)
import rubric  # noqa: E402

SKILL = os.path.join(REPO, "skills", "design-and-fix")
REFS = os.path.join(SKILL, "references")
TEMPLATES = os.path.join(SKILL, "templates")
ADR_KEY = "NNNN-<kebab-title>.md"
LEGACY = "1.0.1/"
ADR_EXTRAS = ("Alternatives", "Assumptions")
ISSUE_HEADER = "| ID | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |"
COMBINED_HEADER = ("| ID | Severity | Status | Source | Issue | Evidence | Root cause | Consequence "
                   "| Fix | Verify | Why |")
QUICK_HEADER = "| ID | Severity | Status | Source | Issue | Evidence | Fix | Verify | Why |"
FORMAT_LINE = "> Format: 1.0.2"
AUDIT_LINE = ("> Audit: plugin 1.0.2 · scanner schema 3 · rev <sha|unknown> "
              "· config <config_hash> · <timestamp>")


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
            "Capacity Estimates", "Key Architectural Decisions", "Requirements → Decisions",
            "Assumptions & Constraints"])
        self.assertIn("Security & Threat Model", exp["02-architecture-decisions.md"])
        self.assertNotIn("Security", exp["02-architecture-decisions.md"])
        # Format 1.0.1 header rows stay available for archived audits and older examples.
        self.assertEqual(exp[LEGACY + "01-system-design.md"], [
            "Overview", "System Context", "Core Components", "Data Flow",
            "Capacity Estimates", "Key Architectural Decisions", "Assumptions & Constraints"])
        self.assertIn("Security", exp[LEGACY + "02-architecture-decisions.md"])
        self.assertEqual(exp[ADR_KEY], ["Context", "Decision", "Consequences"])

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
                heads = headings(read(path))
                if key == ADR_KEY:  # 1.0.2 ADR extras, checked in test_adr_template
                    heads = [h for h in heads if h not in ADR_EXTRAS]
                problem = header_mismatch(heads, exp[key], opt[key])
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
        self.assertIn("\nSupersedes: NNNN", md)
        self.assertIn(FORMAT_LINE, md)
        self.assertEqual(headings(md),
                         ["Context", "Alternatives", "Decision", "Consequences", "Assumptions"])

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
        self.assertIn(ISSUE_HEADER, audit)
        self.assertIn(COMBINED_HEADER, audit)
        self.assertIn(COMBINED_HEADER, read(os.path.join(m2, "02-api-optimization.md")))
        self.assertIn(QUICK_HEADER, read(os.path.join(TEMPLATES, "quick", "quick-audit.md")))

    def test_roadmap_tables_cite_ids(self):
        md = read(os.path.join(TEMPLATES, "mode-2", "03-improvement-roadmap.md"))
        rows = table_header_rows(md)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row.strip(), "| Action (IDs) | Effort | Impact | Why |")

    def test_formatting_rules_contract(self):
        for text in (ISSUE_HEADER, COMBINED_HEADER, QUICK_HEADER,
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


# --- 1.0.2 content contract (Task 12) -----------------------------------------------

MODE1_DOCS = (os.path.join(REFS, "mode-1-new-project.md"),
              os.path.join(TEMPLATES, "mode-1", "01-system-design.md"),
              os.path.join(TEMPLATES, "mode-1", "02-architecture-decisions.md"),
              os.path.join(TEMPLATES, "mode-1", "03-api-design-and-optimization.md"))


def header_block(md: str) -> List[str]:
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            return lines[:i]
    return lines


def parse_weights_table(md: str) -> Dict[str, Dict[str, float]]:
    """`| `keyword` | Category W, Category W |` rows of the rubric's context-weights table."""
    out = {}  # type: Dict[str, Dict[str, float]]
    for line in md.splitlines():
        m = re.match(r"^\| `([^`]+)` \| ([A-Za-z ]+ \d+\.\d+(?:, [A-Za-z ]+ \d+\.\d+)*) \|$",
                     line.strip())
        if m:
            raised = {}
            for part in m.group(2).split(", "):
                cat, w = part.rsplit(" ", 1)
                raised[cat] = float(w)
            out[m.group(1)] = raised
    return out


def template_table_header(md: str, heading: str, index: int = 0) -> List[str]:
    rows = table_header_rows(section(md, heading))
    return [c.strip() for c in rows[index].strip().strip("|").split("|")]


def fill_template(template: str, head: Sequence[str], bodies: Dict[str, list],
                  titles: Optional[Dict[str, str]] = None, skip: Sequence[str] = ()) -> str:
    """Build an output file from a template's headings and table columns.

    `bodies` maps a template heading to a list of lines and ("table", [row dicts]) items;
    rows are laid out in the template's column order; a template column the row dict does
    not fill raises KeyError (extra keys such as Severity in a Critical table are ignored)."""
    md = read(template)
    out = list(head) + [""]
    titles = titles or {}
    for heading in headings(md):
        if heading in skip:
            continue
        # Keys may name a heading by prefix ("Architecture Health Score").
        key = heading if heading in bodies or heading in titles else next(
            (k for k in list(bodies) + list(titles) if heading.startswith(k)), heading)
        out += ["## " + titles.get(key, heading), ""]
        for item in bodies.get(key, []):
            if isinstance(item, tuple):
                cols = template_table_header(md, heading)
                for rec in item[1]:
                    for col in cols:
                        if col not in rec:
                            raise KeyError("%s: test did not fill column %r" % (heading, col))
                out.append("| " + " | ".join(cols) + " |")
                out.append("|" + "---|" * len(cols))
                for rec in item[1]:
                    out.append("| " + " | ".join(rec[c] for c in cols) + " |")
            else:
                out.append(item)
        out.append("")
    return "\n".join(out)


def issue(id_: str, severity: str, rc: str = "own cause") -> Dict[str, str]:
    return {"ID": id_, "Severity": severity, "Status": "confirmed", "Source": "scanner+agent",
            "Issue": "problem", "Evidence": "services/api/app.py:10-12, services/api/db.py:3",
            "Root cause": rc, "Consequence": "bad outcome", "Fix": "change it",
            "Verify": "test proves it", "Why": "reason"}


class FormatOneZeroTwoDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = read(os.path.join(REFS, "formatting-rules.md"))
        cls.rubric = read(os.path.join(REFS, "health-score-rubric.md"))
        cls.skill = read(os.path.join(SKILL, "SKILL.md"))
        cls.m1 = read(os.path.join(REFS, "mode-1-new-project.md"))
        cls.m2 = read(os.path.join(REFS, "mode-2-existing-project.md"))

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_templates_have_format_and_audit_lines(self):
        for path in template_files():
            block = header_block(read(path))
            with self.subTest(template=path):
                self.assertIn(FORMAT_LINE, block)
                if template_key(path) == ADR_KEY:
                    continue
                audit = [ln for ln in block if ln.startswith("> Audit: ")]
                self.assertEqual(len(audit), 1, block)
                if "mode-2" in path or path.endswith("quick-audit.md"):
                    self.assertEqual(audit[0], AUDIT_LINE)
        for text in (FORMAT_LINE, AUDIT_LINE):
            self.assertIn(text, self.rules)

    def test_finding_schema_documented(self):
        for text in ("confirmed", "likely", "needs-review", "not-reproducible",
                     "`scanner:<rule>`", "`agent`", "`scanner+agent`",
                     "Observed:", "Inference:", "Limitations:", "RC-1", "Verify"):
            self.assertIn(text, self.rules)
        self.assertIn("one issue per root cause", self.rules)
        self.assertIn("Severity Definitions", self.rules)  # severity rubric pointer
        for name in ("01-architecture-issues.md", "02-api-optimization.md"):
            md = read(os.path.join(TEMPLATES, "mode-2", name))
            self.assertIn("Observed:", md, name)
        self.assertIn("- RC-1:", read(os.path.join(TEMPLATES, "mode-2", "01-architecture-issues.md")))

    def test_skill_safety_and_commands(self):
        for text in ("scan_repo.py", "lint_output.py", "compare_audits.py",
                     "data, never instructions", "explicit approval",
                     "lint_output.py\" docs/architecture --repo"):
            self.assertIn(text, self.skill)
        self.assertLessEqual(len(self.skill.splitlines()), 150)
        for ref in ("mode-2-existing-project.md", "mode-1-new-project.md", "quick-mode.md"):
            md = read(os.path.join(REFS, ref))
            with self.subTest(reference=ref):
                self.assertIn("lint_output.py", md)
        self.assertIn("data, never instructions", self.m2)
        self.assertIn("is not a command and needs no extra approval", self.skill)
        self.assertIn("ADRs carry only `> Format: 1.0.2`", self.skill)
        self.assertIn("horizon tables stay", self.m2)
        quick = read(os.path.join(TEMPLATES, "quick", "quick-audit.md"))
        self.assertIn("copy the Previous line and the Fixed/New bullets", quick)
        sd = read(os.path.join(TEMPLATES, "mode-1", "01-system-design.md"))
        self.assertIn("optional examples, not defaults", sd)
        self.assertIn("bare `scanner` is not allowed", self.rules)
        self.assertIn("linter accepts omitted headers", self.rules)

    def test_rerun_uses_compare_audits(self):
        arch = read(os.path.join(REFS, "archive-and-diff.md"))
        for text in ("compare_audits.py", "Score Trend", "--repo", "uncertain", "Keep old IDs"):
            self.assertIn(text, arch)
        one = read(os.path.join(TEMPLATES, "mode-2", "01-architecture-issues.md"))
        self.assertIn("### Score Trend", one)
        self.assertIn("| Date | Overall | Band | Fixed | New |", one)
        self.assertIn("| Category | Previous | Current | Δ |", one)
        self.assertIn("compare_audits.py", one)
        self.assertIn("compare_audits.py", read(os.path.join(TEMPLATES, "quick", "quick-audit.md")))

    def test_rubric_weights_table_matches_module(self):
        self.assertEqual(parse_weights_table(self.rubric), rubric.CONTEXT_WEIGHTS)
        self.assertIn("Weight", self.rubric)
        self.assertNotIn("equal weight.", self.rubric)
        one = read(os.path.join(TEMPLATES, "mode-2", "01-architecture-issues.md"))
        self.assertIn("| Category | Score | Band | Weight | Main driver (IDs) |", one)

    def test_recommendations_contract(self):
        for text in ("correctness", "security", "maintainability", "performance", "cost",
                     "Hypothesis", "Risk", "smallest change"):
            self.assertIn(text, self.m2)
        self.assertIn("value × confidence", self.rules)
        api = read(os.path.join(TEMPLATES, "mode-2", "02-api-optimization.md"))
        for col in ("Category", "Impact", "Effort", "Risk", "Verification", "Why"):
            self.assertIn(col, template_table_header(api, "Quick Wins vs Structural Fixes"))
        self.assertIn("Hypothesis", api)

    def test_api_review_checklist(self):
        sec = section(self.m2, "API Review Checklist")
        for rule in ("list-route-no-pagination", "mixed-error-shapes", "no-version-prefix",
                     "unvalidated-body", "openapi-drift"):
            self.assertIn("`%s`" % rule, sec)
        for topic in ("Consistency", "Validation", "Error format", "Pagination", "Auth boundaries",
                      "Versioning", "Backward compatibility"):
            self.assertIn(topic, sec)
        for rule in ("docker-root-user", "docker-latest-tag", "docker-no-healthcheck",
                     "k8s-no-probes", "k8s-no-limits", "ci-no-test-step", "missing-lockfile",
                     "unpinned-dependency"):
            self.assertIn("`%s`" % rule, self.m2)
        for flag in ("--no-gitignore", "--config", "--fail-on", "sarif"):
            self.assertIn(flag, self.m2)
        for field in ("fingerprint", "rule_version", "confidence"):
            self.assertIn(field, self.m2)

    def test_mode1_improvements(self):
        ctx = read(os.path.join(REFS, "context-collection.md"))
        for text in ("Workload", "Deployment environment", "Budget", "Team size",
                     "Data sensitivity", "Latency", "Availability", "Operational constraints",
                     "max 6 questions"):
            self.assertIn(text, ctx)
        sd = read(os.path.join(TEMPLATES, "mode-1", "01-system-design.md"))
        self.assertIn("Required now / Later", template_table_header(sd, "Core Components"))
        self.assertEqual(template_table_header(sd, "Requirements → Decisions"),
                         ["Requirement", "Decision(s)", "How it is met"])
        self.assertIn("Option", template_table_header(sd, "Key Architectural Decisions", 1))
        for text in ("2–3 options", "Proportionality", "Required now / Later",
                     "Requirements → Decisions"):
            self.assertIn(text, self.m1)
        self.assertIn("Proportionality", self.rules)
        adr = read(os.path.join(REFS, "adr-guide.md"))
        for text in ("Alternatives", "Assumptions", "Supersedes: NNNN"):
            self.assertIn(text, adr)

    def test_security_threat_model_everywhere(self):
        ad = read(os.path.join(TEMPLATES, "mode-1", "02-architecture-decisions.md"))
        body = section(ad, "Security & Threat Model")
        self.assertIn("STRIDE", body)
        self.assertIn("Trust boundar", body)
        for path in MODE1_DOCS + (os.path.join(REFS, "refinement-rules.md"),):
            with self.subTest(doc=os.path.basename(path)):
                md = read(path)
                self.assertNotIn("| Security |", md)
                self.assertNotIn("## Security\n", md)
        self.assertIn("Security & Threat Model", self.m1)
        self.assertIn("Security & Threat Model", read(os.path.join(REFS, "refinement-rules.md")))

    # -- filled templates lint clean ------------------------------------------------

    def lint_dir(self, files: Dict[str, str]) -> List[str]:
        folder = tempfile.mkdtemp(dir=self.tmp)
        for name, text in files.items():
            path = os.path.join(folder, *name.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return ["%s:%d: %s" % (f.file, f.line, f.message) for f in lint_output.lint(folder)]

    def mode2_files(self, context: str, weights: Dict[str, str], headline: str) -> Dict[str, str]:
        m2 = os.path.join(TEMPLATES, "mode-2")
        head = ["# T", "", "> Scope: services/api", "> Context: " + context, FORMAT_LINE,
                "> Audit: plugin 1.0.2 · scanner schema 3 · rev unknown · 2026-11-02T10:00Z"]
        scores = {"System Design": ("10", "Strong", "—"), "API Design": ("6", "Mixed", "API-001"),
                  "Scalability": ("10", "Strong", "—"), "Reliability": ("6", "Mixed", "REL-001"),
                  "Security": ("3", "Weak", "SEC-001"), "Observability": ("10", "Strong", "—"),
                  "Maintainability": ("8", "Good", "MNT-001")}
        score_rows = [{"Category": c, "Score": s, "Band": b, "Weight": weights.get(c, "1.0"),
                       "Main driver (IDs)": d} for c, (s, b, d) in scores.items()]
        one = fill_template(os.path.join(m2, "01-architecture-issues.md"), head, {
            "Architecture Health Score": ["Evidence: 4 files read, 3 scanner signals reviewed, "
                                          "2 confirmed", ("table", score_rows)],
            "Critical Issues": [("table", [dict(issue("SEC-001", "Critical"),
                                                Source="scanner:hardcoded-secret")]),
                                "- SEC-001: Observed: a. Inference: b. Limitations: none. "
                                "Root cause: RC-1"],
            "High Issues": [("table", [issue("REL-001", "High", "RC-1")]),
                            "- REL-001: Observed: a. Inference: b. Limitations: none."],
            "Medium / Low Issues": [("table", [issue("MNT-001", "Medium")]),
                                    "- RC-1: one HTTP client built without defaults "
                                    "(SEC-001, REL-001)"],
        }, titles={"Architecture Health Score": "Architecture Health Score: " + headline},
            skip=("Change Since Last Audit",))
        rec = {"Recommendation (IDs)": "API-001: add limit", "Category": "performance",
               "Impact": "High", "Effort": "S", "Risk": "Low",
               "Verification": "Hypothesis: time GET /orders p95 before/after", "Why": "r"}
        two = fill_template(os.path.join(m2, "02-api-optimization.md"), head, {
            "Issues Found": [("table", [issue("API-001", "High")]),
                             "- API-001: Observed: a. Inference: b. Limitations: none."],
            "Quick Wins vs Structural Fixes": [("table", [rec])],
        })

        def road(ids: str, effort: str) -> Dict[str, str]:
            return {"Action (IDs)": ids + ": act", "Effort": effort, "Impact": "High", "Why": "r"}

        three = fill_template(os.path.join(m2, "03-improvement-roadmap.md"), head, {
            "Quick Wins (days)": [("table", [road("SEC-001", "S")])],
            "Medium-term (weeks)": [("table", [road("REL-001, API-001", "M")])],
            "Long-term (months/quarters)": [("table", [road("MNT-001", "L")])],
            "Suggested Sequence": ["1. SEC-001: act. Why: r"],
        })
        return {"01-architecture-issues.md": one, "02-api-optimization.md": two,
                "03-improvement-roadmap.md": three}

    def test_filled_mode2_templates_lint_clean(self):
        self.assertEqual(self.lint_dir(self.mode2_files(
            "1k users, team of 2", {}, "Stable with risks (7.6 / 10)")), [])
        # A Context keyword raises Security to 2.0: (10+6+10+6+3*2+10+8) / 8 = 7.0
        self.assertEqual(self.lint_dir(self.mode2_files(
            "payments service, 1k users", {"Security": "2.0"}, "Stable with risks (7.0 / 10)")), [])

    def test_filled_mode2_template_missing_verify_is_flagged(self):
        files = self.mode2_files("1k users", {}, "Stable with risks (7.6 / 10)")
        files["01-architecture-issues.md"] = files["01-architecture-issues.md"].replace(
            "| test proves it |", "|  |", 1)
        self.assertTrue(any("Verify" in f for f in self.lint_dir(files)))

    def test_linter_uses_legacy_headers_only_for_101_files(self):
        old = ("Pattern Choice, Scaling Strategy, Data Storage, Data Model, Failure Modes, "
               "Security, Observability, Deployment & Cost, Trade-offs Accepted").split(", ")
        new = [h if h != "Security" else "Security & Threat Model" for h in old]

        def doc(heads: List[str], fmt: bool) -> Dict[str, str]:
            audit = "> Audit: plugin 1.0.2 · scanner not run · rev unknown"
            head = ["# A", "", "> Scope: s", "> Context: c"] + ([FORMAT_LINE, audit] if fmt else [])
            return {"02-architecture-decisions.md":
                    "\n".join(head + [""] + ["## %s\n\nx\n" % h for h in heads])}

        self.assertEqual(self.lint_dir(doc(old, fmt=False)), [])
        self.assertEqual(self.lint_dir(doc(new, fmt=True)), [])
        self.assertTrue(any("standard headers" in f for f in self.lint_dir(doc(old, fmt=True))))
        self.assertTrue(any("standard headers" in f for f in self.lint_dir(doc(new, fmt=False))))

    def test_linter_source_vocabulary(self):
        files = self.mode2_files("1k users", {}, "Stable with risks (7.6 / 10)")
        one = files["01-architecture-issues.md"]
        for good in ("scanner:http-no-timeout", "scanner+agent:sql-string-concat", "agent"):
            files["01-architecture-issues.md"] = one.replace("| scanner:hardcoded-secret |",
                                                             "| %s |" % good)
            self.assertEqual(self.lint_dir(files), [], good)
        for bad in ("tool", "scanner", "scanner (http-no-timeout)"):
            files["01-architecture-issues.md"] = one.replace("| scanner:hardcoded-secret |",
                                                             "| %s |" % bad)
            self.assertTrue(any("source %r" % bad in f for f in self.lint_dir(files)), bad)

    def test_linter_allows_omitted_sections_but_not_wrong_order(self):
        def cut(text: str, start: str, end: str = "") -> str:
            i = text.index(start)
            return text[:i] + (text[text.index(end, i):] if end else "")

        files = self.mode2_files("1k users", {}, "Stable with risks (7.9 / 10)")
        one = cut(files["01-architecture-issues.md"], "## Medium / Low Issues")
        one += "- RC-1: one HTTP client built without defaults (SEC-001, REL-001)\n"
        one = one.replace("| Maintainability | 8 | Good | 1.0 | MNT-001 |",
                          "| Maintainability | 10 | Strong | 1.0 | — |")
        files["01-architecture-issues.md"] = one
        files["02-api-optimization.md"] = cut(files["02-api-optimization.md"],
                                              "## Caching Opportunities",
                                              "## Quick Wins vs Structural Fixes")
        files["03-improvement-roadmap.md"] = cut(files["03-improvement-roadmap.md"],
                                                 "## Long-term (months/quarters)",
                                                 "## Suggested Sequence")
        self.assertEqual(self.lint_dir(files), [])
        swapped = (one.replace("## Critical Issues", "## TMP").replace("## High Issues",
                   "## Critical Issues").replace("## TMP", "## High Issues"))
        files["01-architecture-issues.md"] = swapped
        self.assertTrue(any("out of order" in f for f in self.lint_dir(files)))
        files["01-architecture-issues.md"] = one.replace("## High Issues", "## Major Issues")
        self.assertTrue(any("unknown header 'Major Issues'" in f for f in self.lint_dir(files)))

    def test_linter_required_headers(self):
        self.assertEqual(lint_output.load_required(), {
            "01-architecture-issues.md": ["Architecture Health Score"],
            "02-api-optimization.md": ["Issues Found"],
            "03-improvement-roadmap.md": ["Suggested Sequence"],
            "quick-audit.md": ["Health Band"],
            "01-system-design.md": ["Overview", "Assumptions & Constraints"],
            "quick-design.md": ["Overview", "Assumptions & Constraints"]})
        files = self.mode2_files("1k users", {}, "Stable with risks (7.6 / 10)")
        one = files["01-architecture-issues.md"]
        i, j = one.index("## Architecture Health Score"), one.index("## Critical Issues")
        files["01-architecture-issues.md"] = one[:i] + one[j:]
        self.assertTrue(any("required header 'Architecture Health Score' is missing" in f
                            for f in self.lint_dir(files)))
        road = files["03-improvement-roadmap.md"]
        files["01-architecture-issues.md"] = one
        files["03-improvement-roadmap.md"] = road[:road.index("## Suggested Sequence")]
        self.assertTrue(any("required header 'Suggested Sequence' is missing" in f
                            for f in self.lint_dir(files)))

    def test_filled_quick_audit_template_lint_clean(self):
        head = ["# Q", "", "> Scope: services/api", "> Context: 1k users", FORMAT_LINE,
                "> Audit: plugin 1.0.2 · scanner schema 3 · rev abc123 · 2026-11-02T10:00Z"]
        md = fill_template(os.path.join(TEMPLATES, "quick", "quick-audit.md"), head, {
            "Health Band": ["**Fragile (4.5 / 10)**: structural fixes needed before growth."],
            "Top Issues": [("table", [issue("SEC-001", "Critical"), issue("REL-001", "High")]),
                           "- SEC-001: Observed: a. Inference: b. Limitations: none.",
                           "- REL-001: Observed: a. Inference: b. Limitations: none."],
            "Quick Wins": [("table", [{"Action (IDs)": "SEC-001: act", "Effort": "S",
                                        "Impact": "High", "Why": "r"}])],
            "Next Steps": ["1. REL-001: act. Why: r"],
        }, skip=("Change Since Last Audit",))
        self.assertEqual(self.lint_dir({"quick-audit.md": md}), [])

    def test_filled_mode1_templates_and_adrs_lint_clean(self):
        m1 = os.path.join(TEMPLATES, "mode-1")
        head = ["# D", "", "> Scope: shop", "> Context: 1k users, team of 2", FORMAT_LINE,
                "> Audit: plugin 1.0.2 · scanner not run · rev unknown · 2026-11-02T10:00Z"]
        one = fill_template(os.path.join(m1, "01-system-design.md"), head, {
            "Core Components": [("table", [{"Component": "API", "Responsibility": "r",
                                            "Required now / Later": "Required now",
                                            "Why it exists": "w"}])],
            "Key Architectural Decisions": [("table", [{
                "Decision": "Pattern", "Chosen": "Modular monolith",
                "Alternative considered": "Microservices", "Why": "w"}])],
            "Requirements → Decisions": [("table", [{
                "Requirement": "p95 < 300 ms", "Decision(s)": "Pattern", "How it is met": "h"}])],
        })
        two = fill_template(os.path.join(m1, "02-architecture-decisions.md"), head, {})
        three = fill_template(os.path.join(m1, "03-api-design-and-optimization.md"), head, {})

        def adr_head(n: str, status: str, extra: List[str]) -> List[str]:
            return (["# %s. Decision %s" % (n, n), "", "Date: 2026-11-02", "Status: " + status]
                    + extra + [FORMAT_LINE])

        adr_t = os.path.join(TEMPLATES, "adr", "0000-template.md")
        files = {"01-system-design.md": one, "02-architecture-decisions.md": two,
                 "03-api-design-and-optimization.md": three,
                 "adr/0001-a.md": fill_template(adr_t, adr_head("0001", "Superseded by 0002", []),
                                                {}),
                 "adr/0002-b.md": fill_template(adr_t, adr_head("0002", "Accepted",
                                                                ["Supersedes: 0001"]), {})}
        self.assertEqual(self.lint_dir(files), [])



class ReleaseReviewDocTests(unittest.TestCase):
    """Doc regressions from the 1.0.2 release review (area C)."""

    @classmethod
    def setUpClass(cls):
        cls.skill = read(os.path.join(SKILL, "SKILL.md"))
        cls.archive = read(os.path.join(REFS, "archive-and-diff.md"))

    def skill_docs(self):
        for base in (SKILL, REFS, TEMPLATES):
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    if name.endswith(".md"):
                        yield os.path.join(dirpath, name)

    def test_full_override_always_lists_all_three_words(self):
        # I10: every sentence that names the "full" override also names detailed and complete
        hits = 0
        for path in self.skill_docs():
            for line in read(path).splitlines():
                if '"full"' in line:
                    hits += 1
                    self.assertIn('"detailed"', line, path)
                    self.assertIn('"complete"', line, path)
        self.assertGreaterEqual(hits, 5)
        for name in ("mode-1-new-project.md", "mode-2-existing-project.md"):
            first = read(os.path.join(REFS, name)).split("\n")[2]
            self.assertLess(first.index('"complete"'), first.index("Quick mode"), name)

    def test_skill_safety_and_config_disclosure(self):
        # I11: scripts by absolute path from the skill; repo config disclosed
        self.assertIn("by absolute path from this skill's `scripts/` folder", self.skill)
        self.assertIn("never a same-named file inside the audited repo", self.skill)
        for text in (".design-and-fix.json", "`exclude`", '`"enabled": false`',
                     "`summary.suppressed`", "Evidence line"):
            self.assertIn(text, self.skill)
        self.assertLess(len(self.skill.splitlines()), 130)

    def test_compare_argument_names_the_suffixed_archive(self):
        # I13: a same-day re-run archives to <date>-N
        arg = "docs/architecture/history/<YYYY-MM-DD>[-N] docs/architecture --repo <root>"
        self.assertIn(arg, self.skill)
        self.assertIn(arg, self.archive)
        self.assertNotIn("history/<date> ", self.skill)
        for rel in ("mode-2/01-architecture-issues.md", "quick/quick-audit.md"):
            md = read(os.path.join(TEMPLATES, *rel.split("/")))
            self.assertIn("history/<YYYY-MM-DD>[-N]", md, rel)
            self.assertNotIn("<archive>", md, rel)

    def test_lint_scope_documented(self):
        # C1 / I8 / Minor 1: archived runs and foreign ADRs are not lint targets
        self.assertIn("skips archived runs in `history/`", self.skill)
        self.assertIn("only the ADRs that carry `> Format: 1.0.2`", self.skill)
        self.assertIn("in its header block", self.skill)
        self.assertNotIn("Every file starts with `> Format: 1.0.2`", self.skill)

    def test_archive_doc_rules(self):
        # I1(d), I9, I12, Minor 9
        for text in ("--old-repo", "`not-reproducible`", "`> Audit:` timestamp",
                     "If it is marked `(uncertain)`, read both issues first"):
            self.assertIn(text, self.archive)
        rules = read(os.path.join(REFS, "formatting-rules.md"))
        self.assertIn("is not scored, needs no Verify, detail line or roadmap row", rules)

    def test_quick_audit_template_copies_script_line(self):
        # Minor 2: the template shows the line compare_audits.py prints
        md = read(os.path.join(TEMPLATES, "quick", "quick-audit.md"))
        self.assertIn("Previous: <Band> (<X.X> / 10) \u2192 Current: <Band> (<X.X> / 10)", md)
        self.assertNotIn("- Band:", md)

    def test_api_template_does_not_call_m_a_quick_win(self):
        # Minor 5: roadmap Quick Wins are S only
        for path in self.skill_docs():
            self.assertNotIn("Quick wins (S/M", read(path), path)
        for name in ("mode-2-audit-monorepo", "mode-2-audit-express-boilerplate"):
            md = read(os.path.join(REPO, "examples", name, "02-api-optimization.md"))
            self.assertNotIn("Quick wins (S/M", md, name)

    def test_rubric_doc_negation_and_weight_column(self):
        # I4 / Minor 3: negation documented; Weight column optional when all weights are 1.0
        rub = read(os.path.join(REFS, "health-score-rubric.md"))
        self.assertIn("A negated keyword does not count", rub)
        self.assertNotIn("always carries a `Weight` column", rub)
        self.assertEqual(rubric.NEGATIONS, ("no", "not", "non", "without", "free of"))
        for word in rubric.NEGATIONS:
            self.assertIn("`%s`" % word, rub)


if __name__ == "__main__":
    unittest.main()
