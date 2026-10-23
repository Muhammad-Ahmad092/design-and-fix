from __future__ import annotations

import os
import tempfile
import unittest

from helpers import FIXTURES
from dfscan.imports import build_graph, graph_signals
from dfscan.report import scan
from dfscan.walker import walk

MONO = os.path.join(FIXTURES, "monorepo")
CLEAN = os.path.join(FIXTURES, "clean-service")


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class ImportGraphTests(unittest.TestCase):
    def test_cycle_db_util(self):
        g = build_graph(walk(MONO))
        self.assertIn(["services/payments/src/lib/db.ts",
                       "services/payments/src/lib/util.ts",
                       "services/payments/src/lib/db.ts"], g["cycles"])

    def test_no_cycles_clean(self):
        self.assertEqual(build_graph(walk(CLEAN))["cycles"], [])

    def test_god_module_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "hub.ts", "export const x = 1;\n" * 520)
            for i in range(10):
                _write(d, "u%d.ts" % i, "import { x } from './hub';\n")
            g = build_graph(walk(d))
            self.assertEqual(len(g["god_modules"]), 1)
            self.assertEqual(g["god_modules"][0]["file"], "hub.ts")
            self.assertEqual(g["god_modules"][0]["fan_in"], 10)
            sigs = graph_signals(g)
            self.assertEqual([s.rule for s in sigs], ["god-module"])

    def test_python_relative_import_edge(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "pkg/a.py", "from .b import x\n")
            _write(d, "pkg/b.py", "x = 1\n")
            g = build_graph(walk(d))
            self.assertEqual(g["edges"], 1)
            self.assertEqual(g["modules"], 2)

    def test_python_absolute_and_go(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "pkg/__init__.py", "")
            _write(d, "pkg/mod.py", "x = 1\n")
            _write(d, "main.py", "import pkg.mod\n")
            _write(d, "svc/a.go", 'package svc\nimport (\n\t"example.com/m/lib"\n)\n')
            _write(d, "lib/l1.go", "package lib\n")
            _write(d, "lib/l2.go", "package lib\n")
            g = build_graph(walk(d))
            self.assertEqual(g["edges"], 3)

    def _edges(self, files, scopes=None):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                _write(d, rel, text)
            return build_graph(walk(d), scopes)

    def test_src_layout_cycle(self):  # final review 4c
        g = self._edges({"src/pkg/__init__.py": "",
                         "src/pkg/a.py": "from pkg.b import x\ny = 1\n",
                         "src/pkg/b.py": "from pkg.a import y\nx = 1\n"})
        self.assertIn(["src/pkg/a.py", "src/pkg/b.py", "src/pkg/a.py"], g["cycles"])

    def test_service_root_absolute_import(self):  # final review 4c
        g = self._edges({"services/x/app/__init__.py": "",
                         "services/x/app/main.py": "from app.models import M\n",
                         "services/x/app/models.py": "M = 1\n"})
        self.assertEqual(g["edges"], 1)

    def test_scope_root_absolute_import(self):  # final review 4c
        g = self._edges({"svc/core/main.py": "import util\n", "svc/util.py": "x = 1\n"},
                        scopes=["svc"])
        self.assertEqual(g["edges"], 1)

    def test_from_package_import_submodule(self):  # final review 4a
        g = self._edges({"pkg/__init__.py": "", "pkg/sub.py": "x = 1\n",
                         "main.py": "from pkg import sub\n"})
        self.assertEqual(g["edges"], 2)  # main -> pkg/__init__.py and main -> pkg/sub.py
        g = self._edges({"pkg/sub.py": "x = 1\n", "main.py": "from pkg import sub\n"})
        self.assertEqual(g["edges"], 1)  # namespace package, no __init__.py

    def test_multiline_parenthesised_import(self):  # final review 4b
        g = self._edges({"pkg/__init__.py": "", "pkg/a.py": "x = 1\n", "pkg/b.py": "y = 1\n",
                         "main.py": "from pkg import (\n    a,\n    b,\n)\n"})
        self.assertEqual(g["edges"], 3)

    def test_commented_imports_create_no_edges(self):  # final review 4d
        g = self._edges({"pkg/a.py": "# from .b import x\nz = 1\n", "pkg/b.py": "x = 1\n",
                         "web/a.ts": "// import { y } from './b';\n/* require('./b') */\n",
                         "web/b.ts": "export const y = 1;\n"})
        self.assertEqual(g["edges"], 0)

    def test_stdlib_name_not_shadowed_by_sibling_module(self):  # polish 3
        g = self._edges({"pkg/__init__.py": "", "pkg/sub/__init__.py": "",
                         "pkg/sub/c.py": "import json\n", "pkg/sub/json.py": "x = 1\n"})
        self.assertEqual(g["edges"], 0)
        g = self._edges({"pkg/__init__.py": "", "pkg/sub/__init__.py": "",
                         "pkg/sub/c.py": "import pkg.sub.json\n", "pkg/sub/json.py": "x = 1\n"})
        self.assertEqual(g["edges"], 1)  # reachable through its parent root
        g = self._edges({"pkg/__init__.py": "", "pkg/c.py": "from . import d\n",
                         "pkg/d.py": "x = 1\n"})
        self.assertEqual(g["edges"], 1)  # relative import

    def test_scan_includes_cycle_signal(self):
        r = scan(MONO, use_git=False)
        self.assertTrue(any(s["rule"] == "import-cycle" for s in r["signals"]))
        self.assertGreater(r["import_graph"]["modules"], 0)


if __name__ == "__main__":
    unittest.main()
