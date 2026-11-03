from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import FIXTURES, SCRIPTS

import compare_audits as ca  # noqa: E402  (helpers puts SCRIPTS on sys.path)

HIST = os.path.join(FIXTURES, "audit-history")
OLD = os.path.join(HIST, "old")
NEW = os.path.join(HIST, "new")
REPO_OLD = os.path.join(HIST, "repo-old")
REPO_NEW = os.path.join(HIST, "repo-new")
HISTORY = os.path.join(HIST, "history")


def _ids(entries):
    return sorted(e["id"] for e in entries)


def _by_id(entries):
    return {e["id"]: e for e in entries}


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _audit(rows, header="| ID | Severity | Issue | Evidence |\n|---|---|---|---|\n"):
    body = "".join("| " + " | ".join(r) + " |\n" for r in rows)
    return ("# Quick Audit\n\n## Health Band\n\n**Weak (4.0 / 10)**: x.\n\n## Top Issues\n\n"
            + header + body)


def _audit_with_header(audit_tail):
    return ("# Quick Audit\n\n> Audit: plugin 1.0.2 \u00b7 scanner schema 3 \u00b7 " + audit_tail
            + " \u00b7 2026-11-02T10:00:00Z\n\n## Health Band\n\n**Weak (4.0 / 10)**: x.\n")


