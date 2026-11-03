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



def _graph(files):
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            _write(d, rel, text)
        return build_graph(walk(d))


class R2ImportFalsePositiveTests(unittest.TestCase):
    def test_cycle_through_init_downgraded(self):  # R2-fp5
        g = _graph({"pkg/__init__.py": "from .app import App\n",
                    "pkg/app.py": "from pkg import helpers\nclass App: pass\n",
                    "pkg/helpers.py": "x = 1\n"})
        self.assertEqual(g["cycles"], [["pkg/__init__.py", "pkg/app.py", "pkg/__init__.py"]])
        self.assertEqual(g["cycles_meta"], [{"via_init": True}])
        sig = graph_signals(g)[0]
        self.assertEqual((sig.severity_hint, sig.confidence), ("Medium", "low"))
        self.assertIn("through package __init__ (often a re-export, not a runtime cycle)", sig.snippet)
        # JS index barrel is treated the same way.
        g = _graph({"src/index.ts": "export * from './a';\n",
                    "src/a.ts": "import { b } from './index';\nexport const a = b;\n"})
        self.assertEqual(g["cycles_meta"], [{"via_init": True}])
        # A plain module cycle keeps High / high and via_init false.
        g = _graph({"m/a.py": "from . import b\n", "m/b.py": "from . import a\n"})
        self.assertEqual(g["cycles_meta"], [{"via_init": False}])
        sig = graph_signals(g)[0]
        self.assertEqual((sig.severity_hint, sig.confidence), ("High", "high"))

    def test_real_cycle_inside_init_component_stays_high(self):  # R2-fp5 guard
        g = _graph({"pkg/__init__.py": "from .a import x\n",
                    "pkg/a.py": "from pkg import b\nimport pkg\n",
                    "pkg/b.py": "from pkg import a\n"})
        self.assertEqual(g["cycles_meta"], [{"via_init": False}])
        self.assertNotIn("__init__", " ".join(g["cycles"][0]))


class BaselineImportFalsePositiveTests(unittest.TestCase):
    def test_function_local_import_not_a_cycle(self):  # import-cycle 1
        g = _graph({"app/models.py": "from app import utils\nx = 1\n",
                    "app/utils.py": "def f():\n    from app import models\n    return models.x\n"})
        self.assertEqual(g["cycles"], [])
        self.assertEqual(g["edges"], 2)  # deferred import still counts as a dependency
        # Module-level conditional imports are still edges.
        g = _graph({"app/models.py": "from app import utils\n",
                    "app/utils.py": "try:\n    from app import models\nexcept ImportError:\n    models = None\n"})
        self.assertEqual(len(g["cycles"]), 1)

    def test_type_checking_import_not_a_cycle(self):  # import-cycle 1 (type-only)
        g = _graph({"app/a.py": "from app import b\n",
                    "app/b.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app import a\n"})
        self.assertEqual(g["cycles"], [])
        g = _graph({"app/a.py": "from app import b\n",
                    "app/b.py": "import typing as t\n\nif t.TYPE_CHECKING:  # pragma: no cover\n"
                                "    from .a import A\n"})
        self.assertEqual(g["cycles"], [])

    def test_init_reexport_cycle_downgraded(self):  # import-cycle 2
        g = _graph({"flask/__init__.py": "from .app import Flask as Flask\n",
                    "flask/app.py": "from . import json\nfrom flask import cli\n",
                    "flask/cli.py": "x = 1\n", "flask/json/__init__.py": "x = 1\n"})
        self.assertEqual(g["cycles"], [["flask/__init__.py", "flask/app.py", "flask/__init__.py"]])
        self.assertEqual(g["cycles_meta"], [{"via_init": True}])
        self.assertEqual(graph_signals(g)[0].confidence, "low")
        g = _graph({"msgs/__init__.py": "from msgs.api import *\n",
                    "msgs/api.py": "from msgs import constants\nimport msgs\n",
                    "msgs/constants.py": "x = 1\n"})
        self.assertEqual(g["cycles_meta"], [{"via_init": True}])

    def test_ts_type_only_and_dynamic_imports_not_cycles(self):  # import-cycle 3
        g = _graph({"src/a.ts": "import type { B } from './b';\nexport const a = 1;\n",
                    "src/b.ts": "import { a } from './a';\nexport type B = number;\n"})
        self.assertEqual(g["cycles"], [])
        g = _graph({"src/a.ts": "import { type B } from './b';\nexport const a = 1;\n",
                    "src/b.ts": "import { a } from './a';\nexport type B = number;\n"})
        self.assertEqual(g["cycles"], [])
        g = _graph({"src/a.ts": "export const load = () => import('./b');\n",
                    "src/b.ts": "import { load } from './a';\n"})
        self.assertEqual(g["cycles"], [])

    def test_god_module_ignores_test_fan_in_and_test_candidates(self):  # god-module 2, 3
        files = {"pkg/hub.py": "x = 1\n" * 520}
        for i in range(10):
            files["tests/test_u%d.py" % i] = "from pkg import hub\n"
        for i in range(3):
            files["pkg/u%d.py" % i] = "from pkg import hub\n"
        # Total fan-in >= 10 still triggers; fewer than 10 non-test importers -> low confidence.
        g = _graph(files)
        self.assertEqual([(x["file"], x["fan_in"], x["fan_in_non_test"]) for x in g["god_modules"]],
                         [("pkg/hub.py", 13, 3)])
        self.assertEqual(graph_signals(g)[0].confidence, "low")
        # Test files (by file name) are never candidates.
        files = {"pkg/test_hub.py": "x = 1\n" * 520}
        for i in range(12):
            files["pkg/u%d.py" % i] = "from pkg import test_hub\n"
        self.assertEqual(_graph(files)["god_modules"], [])

    def test_god_module_in_library_test_folder_counts(self):  # fix round 1 (django/test/utils.py)
        files = {"django/test/utils.py": "x = 1\n" * 520}
        for i in range(12):
            files["django/m%d.py" % i] = "from django.test import utils\n"
        self.assertEqual([x["file"] for x in _graph(files)["god_modules"]], ["django/test/utils.py"])

    def test_god_module_cohesive_collection_low(self):  # god-module 1 (not fixable: lower confidence)
        files = {"pkg/validators.py": "x = 1\n" * 520}
        for i in range(10):
            files["pkg/u%d.py" % i] = "from pkg import validators\n"
        sig = graph_signals(_graph(files))[0]
        self.assertEqual((sig.rule, sig.confidence), ("god-module", "low"))


# ---------------------------------------------------------------- release review 1.0.2 (area A)
class DeferredImportLinearTests(unittest.TestCase):  # review M8
    def test_deferred_lines_semantics(self):
        from dfscan.imports import _py_deferred_lines
        text = ("import os\n"
                "try:\n    import json\nexcept ImportError:\n    json = None\n"
                "def f():\n    import re\n    if x:\n        from a import b\n"
                "if TYPE_CHECKING:\n    from c import d\n"
                "class K:\n    import e\n    def m(self):\n\n        import g\n")
        self.assertEqual(_py_deferred_lines(text), {7, 9, 11, 16})

    def test_many_indented_imports_are_fast(self):
        import time
        from dfscan.imports import _py_deferred_lines
        text = "def f():\n" + "    import os\n" * 4000
        t0 = time.perf_counter()
        got = _py_deferred_lines(text)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(len(got), 4000)


if __name__ == "__main__":
    unittest.main()
