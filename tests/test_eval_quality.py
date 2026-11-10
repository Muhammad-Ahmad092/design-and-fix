from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from helpers import FIXTURES, REPO, SCRIPTS  # noqa: F401  (helpers puts SCRIPTS on sys.path)

EVALS = os.path.join(REPO, "evals")
if EVALS not in sys.path:
    sys.path.insert(0, EVALS)

import report_quality as rq  # noqa: E402
import verify_precision as vp  # noqa: E402

HEADER = ("| ID | Status | Source | Issue | Evidence | Root cause | Consequence | Fix | Verify | Why |\n"
          "|---|---|---|---|---|---|---|---|---|---|\n")


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _row(iid, status, source, issue, evidence, cause="x", cons="y"):
    return "| %s | %s | %s | %s | %s | %s | %s | f | v | w |\n" % (
        iid, status, source, issue, evidence, cause, cons)


def _audit(rows):
    return "# Architecture Issues\n\n## Critical Issues\n\n" + HEADER + "".join(rows)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.audit = os.path.join(self._td.name, "audit")
        self.repo = os.path.join(self._td.name, "repo")
        _write(self.repo, "src/app.py", "a = 1\nb = 2\nc = 3\n")
        _write(self.repo, "src/routes.py", "@app.get('/items')\ndef items(): pass\n")

    def put(self, *rows):
        _write(self.audit, "01-architecture-issues.md", _audit(rows))


class VerifyPrecisionTests(_Tmp):
    def test_extract_scanner_sourced_issues_only(self):
        self.put(
            _row("SEC-001", "confirmed", "scanner:hardcoded-secret", "Secret", "`src/app.py:1`"),
            _row("REL-001", "likely", "agent", "No retries", "`src/app.py:2`"),
            _row("REL-002", "confirmed", "scanner:http-no-timeout, query-in-loop", "Slow",
                 "`src/app.py:3`"),
            _row("MNT-001", "not-reproducible", "scanner:god-module", "Big", "`src/app.py:1`"),
        )
        rows = vp.extract(self.audit)
        self.assertEqual([(r["id"], r["rule"]) for r in rows], [
            ("SEC-001", "hardcoded-secret"), ("REL-002", "http-no-timeout"),
            ("REL-002", "query-in-loop"), ("MNT-001", "god-module")])
        first = rows[0]
        self.assertEqual(first["evidence"], "src/app.py:1")
        self.assertEqual(first["status"], "confirmed")
        self.assertEqual(first["label"], "")

    def test_report_precision_per_rule_excludes_rejected(self):
        rows = [
            {"id": "SEC-001", "rule": "a", "status": "confirmed", "label": "tp"},
            {"id": "SEC-002", "rule": "a", "status": "confirmed", "label": "fp"},
            {"id": "SEC-003", "rule": "a", "status": "likely", "label": "unsure"},
            {"id": "SEC-004", "rule": "a", "status": "not-reproducible", "label": "fp"},
            {"id": "REL-001", "rule": "b", "status": "confirmed", "label": "tp"},
            {"id": "REL-002", "rule": "b", "status": "confirmed", "label": ""},
        ]
        stats = vp.summarize(rows)
        self.assertEqual(stats["a"], {"tp": 1, "fp": 1, "unsure": 1, "unlabelled": 0, "rejected": 1})
        self.assertEqual(stats["b"]["unlabelled"], 1)
        text = vp.render(rows)
        self.assertIn("| a | 2 | 1 | 1 | 1 | 0 | 1 | 50% ", text)
        self.assertIn("| b | 1 | 1 | 0 | 0 | 1 | 0 | 100% ", text)

    def test_cli_extract_then_report(self):
        self.put(_row("SEC-001", "confirmed", "scanner:hardcoded-secret", "Secret", "`src/app.py:1`"))
        sheet = os.path.join(self._td.name, "sheet.json")
        self.assertEqual(vp.main(["extract", self.audit, "--out", sheet]), 0)
        with open(sheet, encoding="utf-8") as fh:
            data = json.load(fh)
        data[0]["label"] = "tp"
        with open(sheet, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh)
        out = os.path.join(self._td.name, "r.md")
        self.assertEqual(vp.main(["report", sheet, "--out", out]), 0)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("| hardcoded-secret | 1 | 1 | 0 |", fh.read())

    def test_bad_label_is_an_error(self):
        sheet = os.path.join(self._td.name, "s.json")
        _write(self._td.name, "s.json", json.dumps([{"rule": "a", "label": "maybe"}]))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vp.main(["report", sheet]), 2)


