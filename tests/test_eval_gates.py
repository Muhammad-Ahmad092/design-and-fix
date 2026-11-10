from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

from helpers import REPO, SCRIPTS  # noqa: F401  (puts SCRIPTS on sys.path)

EVALS = os.path.join(REPO, "evals")
if EVALS not in sys.path:
    sys.path.insert(0, EVALS)

import agreement  # noqa: E402
import regression  # noqa: E402
from dfscan import registry  # noqa: E402


def _report(rows, versions=None):
    """A minimal precision report; `versions` defaults to the registry's current rule versions."""
    registry.ensure_loaded()
    if versions is None:
        versions = {r: (registry.RULES[r].version if r in registry.RULES else "1")
                    for r, _, _ in rows}
    head = ["- rule versions: " + ", ".join("%s=%s" % kv for kv in sorted(versions.items())), ""]
    out = ["# x", ""] + (head if versions else []) + [
        "| Rule | Corpus signals | Sample n | tp | fp | unsure | Precision | 95% CI |",
        "|---|---|---|---|---|---|---|---|"]
    for rule, tp, fp in rows:
        out.append("| %s | %d | %d | %d | %d | 0 | x | x |" % (rule, tp + fp, tp + fp, tp, fp))
    return "\n".join(out) + "\n"


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = regression.main(argv)
    return code, buf.getvalue()


class AgreementTests(unittest.TestCase):
    def test_kappa_perfect_and_chance(self):
        self.assertAlmostEqual(agreement.kappa(["tp", "fp"] * 5, ["tp", "fp"] * 5), 1.0)
        self.assertAlmostEqual(agreement.kappa(["tp", "tp", "fp", "fp"], ["tp", "fp", "tp", "fp"]), 0.0)

    def test_blind_copy_strips_labels(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "a"), os.path.join(d, "b")
            os.mkdir(src)
            _write(os.path.join(src, "r.json"), json.dumps(
                [{"repo": "x", "file": "f", "line": 1, "label": "tp", "note": "n"}]))
            agreement.blind_copy(src, dst)
            with open(os.path.join(dst, "r.json"), encoding="utf-8") as fh:
                entry = json.load(fh)[0]
            self.assertNotIn("label", entry)
            self.assertNotIn("note", entry)
            self.assertEqual(entry["file"], "f")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.floors = os.path.join(self.tmp.name, "floors.json")
        self.report = os.path.join(self.tmp.name, "precision.md")

    def _args(self):
        """Gate arguments pinned to the live fixture scan, so that only the floors vary."""
        exp = os.path.join(self.tmp.name, "expected.json")
        fp = os.path.join(self.tmp.name, "fp.json")
        _write(exp, json.dumps({"monorepo": {"must_find": []}}))
        _write(fp, json.dumps(regression.fingerprints(regression._scan_fixture())))
        return ["--floors", self.floors, "--report", self.report,
                "--expected", exp, "--fingerprints", fp]

    def test_floor_breach_exits_1(self):
        _write(self.floors, json.dumps({"r": {"floor": 0.7, "n": 12}}))
        _write(self.report, _report([("r", 5, 7)]))
        code, out = _run(self._args())
        self.assertEqual(code, 1)
        self.assertIn("r: precision", out)

    def test_small_n_rule_never_gates(self):
        _write(self.floors, json.dumps({"r": {"floor": 0.7, "n": 12}}))
        _write(self.report, _report([("r", 1, 8)]))
        self.assertEqual(_run(self._args())[0], 0)

    def test_at_floor_passes(self):
        _write(self.floors, json.dumps({"r": {"floor": 0.5, "n": 12}}))
        _write(self.report, _report([("r", 5, 5)]))
        self.assertEqual(_run(self._args())[0], 0)

    def test_fingerprint_change_exits_1(self):
        _write(self.floors, "{}")
        _write(self.report, _report([]))
        args = self._args()
        self.assertEqual(_run(args)[0], 0)
        fp = args[args.index("--fingerprints") + 1]
        with open(fp, encoding="utf-8") as fh:
            stored = json.load(fh)
        stored.append(["x-rule", "x.py", "0000000000000000"])
        _write(fp, json.dumps(stored))
        code, out = _run(args)
        self.assertEqual(code, 1)
        self.assertIn("fingerprints", out)

    def test_recall_drop_exits_1(self):
        _write(self.floors, "{}")
        _write(self.report, _report([]))
        args = self._args()
        exp = args[args.index("--expected") + 1]
        _write(exp, json.dumps({"monorepo": {"must_find": [["no-such-rule", "nofile.py"]]}}))
        code, out = _run(args)
        self.assertEqual(code, 1)
        self.assertIn("recall", out)

    def test_stale_rule_version_exits_1(self):
        self.assertEqual(regression.stale_rules({"r": "1"}, {"r": "2"}, {"r": {}}),
                         ["precision is stale for r (measured v1, now v2): re-measure"])
        self.assertEqual(regression.stale_rules({"r": "1"}, {"r": "2"}, {}), [])  # unmeasured
        self.assertEqual(regression.measured_versions("- rule versions: a=1, b=22\n"),
                         {"a": "1", "b": "22"})
        registry.ensure_loaded()
        rid = sorted(registry.RULES)[0]
        _write(self.floors, "{}")
        _write(self.report, _report([(rid, 5, 5)], versions={rid: "0-old"}))
        code, out = _run(self._args())
        self.assertEqual(code, 1)
        self.assertIn("precision is stale for %s" % rid, out)

    def test_missing_rule_versions_line_exits_2(self):
        registry.ensure_loaded()
        rid = sorted(registry.RULES)[0]
        _write(self.floors, "{}")
        _write(self.report, _report([(rid, 5, 5)], versions={}))
        code, _ = _run(self._args())
        self.assertEqual(code, 2)

    def test_measured_needs_n(self):
        stats = regression.parse_report(_report([("a", 4, 0), ("b", 5, 0), ("c", 10, 0)]))
        self.assertEqual(regression.make_measured(stats), {"b": "medium", "c": "high"})

    def test_floors_and_measured_generation(self):
        stats = regression.parse_report(_report([("a", 9, 1), ("b", 1, 9), ("c", 6, 4)]))
        floors = regression.make_floors(stats)
        self.assertEqual(floors["a"], {"floor": 0.8, "n": 10})
        self.assertEqual(floors["b"]["floor"], 0.0)
        self.assertEqual(regression.make_measured(stats), {"a": "high", "b": "low", "c": "medium"})


class MeasuredConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "measured.json")
        self.old = (registry.MEASURED_PATH, registry._measured)
        self.addCleanup(self._restore)
        registry.MEASURED_PATH = self.path
        registry._measured = None

    def _restore(self):
        registry.MEASURED_PATH, registry._measured = self.old

    def test_measured_confidence_overrides_registry(self):
        _write(self.path, json.dumps({"zz-test-a": "low", "zz-test-b": "high", "bad": "huge"}))
        self.assertEqual(registry.measured_confidence("zz-test-a"), "low")
        self.assertIsNone(registry.measured_confidence("absent"))
        self.assertIsNone(registry.measured_confidence("bad"))

        def meta(rid, conf):
            return registry.RuleMeta(rid, "1", "Security", "High", conf, "d", "l", "r")

        try:
            registry.register(meta("zz-test-a", "high"))
            registry.register(meta("zz-test-b", "medium"))  # measured high must not raise it
            registry.register(meta("zz-test-c", "medium"))  # absent: registry value kept
            self.assertEqual(registry.RULES["zz-test-a"].confidence, "low")
            self.assertEqual(registry.RULES["zz-test-b"].confidence, "medium")
            self.assertEqual(registry.RULES["zz-test-c"].confidence, "medium")
        finally:
            for rid in ("zz-test-a", "zz-test-b", "zz-test-c"):
                registry.RULES.pop(rid, None)

    def test_missing_file_is_empty(self):
        self.assertIsNone(registry.measured_confidence("anything"))


class CiTests(unittest.TestCase):
    def test_ci_release_gate_job_runs_on_tags(self):
        with open(os.path.join(REPO, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("\n  release-gate:\n", text)
        block = text.split("\n  release-gate:\n", 1)[1]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v')", block)
        self.assertIn("python evals/regression.py", block)
        self.assertIn("timeout-minutes:", block)
        self.assertIn("needs: [test]", block)
        self.assertIn('tags: ["v*"]', text)
        self.assertIn("permissions:\n  contents: read", text)
        for ref in re.findall(r"uses:\s*(\S+)", block):
            self.assertRegex(ref, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
