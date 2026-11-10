from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import FIXTURES, SCRIPTS, run_cli
from dfscan import config as dfconfig
from dfscan.registry import RULES, SEVERITIES
from dfscan.report import scan
from dfscan.meta import PLUGIN_VERSION
from dfscan.sarif import to_sarif

MONO = os.path.join(FIXTURES, "monorepo")
CFG = ".design-and-fix.json"
FETCH = "const r = await fetch(url);\n"


def put(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def cfg_file(root: str, data) -> None:
    put(root, CFG, data if isinstance(data, str) else json.dumps(data))


class TempTree(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def signals(self, **kw):
        cfg = dfconfig.load_config(self.d)
        return scan(self.d, use_git=False, rule_config=cfg.rules, exclude=cfg.exclude,
                    include=cfg.include, **kw)


class ConfigTests(TempTree):
    def test_config_errors_are_actionable(self):
        cases = {
            "{bad json": "line 1",
            '{"nonsense": 1}': "nonsense",
            '{"exclude": "dist"}': "exclude",
            '{"fail_on": "Severe"}': "fail_on",
            '{"rules": {"no-such-rule": {}}}': "rules.no-such-rule",
            '{"rules": {"http-no-timeout": {"enabled": "no"}}}': "rules.http-no-timeout.enabled",
            '{"limits": {"max_files": "many"}}': "limits.max_files",
            '{"limits": {"bogus": 1}}': "limits.bogus",
            "[1]": "<root>",
        }
        for text, key in cases.items():
            cfg_file(self.d, text)
            code, out = run_cli(self.d, "--no-git", "--quiet")
            self.assertEqual(code, 2, text)
            msg = json.loads(out)["error"]  # JSON error, never a traceback
            self.assertIn(key, msg)
            self.assertIn("fix", msg)
        code, out = run_cli(self.d, "--config", os.path.join(self.d, "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("fix", json.loads(out)["error"])

    def test_defaults_without_file(self):
        cfg = dfconfig.load_config(self.d)
        self.assertEqual((cfg.fail_on, cfg.exclude, cfg.include, cfg.rules), ("none", [], [], {}))
        self.assertEqual(scan(self.d, use_git=False)["meta"]["config_hash"], "")
        r = scan(self.d, use_git=False, config_hash=dfconfig.config_hash(cfg))
        self.assertEqual(r["meta"]["config_hash"], dfconfig.config_hash(dfconfig.Config()))

    def test_config_hash_changes_with_settings(self):
        a = dfconfig.config_hash(dfconfig.Config())
        b = dfconfig.config_hash(dfconfig.parse_config({"fail_on": "High"}, "x"))
        self.assertNotEqual(a, b)
        self.assertEqual(b, dfconfig.config_hash(dfconfig.parse_config({"fail_on": "High"}, "y")))

    def test_rule_disable_and_severity_override(self):
        put(self.d, "svc/a.ts", FETCH)
        put(self.d, "svc/b.ts", "try { f() } catch (e) {}\n")
        base = {s["rule"]: s for s in self.signals()["signals"]}
        self.assertEqual(base["http-no-timeout"]["severity_hint"], "High")
        self.assertIn("swallowed-error", base)
        cfg_file(self.d, {"rules": {"http-no-timeout": {"enabled": False},
                                    "swallowed-error": {"severity": "Critical"}}})
        got = {s["rule"]: s for s in self.signals()["signals"]}
        self.assertNotIn("http-no-timeout", got)
        self.assertEqual(got["swallowed-error"]["severity_hint"], "Critical")

    def test_exclude_include_globs(self):
        put(self.d, "src/a.ts", FETCH)
        put(self.d, "legacy/b.ts", FETCH)
        put(self.d, "src/gen/c.ts", FETCH)
        files = lambda r: sorted(  # noqa: E731
            s["file"] for s in r["signals"] if s["rule"] == "http-no-timeout")
        self.assertEqual(files(self.signals()), ["legacy/b.ts", "src/a.ts", "src/gen/c.ts"])
        cfg_file(self.d, {"exclude": ["legacy/", "gen"]})
        self.assertEqual(files(self.signals()), ["src/a.ts"])
        cfg_file(self.d, {"include": ["legacy/**"]})
        self.assertEqual(files(self.signals()), ["legacy/b.ts"])

    def test_limits_wired(self):
        put(self.d, "a.ts", FETCH + "// pad\n" * 10)
        put(self.d, "b.ts", FETCH)
        cfg_file(self.d, {"limits": {"max_file_bytes": 30}})
        code, out = run_cli(self.d, "--no-git", "--quiet")
        r = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn({"path": "a.ts", "reason": "too_large"}, r["skipped_files"])
        cfg_file(self.d, {"limits": {"max_files": 1}})
        r = json.loads(run_cli(self.d, "--no-git", "--quiet")[1])
        self.assertTrue(r["summary"]["partial"])

    def test_verbose_from_config(self):
        put(self.d, "a.ts", FETCH)
        cfg_file(self.d, {"output": {"verbose": True}})
        r = json.loads(run_cli(self.d, "--no-git", "--quiet")[1])
        self.assertTrue(any(n.startswith("timing ") for n in r["notes"]))


class SuppressionTests(TempTree):
    def test_inline_suppression(self):
        put(self.d, "a.ts", "const a = fetch(u); // dfscan:ignore http-no-timeout\n"
                            "// dfscan:ignore http-no-timeout, swallowed-error\n"
                            "const b = fetch(u);\n"
                            "const c = fetch(u);\n")
        put(self.d, "b.py", "import requests\n# dfscan:ignore\nrequests.get(u)\n"
                            "requests.post(u)  # dfscan:ignore swallowed-error\n")
        r = scan(self.d, use_git=False)
        got = sorted((s["file"], s["line"]) for s in r["signals"] if s["rule"] == "http-no-timeout")
        self.assertEqual(got, [("a.ts", 4), ("b.py", 4)])  # wrong rule id does not suppress
        self.assertEqual(r["summary"]["suppressed"], 3)

    def test_marker_outside_comment_is_ignored(self):
        put(self.d, "svc/a.ts", 'const s = "dfscan:ignore";\nconst r = fetch(u);\n')
        r = scan(self.d, use_git=False)
        self.assertEqual([s["rule"] for s in r["signals"] if s["line"]], ["http-no-timeout"])
        self.assertEqual(r["summary"]["suppressed"], 0)

    def test_folder_signal_not_suppressible_inline(self):
        put(self.d, "svc/a.ts", "// dfscan:ignore\nexport const x = 1;\n")
        r = scan(self.d, use_git=False)
        folder = [s for s in r["signals"] if s["line"] == 0]
        self.assertEqual(r["summary"]["suppressed"], 0)
        if folder:  # folder rules only fire for sizeable folders; when present they stay
            self.assertTrue(all(s["line"] == 0 for s in folder))


class FailOnTests(TempTree):
    def test_fail_on_exit_codes(self):
        base = (MONO, "--no-git", "--quiet")
        self.assertEqual(run_cli(*base)[0], 0)  # default never fails
        self.assertEqual(run_cli(*base, "--fail-on", "High")[0], 3)
        self.assertEqual(run_cli(*base, "--fail-on", "critical")[0], 3)
        self.assertEqual(run_cli(*base, "--fail-on", "none")[0], 0)
        self.assertEqual(run_cli(*base, "--fail-on", "bogus")[0], 2)
        put(self.d, "a.ts", "// dfscan:ignore http-no-timeout\nfetch(u);\n")
        self.assertEqual(run_cli(self.d, "--no-git", "--quiet", "--fail-on", "High")[0], 0)
        put(self.d, "b.ts", "fetch(u);\n")
        self.assertEqual(run_cli(self.d, "--no-git", "--quiet", "--fail-on", "High")[0], 3)

    def test_fail_on_config_and_cli_precedence(self):
        put(self.d, "b.ts", "fetch(u);\n")
        cfg_file(self.d, {"fail_on": "High"})
        args = (self.d, "--no-git", "--quiet")
        self.assertEqual(run_cli(*args)[0], 3)
        self.assertEqual(run_cli(*args, "--fail-on", "none")[0], 0)  # CLI wins
        cfg_file(self.d, {"fail_on": "High", "rules": {"http-no-timeout": {"severity": "Low"}}})
        self.assertEqual(run_cli(*args)[0], 0)
        self.assertEqual(run_cli(*args, "--fail-on", "Low")[0], 3)

    def test_partial_scan_fails_closed_with_gate(self):  # final review I4
        note = "partial scan: --fail-on gate fails closed"
        for limits in ({"timeout_seconds": 0.000001}, {"max_files": 1}):
            with self.subTest(limits=limits):
                cfg = os.path.join(self.d, "limits.json")
                with open(cfg, "w", encoding="utf-8") as fh:
                    json.dump({"limits": limits}, fh)
                base = (MONO, "--no-git", "--quiet", "--config", cfg)
                code, out = run_cli(*base)
                self.assertEqual(code, 0)  # no gate: a partial scan still exits 0
                self.assertTrue(json.loads(out)["summary"]["partial"])
                self.assertNotIn(note, json.loads(out)["notes"])
                code, out = run_cli(*base, "--fail-on", "Low")
                result = json.loads(out)
                self.assertTrue(result["summary"]["partial"])
                self.assertEqual(result["signals"], [])  # no threshold hit, yet the gate fails
                self.assertEqual(code, 3)
                self.assertIn(note, result["notes"])
                self.assertEqual(run_cli(*base, "--fail-on", "none")[0], 0)
        # A complete scan with a gate and nothing at the threshold still exits 0, without the note.
        put(self.d, "clean/a.py", "x = 1\n")
        code, out = run_cli(os.path.join(self.d, "clean"), "--no-git", "--quiet",
                            "--fail-on", "Critical")
        self.assertEqual(code, 0)
        self.assertNotIn(note, json.loads(out)["notes"])

    def test_fail_on_applies_to_sarif_format(self):
        code, out = run_cli(MONO, "--no-git", "--quiet", "--format", "sarif", "--fail-on", "High")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["version"], "2.1.0")


class SarifTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = scan(MONO, use_git=False)
        cls.sarif = to_sarif(cls.result)

    def test_sarif_shape_and_rules(self):
        s = self.sarif
        self.assertEqual(s["version"], "2.1.0")
        run = s["runs"][0]
        driver = run["tool"]["driver"]
        self.assertEqual((driver["name"], driver["version"]), ("design-and-fix", PLUGIN_VERSION))
        self.assertEqual([r["id"] for r in driver["rules"]], sorted(RULES))
        r0 = driver["rules"][0]
        meta = RULES[r0["id"]]
        self.assertEqual(r0["shortDescription"]["text"], meta.description)
        self.assertEqual(r0["fullDescription"]["text"], meta.limitations)
        self.assertEqual(r0["help"]["text"], meta.remediation)
        self.assertEqual(r0["properties"]["category"], meta.category)
        self.assertIn("%SRCROOT%", run["originalUriBaseIds"])
        self.assertEqual(len(run["results"]), len(self.result["signals"]))
        levels = {"Critical": "error", "High": "error", "Medium": "warning", "Low": "note"}
        for res, sig in zip(run["results"], self.result["signals"]):
            self.assertEqual(res["ruleId"], sig["rule"])
            self.assertEqual(res["level"], levels[sig["severity_hint"]])
            self.assertEqual(res["message"]["text"], sig["snippet"])
            self.assertEqual(res["partialFingerprints"], {"dfscan/v1": sig["fingerprint"]})
            self.assertIn(res["ruleId"], {r["id"] for r in driver["rules"]})
        json.dumps(s)

    def test_sarif_paths_relative_posix(self):
        for res in self.sarif["runs"][0]["results"]:
            phys = res["locations"][0]["physicalLocation"]
            uri = phys["artifactLocation"]["uri"]
            self.assertEqual(phys["artifactLocation"]["uriBaseId"], "%SRCROOT%")
            self.assertNotIn("\\", uri)
            self.assertFalse(os.path.isabs(uri) or ":" in uri, uri)
            self.assertFalse(uri.startswith("/"))
        self.assertNotIn("\\", json.dumps([
            r["locations"] for r in self.sarif["runs"][0]["results"]]))

    def test_sarif_regions(self):
        by = {(r["ruleId"], r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]): r
              for r in self.sarif["runs"][0]["results"]}
        file_level = by[("http-no-timeout", "services/catalog/app.py")]
        self.assertEqual(file_level["locations"][0]["physicalLocation"]["region"]["startLine"], 28)
        folder = by[("no-tests", "services/catalog")]  # folder-level: folder uri, no region
        self.assertNotIn("region", folder["locations"][0]["physicalLocation"])

    def test_sarif_line_zero_in_file_gets_line_one(self):
        res = {"root": MONO, "signals": [{
            "rule": "http-no-timeout", "severity_hint": "High", "snippet": "x", "fingerprint": "f",
            "file": "services/catalog/app.py", "line": 0}]}
        phys = to_sarif(res)["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        self.assertEqual(phys["region"]["startLine"], 1)

    def test_sarif_cli_output(self):
        code, out = run_cli(MONO, "--no-git", "--quiet", "--format", "sarif")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["runs"][0]["tool"]["driver"]["name"], "design-and-fix")


class SchemaTests(unittest.TestCase):
    def test_config_schema_matches_loader(self):
        with open(os.path.join(SCRIPTS, "config.schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        props = schema["properties"]
        self.assertEqual(set(props), set(dfconfig.TOP_KEYS))
        self.assertEqual(set(props["limits"]["properties"]), set(dfconfig.LIMIT_KEYS))
        self.assertEqual(set(props["output"]["properties"]), set(dfconfig.OUTPUT_KEYS))
        rule_props = props["rules"]["additionalProperties"]["properties"]
        self.assertEqual(set(rule_props), set(dfconfig.RULE_KEYS))
        self.assertEqual(rule_props["severity"]["enum"], list(SEVERITIES))
        self.assertEqual(props["fail_on"]["enum"], list(dfconfig.FAIL_LEVELS))
        for sch in (schema, props["limits"], props["output"], props["rules"]["additionalProperties"]):
            self.assertIs(sch["additionalProperties"], False)
        # every schema key is accepted by the loader, an unknown one is not
        cfg = dfconfig.parse_config({
            "$schema": "x", "exclude": ["a"], "include": ["b"],
            "rules": {"http-no-timeout": {"enabled": False, "severity": "Low"}},
            "limits": {"max_file_bytes": 5, "max_files": 6, "timeout_seconds": 7},
            "fail_on": "Medium", "output": {"verbose": True}}, "t")
        self.assertEqual((cfg.fail_on, cfg.limits["timeout_seconds"], cfg.verbose),
                         ("Medium", 7, True))
        with self.assertRaises(dfconfig.ConfigError):
            dfconfig.parse_config({"limits": {"nope": 1}}, "t")
        for level in dfconfig.FAIL_LEVELS:
            self.assertEqual(dfconfig.parse_config({"fail_on": level}, "t").fail_on, level)


# ---------------------------------------------------------------- release review 1.0.2 (area A)
class SuppressionGrammarTests(TempTree):  # review M1, M2, M3
    def http_lines(self, r):
        return sorted(s["line"] for s in r["signals"] if s["rule"] == "http-no-timeout")

    def test_marker_in_url_or_string_does_not_suppress(self):
        put(self.d, "a.py", "import requests\n"
                            "requests.get('http://example.com/dfscan:ignore')\n"
                            "requests.get('https://x.io/a#dfscan:ignore')\n"
                            "requests.get(u)  # dfscan:ignore http-no-timeout\n")
        put(self.d, "b.ts", "fetch(u);// dfscan:ignore http-no-timeout\n"
                            "fetch('a // dfscan:ignore');\n")
        r = scan(self.d, use_git=False)
        got = sorted((s["file"], s["line"]) for s in r["signals"] if s["rule"] == "http-no-timeout")
        self.assertEqual(got, [("a.py", 2), ("a.py", 3), ("b.ts", 2)])
        self.assertEqual(r["summary"]["suppressed"], 2)

    def test_unparseable_remainder_is_not_a_bare_ignore(self):
        put(self.d, "a.py", "import requests\n"
                            "# dfscan:ignore Reason: flaky upstream\n"
                            "requests.get('http://a')\n"
                            "# dfscan:ignore: swallowed-error\n"
                            "requests.get('http://b')\n"
                            "# dfscan:ignore: http-no-timeout -- slow upstream, tracked\n"
                            "requests.get('http://c')\n"
                            "# dfscan:ignore -- every rule here, reviewed\n"
                            "requests.get('http://d')\n"
                            "# dfscan:ignore swallowed-error http-no-timeout\n"
                            "requests.get('http://e')\n")
        put(self.d, "c.ts", "/* dfscan:ignore http-no-timeout */ fetch(u);\n")
        r = scan(self.d, use_git=False)
        self.assertEqual(self.http_lines(r), [3, 5])
        self.assertEqual(r["summary"]["suppressed"], 4)

    def test_bom_does_not_break_line_one_comment(self):
        with open(os.path.join(self.d, "a.py"), "wb") as fh:
            fh.write(b"\xef\xbb\xbf# dfscan:ignore http-no-timeout\nrequests.get('http://a')\n")
        r = scan(self.d, use_git=False)
        self.assertEqual(self.http_lines(r), [])
        self.assertEqual(r["summary"]["suppressed"], 1)

    def test_env_file_suppression(self):
        put(self.d, ".env", "# dfscan:ignore hardcoded-secret\nAPI_TOKEN=abcd1234efgh5678\n")
        r = scan(self.d, use_git=False)
        self.assertEqual(r["summary"]["suppressed"], 1)


class FingerprintStabilityTests(TempTree):  # review M4
    def test_suppressing_one_identical_line_keeps_the_other_id(self):
        body = "import requests\ndef f():\n    requests.get(u)\n    requests.get(u)\n"
        put(self.d, "a.py", body)
        before = {s["line"]: s["fingerprint"] for s in scan(self.d, use_git=False)["signals"]
                  if s["rule"] == "http-no-timeout"}
        put(self.d, "a.py", body.replace("    requests.get(u)\n    requests.get(u)\n",
                                         "    # dfscan:ignore\n    requests.get(u)\n"
                                         "    requests.get(u)\n"))
        after = {s["line"]: s["fingerprint"] for s in scan(self.d, use_git=False)["signals"]
                 if s["rule"] == "http-no-timeout"}
        self.assertEqual(len(set(before.values())), 2)
        self.assertEqual(after, {5: before[4]})  # line 4 (old line 3) is suppressed
        cfg = {"http-no-timeout": {"enabled": False}}
        r = scan(self.d, use_git=False, rule_config=cfg)
        self.assertFalse([s for s in r["signals"] if s["rule"] == "http-no-timeout"])


class ConfigReviewTests(TempTree):  # review I2, M5, M12, config notes
    def test_invalid_glob_in_config_is_a_config_error(self):
        cfg_file(self.d, {"exclude": ["ok/**", "[z-a]"]})
        put(self.d, "a.ts", FETCH)
        with self.assertRaises(dfconfig.ConfigError) as ctx:
            dfconfig.load_config(self.d)
        self.assertIn("exclude[1]", ctx.exception.message)
        code, out = run_cli(self.d, "--no-git", "--quiet")
        self.assertEqual(code, 2)
        self.assertIn("exclude[1]", json.loads(out)["error"])

    def test_pathological_json_is_a_config_error(self):
        put(self.d, "a.ts", FETCH)
        for text in ("[" * 100000 + "]" * 100000,
                     '{"limits": {"max_files": %s}}' % ("9" * 5000)):
            cfg_file(self.d, text)
            code, out = run_cli(self.d, "--no-git", "--quiet")
            self.assertEqual(code, 2, out[:200])
            self.assertIn("config error", json.loads(out)["error"])

    def test_parse_config_loads_registry_itself(self):
        import subprocess
        import sys
        code = ("import sys; sys.path.insert(0, %r); from dfscan import config; "
                "c = config.parse_config({'rules': {'http-no-timeout': {'enabled': False}}}, 'x'); "
                "print(c.rules)" % SCRIPTS)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("http-no-timeout", proc.stdout)

    def test_notes_disclose_disabled_rules_and_patterns(self):
        put(self.d, "src/a.ts", FETCH)
        cfg_file(self.d, {"exclude": ["legacy/"], "include": ["src/**"],
                          "rules": {"no-tests": {"enabled": False},
                                    "http-no-timeout": {"severity": "Low"}}})
        notes = self.signals()["notes"]
        self.assertIn("rules disabled by config or profile: no-tests (their signals are dropped and not "
                      "counted in summary.suppressed)", notes)
        self.assertIn("config exclude patterns: legacy/", notes)
        self.assertTrue(any(n.startswith("config include patterns: src/**") for n in notes))
        plain = scan(self.d, use_git=False)["notes"]
        self.assertFalse([n for n in plain if n.startswith("config ")])


class SchemaDraftTests(unittest.TestCase):  # review M11
    def test_scan_schema_declares_draft_2020_12(self):
        with open(os.path.join(SCRIPTS, "scan.schema.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["$schema"], "https://json-schema.org/draft/2020-12/schema")


if __name__ == "__main__":
    unittest.main()



class IgnoreLineAboveTests(TempTree):
    def test_code_line_marker_does_not_hide_next_line(self):
        put(self.d, "a.ts", "const a = fetch(u); // dfscan:ignore http-no-timeout\n"
                            "const b = fetch(u);\n")
        r = scan(self.d, use_git=False)
        got = [s["line"] for s in r["signals"] if s["rule"] == "http-no-timeout"]
        self.assertEqual(got, [2])
        self.assertEqual(r["summary"]["suppressed"], 1)

    def test_comment_only_line_above_still_suppresses(self):
        put(self.d, "a.ts", "// dfscan:ignore http-no-timeout\nconst b = fetch(u);\n")
        r = scan(self.d, use_git=False)
        self.assertEqual([s for s in r["signals"] if s["rule"] == "http-no-timeout"], [])


class ConfigHardeningTests(TempTree):
    def test_backslash_globs_normalised(self):
        bs = chr(92)
        cfg = dfconfig.parse_config({"exclude": ["legacy" + bs + "old" + bs + "**"],
                                     "include": ["src" + bs + "a.ts"]}, CFG)
        self.assertEqual(cfg.exclude, ["legacy/old/**"])
        self.assertEqual(cfg.include, ["src/a.ts"])

    def test_nan_and_infinity_timeout_rejected(self):
        for raw in ("NaN", "Infinity", "-Infinity"):
            cfg_file(self.d, '{"limits": {"timeout_seconds": %s}}' % raw)
            with self.assertRaises(dfconfig.ConfigError):
                dfconfig.load_config(self.d)
        cfg_file(self.d, '{"limits": {"timeout_seconds": NaN}}')
        self.assertEqual(run_cli(self.d, "--no-git", "--quiet")[0], 2)


class SarifUriTests(unittest.TestCase):
    def test_uri_percent_encoded_keeps_slash(self):
        res = {"root": "", "signals": [{"rule": "http-no-timeout", "severity_hint": "Medium",
                                        "file": "my dir/a b#1.ts", "line": 3, "snippet": "x",
                                        "fingerprint": "f"}]}
        loc = to_sarif(res)["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        self.assertEqual(loc["artifactLocation"]["uri"], "my%20dir/a%20b%231.ts")


class SarifUncertainReasonTests(unittest.TestCase):
    def _result(self, **extra):
        sig = {"rule": "http-no-timeout", "severity_hint": "Medium", "file": "a.ts", "line": 3,
               "snippet": "x", "fingerprint": "f"}
        sig.update(extra)
        return to_sarif({"root": "", "signals": [sig]})["runs"][0]["results"][0]

    def test_uncertain_reason_becomes_property(self):
        res = self._result(uncertain_reason="auth config outside scope")
        self.assertEqual(res["properties"], {"uncertainReason": "auth config outside scope"})

    def test_no_property_without_uncertain_reason(self):
        self.assertNotIn("properties", self._result())
        self.assertNotIn("properties", self._result(uncertain_reason=""))


class ConfigDefaultOffTests(TempTree):  # Review Focus 1: a 1.0.2 config keeps its rules
    def test_existing_enabled_true_keeps_default_off_rule(self):
        put(self.d, "src/a.ts", FETCH)
        cfg_file(self.d, {"rules": {"http-no-timeout": {"enabled": True},
                                    "openapi-drift": {"enabled": True}}})
        with mock.patch.object(RULES["http-no-timeout"], "default_enabled", False):
            r = self.signals()
        self.assertIn("http-no-timeout", {s["rule"] for s in r["signals"]})
        self.assertFalse([n for n in r["notes"] if "off by default" in n])

    def test_silent_config_gets_note_not_signal(self):
        put(self.d, "src/a.ts", FETCH)
        cfg_file(self.d, {"rules": {"no-tests": {"severity": "Low"},
                                    "openapi-drift": {"enabled": True}}})
        with mock.patch.object(RULES["http-no-timeout"], "default_enabled", False):
            r = self.signals()
        self.assertNotIn("http-no-timeout", {s["rule"] for s in r["signals"]})
        self.assertIn("rules off by default (enable in config): http-no-timeout", r["notes"])
        self.assertEqual(r["summary"]["default_off_rules"], ["http-no-timeout"])


class ProfileTests(TempTree):  # Task 15: profiles; Review Focus 4: config file beats profile
    def rules_of(self, *args):
        code, out = run_cli(self.d, "--no-git", "--quiet", *args)
        self.assertEqual(code, 0, out)
        res = json.loads(out)
        return {s["rule"] for s in res["signals"]}, res

    def test_profiles_are_valid_partial_configs(self):
        from dfscan import profiles
        self.assertEqual(set(profiles.PROFILES), {
            "balanced", "security-focused", "api-focused", "performance-focused",
            "architecture-focused", "quick"})
        self.assertEqual(profiles.PROFILES["balanced"], {})
        for name, data in profiles.PROFILES.items():
            with self.subTest(profile=name):
                self.assertLessEqual(set(data), {"rules", "limits"})
                dfconfig.parse_config(data, "<profile %s>" % name)  # raises on a bad entry
                for entry in data.get("rules", {}).values():
                    self.assertEqual(set(entry), {"enabled"})  # selection only, never severity

    def test_focused_profiles_cover_every_rule(self):
        from dfscan import profiles
        for name, (cats, extras) in profiles.FOCUS.items():
            with self.subTest(profile=name):
                disabled = {r for r, e in profiles.PROFILES[name]["rules"].items()
                            if e["enabled"] is False}
                expected = {r for r, m in RULES.items()
                            if m.category not in cats and r not in extras}
                self.assertEqual(disabled, expected)
                self.assertFalse(any(e["enabled"] for e in profiles.PROFILES[name]["rules"].values()))

    def test_profile_disables_out_of_focus_rules(self):
        put(self.d, "src/a.ts", FETCH)  # http-no-timeout (Reliability)
        rules, _ = self.rules_of()
        self.assertIn("http-no-timeout", rules)
        rules, res = self.rules_of("--profile", "security-focused")
        self.assertNotIn("http-no-timeout", rules)
        self.assertTrue(any(n.startswith("profile security-focused") for n in res["notes"]))

    def test_profile_then_config_precedence(self):
        from dfscan import profiles
        put(self.d, "src/a.ts", FETCH)
        # A profile that turns a rule on loses to a config file that turns it off.
        fake = dict(profiles.PROFILES, enabler={"rules": {"http-no-timeout": {"enabled": True}},
                                                "limits": {"max_files": 7}})
        cfg_file(self.d, {"rules": {"http-no-timeout": {"enabled": False}},
                          "limits": {"max_files": 9}})
        with mock.patch.object(profiles, "PROFILES", fake):
            cfg = dfconfig.load_config(self.d, profile="enabler")
        self.assertIs(cfg.rules["http-no-timeout"]["enabled"], False)
        self.assertEqual(cfg.limits["max_files"], 9)
        self.assertEqual(cfg.profile, "enabler")
        # Keys the config file does not set still come from the profile.
        cfg_file(self.d, {"rules": {"http-no-timeout": {"severity": "Low"}}})
        with mock.patch.object(profiles, "PROFILES", fake):
            cfg = dfconfig.load_config(self.d, profile="enabler")
        self.assertEqual(cfg.rules["http-no-timeout"], {"enabled": True, "severity": "Low"})
        self.assertEqual(cfg.limits["max_files"], 7)
        # A real profile that disables a rule loses to a config file that enables it.
        cfg_file(self.d, {"rules": {"http-no-timeout": {"enabled": True}}})
        rules, _ = self.rules_of("--profile", "security-focused")
        self.assertIn("http-no-timeout", rules)

    def test_cli_flag_beats_profile_and_config(self):
        cfg_file(self.d, {"profile": "quick", "fail_on": "none"})
        put(self.d, "src/a.ts", FETCH)
        code, _ = run_cli(self.d, "--no-git", "--quiet", "--fail-on", "High")
        self.assertEqual(code, 3)

    def test_config_file_profile_key_and_cli_override(self):
        put(self.d, "src/a.ts", FETCH)
        cfg_file(self.d, {"profile": "security-focused"})
        rules, res = self.rules_of()
        self.assertNotIn("http-no-timeout", rules)
        rules, res = self.rules_of("--profile", "balanced")
        self.assertIn("http-no-timeout", rules)
        cfg_file(self.d, {"profile": "nope"})
        code, out = run_cli(self.d, "--no-git", "--quiet")
        self.assertEqual(code, 2)
        self.assertIn("profile", json.loads(out)["error"])

    def test_unknown_cli_profile_is_usage_error(self):
        code, _ = run_cli(self.d, "--no-git", "--quiet", "--profile", "turbo")
        self.assertEqual(code, 2)

    def test_profile_changes_config_hash(self):
        a = dfconfig.config_hash(dfconfig.load_config(self.d))
        b = dfconfig.config_hash(dfconfig.load_config(self.d, profile="security-focused"))
        c = dfconfig.config_hash(dfconfig.load_config(self.d, profile="balanced"))
        self.assertNotEqual(a, b)
        self.assertEqual(a, c)

    def test_schema_lists_every_profile(self):
        from dfscan import profiles
        with open(os.path.join(SCRIPTS, "config.schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        self.assertEqual(set(schema["properties"]["profile"]["enum"]), set(profiles.PROFILES))
