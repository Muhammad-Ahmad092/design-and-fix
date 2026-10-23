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


if __name__ == "__main__":
    unittest.main()
