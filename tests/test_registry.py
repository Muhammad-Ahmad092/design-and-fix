from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from typing import Any, Dict, Optional
from unittest import mock

from helpers import FIXTURES, SCRIPTS
from schema_validate import validate
from dfscan import patterns, registry
from dfscan.fingerprint import signal_fingerprint
from dfscan.meta import PLUGIN_VERSION
from dfscan.model import Signal
from dfscan.registry import RuleMeta
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


# Release decision (1.1.0): openapi-drift measured 0% precision at n=14, so it ships off by default.
RELEASE_DEFAULT_OFF = ["openapi-drift"]
OPENAPI_NOTE = "rules off by default (enable in config): openapi-drift"


def _scan_with_rule_off(rule_id: str, config: Optional[Dict[str, Any]] = None) -> dict:
    """Scan the monorepo fixture with `rule_id` patched to default-off (restored afterwards)."""
    with mock.patch.object(registry.RULES[rule_id], "default_enabled", False):
        return scan(MONO, use_git=False, rule_config=(config or {}).get("rules"))


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
        self.assertEqual(m["plugin_version"], PLUGIN_VERSION)
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
        # 7 in 1.0.2 (key words, masking, dotenv comments); bumped again by the 1.1.0 rework.
        self.assertGreaterEqual(int(meta.version), 7)
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


class DefaultOffTests(unittest.TestCase):
    def test_only_release_decision_rules_are_off_by_default(self):
        off = sorted(rid for rid, m in registry.RULES.items() if not m.default_enabled)
        self.assertEqual(off, RELEASE_DEFAULT_OFF)

    def test_is_enabled_precedence(self):
        registry.RULES["demo-off"] = RuleMeta("demo-off", "1", "Security", "High", "low",
                                              "d", "l", "r", ["Python"], default_enabled=False)
        self.addCleanup(registry.RULES.pop, "demo-off")
        self.assertFalse(registry.is_enabled("demo-off", {}))
        self.assertTrue(registry.is_enabled("demo-off", {"demo-off": {"enabled": True}}))
        self.assertFalse(registry.is_enabled("http-no-timeout",
                                             {"http-no-timeout": {"enabled": False}}))
        self.assertTrue(registry.is_enabled("http-no-timeout", {}))
        self.assertTrue(registry.is_enabled("http-no-timeout",
                                            {"http-no-timeout": {"severity": "Low"}}))
        self.assertFalse(registry.is_enabled("openapi-drift", {}))
        self.assertTrue(registry.is_enabled("openapi-drift", {"openapi-drift": {"enabled": True}}))

    def test_default_off_rule_dropped_and_noted(self):  # Review Focus 1
        res = _scan_with_rule_off("http-no-timeout")
        self.assertNotIn("http-no-timeout", {s["rule"] for s in res["signals"]})
        self.assertNotIn("openapi-drift", {s["rule"] for s in res["signals"]})
        self.assertEqual(res["summary"]["default_off_rules"], ["http-no-timeout", "openapi-drift"])
        self.assertIn("rules off by default (enable in config): http-no-timeout, openapi-drift",
                      res["notes"])
        self.assertEqual(validate(res, _schema()), [])

    def test_config_reenables_default_off_rule(self):  # Review Focus 1
        res = _scan_with_rule_off("http-no-timeout",
                                  config={"rules": {"http-no-timeout": {"enabled": True}}})
        self.assertIn("http-no-timeout", {s["rule"] for s in res["signals"]})
        self.assertEqual(res["summary"]["default_off_rules"], RELEASE_DEFAULT_OFF)
        self.assertEqual([n for n in res["notes"] if "off by default" in n], [OPENAPI_NOTE])

    def test_config_disabling_default_off_rule_is_not_double_noted(self):
        res = _scan_with_rule_off("http-no-timeout",
                                  config={"rules": {"http-no-timeout": {"enabled": False}}})
        self.assertNotIn("http-no-timeout", {s["rule"] for s in res["signals"]})
        self.assertEqual(res["summary"]["default_off_rules"], RELEASE_DEFAULT_OFF)
        self.assertEqual([n for n in res["notes"] if "off by default" in n], [OPENAPI_NOTE])
        self.assertTrue([n for n in res["notes"] if n.startswith("rules disabled by config or profile: ")])

    def test_only_release_default_off_rules_noted_by_default(self):
        res = scan(MONO, use_git=False)
        self.assertEqual(res["summary"]["default_off_rules"], RELEASE_DEFAULT_OFF)
        self.assertEqual([n for n in res["notes"] if "off by default" in n], [OPENAPI_NOTE])

    def test_no_default_off_note_when_all_rules_enabled(self):
        res = scan(MONO, use_git=False, rule_config={"openapi-drift": {"enabled": True}})
        self.assertEqual(res["summary"]["default_off_rules"], [])
        self.assertFalse([n for n in res["notes"] if "off by default" in n])

    def test_rule_docs_default_column(self):
        import gen_rule_docs
        text = gen_rule_docs.render()
        header = [ln for ln in text.splitlines() if ln.startswith("| Rule |")][0]
        self.assertIn("| Default |", header)
        row = [ln for ln in text.splitlines() if ln.startswith("| `http-no-timeout` |")][0]
        self.assertIn("| on |", row)
        with mock.patch.object(registry.RULES["http-no-timeout"], "default_enabled", False):
            row = [ln for ln in gen_rule_docs.render().splitlines()
                   if ln.startswith("| `http-no-timeout` |")][0]
        self.assertIn("| off |", row)


