from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from helpers import FIXTURES, SCRIPTS
from schema_validate import validate
from dfscan import patterns, registry
from dfscan.fingerprint import signal_fingerprint
from dfscan.model import Signal
from dfscan.report import scan

MONO = os.path.join(FIXTURES, "monorepo")
CLEAN = os.path.join(FIXTURES, "clean-service")
KEYS_1_0_1 = {
    "schema_version": int, "root": str, "scope": list, "detected_stack": list,
    "frameworks": list, "candidate_folders": list, "import_graph": dict, "routes": list,
    "hotspots": dict, "signals": list, "summary": dict, "notes": list,
}
SUMMARY_1_0_1 = {
    "files_scanned": int, "code_files": int, "approx_loc": int, "test_files": int,
    "signals_by_category": dict, "truncated": dict, "routes_total": int,
}


def _schema() -> dict:
    with open(os.path.join(SCRIPTS, "scan.schema.json"), encoding="utf-8") as fh:
        return json.load(fh)


class RegistryTests(unittest.TestCase):
    def test_every_emitted_rule_is_registered(self):
        r = scan(MONO, use_git=False)
        self.assertTrue(r["signals"])
        for s in r["signals"]:
            self.assertIn(s["rule"], registry.RULES)
            self.assertEqual(s["rule_version"], registry.RULES[s["rule"]].version)
            self.assertEqual(len(s["fingerprint"]), 16)
        self.assertEqual(len(registry.RULES), 26)

    def test_invalid_signal_dropped(self):
        def bad_rule(fi, text):
            if fi.rel.endswith("app.py"):
                return [Signal("swallowed-error", "Bogus", "High", fi.rel, 1, "x", "high")]
            return []

        with mock.patch.object(patterns, "FILE_RULES", [bad_rule]):
            r = scan(MONO, use_git=False)
        self.assertEqual(r["summary"]["invalid_signals"], 1)
        self.assertFalse([s for s in r["signals"] if s["rule"] == "swallowed-error"])

    def test_validate_signal_rules(self):
        files = {"a/b.py", "a/c.py"}
        ok = Signal("no-tests", "Maintainability", "Medium", "a", 0, "x", "medium")
        self.assertIsNone(registry.validate_signal(ok, files))
        for bad in (
            Signal("nope", "Security", "High", "a/b.py", 1, "x", "high"),
            Signal("no-tests", "Security", "Urgent", "a/b.py", 1, "x", "high"),
            Signal("no-tests", "Security", "High", "a/b.py", 1, "x", "sure"),
            Signal("no-tests", "Security", "High", "a/zzz.py", 1, "x", "high"),
            Signal("no-tests", "Security", "High", "a/b.py", -1, "x", "high"),
        ):
            self.assertIsNotNone(registry.validate_signal(bad, files), bad)

    def test_fingerprint_line_independent(self):
        code = "def handler():\n    requests.get(url, 5)\n"
        shifted = "# c\n" * 5 + code
        a = signal_fingerprint("http-no-timeout", "x.py", code, 2, "Python")
        b = signal_fingerprint("http-no-timeout", "x.py", shifted, 7, "Python")
        self.assertEqual(a, b)
        other = "def handler():\n    requests.post(url, 5)\n"
        self.assertNotEqual(a, signal_fingerprint("http-no-timeout", "x.py", other, 2, "Python"))
        renamed = "def other():\n    requests.get(url, 5)\n"
        self.assertNotEqual(a, signal_fingerprint("http-no-timeout", "x.py", renamed, 2, "Python"))

    def test_fingerprint_formfeed_and_crlf_equivalence(self):
        code = "def handler():\n    requests.get(url, 5)\n"
        ff = "\x0c\n" + code
        self.assertEqual(signal_fingerprint("r", "x.py", code, 2, "Python"),
                         signal_fingerprint("r", "x.py", ff, 3, "Python"))
        crlf = code.replace("\n", "\r\n")
        self.assertEqual(signal_fingerprint("r", "x.py", code, 2, "Python"),
                         signal_fingerprint("r", "x.py", crlf, 2, "Python"))

    def test_fingerprint_occurrence_distinguishes_duplicates(self):
        src = "import requests\n\ndef f(u):\n    requests.get(u)\n    requests.get(u)\n"
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src)
            runs = [[s["fingerprint"] for s in scan(tmp, use_git=False)["signals"]
                     if s["rule"] == "http-no-timeout"] for _ in range(2)]
        self.assertEqual(len(runs[0]), 2)
        self.assertNotEqual(runs[0][0], runs[0][1])
        self.assertEqual(runs[0], runs[1])

    def test_schema_validator_flags_missing_key(self):
        r = scan(MONO, use_git=False)
        del r["meta"]
        errs = validate(r, _schema())
        self.assertTrue(any("meta" in e for e in errs), errs)

    def test_schema3_additive(self):
        r = scan(MONO, use_git=False)
        self.assertEqual(r["schema_version"], 3)
        for k, t in KEYS_1_0_1.items():
            self.assertIsInstance(r[k], t, k)
        for k, t in SUMMARY_1_0_1.items():
            self.assertIsInstance(r["summary"][k], t, k)
        self.assertEqual(r["summary"]["invalid_signals"], 0)
        self.assertEqual(r["summary"]["suppressed"], 0)
        self.assertIs(r["summary"]["partial"], False)
        self.assertEqual(r["skipped_files"], [])
        self.assertEqual(r["meta"]["schema_version"], 3)

    def test_scan_validates_against_schema(self):
        schema = _schema()
        with tempfile.TemporaryDirectory() as empty:
            for root in (MONO, CLEAN, empty):
                self.assertEqual(validate(scan(root, use_git=False), schema), [], root)

    def test_meta_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = scan(tmp, use_git=True)["meta"]
        self.assertEqual(m["repo_revision"], "unknown")
        self.assertEqual(m["plugin_version"], "1.0.2")
        self.assertTrue(m["timestamp"].endswith("Z"))
        self.assertEqual(set(m["rule_versions"]), set(registry.RULES))

    def test_registry_metadata_complete(self):
        for rid, meta in registry.RULES.items():
            self.assertEqual(rid, meta.id)
            for f in ("version", "description", "limitations", "remediation"):
                self.assertTrue(getattr(meta, f).strip(), "%s.%s" % (rid, f))
            self.assertIn(meta.category, registry.CATEGORIES)
            self.assertIn(meta.default_severity, registry.SEVERITIES)
            self.assertIn(meta.confidence, registry.CONFIDENCES)


# ---------------------------------------------------------------- release review 1.0.2 (area A)
class ReviewRegistryTests(unittest.TestCase):
    def test_hardcoded_secret_version_bumped_and_limitations_name_keys(self):
        meta = registry.RULES["hardcoded-secret"]
        self.assertEqual(meta.version, "7")  # key words, masking and dotenv comments changed
        for word in ("access key", "client secret", "secret access key", "UTF-16"):
            self.assertIn(word, meta.limitations)

    def test_ensure_loaded_is_idempotent(self):
        registry.ensure_loaded()
        n = len(registry.RULES)
        registry.ensure_loaded()
        self.assertEqual(len(registry.RULES), n)
        self.assertIn("http-no-timeout", registry.RULES)

    def test_long_snippet_masked_before_truncation(self):
        tail = "pass" + "word=\"" + "Zq8f" + "Hkd9" + "3LmP\""
        sig = Signal("http-no-timeout", "Reliability", "High", "a.py", 1, "x" * 140 + " " + tail,
                     "high")
        self.assertLessEqual(len(sig.snippet), 160)
        self.assertNotIn("Zq8f", sig.snippet)


if __name__ == "__main__":
    unittest.main()
