from __future__ import annotations

import json
import os
import re
import unittest
from typing import Dict, List, Tuple

# helpers must come first: it puts the scripts directory on sys.path, so this file also runs alone.
from helpers import (FIXTURES, REPO, expected_headers, header_mismatch, headings,
                     overall_band, rerun_only_headers, subscore)

import lint_output  # noqa: E402
import rubric  # noqa: E402

EXAMPLES = os.path.join(REPO, "examples")
EVALS = os.path.join(REPO, "evals")
MODE2 = os.path.join(EXAMPLES, "mode-2-audit-monorepo")
REAL = os.path.join(EXAMPLES, "mode-2-audit-express-boilerplate")
MODE1 = os.path.join(EXAMPLES, "mode-1-food-delivery")
MONOREPO = os.path.join(FIXTURES, "monorepo")
# The pinned real repo lives outside this repo (evals/fetch_corpus.py); its files are checked
# only when the corpus has been fetched.
CORPUS_REAL = os.path.normpath(os.path.join(
    REPO, "..", "design-and-fix-corpus", "node-express-boilerplate", "node-express-boilerplate-1.7.0"))
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
EVIDENCE_RE = re.compile(r"^([A-Za-z0-9_./@-]+):(\d+)(?:-(\d+))?$")
AUDIT_RE = re.compile(
    r"^> Audit: plugin 1\.0\.2 · scanner (?:schema 3|not run) · rev (?:unknown|[0-9a-f]{7,40})"
    r"(?: · config [0-9a-f]{12})? · \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.M)
PIN_RE = re.compile(r"\b[0-9a-f]{40}\b|\btag `v?\d+\.\d+\.\d+[^`]*`")
REAL_URL = "https://github.com/hagopj13/node-express-boilerplate"
MODE2_EXAMPLES = ((MODE2, MONOREPO), (REAL, CORPUS_REAL))


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


def mode2_issues(folder: str = MODE2) -> Dict[str, List[Dict[str, str]]]:
    """Issues defined per Mode 2 file: {"01": [...], "02": [...]}."""
    one = read(os.path.join(folder, MODE2_FILES[0]))
    two = read(os.path.join(folder, MODE2_FILES[1]))
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
        for folder, names in ((MODE2, MODE2_FILES), (REAL, MODE2_FILES), (MODE1, MODE1_FILES)):
            for name in names:
                md = read(os.path.join(folder, name))
                optional = list(opt.get(name, ()))
                if folder == REAL and name == MODE2_FILES[0]:
                    optional += ["Critical Issues", "High Issues"]  # Medium/Low-only audit
                problem = header_mismatch(headings(md), exp[name], optional)
                self.assertIsNone(problem, "%s/%s: %s" % (os.path.basename(folder), name, problem))
                self.assertIn("> Scope:", md, name)
                self.assertIn("> Context:", md, name)
                self.assertIn("\n> Format: 1.0.2\n", md, name)
                self.assertRegex(md, AUDIT_RE, name)
        adr_dir = os.path.join(MODE1, "adr")
        adrs = sorted(f for f in os.listdir(adr_dir) if f.endswith(".md"))
        self.assertTrue(1 <= len(adrs) <= 5, adrs)
        for i, name in enumerate(adrs, start=1):
            self.assertRegex(name, r"^%04d-[a-z0-9]+(-[a-z0-9]+)*\.md$" % i)
            md = read(os.path.join(adr_dir, name))
            base = [h for h in headings(md) if h not in ("Alternatives", "Assumptions")]
            self.assertIsNone(header_mismatch(base, exp[ADR_KEY]), name)
            self.assertEqual(headings(md), ["Context", "Alternatives", "Decision", "Consequences",
                                            "Assumptions"], name)
            self.assertTrue(md.startswith("# %04d. " % i), name)
            self.assertRegex(md, r"\nDate: \d{4}-\d{2}-\d{2}\n")
            self.assertRegex(md, r"\nStatus: (Accepted|Superseded by \d{4})\n")
            self.assertIn("\n> Format: 1.0.2\n", md, name)
        design = read(os.path.join(MODE1, MODE1_FILES[0]))
        decisions = tables(section(design, "Key Architectural Decisions"))
        self.assertEqual(len(decisions[0][1]), len(adrs))
        self.assertIn("Requirements → Decisions", headings(design))
        self.assertIn("Security & Threat Model",
                      headings(read(os.path.join(MODE1, MODE1_FILES[1]))))

    def test_example_ids_in_roadmap(self):
        for folder, _repo in MODE2_EXAMPLES:
            issues = mode2_issues(folder)
            for rec in issues["01"]:
                self.assertFalse(rec["ID"].startswith("API-"), rec["ID"])
            for rec in issues["02"]:
                self.assertTrue(rec["ID"].startswith("API-"), rec["ID"])
            defined = [rec["ID"] for recs in issues.values() for rec in recs]
            self.assertTrue(defined)
            for d in defined:
                self.assertRegex(d, r"^%s$" % ID_RE.pattern)
            self.assertEqual(len(defined), len(set(defined)), "an ID is defined twice")

            roadmap = read(os.path.join(folder, MODE2_FILES[2]))
            cited = []
            for horizon in HORIZONS:
                for header, rows in tables(section(roadmap, horizon)):
                    self.assertEqual(header, ["Action (IDs)", "Effort", "Impact", "Why"])
                    for row in rows:
                        ids = ID_RE.findall(row[0].split(":", 1)[0])
                        self.assertTrue(ids, "Action cell must start with IDs: %r" % row[0])
                        self.assertIn(row[1], ("S", "M", "L", "XL"))
                        self.assertIn(row[2], ("Low", "Medium", "High"))
                        cited.extend(ids)
            for d in defined:
                self.assertEqual(cited.count(d), 1, "%s in %d horizon rows" % (d, cited.count(d)))
            self.assertEqual(set(cited) - set(defined), set(), "roadmap cites undefined IDs")

    def test_example_issue_fields(self):
        for folder, _repo in MODE2_EXAMPLES:
            for recs in mode2_issues(folder).values():
                for rec in recs:
                    self.assertIn(rec["Status"], ("confirmed", "likely", "needs-review",
                                                  "not-reproducible"), rec["ID"])
                    self.assertRegex(rec["Source"],
                                     r"^(agent|scanner:[\w.-]+|scanner\+agent(:[\w.-]+)?)$")
                    if rec["Severity"] in ("Critical", "High"):
                        self.assertNotIn(rec["Verify"], ("", "—"), rec["ID"])

    def test_example_evidence_exists(self):
        checked = 0
        for folder, repo in MODE2_EXAMPLES:
            # The real-world example's repo is outside this repo: format always, files if fetched.
            check_files = folder == MODE2 or os.path.isdir(repo)
            for recs in mode2_issues(folder).values():
                for rec in recs:
                    tokens = [t.strip().strip("`")
                              for t in re.split(r",\s*|<br>", rec["Evidence"]) if t.strip()]
                    self.assertTrue(tokens, rec["ID"])
                    for tok in tokens:
                        if tok == "Not verified":
                            continue
                        m = EVIDENCE_RE.match(tok)
                        self.assertIsNotNone(m, "%s: bad evidence %r" % (rec["ID"], tok))
                        self.assertFalse(m.group(1).startswith("/"), tok)
                        if check_files:
                            path = os.path.join(repo, *m.group(1).split("/"))
                            self.assertTrue(os.path.isfile(path), "%s: %s missing" % (rec["ID"], tok))
                            with open(path, encoding="utf-8") as fh:
                                length = len(fh.read().splitlines())
                            last = int(m.group(3) or m.group(2))
                            self.assertTrue(1 <= int(m.group(2)) <= last <= length,
                                            "%s: %s beyond %d lines" % (rec["ID"], tok, length))
                        checked += 1
                    self.assertIn(rec["Severity"], ("Critical", "High", "Medium", "Low"))
        self.assertGreater(checked, 0)

    def test_example_score_consistent(self):
        for folder, _repo in MODE2_EXAMPLES:
            issues = [rec for recs in mode2_issues(folder).values() for rec in recs]
            md = read(os.path.join(folder, MODE2_FILES[0]))
            weights = rubric.weights_for(re.search(r"^> Context: (.*)$", md, re.M).group(1))
            health = tables(section(md, "Architecture Health Score"))
            self.assertTrue(health)
            header, rows = health[0]
            self.assertEqual(header, ["Category", "Score", "Band", "Weight", "Main driver (IDs)"])
            self.assertEqual([r[0] for r in rows], list(CATEGORY_PREFIX))
            assessed = {}
            for row in rows:
                prefix = CATEGORY_PREFIX[row[0]]
                sevs = [rec["Severity"] for rec in issues if rec["ID"].startswith(prefix + "-")]
                self.assertEqual(float(row[3]), weights[row[0]], row[0])
                if row[1] == "Not assessed":
                    self.assertEqual(sevs, [], row[0])
                    continue
                expected = subscore(sevs)
                self.assertEqual(int(row[1]), expected, row[0])
                self.assertEqual(row[2], SUBSCORE_BAND[expected], row[0])
                assessed[row[0]] = expected
            score = rubric.overall(assessed, weights)
            self.assertIn("## Architecture Health Score: %s (%.1f / 10)" % (overall_band(score), score), md)

    def test_examples_pass_output_linter(self):
        # The real-world example is linted without --repo: its repo lives outside this one.
        for folder, repo in ((MODE2, MONOREPO), (REAL, None), (MODE1, None)):
            found = lint_output.lint(folder, repo=repo)
            self.assertEqual([], ["%s:%d: %s" % (f.file, f.line, f.message) for f in found])

    def test_real_example_pinned(self):
        for name in MODE2_FILES:
            scope = re.search(r"^> Scope: (.*)$", read(os.path.join(REAL, name)), re.M).group(1)
            self.assertRegex(scope, PIN_RE, name)
            self.assertIn(REAL_URL, scope, name)

    def test_scan_json_matches_audit_header(self):
        for folder, root in ((MODE2, "tests/fixtures/monorepo"),
                             (REAL, "node-express-boilerplate@v1.7.0")):
            with open(os.path.join(folder, "scan.json"), encoding="utf-8") as fh:
                scan = json.load(fh)
            self.assertEqual(scan["root"], root)
            self.assertEqual(scan["schema_version"], 3)
            self.assertEqual(scan["meta"]["plugin_version"], "1.0.2")
            for name in MODE2_FILES:
                md = read(os.path.join(folder, name))
                self.assertIn(scan["meta"]["timestamp"], md, name)
                self.assertIn("config " + scan["meta"]["config_hash"], md, name)

    def test_monorepo_scan_json_is_the_documented_scoped_scan(self):
        # I14: scan.json comes from the README command (three --scope flags), not a root scan
        with open(os.path.join(MODE2, "scan.json"), encoding="utf-8") as fh:
            scan = json.load(fh)
        scope = ["services/payments", "services/orders", "services/catalog"]
        self.assertEqual(scan["scope"], scope)
        self.assertEqual(scan["meta"]["scope"], scope)
        readme = read(os.path.join(EXAMPLES, "README.md"))
        self.assertIn(" ".join("--scope " + s for s in scope), readme)
        self.assertFalse(any(r["file"].startswith("App/") for r in scan["routes"]))
        api = read(os.path.join(MODE2, "02-api-optimization.md"))
        self.assertIn("(%d)" % len(scan["routes"]), api)

    def test_examples_invent_no_facts_and_sources_match_scan(self):
        # Minor 12: no rate that the Context line or scan does not give
        self.assertNotIn("50 req/s", read(os.path.join(REAL, "02-api-optimization.md")))
        # Minor 13: the scanner flagged only catalog; payments was found by reading
        with open(os.path.join(MODE2, "scan.json"), encoding="utf-8") as fh:
            flagged = {s["file"] for s in json.load(fh)["signals"]
                       if s["rule"] == "no-health-endpoint"}
        self.assertEqual(flagged, {"services/catalog/app.py"})
        self.assertIn("| OBS-001 | Medium | confirmed | scanner+agent:no-health-endpoint |",
                      read(os.path.join(MODE2, "01-architecture-issues.md")))

    def test_no_local_paths(self):
        bad = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|(?<![\w.-])/Users/[^/\s]+/"
                         r"|(?<![\w.-])/home/[^/\s]+/|\b[A-Za-z]:\\\\")
        for base in (EXAMPLES, EVALS):
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]  # evals/*.py bytecode
                for name in files:
                    path = os.path.join(dirpath, name)
                    text = read(path)
                    m = bad.search(text)
                    self.assertIsNone(m, "%s contains local path %r" % (path, m and m.group(0)))
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
