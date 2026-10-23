from __future__ import annotations

import os
import re
import unittest
from typing import Dict, List, Tuple

from helpers import (FIXTURES, REPO, expected_headers, header_mismatch, headings,
                     overall_band, overall_score, rerun_only_headers, subscore)

EXAMPLES = os.path.join(REPO, "examples")
EVALS = os.path.join(REPO, "evals")
MODE2 = os.path.join(EXAMPLES, "mode-2-audit-monorepo")
MODE1 = os.path.join(EXAMPLES, "mode-1-food-delivery")
MONOREPO = os.path.join(FIXTURES, "monorepo")
RULES = os.path.join(REPO, "skills", "design-and-fix", "references", "formatting-rules.md")
ADR_KEY = "NNNN-<kebab-title>.md"

MODE2_FILES = ["01-architecture-issues.md", "02-api-optimization.md", "03-improvement-roadmap.md"]
MODE1_FILES = ["01-system-design.md", "02-architecture-decisions.md",
               "03-api-design-and-optimization.md"]
SKILL_CASES = ["01-audit-monorepo.md", "02-quick-question.md", "03-non-interactive.md",
               "04-design-food-delivery.md", "05-rerun-diff.md"]

CATEGORY_PREFIX = {
    "System Design": "SYS", "API Design": "API", "Scalability": "SCL", "Reliability": "REL",
    "Security": "SEC", "Observability": "OBS", "Maintainability": "MNT",
}
SUBSCORE_BAND = {10: "Strong", 9: "Strong", 8: "Good", 7: "Good", 6: "Mixed", 5: "Mixed",
                 4: "Weak", 3: "Weak", 2: "Severe", 1: "Severe"}
HORIZONS = ("Quick Wins", "Medium-term", "Long-term")
ID_RE = re.compile(r"\b(?:SYS|API|SCL|REL|SEC|OBS|MNT)-\d{3}\b")
EVIDENCE_RE = re.compile(r"^([A-Za-z0-9_./-]+):(\d+)$")


