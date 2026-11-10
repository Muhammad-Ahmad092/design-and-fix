"""Scanner hardening from the 1.1.0 final review (area A): linear-time regexes, masking of every
output field, scope case on case-insensitive file systems, per-rule failure isolation, bounded
per-route and per-loop rescans, the snippet mask window and SARIF default-off rules."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from typing import Dict, List
from unittest import mock

from helpers import FIXTURES
from dfscan import patterns
from dfscan.deps_rules import run_deps_rules
from dfscan.imports import build_graph
from dfscan.model import SNIPPET_MASK_WINDOW, Signal
from dfscan.report import render_markdown, scan
from dfscan.routes import find_routes, route_signals
from dfscan.sarif import to_sarif
from dfscan.walker import walk

MONO = os.path.join(FIXTURES, "monorepo")
BLANKS = 50000
FAST = 1.0  # seconds; each input below took 10 s to minutes before the fix


def _tree(root: str, files: Dict[str, str]) -> None:
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def _github_token() -> str:
    # Assembled at runtime so no provider-shaped literal sits in the source.
    return "gh" + "p_" + ("A1b2C3d4E5" * 4)[:36]


class BlankLineRunTests(unittest.TestCase):
    """`^\\s*` with re.M crosses newlines and backtracks quadratically over blank-line runs."""

    def _timed(self, files: Dict[str, str], run) -> float:
        with tempfile.TemporaryDirectory() as d:
            _tree(d, files)
            walked = walk(d)
            t0 = time.perf_counter()
            run(walked)
            return time.perf_counter() - t0

    def test_python_indented_import_precheck(self):
        files = {"b.py": "import os\n" + " \n" * BLANKS}
        self.assertLess(self._timed(files, build_graph), FAST)

    def test_go_single_import(self):
        files = {"go.mod": "module example.com/m\n", "main.go": "package main\n" + " \n" * BLANKS}
        self.assertLess(self._timed(files, build_graph), FAST)

    def test_pyproject_build_system_search(self):
        files = {"pyproject.toml": "[project]\nname = 'x'\n" + " \n" * BLANKS}
        self.assertLess(self._timed(files, run_deps_rules), FAST)

    def test_gemfile_gem_search(self):
        files = {"Gemfile": "source 'https://rubygems.org'\n" + " \n" * BLANKS}
        self.assertLess(self._timed(files, run_deps_rules), FAST)

    def test_indented_import_still_detected(self):
        # The [ \t] pre-check still finds tab- and space-indented imports (function-local edge).
        with tempfile.TemporaryDirectory() as d:
            _tree(d, {"app/models.py": "from app import utils\n",
                      "app/utils.py": "def f():\n\tfrom app import models\n"})
            self.assertEqual(build_graph(walk(d))["cycles"], [])


class OutputMaskingTests(unittest.TestCase):
    def test_route_path_and_notes_are_masked_everywhere(self):  # final review I2
        token = _github_token()
        files = {"app/c.py": ("from flask import Flask\napp = Flask(__name__)\n\n"
                              "@app.route('/hooks/%s', methods=['POST'])\n"
                              "def hook():\n    return 'ok'\n" % token)}
        with tempfile.TemporaryDirectory() as d:
            _tree(d, files)
            result = scan(d, use_git=False, exclude=["vendor/" + token])
        paths = [r["path"] for r in result["routes"]]
        self.assertEqual(paths, ["/hooks/***"])
        self.assertTrue(any("vendor/***" in n for n in result["notes"]), result["notes"])
        for rendered in (json.dumps(result), render_markdown(result), json.dumps(to_sarif(result))):
            self.assertNotIn(token, rendered)
            self.assertNotIn(token[:20], rendered)

    def test_scan_json_of_a_previous_run_is_clean(self):
        # The scanner's own output, written inside the scan root, must not create findings.
        token = _github_token()
        with tempfile.TemporaryDirectory() as d:
            _tree(d, {"app/c.py": ("from flask import Flask\napp = Flask(__name__)\n"
                                   "@app.route('/hooks/%s', methods=['POST'])\n"
                                   "def hook():\n    return 'ok'\n" % token)})
            first = scan(d, use_git=False)
            _tree(d, {"reports/scan.json": json.dumps(first, indent=2) + "\n"})
            second = scan(d, use_git=False)
        self.assertEqual([s for s in second["signals"] if s["file"] == "reports/scan.json"], [])

    def test_mask_window_drops_a_token_split_at_the_edge(self):  # final review M6
        token = _github_token()
        line = 'token="%s" %s rest' % ("a" * 1000, token)
        start = line.index(token)
        self.assertLess(start, SNIPPET_MASK_WINDOW)
        self.assertGreater(start + len(token), SNIPPET_MASK_WINDOW)
        for sig in (Signal("swallowed-error", "Reliability", "Medium", "a.py", 1, line, "low"),
                    Signal("swallowed-error", "Reliability", "Medium", "a.py", 1, "x", "low",
                           uncertain_reason=line)):
            for text in (sig.snippet, sig.uncertain_reason):
                self.assertNotIn(token[:8], text)
        # A short line keeps its text (the window logic only applies past the window).
        self.assertEqual(Signal("r", "c", "Low", "a.py", 1, "x = 1", "low").snippet, "x = 1")


class ScopeCaseTests(unittest.TestCase):
    def test_scope_spelled_in_another_case(self):  # final review M2
        with tempfile.TemporaryDirectory() as d:
            _tree(d, {"Services/api/a.py": "x = 1\n", "other/b.py": "y = 2\n"})
            insensitive = os.path.isdir(os.path.join(d, "SERVICES", "API"))
            r = scan(d, scope=["SERVICES/API"], use_git=False)
            exact = scan(d, scope=["Services/api"], use_git=False)
        if insensitive:  # Windows, macOS: the scope resolves to the folders' real names
            self.assertEqual(r["scope"], ["Services/api"])
            self.assertEqual(r["summary"]["files_scanned"], exact["summary"]["files_scanned"])
            self.assertEqual(r["summary"]["files_scanned"], 1)
        else:  # case-sensitive file system: a different folder, left as given
            self.assertEqual(r["scope"], ["SERVICES/API"])


class RuleIsolationTests(unittest.TestCase):
    def test_one_failing_rule_keeps_the_rest(self):  # final review M3
        def rule_boom(fi, text):
            if fi.rel.endswith("a.py"):
                raise ValueError("boom")
            return [Signal("swallowed-error", "Reliability", "Medium", fi.rel, 1, "x", "low")]

        secret = "gh" + "p_" + "Z9y8X7w6V5" * 4
        files = {"svc/a.py": 'import requests\nT = "%s"\nrequests.get(u)\n' % secret,
                 "svc/b.py": "x = 1\n", "svc/c.py": "y = 2\n"}
        with tempfile.TemporaryDirectory() as d:
            _tree(d, files)
            with mock.patch.object(patterns, "FILE_RULES",
                                   [rule_boom] + list(patterns.FILE_RULES)):
                r = scan(d, use_git=False)
        got = {(s["rule"], s["file"]) for s in r["signals"]}
        self.assertIn(("swallowed-error", "svc/b.py"), got)
        self.assertIn(("hardcoded-secret", "svc/a.py"), got)
        self.assertIn(("http-no-timeout", "svc/a.py"), got)
        self.assertIn("rule boom failed on svc/a.py (ValueError): its signals there are missing",
                      r["notes"])
        self.assertFalse(any("pattern_rules failed" in n for n in r["notes"]))


class RescanBoundTests(unittest.TestCase):
    def test_many_express_routes_with_late_use(self):  # final review M4
        # An undeclared receiver makes every route look for an enclosing function taking it.
        n = 4000
        body = "".join("router.post('/r%d', h%d);\n" % (i, i) for i in range(n))
        with tempfile.TemporaryDirectory() as d:
            _tree(d, {"routes.js": body + "router.use(auth);\n"})
            walked = walk(d)
            routes = find_routes(walked)
            t0 = time.perf_counter()
            sigs = route_signals(routes, walked)
            took = time.perf_counter() - t0
        self.assertEqual(len(routes), n)
        self.assertEqual(len([s for s in sigs if s.rule == "unauth-write-route"]), n)
        self.assertLess(took, 3.0)  # about 16 s before the function headers were memoised

    def test_nested_go_loops(self):  # final review M4
        n = 8000
        text = ("package main\n\nfunc f(db *sql.DB) {\n" + "for i := 0; i < n; i++ {\n" * n
                + "db.Query(\"SELECT 1\")\n" + "}\n" * n + "}\n")
        with tempfile.TemporaryDirectory() as d:
            _tree(d, {"main.go": text})
            fi = [f for f in walk(d) if f.rel == "main.go"][0]
            t0 = time.perf_counter()
            sigs = patterns.rule_query_in_loop(fi, text)
            took = time.perf_counter() - t0
        self.assertEqual(len(sigs), 1)
        self.assertLess(took, 3.0)  # about 7 s before _db_calls was memoised per line


class SarifDefaultOffTests(unittest.TestCase):
    def test_default_off_rule_is_disabled_in_sarif(self):  # final review M7
        rules = {r["id"]: r for r in
                 to_sarif(scan(MONO, use_git=False))["runs"][0]["tool"]["driver"]["rules"]}
        self.assertIs(rules["openapi-drift"]["defaultConfiguration"]["enabled"], False)
        on: List[str] = [rid for rid, r in rules.items() if "enabled" in r["defaultConfiguration"]]
        self.assertEqual(on, ["openapi-drift"])


if __name__ == "__main__":
    unittest.main()