class ReportQualityTests(_Tmp):
    def test_quality_flags_invented_path(self):
        self.put(_row("SEC-001", "confirmed", "agent", "Bad", "`src/ghost.py:3`"))
        fails = rq.check(self.audit, self.repo)
        self.assertEqual(len(fails), 1)
        self.assertIn("src/ghost.py:3", fails[0])
        self.assertIn("does not exist", fails[0])

    def test_quality_flags_line_beyond_file(self):
        self.put(_row("SEC-001", "confirmed", "agent", "Bad", "`src/app.py:2-9`"))
        fails = rq.check(self.audit, self.repo)
        self.assertEqual(len(fails), 1)
        self.assertIn("beyond the end of the file (3 lines)", fails[0])

    def test_quality_flags_confirmed_without_evidence(self):
        self.put(_row("SEC-001", "confirmed", "agent", "Bad", "Not verified"),
                 _row("SEC-002", "likely", "agent", "Maybe", "Not verified"))
        fails = rq.check(self.audit, self.repo)
        self.assertEqual(len(fails), 1)
        self.assertIn("SEC-001: confirmed issue has no Evidence", fails[0])

    def test_quality_flags_invented_prose_file_and_route(self):
        self.put(_row("SEC-001", "confirmed", "agent", "Bad", "`src/app.py:1`",
                      cause="`src/nowhere.py` skips the check; `GET /ghosts` is open",
                      cons="`app.py` and `GET /items/<id>` are fine; `res.json` too"))
        fails = rq.check(self.audit, self.repo)
        self.assertEqual(len(fails), 2, fails)
        self.assertTrue(any("'src/nowhere.py'" in f for f in fails))
        self.assertTrue(any("'/ghosts'" in f for f in fails))

    def test_absence_evidence_and_absent_files_in_prose_pass(self):
        self.put(_row("MNT-001", "confirmed", "scanner:missing-lockfile", "No lockfile", "`src/`",
                      cause="No `package-lock.json` next to the manifest"))
        self.assertEqual(rq.check(self.audit, self.repo), [])

    def test_quality_flags_id_reused_with_different_title(self):
        self.put(_row("SEC-001", "confirmed", "agent", "Hardcoded database password", "`src/app.py:1`"))
        _write(self.audit, "history/2026-01-01/01-architecture-issues.md",
               _audit([_row("SEC-001", "confirmed", "agent", "Missing pagination on orders list",
                            "`src/app.py:1`")]))
        fails = rq.check(self.audit, self.repo)
        self.assertEqual(len(fails), 1)
        self.assertIn("SEC-001 reused with a different title", fails[0])

    def test_same_title_in_history_passes(self):
        row = _row("SEC-001", "confirmed", "agent", "Hardcoded database password", "`src/app.py:1`")
        self.put(row)
        _write(self.audit, "history/2026-01-01/01-architecture-issues.md", _audit([row]))
        self.assertEqual(rq.check(self.audit, self.repo), [])

    def test_quality_passes_examples(self):
        audit = os.path.join(REPO, "examples", "mode-2-audit-monorepo")
        repo = os.path.join(FIXTURES, "monorepo")
        self.assertEqual(rq.check(audit, repo), [])

    def test_examples_signals_are_extractable(self):
        rows = vp.extract(os.path.join(REPO, "examples", "mode-2-audit-monorepo"))
        self.assertGreater(len(rows), 5)
        self.assertTrue(all(r["rule"] and r["evidence"] for r in rows))

    def test_cli_exit_codes(self):
        self.put(_row("SEC-001", "confirmed", "agent", "Bad", "`src/ghost.py:3`"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(rq.main([self.audit, "--repo", self.repo]), 1)
            self.assertEqual(rq.main([self.audit, "--repo", os.path.join(self.repo, "nope")]), 2)


if __name__ == "__main__":
    unittest.main()
