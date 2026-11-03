from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import FIXTURES, REPO, run_cli
from dfscan.hotspots import compute_hotspots, parse_git_log
from dfscan.report import scan
from dfscan.walker import walk

MONO = os.path.join(FIXTURES, "monorepo")
CLEAN = os.path.join(FIXTURES, "clean-service")
EVALS = os.path.join(REPO, "evals")


class ReportTests(unittest.TestCase):
    def test_parse_git_log(self):
        self.assertEqual(parse_git_log("a.ts\nb.ts\n\na.ts\n"), {"a.ts": 2, "b.ts": 1})

    def test_hotspots_unavailable_outside_git(self):
        tmp = tempfile.mkdtemp()
        try:
            dest = os.path.join(tmp, "m")
            shutil.copytree(MONO, dest)
            h = compute_hotspots(dest, walk(dest))
            self.assertFalse(h["available"])
            self.assertEqual(h["files"], [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_git_flag_note(self):
        self.assertFalse(scan(MONO, use_git=False)["hotspots"]["available"])

    def test_scope_limits_signals(self):
        r = scan(MONO, scope=["services/catalog"], use_git=False)
        self.assertTrue(r["signals"])
        self.assertTrue(all(s["file"].startswith("services/catalog") for s in r["signals"]))
        self.assertTrue(any(f["path"] == "services/payments" for f in r["candidate_folders"]))
        self.assertEqual(r["summary"]["files_scanned"] > 0, True)

    def test_expected_eval_file(self):
        with open(os.path.join(EVALS, "scanner-expected.json"), encoding="utf-8") as fh:
            exp = json.load(fh)
        got = {(s["rule"], s["file"]) for s in scan(MONO, use_git=False)["signals"]}
        self.assertTrue({tuple(x) for x in exp["monorepo"]["must_find"]} <= got)
        self.assertFalse({tuple(x) for x in exp["monorepo"]["must_not_find"]} & got)
        self.assertLessEqual(len(scan(CLEAN, use_git=False)["signals"]),
                             exp["clean-service"]["max_signals"])

    def test_markdown_format(self):
        code, out = run_cli(MONO, "--no-git", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Scan summary"))
        self.assertIn("## Signals", out)
        self.assertIn("## Hotspots", out)

    def test_empty_repo_full_scan(self):
        tmp = tempfile.mkdtemp()
        try:
            r = scan(tmp, use_git=False)
            self.assertEqual((r["candidate_folders"], r["signals"], r["routes"]), ([], [], []))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_route_signals_and_routes_are_capped(self):  # final review 2
        body = "".join("app.post('/w%d', h);\n" % i for i in range(210))
        body += "".join("app.get('/r%d', h);\n" % i for i in range(400))
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "svc", "x"))
            with open(os.path.join(tmp, "svc", "x", "a.js"), "w") as fh:
                fh.write(body)
            r = scan(tmp, use_git=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        n = sum(1 for s in r["signals"] if s["rule"] == "unauth-write-route")
        self.assertEqual(n, 200)
        self.assertEqual(r["summary"]["truncated"], {"unauth-write-route": 210})
        self.assertEqual(len(r["routes"]), 500)
        self.assertEqual(r["summary"]["routes_total"], 610)
        self.assertEqual(sum(r["summary"]["signals_by_category"].values()), len(r["signals"]))

    def test_git_argv_disables_quote_path(self):  # final review 12
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("dfscan.hotspots.shutil.which", return_value="git"):
            with mock.patch("dfscan.hotspots._find_repo_root", return_value=MONO):
                with mock.patch("dfscan.hotspots.subprocess.run", return_value=done) as run:
                    h = compute_hotspots(MONO, walk(MONO))
        argv = run.call_args[0][0]
        self.assertTrue(h["available"])
        self.assertEqual(argv[:6], ["git", "-C", os.path.abspath(MONO), "-c",
                                    "core.quotePath=false", "log"])


# ---------------------------------------------------------------- release review 1.0.2 (area A)
def _put(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class StampingReviewTests(unittest.TestCase):  # review I3, M11, rule-runner guard
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_many_signals_in_one_file_stamp_in_linear_time(self):
        import time
        _put(self.d, "big.py", "import requests\n" + "requests.get(url)\n" * 5000)
        t0 = time.perf_counter()
        r = scan(self.d, use_git=False, verbose=True)
        self.assertLess(time.perf_counter() - t0, 10.0)
        self.assertEqual(r["summary"]["truncated"], {"http-no-timeout": 5000})
        self.assertTrue(any(n.startswith("timing stamp: ") for n in r["notes"]), r["notes"])
        self.assertEqual(len({s["fingerprint"] for s in r["signals"]}), len(r["signals"]))

    def test_line_fingerprint_matches_signal_fingerprint(self):
        from dfscan.fingerprint import enclosing_names, line_fingerprint, signal_fingerprint
        text = ("import x\nclass A:\n    def run(self):\n        get('a', 1)\n\n"
                "async function go() {\n  fetch(u)\n}\nz = 2\r\n")
        lines = text.split("\n")
        names = enclosing_names(lines)
        for line in range(1, len(lines) + 3):
            for occ in (0, 2):
                self.assertEqual(line_fingerprint("r", "f.py", lines, names, line, occ),
                                 signal_fingerprint("r", "f.py", text, line, "Python",
                                                    occurrence=occ), line)

    def test_invalid_signals_get_a_note(self):
        from dfscan.model import Signal
        bad = Signal("http-no-timeout", "Nope", "High", "a.py", 1, "x", "high")
        _put(self.d, "a.py", "x = 1\n")
        with mock.patch("dfscan.report.run_infra_rules", return_value=[bad]):
            r = scan(self.d, use_git=False)
        self.assertEqual(r["summary"]["invalid_signals"], 1)
        self.assertIn("1 invalid signal(s) dropped", r["notes"])

    def test_crashing_rule_family_adds_note_and_scan_continues(self):
        _put(self.d, "a.py", "import requests\nrequests.get(u)\n")
        with mock.patch("dfscan.report.run_deps_rules", side_effect=KeyError("boom")):
            r = scan(self.d, use_git=False)
        self.assertIn("deps_rules failed (KeyError): its signals are missing", r["notes"])
        self.assertTrue([s for s in r["signals"] if s["rule"] == "http-no-timeout"])

    def test_rule_runner_notes_reach_output(self):
        def fake(files, notes=None):
            notes.append("skipped package.json: malformed JSON")
            return []
        _put(self.d, "a.py", "x = 1\n")
        with mock.patch("dfscan.report.run_deps_rules", side_effect=fake):
            r = scan(self.d, use_git=False)
        self.assertIn("skipped package.json: malformed JSON", r["notes"])

    def test_k8s_kind_line_with_trailing_comment(self):
        from dfscan.walker import is_k8s_workload
        self.assertTrue(is_k8s_workload("apiVersion: apps/v1\nkind: Deployment  # web\n"))
        self.assertTrue(is_k8s_workload("kind: 'StatefulSet' #db\n"))
        self.assertFalse(is_k8s_workload("kind: Service  # Deployment\n"))


if __name__ == "__main__":
    unittest.main()