def read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def section(md: str, heading: str, exact: bool = False) -> str:
    """Body of the first '## ' section whose heading starts with (or, if exact, equals) heading."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        text = line.rstrip()
        if (text == "## " + heading) if exact else text.startswith("## " + heading):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def tables(md: str) -> List[Tuple[List[str], List[List[str]]]]:
    """Every Markdown table in md as (header cells, data rows), separator row dropped."""
    out = []
    block = []  # type: List[str]
    for line in md.splitlines() + [""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if len(block) >= 2:
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in block]
            out.append((rows[0], rows[2:]))
        block = []
    return out


def issue_rows(md: str, heading_prefix: str, severity=None) -> List[Dict[str, str]]:
    """Rows of the issue tables in a section, as dicts; `severity` fills a missing column."""
    out = []
    for header, rows in tables(section(md, heading_prefix)):
        if not header or header[0] != "ID":
            continue
        for row in rows:
            rec = dict(zip(header, row))
            if severity and "Severity" not in rec:
                rec["Severity"] = severity
            out.append(rec)
    return out


def mode2_issues() -> Dict[str, List[Dict[str, str]]]:
    """Issues defined per Mode 2 file: {"01": [...], "02": [...]}."""
    one = read(os.path.join(MODE2, MODE2_FILES[0]))
    two = read(os.path.join(MODE2, MODE2_FILES[1]))
    return {
        "01": (issue_rows(one, "Critical Issues", "Critical")
               + issue_rows(one, "High Issues", "High")
               + issue_rows(one, "Medium / Low Issues")),
        "02": issue_rows(two, "Issues Found"),
    }


class ExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = read(RULES)

    def test_example_headers(self):
        exp = expected_headers(self.rules)
        opt = rerun_only_headers(self.rules)
        for folder, names in ((MODE2, MODE2_FILES), (MODE1, MODE1_FILES)):
            for name in names:
                md = read(os.path.join(folder, name))
                problem = header_mismatch(headings(md), exp[name], opt.get(name, ()))
                self.assertIsNone(problem, "%s: %s" % (name, problem))
                self.assertIn("> Scope:", md, name)
                self.assertIn("> Context:", md, name)
        adr_dir = os.path.join(MODE1, "adr")
        adrs = sorted(f for f in os.listdir(adr_dir) if f.endswith(".md"))
        self.assertTrue(1 <= len(adrs) <= 5, adrs)
        for i, name in enumerate(adrs, start=1):
            self.assertRegex(name, r"^%04d-[a-z0-9]+(-[a-z0-9]+)*\.md$" % i)
            md = read(os.path.join(adr_dir, name))
            self.assertIsNone(header_mismatch(headings(md), exp[ADR_KEY]), name)
            self.assertTrue(md.startswith("# %04d. " % i), name)
            self.assertRegex(md, r"\nDate: \d{4}-\d{2}-\d{2}\n")
            self.assertRegex(md, r"\nStatus: (Accepted|Superseded by \d{4})\n")
        decisions = tables(section(read(os.path.join(MODE1, MODE1_FILES[0])),
                                   "Key Architectural Decisions"))
        self.assertEqual(len(decisions[0][1]), len(adrs))

    def test_example_ids_in_roadmap(self):
        issues = mode2_issues()
        for rec in issues["01"]:
            self.assertFalse(rec["ID"].startswith("API-"), rec["ID"])
        for rec in issues["02"]:
            self.assertTrue(rec["ID"].startswith("API-"), rec["ID"])
        defined = [rec["ID"] for recs in issues.values() for rec in recs]
        self.assertTrue(defined)
        for d in defined:
            self.assertRegex(d, r"^%s$" % ID_RE.pattern)
        self.assertEqual(len(defined), len(set(defined)), "an ID is defined twice")

        roadmap = read(os.path.join(MODE2, MODE2_FILES[2]))
        cited = []
        for horizon in HORIZONS:
            for header, rows in tables(section(roadmap, horizon)):
                self.assertEqual(header, ["Action (IDs)", "Effort", "Impact", "Why"])
                for row in rows:
                    ids_part = row[0].split(":", 1)[0]
                    ids = ID_RE.findall(ids_part)
                    self.assertTrue(ids, "Action cell must start with IDs: %r" % row[0])
                    self.assertIn(row[1], ("S", "M", "L", "XL"))
                    self.assertIn(row[2], ("Low", "Medium", "High"))
                    cited.extend(ids)
        for d in defined:
            self.assertEqual(cited.count(d), 1, "%s in %d horizon rows" % (d, cited.count(d)))
        self.assertEqual(set(cited) - set(defined), set(), "roadmap cites undefined IDs")

    def test_example_evidence_exists(self):
        checked = 0
        for recs in mode2_issues().values():
            for rec in recs:
                cell = rec["Evidence"]
                tokens = [t.strip().strip("`") for t in re.split(r",\s*|<br>", cell) if t.strip()]
                self.assertTrue(tokens, rec["ID"])
                for tok in tokens:
                    if tok == "Not verified":
                        continue
                    m = EVIDENCE_RE.match(tok)
                    self.assertIsNotNone(m, "%s: bad evidence %r" % (rec["ID"], tok))
                    path = os.path.join(MONOREPO, *m.group(1).split("/"))
                    self.assertTrue(os.path.isfile(path), "%s: %s missing" % (rec["ID"], tok))
                    with open(path, encoding="utf-8") as fh:
                        length = len(fh.read().splitlines())
                    self.assertTrue(1 <= int(m.group(2)) <= length,
                                    "%s: %s beyond %d lines" % (rec["ID"], tok, length))
                    checked += 1
                self.assertIn(rec["Severity"], ("Critical", "High", "Medium", "Low"))
        self.assertGreater(checked, 0)

    def test_example_score_consistent(self):
        issues = [rec for recs in mode2_issues().values() for rec in recs]
        md = read(os.path.join(MODE2, MODE2_FILES[0]))
        health = tables(section(md, "Architecture Health Score"))
        self.assertTrue(health)
        header, rows = health[0]
        self.assertEqual(header, ["Category", "Score", "Band", "Main driver (IDs)"])
        self.assertEqual([r[0] for r in rows], list(CATEGORY_PREFIX))
        assessed = []
        for row in rows:
            prefix = CATEGORY_PREFIX[row[0]]
            sevs = [rec["Severity"] for rec in issues if rec["ID"].startswith(prefix + "-")]
            if row[1] == "Not assessed":
                self.assertEqual(sevs, [], row[0])
                continue
            expected = subscore(sevs)
            self.assertEqual(int(row[1]), expected, row[0])
            self.assertEqual(row[2], SUBSCORE_BAND[expected], row[0])
            assessed.append(expected)
        score = overall_score(assessed)
        band = overall_band(score)
        self.assertIn("## Architecture Health Score: %s (%.1f / 10)" % (band, score), md)

    def test_no_local_paths(self):
        bad = ("C:\\", "C:/", "/Users/", "\\Users\\", "/home/")
        for base in (EXAMPLES, EVALS):
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    path = os.path.join(dirpath, name)
                    text = read(path)
                    for b in bad:
                        self.assertNotIn(b, text, "%s contains %r" % (path, b))
                    self.assertNotIn("\r\n", text, "%s has CRLF line endings" % path)

    def test_skill_cases_structure(self):
        folder = os.path.join(EVALS, "skill-cases")
        self.assertEqual(sorted(f for f in os.listdir(folder) if f.endswith(".md")), SKILL_CASES)
        for name in SKILL_CASES:
            md = read(os.path.join(folder, name))
            self.assertEqual(headings(md), ["Prompt", "Setup", "Must", "Must not"], name)
            for part in ("Must", "Must not"):
                body = section(md, part, exact=True)
                self.assertIn("- [ ] ", body, "%s: %s has no checklist" % (name, part))
        self.assertTrue(os.path.isfile(os.path.join(EVALS, "README.md")))
        self.assertTrue(os.path.isfile(os.path.join(EXAMPLES, "README.md")))


if __name__ == "__main__":
    unittest.main()