class UncertainReasonTests(unittest.TestCase):
    def test_uncertain_reason_secret_masked_and_capped(self):
        from dfscan.model import REASON_MAX
        from dfscan.sarif import to_sarif
        secret = "Zq8f" + "Hkd9" + "3LmP" + "x7Rt"
        reason = "auth config read: pass" + "word=\"" + secret + "\""
        short = Signal("r", "Security", "High", "a.py", 1, "x", "low", uncertain_reason=reason)
        self.assertNotIn(secret, short.uncertain_reason)
        long_reason = "y" * 230 + " pass" + "word=\"" + secret + "\""
        cut = Signal("r", "Security", "High", "a.py", 1, "x", "low", uncertain_reason=long_reason)
        self.assertLessEqual(len(cut.uncertain_reason), REASON_MAX)
        self.assertNotIn(secret[:4], cut.uncertain_reason)

        def rule(fi, text):
            if fi.rel.endswith("catalog/app.py"):
                return [Signal("swallowed-error", "Reliability", "Medium", fi.rel, 1, "x", "low",
                               uncertain_reason=reason)]
            return []

        with mock.patch.object(patterns, "FILE_RULES", [rule]):
            r = scan(MONO, use_git=False)
        hits = [s for s in r["signals"] if s["rule"] == "swallowed-error"]
        self.assertEqual(len(hits), 1)
        self.assertIn("***", hits[0]["uncertain_reason"])
        self.assertNotIn(secret, json.dumps(r))
        self.assertNotIn(secret, json.dumps(to_sarif(r)))

    def test_uncertain_reason_serialised_only_when_set(self):
        self.assertNotIn("uncertain_reason",
                         Signal("r", "Security", "High", "a.py", 1, "x", "low").to_dict())
        d = Signal("r", "Security", "High", "a.py", 1, "x", "low",
                   uncertain_reason="auth config not found").to_dict()
        self.assertEqual(d["uncertain_reason"], "auth config not found")

    def test_uncertain_reason_survives_scan_and_validates(self):
        def rule(fi, text):
            if fi.rel.endswith("catalog/app.py"):
                return [Signal("swallowed-error", "Reliability", "Medium", fi.rel, 1, "x", "low",
                               uncertain_reason="auth config not found")]
            return []

        with mock.patch.object(patterns, "FILE_RULES", [rule]):
            r = scan(MONO, use_git=False)
        hits = [s for s in r["signals"] if s["rule"] == "swallowed-error"]
        self.assertEqual([s["uncertain_reason"] for s in hits], ["auth config not found"])
        self.assertEqual(validate(r, _schema()), [])
        items = _schema()["properties"]["signals"]["items"]
        self.assertEqual(items["properties"]["uncertain_reason"]["type"], "string")
        self.assertNotIn("uncertain_reason", items["required"])
        summary = _schema()["properties"]["summary"]
        self.assertIn("default_off_rules", summary["properties"])
        self.assertNotIn("default_off_rules", summary["required"])
        self.assertIn("skipped", summary["properties"]["default_off_rules"]["description"])


# ---------------------------------------------------------------- final review 1.1.0 (I5)
_TIER_RES = (
    re.compile(r"\b(high|medium|low)\s+confidence\b", re.I),        # "at medium confidence"
    re.compile(r"\bconfidence\s+[\"'`]?(high|medium|low)\b", re.I),  # "confidence low"
)


class ConfidenceWordingTests(unittest.TestCase):
    """report._stamp caps every signal at the rule's registry confidence (measured.json can only
    lower it), so rule text must not promise a confidence tier the scan never emits."""

    def _tiers(self, text: str):
        return [m.group(1).lower() for rx in _TIER_RES for m in rx.finditer(text)]

    def test_no_text_mentions_a_tier_above_the_effective_ceiling(self):
        for rid, meta in sorted(registry.RULES.items()):
            ceiling = registry.CONFIDENCES.index(meta.confidence)
            text = meta.description + " " + meta.limitations
            for tier in self._tiers(text):
                self.assertGreaterEqual(
                    registry.CONFIDENCES.index(tier), ceiling,
                    "%s mentions %s confidence above its ceiling %s" % (rid, tier, meta.confidence))

    def test_rules_capped_at_low_say_always_low(self):
        # A rule capped at low that describes tiers ("X emits low confidence") must say every
        # signal is low; "Always low" on a rule whose ceiling is higher would be stale.
        for rid, meta in sorted(registry.RULES.items()):
            text = meta.description + " " + meta.limitations
            always = "always low confidence" in text.lower()
            if meta.confidence == "low" and self._tiers(text):
                self.assertTrue(always, "%s is capped at low but its text describes tiers" % rid)
            if always:
                self.assertEqual(meta.confidence, "low", rid)

    def test_checker_catches_a_promise_above_the_ceiling(self):
        self.assertEqual(self._tiers("Known formats are reported at high confidence."), ["high"])
        self.assertEqual(self._tiers("emits confidence medium"), ["medium"])
        self.assertEqual(self._tiers("Always low confidence."), ["low"])


if __name__ == "__main__":
    unittest.main()