class ParseTests(unittest.TestCase):
    def test_parse_issues_severity_from_heading_or_column(self):
        issues = {i.id: i for i in ca.parse_issues(NEW)}
        self.assertEqual(sorted(issues), ["MNT-001", "REL-001", "SCL-001", "SCL-002", "SEC-001"])
        self.assertEqual(issues["SEC-001"].severity, "Critical")   # section heading
        self.assertEqual(issues["SCL-002"].severity, "Medium")     # Severity column
        self.assertEqual(issues["SEC-001"].status, "confirmed")    # Status column found by name
        self.assertEqual(issues["REL-001"].evidence, [("src/pay.py", 7)])
        self.assertEqual(issues["REL-001"].file, "01-architecture-issues.md")
        self.assertEqual(issues["REL-001"].title, "Provider call has no timeout")

    def test_parse_old_format_without_status_column(self):
        issues = {i.id: i for i in ca.parse_issues(OLD)}
        self.assertEqual(issues["REL-002"].severity, "High")
        self.assertEqual(issues["REL-002"].status, "")
        # duplicated evidence cells collapse to distinct entries, order kept
        self.assertEqual(issues["OBS-001"].evidence, [("src/gone.py", 1)])

    def test_evidence_cell_variants(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "quick-audit.md", _audit([
                ("SEC-001", "High", "a", "`a/b.py:3`, `c.ts:9`-12<br>`d\\e.go:4`"),
                ("REL-001", "Low", "b", "none cited"),
            ]))
            issues = {i.id: i for i in ca.parse_issues(d)}
            self.assertEqual(issues["SEC-001"].evidence, [("a/b.py", 3), ("c.ts", 9), ("d/e.go", 4)])
            self.assertEqual(issues["REL-001"].evidence, [])

    def test_columns_located_by_name_not_position(self):
        with tempfile.TemporaryDirectory() as d:
            # a table whose first header is not ID is not an issue table
            _write(d, "quick-audit.md", _audit(
                [("Open", "SEC-001", "x.py:1", "Critical", "title")],
                header="| Status | ID | Evidence | Severity | Issue |\n|---|---|---|---|---|\n"))
            self.assertEqual(ca.parse_issues(d), [])
            _write(d, "quick-audit.md", _audit(
                [("SEC-001", "x.py:1", "Critical", "Open", "Verified", "title")],
                header="| ID | Evidence | Severity | Status | Verify | Issue |\n|---|---|---|---|---|---|\n"))
            (i,) = ca.parse_issues(d)
            self.assertEqual((i.severity, i.status, i.title, i.evidence),
                             ("Critical", "Open", "title", [("x.py", 1)]))

    def test_read_score_and_metadata(self):
        self.assertEqual(ca.read_score(NEW), (6.3, "Stable with risks"))
        self.assertEqual(ca.read_score(OLD), (3.9, "Unsafe"))
        self.assertEqual(ca.read_metadata(NEW).get("Revision"), "abc1234")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ca.read_score(d), (None, ""))
            _write(d, "quick-audit.md", _audit([]))
            self.assertEqual(ca.read_score(d), (4.0, "Weak"))
            # a re-run quick audit starts with "Band: A -> B"; the real score is the Health Band one
            _write(d, "quick-audit.md",
                   "# Quick Audit\n\n## Change Since Last Audit\n\n- Band: Weak (3.0 / 10) → Mixed (5.0 / 10)\n\n"
                   "## Health Band\n\n**Mixed (5.0 / 10)**: x.\n")
            self.assertEqual(ca.read_score(d), (5.0, "Mixed"))

    def test_revision_parsed_from_audit_line(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "quick-audit.md", _audit_with_header("rev 0123abc \u00b7 config 99aa"))
            self.assertEqual(ca.read_metadata(d).get("Revision"), "0123abc")
            _write(d, "quick-audit.md", _audit_with_header("rev unknown"))
            self.assertEqual(ca.read_metadata(d).get("Revision"), "unknown")
            _write(d, "quick-audit.md", _audit_with_header("rev 0123abc") + "> Revision: explicit1\n")
            self.assertEqual(ca.read_metadata(d).get("Revision"), "explicit1")

    def test_markdown_revision_line_from_audit_header(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "old/quick-audit.md", _audit_with_header("rev aaa1111"))
            _write(d, "new/quick-audit.md", _audit_with_header("rev bbb2222"))
            out = ca.render_markdown(ca.compare(os.path.join(d, "old"), os.path.join(d, "new"), d))
            self.assertIn("Revision: aaa1111 \u2192 bbb2222\n", out)

    def test_evidence_path_with_spaces(self):
        self.assertEqual(ca._parse_evidence("`my dir/a b.py:3`, c.py:4<br>x y/z.ts:5-9"),
                         [("my dir/a b.py", 3), ("c.py", 4), ("x y/z.ts", 5)])


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_line_independent(self):
        old = {i.id: i for i in ca.parse_issues(OLD)}["REL-001"]
        new = {i.id: i for i in ca.parse_issues(NEW)}["REL-001"]
        fp_old = ca.issue_fingerprint(old, REPO_OLD)
        fp_new = ca.issue_fingerprint(new, REPO_NEW)
        self.assertIsNotNone(fp_old)
        self.assertEqual(fp_old, fp_new)

    def test_fingerprint_uses_id_prefix_as_rule(self):
        a = ca.Issue("REL-001", "High", "", [("src/db.py", 2)], "t", "f")
        b = ca.Issue("SEC-001", "High", "", [("src/db.py", 2)], "t", "f")
        self.assertNotEqual(ca.issue_fingerprint(a, REPO_NEW), ca.issue_fingerprint(b, REPO_NEW))

    def test_fingerprint_none_when_file_or_line_missing(self):
        gone = ca.Issue("OBS-001", "Low", "", [("src/gone.py", 1)], "t", "f")
        self.assertIsNone(ca.issue_fingerprint(gone, REPO_NEW))
        self.assertIsNotNone(ca.issue_fingerprint(gone, REPO_OLD))
        past_end = ca.Issue("OBS-001", "Low", "", [("src/db.py", 99)], "t", "f")
        self.assertIsNone(ca.issue_fingerprint(past_end, REPO_NEW))
        none_cited = ca.Issue("OBS-001", "Low", "", [], "t", "f")
        self.assertIsNone(ca.issue_fingerprint(none_cited, REPO_NEW))

    def test_fingerprint_rejects_paths_outside_repo(self):
        esc = ca.Issue("SEC-001", "High", "", [("../repo-old/src/db.py", 2)], "t", "f")
        self.assertIsNone(ca.issue_fingerprint(esc, REPO_NEW))


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmp = ca.compare(OLD, NEW, REPO_NEW, old_repo_root=REPO_OLD)

    def test_unchanged_same_fingerprint_despite_line_shift(self):
        e = _by_id(self.cmp.unchanged)
        self.assertIn("REL-001", e)       # moved from pay.py:5 to pay.py:7
        self.assertIn("SCL-001", e)
        self.assertFalse(e["REL-001"]["uncertain"])
        self.assertEqual(e["REL-001"]["old_id"], "REL-001")

    def test_changed_same_fingerprint_different_severity(self):
        e = _by_id(self.cmp.changed)
        self.assertEqual(_ids(self.cmp.changed), ["SEC-001"])
        self.assertIn("High", e["SEC-001"]["reason"])
        self.assertIn("Critical", e["SEC-001"]["reason"])
        self.assertFalse(e["SEC-001"]["uncertain"])

    def test_moved_file_renamed_same_snippet(self):
        e = _by_id(self.cmp.moved)
        self.assertEqual(_ids(self.cmp.moved), ["MNT-001"])
        self.assertFalse(e["MNT-001"]["uncertain"])
        self.assertIn("src/util.py", e["MNT-001"]["reason"])
        self.assertIn("src/lib/util.py", e["MNT-001"]["reason"])

    def test_new_and_resolved(self):
        self.assertEqual(_ids(self.cmp.new), ["SCL-002"])
        self.assertIsNone(self.cmp.new[0]["old_id"])
        self.assertFalse(self.cmp.new[0]["uncertain"])
        self.assertIn("REL-002", _ids(self.cmp.resolved))
        self.assertFalse(_by_id(self.cmp.resolved)["REL-002"]["uncertain"])  # file still exists, line gone

    def test_compare_missing_evidence_file(self):
        e = _by_id(self.cmp.resolved)
        self.assertIn("OBS-001", e)      # src/gone.py deleted in the new repo
        self.assertTrue(e["OBS-001"]["uncertain"])
        self.assertIn("src/gone.py", e["OBS-001"]["reason"])

    def test_every_old_issue_classified_exactly_once(self):
        total = (len(self.cmp.unchanged) + len(self.cmp.changed) + len(self.cmp.moved)
                 + len(self.cmp.resolved))
        self.assertEqual(total, len(ca.parse_issues(OLD)))

    def test_compare_with_only_current_repo_degrades_gracefully(self):
        # Old evidence resolved against the new repo: shifted lines still match through the
        # same-file fallback (uncertain), deleted files resolve as uncertain, nothing crashes
        cmp = ca.compare(OLD, NEW, REPO_NEW)
        e = _by_id(cmp.resolved)
        self.assertIn("REL-002", e)
        self.assertTrue(_by_id(cmp.unchanged)["REL-001"]["uncertain"])
        self.assertTrue(e["OBS-001"]["uncertain"])

    def test_fuzzy_fallback_marked_uncertain(self):
        # A 1.0.1-style archive whose evidence cannot be resolved: same prefix + same file + similar title
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old")
            new = os.path.join(d, "new")
            _write(old, "quick-audit.md", _audit([
                ("SCL-007", "Medium", "Unbounded list query in items handler", "`src/api.py:99`"),
                ("SEC-009", "High", "Hardcoded secret", "`src/db.py:98`")]))
            _write(new, "quick-audit.md", _audit([
                ("SCL-001", "Medium", "Unbounded list query in the items handler", "`src/api.py:2`")]))
            cmp = ca.compare(old, new, REPO_NEW)
            self.assertEqual(_ids(cmp.unchanged), ["SCL-007"])
            e = cmp.unchanged[0]
            self.assertTrue(e["uncertain"])
            self.assertEqual(e["old_id"], "SCL-007")
            self.assertEqual(cmp.id_map, {"SCL-007": "SCL-001"})
            self.assertEqual(_ids(cmp.resolved), ["SEC-009"])
            self.assertTrue(cmp.resolved[0]["uncertain"])
            self.assertEqual(cmp.new, [])

    def test_fuzzy_requires_same_prefix_and_file(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old")
            new = os.path.join(d, "new")
            _write(old, "quick-audit.md",
                   _audit([("SCL-007", "Medium", "Unbounded list query", "`src/api.py:99`")]))
            _write(new, "quick-audit.md", _audit([
                ("REL-001", "Medium", "Unbounded list query", "`src/api.py:2`"),
                ("SCL-001", "Medium", "Unbounded list query", "`src/db.py:2`")]))
            cmp = ca.compare(old, new, REPO_NEW)
            self.assertEqual(_ids(cmp.new), ["REL-001", "SCL-001"])
            self.assertEqual(_ids(cmp.resolved), ["SCL-007"])

    def test_status_change_is_changed(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old")
            new = os.path.join(d, "new")
            h = "| ID | Severity | Status | Issue | Evidence |\n|---|---|---|---|---|\n"
            _write(old, "quick-audit.md",
                   _audit([], header=h) + "| SEC-001 | High | Open | t | `src/db.py:2` |\n")
            _write(new, "quick-audit.md",
                   _audit([], header=h) + "| SEC-001 | High | Accepted | t | `src/db.py:2` |\n")
            cmp = ca.compare(old, new, REPO_NEW)
            self.assertEqual(_ids(cmp.changed), ["SEC-001"])
            self.assertIn("Accepted", cmp.changed[0]["reason"])

    def test_empty_audit_dirs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            cmp = ca.compare(d, d, d)
            self.assertEqual((cmp.new, cmp.resolved, cmp.unchanged, cmp.changed, cmp.moved),
                             ([], [], [], [], []))


class TrendTests(unittest.TestCase):
    def test_trend_table(self):
        rows = ca.trend(HISTORY, NEW, current_date="2026-11-02")
        # 2026-09-15/ is the archive date; the run itself is dated by its Audit line (09-01).
        # 2026-08-01/ is a 1.0.1 archive without an Audit line: the folder name is used.
        self.assertEqual([r["date"] for r in rows], ["2026-08-01", "2026-09-01", "2026-11-02"])
        self.assertEqual([(r["overall"], r["band"]) for r in rows],
                         [(2.0, "Unsafe"), (3.9, "Unsafe"), (6.3, "Stable with risks")])
        self.assertEqual(rows[0]["fixed"], [])
        self.assertEqual(rows[1]["new"], ["MNT-001", "OBS-001", "REL-002", "SCL-001"])
        self.assertEqual(rows[2]["fixed"], ["OBS-001", "REL-002"])
        self.assertEqual(rows[2]["new"], ["SCL-002"])

    def test_trend_keeps_last_n_runs(self):
        with tempfile.TemporaryDirectory() as d:
            hist = os.path.join(d, "history")
            for n in range(1, 13):
                _write(hist, "2026-01-%02d/quick-audit.md" % n,
                       _audit([("SEC-%03d" % n, "High", "t", "x.py:1")]))
            cur = os.path.join(d, "cur")
            _write(cur, "quick-audit.md", _audit([("SEC-100", "High", "t", "x.py:1")]))
            rows = ca.trend(hist, cur, last=10, current_date="2026-02-01")
            self.assertEqual(len(rows), 10)
            self.assertEqual(rows[-1]["date"], "2026-02-01")
            self.assertEqual(rows[0]["date"], "2026-01-04")
            self.assertEqual(rows[-1]["fixed"], ["SEC-012"])
            self.assertEqual(rows[0]["fixed"], ["SEC-003"])   # diffed against the run before the window

    def test_trend_missing_history_is_just_current(self):
        rows = ca.trend(os.path.join(HIST, "nope"), NEW, current_date="2026-11-02")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["overall"], 6.3)

    def test_suffixed_history_dirs_sort_after_base(self):
        with tempfile.TemporaryDirectory() as d:
            hist = os.path.join(d, "h")
            for name in ("2026-03-01-2", "2026-03-01", "2026-02-01"):
                _write(hist, name + "/quick-audit.md", _audit([]))
            cur = os.path.join(d, "cur")
            _write(cur, "quick-audit.md", _audit([]))
            rows = ca.trend(hist, cur, current_date="2026-04-01")
            self.assertEqual([r["date"] for r in rows],
                             ["2026-02-01", "2026-03-01", "2026-03-01-2", "2026-04-01"])


class ReviewFixTests(unittest.TestCase):
    """Regression tests for the 1.0.2 release review (area C)."""

    def _pair(self, d, old_rows, new_rows, header=None):
        old, new = os.path.join(d, "old"), os.path.join(d, "new")
        kw = {"header": header} if header else {}
        _write(old, "quick-audit.md", _audit(old_rows, **kw))
        _write(new, "quick-audit.md", _audit(new_rows, **kw))
        return old, new

    def test_multi_word_band(self):
        # C2: "Stable with risks" was read as "risks"
        with tempfile.TemporaryDirectory() as d:
            _write(d, "a/01-architecture-issues.md",
                   "# A\n\n## Architecture Health Score: Stable with risks (6.3 / 10)\n")
            _write(d, "b/quick-audit.md",
                   "# Q\n\n## Health Band\n\n**Stable with risks (7.1 / 10)**: fix High issues.\n")
            self.assertEqual(ca.read_score(os.path.join(d, "a")), (6.3, "Stable with risks"))
            self.assertEqual(ca.read_score(os.path.join(d, "b")), (7.1, "Stable with risks"))
            out = ca.render_markdown(ca.compare(os.path.join(d, "a"), os.path.join(d, "b"), d),
                                     ca.trend(None, os.path.join(d, "b"), current_date="2026-11-03"))
            self.assertIn("Previous: Stable with risks (6.3 / 10) \u2192 Current: Stable with "
                          "risks (7.1 / 10)", out)
            self.assertIn("| 2026-11-03 | 7.1 | Stable with risks |", out)

    def test_moved_file_without_snapshot_is_possibly_moved_not_fixed_and_new(self):
        # I1: default mode (no --old-repo); MNT-001 moved to src/lib/util.py
        cmp = ca.compare(OLD, NEW, REPO_NEW)
        self.assertEqual(_ids(cmp.moved), ["MNT-001"])
        e = cmp.moved[0]
        self.assertTrue(e["uncertain"])
        self.assertIn("src/util.py \u2192 src/lib/util.py", e["reason"])
        self.assertNotIn("MNT-001", _ids(cmp.new))
        self.assertNotIn("MNT-001", _ids(cmp.resolved))
        out = ca.render_markdown(cmp)
        self.assertIn("- Possibly moved: MNT-001 (src/util.py \u2192 src/lib/util.py; uncertain)", out)

    def test_same_id_is_never_both_fixed_and_new(self):
        # I1(b): a kept ID with a reworded title and a shifted line is one uncertain match
        with tempfile.TemporaryDirectory() as d:
            old, new = self._pair(d, [("SEC-004", "High", "Token in code", "`src/db.py:1`")],
                                  [("SEC-004", "High", "SQL built from input", "`src/api.py:2`")])
            cmp = ca.compare(old, new, REPO_NEW)
            self.assertEqual((cmp.new, cmp.resolved), ([], []))
            self.assertEqual(_ids(cmp.unchanged), ["SEC-004"])
            self.assertTrue(cmp.unchanged[0]["uncertain"])
            self.assertIn("confirm by reading", cmp.unchanged[0]["reason"])

    def test_extensionless_and_absence_evidence_match_across_runs(self):
        # I2: Dockerfile:15, Makefile:2, .env:3 and folder Evidence parse and fingerprint
        self.assertEqual(ca._parse_evidence("`Dockerfile:15`, `Makefile:2`<br>`.env:3`"),
                         [("Dockerfile", 15), ("Makefile", 2), (".env", 3)])
        self.assertEqual(ca._parse_evidence("`services/orders/`"), [("services/orders", 0)])
        self.assertEqual(ca._parse_evidence("`app/[id]/page.tsx:3`"), [("app/[id]/page.tsx", 3)])
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            _write(repo, "Dockerfile", "FROM node\nCOPY . .\nCMD node x\n")
            _write(repo, "services/orders/x.go", "package x\n")
            old, new = self._pair(d, [("OBS-002", "Medium", "No healthcheck", "`Dockerfile:3`"),
                                      ("REL-003", "Low", "No tests", "`services/orders/`")],
                                  [("OBS-002", "Medium", "Container has no healthcheck",
                                    "`Dockerfile:3`"),
                                   ("REL-003", "Low", "Orders has no tests", "`services/orders/`")])
            cmp = ca.compare(old, new, repo)
            self.assertEqual(_ids(cmp.unchanged), ["OBS-002", "REL-003"])
            self.assertFalse(any(e["uncertain"] for e in cmp.unchanged))
            self.assertEqual((cmp.new, cmp.resolved), ([], []))

    def test_missing_old_status_column_is_not_a_change(self):
        # I3: a 1.0.1 archive (no Status column) vs a 1.0.2 run (confirmed) is not "Changed"
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old")
            new = os.path.join(d, "new")
            _write(old, "quick-audit.md", _audit([("SEC-001", "High", "t", "`src/db.py:2`")]))
            h = "| ID | Severity | Status | Issue | Evidence |\n|---|---|---|---|---|\n"
            _write(new, "quick-audit.md",
                   _audit([], header=h) + "| SEC-001 | High | confirmed | t | `src/db.py:2` |\n")
            cmp = ca.compare(old, new, REPO_NEW)
            self.assertEqual((_ids(cmp.unchanged), cmp.changed), (["SEC-001"], []))

    def test_not_reproducible_counts_as_fixed(self):
        # I9: a row kept with Status not-reproducible is Fixed, not Still open
        with tempfile.TemporaryDirectory() as d:
            h = "| ID | Severity | Status | Issue | Evidence |\n|---|---|---|---|---|\n"
            old = os.path.join(d, "old")
            new = os.path.join(d, "new")
            _write(old, "quick-audit.md",
                   _audit([], header=h) + "| SEC-001 | High | confirmed | t | `src/db.py:2` |\n")
            _write(new, "quick-audit.md", _audit([], header=h)
                   + "| SEC-001 | High | not-reproducible | t | `src/db.py:2` |\n")
            cmp = ca.compare(old, new, REPO_NEW)
            self.assertEqual((_ids(cmp.resolved), cmp.unchanged, cmp.new), (["SEC-001"], [], []))
            self.assertEqual(ca.trend(None, new, current_date="x")[0]["overall"], 4.0)

    def test_trend_date_from_audit_line(self):
        # I12: an archive folder is named after the archive day; the run date is its Audit line
        with tempfile.TemporaryDirectory() as d:
            hist = os.path.join(d, "history")
            _write(hist, "2026-10-22/quick-audit.md", _audit_with_header("rev a")
                   .replace("2026-11-02T10:00:00Z", "2026-09-01T08:00:00Z"))
            cur = os.path.join(d, "cur")
            _write(cur, "quick-audit.md", _audit_with_header("rev b")
                   .replace("2026-11-02T10:00:00Z", "2026-10-22T09:00:00Z"))
            rows = ca.trend(hist, cur)   # no --date: the current run's Audit date
            self.assertEqual([r["date"] for r in rows], ["2026-09-01", "2026-10-22"])

    def test_history_suffix_sorts_numerically(self):
        # Minor 8: <date>-10 comes after <date>-2
        with tempfile.TemporaryDirectory() as d:
            hist = os.path.join(d, "h")
            for name in ("2026-03-01-10", "2026-03-01-2", "2026-03-01"):
                _write(hist, name + "/quick-audit.md", _audit([]))
            cur = os.path.join(d, "cur")
            _write(cur, "quick-audit.md", _audit([]))
            rows = ca.trend(hist, cur, current_date="2026-04-01")
            self.assertEqual([r["date"] for r in rows],
                             ["2026-03-01", "2026-03-01-2", "2026-03-01-10", "2026-04-01"])


class CliTests(unittest.TestCase):
    def _run(self, *args):
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "compare_audits.py")] + list(args),
                              capture_output=True, text=True, encoding="utf-8")
        return proc.returncode, proc.stdout, proc.stderr

    def test_markdown_output(self):
        code, out, err = self._run(OLD, NEW, "--repo", REPO_NEW, "--old-repo", REPO_OLD,
                                   "--history", HISTORY, "--date", "2026-11-02")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("## Change Since Last Audit\n"))
        self.assertIn("Previous: Unsafe (3.9 / 10) → Current: Stable with risks (6.3 / 10)", out)
        self.assertIn("- Fixed: OBS-001 (uncertain), REL-002", out)
        self.assertIn("- New: SCL-002", out)
        self.assertIn("- Still open: MNT-001, REL-001, SCL-001, SEC-001", out)
        self.assertIn("- Changed: SEC-001 (severity High → Critical)", out)
        self.assertIn("- Possibly moved: MNT-001 (src/util.py → src/lib/util.py)", out)
        self.assertIn("### Score Trend\n", out)
        self.assertIn("| Date | Overall | Band | Fixed | New |", out)
        self.assertIn("| 2026-11-02 | 6.3 | Stable with risks | OBS-001, REL-002 | SCL-002 |", out)
        self.assertNotIn("\r", out)

    def test_json_output(self):
        code, out, _ = self._run(OLD, NEW, "--repo", REPO_NEW, "--old-repo", REPO_OLD,
                                 "--format", "json", "--history", HISTORY, "--date", "2026-11-02")
        self.assertEqual(code, 0)
        data = json.loads(out)
        for key in ("new", "resolved", "unchanged", "changed", "moved", "id_map", "trend",
                    "previous", "current"):
            self.assertIn(key, data)
        self.assertEqual(data["current"], {"overall": 6.3, "band": "Stable with risks"})

    def test_old_folder_without_audit_exits_2(self):
        # Minor 7: a wrong archive path used to print "No issues in either audit." and exit 0
        with tempfile.TemporaryDirectory() as d:
            code, out, err = self._run(d, NEW, "--repo", REPO_NEW)
            self.assertEqual((code, out), (2, ""))
            self.assertIn("no audit file", err)

    def test_usage_errors_exit_2(self):
        code, out, err = self._run(os.path.join(HIST, "missing"), NEW, "--repo", REPO_NEW)
        self.assertEqual(code, 2)
        self.assertIn("missing", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
