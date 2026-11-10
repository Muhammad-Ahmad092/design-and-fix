from __future__ import annotations

import json
import os
import tempfile
import unittest

from helpers import FIXTURES, SCRIPTS, run_cli
from dfscan.report import scan
from dfscan.stack import detect_stack
from dfscan.walker import walk

MONO = os.path.join(FIXTURES, "monorepo")


class ScannerCoreTests(unittest.TestCase):
    def test_candidate_folders_recursive(self):  # BUG-1
        r = scan(MONO, use_git=False)
        paths = {f["path"]: f for f in r["candidate_folders"]}
        self.assertIn("services/payments", paths)
        self.assertGreaterEqual(paths["services/payments"]["file_count"], 5)
        self.assertGreaterEqual(paths["services/payments"]["approx_loc"], 600)
        self.assertIn("services", paths)

    def test_typescript_detected_at_depth_4_plus(self):  # BUG-2
        self.assertIn("TypeScript", scan(MONO, use_git=False)["detected_stack"])

    def test_csproj_detected(self):  # BUG-7
        self.assertIn("C# / .NET", scan(MONO, use_git=False)["detected_stack"])

    def test_no_duplicate_stack_labels(self):  # polish 4
        stack = scan(MONO, use_git=False)["detected_stack"]
        self.assertIn("C# / .NET", stack)
        self.assertNotIn("C#", stack)
        self.assertIn("TypeScript", stack)
        with tempfile.TemporaryDirectory() as d:
            for rel in ("a.cs", "web/package.json", "web/x.js", "web/y.ts"):
                path = os.path.join(d, *rel.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("{}\n" if rel.endswith(".json") else "x\n")
            labels = detect_stack(walk(d))[0]
        self.assertEqual(labels, ["C# / .NET", "Node.js / JavaScript", "TypeScript"])

    def test_frameworks(self):
        fw = scan(MONO, use_git=False)["frameworks"]
        for name in ("express", "flask", "gin"):
            self.assertIn(name, fw)

    def test_schema_keys(self):
        r = scan(MONO, use_git=False)
        self.assertEqual(r["schema_version"], 3)
        for k in ("root", "scope", "detected_stack", "frameworks", "candidate_folders",
                  "import_graph", "routes", "hotspots", "signals", "summary", "notes"):
            self.assertIn(k, r)

    def test_paths_are_posix_and_scope_accepts_backslash(self):
        r = scan(MONO, scope=["services\\payments"], use_git=False)
        self.assertEqual(r["scope"], ["services/payments"])
        scoped = walk(MONO, scope=["services\\payments"])
        self.assertTrue(scoped)
        self.assertTrue(all(f.rel.startswith("services/payments/") for f in scoped))
        self.assertTrue(all("\\" not in f["path"] for f in r["candidate_folders"]))

    def test_binary_and_large_files_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "pkg"))
            with open(os.path.join(d, "pkg", "big.ts"), "w") as fh:
                fh.write("const a = 1;\n" * 200000)  # > 1 MB
            with open(os.path.join(d, "pkg", "bin.py"), "wb") as fh:
                fh.write(b"x = 1\n\x00\x01\x02\nprint(1)\n")
            with open(os.path.join(d, "pkg", "ok.py"), "w") as fh:
                fh.write("a = 1\nb = 2\n")
            r = scan(d, use_git=False)
            pkg = {f["path"]: f for f in r["candidate_folders"]}["pkg"]
            self.assertEqual(pkg["approx_loc"], 2)
            self.assertEqual(pkg["file_count"], 1)

    def test_cli_errors_are_json(self):
        code, out = run_cli("does/not/exist")
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out))
        code, out = run_cli(MONO, "--scope", "nope")
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out))

    def test_max_depth_alias_note(self):
        code, out = run_cli(MONO, "--max-depth", "2", "--no-git")
        self.assertEqual(code, 0)
        self.assertIn("--max-depth is deprecated; use --list-depth", json.loads(out)["notes"])

    def test_markdown_survives_cp1252_stdout(self):  # final review 1
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "web"))
            with open(os.path.join(d, "web", "路由.js"), "w", encoding="utf-8") as fh:
                fh.write("app.get('/x', h);\n")
            code, out = run_cli(d, "--no-git", "--format", "markdown",
                                env={"PYTHONIOENCODING": "cp1252"})
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith("# Scan summary"))

    def test_absolute_scope_inside_root(self):  # final review 22
        code, out = run_cli(MONO, "--scope", os.path.join(MONO, "services", "payments"), "--no-git")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["scope"], ["services/payments"])
        code, out = run_cli(os.path.join(MONO, "services"), "--scope", os.path.join(MONO, "App"))
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out))

    def test_folders_only_flag(self):  # final review 3
        code, out = run_cli(MONO, "--folders-only", "--no-git")
        self.assertEqual(code, 0)
        r = json.loads(out)
        for k in ("root", "scope", "detected_stack", "frameworks", "candidate_folders",
                  "import_graph", "routes", "hotspots", "signals", "summary", "notes"):
            self.assertIn(k, r)
        self.assertIn("services/payments", {f["path"] for f in r["candidate_folders"]})
        self.assertIn("TypeScript", r["detected_stack"])
        self.assertIn("express", r["frameworks"])
        self.assertEqual((r["signals"], r["routes"], r["import_graph"]["cycles"]), ([], [], []))
        self.assertEqual(r["import_graph"]["modules"], 0)
        self.assertFalse(r["hotspots"]["available"])
        self.assertIn("folders-only: analyzers skipped", r["notes"])
        self.assertEqual(r["summary"]["signals_by_category"], {})

    def test_mjs_and_cjs_are_javascript(self):  # final review 10
        with tempfile.TemporaryDirectory() as d:
            for name in ("a.mjs", "b.cjs"):
                with open(os.path.join(d, name), "w") as fh:
                    fh.write("export const a = 1;\n")
            self.assertEqual({f.lang for f in walk(d)}, {"JavaScript"})

    def test_config_files_listed_without_loc(self):  # final review 8
        with tempfile.TemporaryDirectory() as d:
            for name in (".env", ".env.local", "settings.yml", "config.json", "app.toml",
                         "app.ini", "app.properties", ".env.example", ".env.sample",
                         ".env.template", "package-lock.json", "go.sum", "poetry.lock"):
                with open(os.path.join(d, name), "w") as fh:
                    fh.write("a=1\n")
            os.makedirs(os.path.join(d, "node_modules"))
            with open(os.path.join(d, "node_modules", "x.json"), "w") as fh:
                fh.write("{}\n")
            got = {f.rel: f for f in walk(d)}
            config = {r for r, f in got.items() if f.is_config}
            self.assertEqual(config, {".env", ".env.local", "settings.yml", "config.json",
                                      "app.toml", "app.ini", "app.properties"})
            self.assertTrue(all(got[r].lang is None and got[r].loc == 0 for r in config))
            r = scan(d, use_git=False)
            self.assertEqual((r["summary"]["code_files"], r["candidate_folders"]), (0, []))

    def test_scan_repo_uses_lf(self):  # final review 17
        with open(os.path.join(SCRIPTS, "scan_repo.py"), "rb") as fh:
            self.assertNotIn(b"\r\n", fh.read())



def _tree(root, files):
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class R2StackTests(unittest.TestCase):
    def test_frameworks_from_parent_manifest(self):  # R2-fp2
        tree = {"pyproject.toml": '[project]\ndependencies = ["flask>=3"]\n',
                "src/pkg/a.py": "x = 1\n"}
        with tempfile.TemporaryDirectory() as d:
            _tree(d, tree)
            # Scope below the scan root: manifests at the scan root are read.
            self.assertIn("flask", scan(d, scope=["src/pkg"], use_git=False)["frameworks"])
            # Scan root below the repo root (.git above it) with --scope: ancestors up to .git are read.
            os.makedirs(os.path.join(d, ".git"))
            r = scan(os.path.join(d, "src"), scope=["pkg"], use_git=False)
            self.assertIn("flask", r["frameworks"])
            self.assertIn("Python", r["detected_stack"])
            # detect_stack reads manifests in the extra directories it is given.
            files = walk(os.path.join(d, "src"))
            self.assertEqual(detect_stack(files)[1], [])
            self.assertEqual(detect_stack(files, extra_manifest_dirs=[d])[1], ["flask"])
        with tempfile.TemporaryDirectory() as d:
            _tree(d, tree)
            # No repo root above the scan root: nothing outside the scan root is read.
            r = scan(os.path.join(d, "src"), scope=["pkg"], use_git=False)
            self.assertNotIn("flask", r["frameworks"])


# ---------------------------------------------------------------- release review 1.0.2 (area A)
class CliArgumentReviewTests(unittest.TestCase):  # review M6, M7
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = self._tmp.name
        self.root = os.path.join(self.base, "root")
        _tree(self.root, {"a/x.py": "x = 1\n", "b/y.py": "y = 1\n"})
        _tree(self.base, {"sib/z.py": "z = 1\n"})

    def test_negative_top_and_depth_rejected(self):
        for flag in ("--top", "--list-depth", "--max-depth"):
            code, _ = run_cli(self.root, "--no-git", "--quiet", flag, "-1")
            self.assertEqual(code, 2, flag)
        code, out = run_cli(self.root, "--no-git", "--quiet", "--top", "0")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["candidate_folders"], [])

    def test_relative_scope_leaving_root_is_an_error(self):
        for scope in ("../sib", "a/../../sib", os.path.join(self.base, "sib")):
            code, out = run_cli(self.root, "--no-git", "--quiet", "--scope", scope)
            self.assertEqual(code, 1, scope)
            self.assertIn("scope outside root", json.loads(out)["error"])
        code, out = run_cli(self.root, "--no-git", "--quiet", "--scope", "b/../a")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["scope"], ["a"])



# ---------------------------------------------------------------- 1.1.0 CLI additions (Task 15)
SEV_ORDER = ("Critical", "High", "Medium", "Low")


class CliAdditionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "root")
        _tree(self.root, {
            "svc/app.py": ("import requests\n\n\ndef f(u):\n    try:\n        return requests.get(u)\n"
                           "    except Exception:\n        pass\n"),
            "svc/more.py": "import requests\n\n\ndef g(u):\n    return requests.post(u)\n",
        })

    def test_version_flag(self):
        code, out = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertEqual(out, "design-and-fix scanner 1.1.0 (schema 3)\n")

    def test_scan_without_path_is_usage_error(self):
        code, _ = run_cli("--no-git")
        self.assertEqual(code, 2)

    def test_list_rules_includes_default_column(self):
        from dfscan import registry
        registry.ensure_loaded()
        code, out = run_cli("--list-rules")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "CATEGORY", "SEVERITY", "CONFIDENCE", "DEFAULT"])
        rows = {ln.split()[0]: ln for ln in lines[1:] if ln.strip()}
        self.assertEqual(set(rows), set(registry.RULES))
        meta = registry.RULES["http-no-timeout"]
        row = rows["http-no-timeout"]
        for text in (meta.category, meta.default_severity, meta.confidence):
            self.assertIn(text, row)
        self.assertTrue(row.rstrip().endswith(" on"), row)
        code, out = run_cli("--list-rules", "--format", "json")
        self.assertEqual(code, 0)
        data = {r["id"]: r for r in json.loads(out)}
        self.assertEqual(data["http-no-timeout"]["default"], "on")
        self.assertEqual(set(data["http-no-timeout"]),
                         {"id", "category", "default_severity", "confidence", "default"})

    def test_list_rules_formats_and_path_conflict(self):  # fix round 1
        code, out = run_cli("--list-rules", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("ID "), out[:40])
        code, out = run_cli(self.root, "--list-rules")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        code, _ = run_cli(self.root, "--explain-rule", "god-module")
        self.assertEqual(code, 2)

    def test_scope_report_matches_schema(self):
        from schema_validate import validate
        with open(os.path.join(SCRIPTS, "scan.schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        prop = schema["properties"]["summary"]["properties"]["scope_report"]
        self.assertEqual(set(prop["required"]),
                         {"scanned", "skipped_by_reason", "excluded_by_config"})
        self.assertNotIn("scope_report", schema["properties"]["summary"]["required"])
        code, out = run_cli(self.root, "--no-git", "--quiet")
        self.assertEqual(validate(json.loads(out), schema), [])
        self.assertNotEqual(validate({"scanned": 1, "skipped_by_reason": {}}, prop), [])

    def test_explain_rule_unknown_id_exits_2(self):
        code, out = run_cli("--explain-rule", "no-such-rule")
        self.assertEqual(code, 2)
        self.assertIn("unknown rule id", json.loads(out)["error"])

    def test_explain_rule_shows_meta_and_precision(self):
        from dfscan import registry
        import gen_rule_docs
        registry.ensure_loaded()
        code, out = run_cli("--explain-rule", "http-no-timeout")
        self.assertEqual(code, 0)
        meta = registry.RULES["http-no-timeout"]
        for text in (meta.id, meta.description, meta.limitations, meta.remediation,
                     "version: " + meta.version, "default: on"):
            self.assertIn(text, out)
        self.assertIn("measured precision:", out)
        precision = os.path.join(gen_rule_docs.REPO, "evals", "precision.md")
        if os.path.isfile(precision):
            with open(precision, encoding="utf-8") as fh:
                measured = gen_rule_docs.parse_precision(fh.read())
            if "http-no-timeout" in measured:
                prec, n = measured["http-no-timeout"]
                self.assertIn("measured precision: %s (n=%s" % (prec, n), out)

    def test_max_findings_caps_and_notes(self):
        code, out = run_cli(self.root, "--no-git", "--quiet")
        self.assertEqual(code, 0)
        full = json.loads(out)["signals"]
        self.assertGreaterEqual(len(full), 3)
        code, out = run_cli(self.root, "--no-git", "--quiet", "--max-findings", "2")
        self.assertEqual(code, 0)
        res = json.loads(out)
        sev = [SEV_ORDER.index(s["severity_hint"]) for s in res["signals"]]
        self.assertEqual(len(sev), 2)
        self.assertEqual(sev, sorted(sev))
        self.assertEqual(sev[0], min(SEV_ORDER.index(s["severity_hint"]) for s in full))
        self.assertTrue(any("max-findings 2: showing 2 of %d signals" % len(full) in n
                            for n in res["notes"]), res["notes"])
        code, out = run_cli(self.root, "--no-git", "--quiet", "--max-findings", "100")
        self.assertFalse(any("max-findings" in n for n in json.loads(out)["notes"]))
        code, _ = run_cli(self.root, "--no-git", "--quiet", "--max-findings", "-1")
        self.assertEqual(code, 2)

    def test_output_dir_writes_scan_json(self):
        out_dir = os.path.join(self._tmp.name, "docs", "architecture", ".scan")
        code, out = run_cli(self.root, "--no-git", "--quiet", "--format", "markdown",
                            "--output-dir", out_dir)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Scan summary"))
        with open(os.path.join(out_dir, "scan.json"), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["schema_version"], 3)
        self.assertIn("signals", saved)

    def test_output_dir_gets_a_gitignore(self):
        # final review C M-11: scan.json holds the absolute root and snippets; keep it out of git
        out_dir = os.path.join(self._tmp.name, "docs", "architecture", ".scan")
        code, _ = run_cli(self.root, "--no-git", "--quiet", "--output-dir", out_dir)
        self.assertEqual(code, 0)
        with open(os.path.join(out_dir, ".gitignore"), encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), "*\n")
        # an existing .gitignore is the user's: never overwritten
        with open(os.path.join(out_dir, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("scan.json\n")
        code, _ = run_cli(self.root, "--no-git", "--quiet", "--output-dir", out_dir)
        self.assertEqual(code, 0)
        with open(os.path.join(out_dir, ".gitignore"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "scan.json\n")

    def test_output_dir_unwritable_is_an_error(self):
        blocker = os.path.join(self._tmp.name, "file.txt")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        code, out = run_cli(self.root, "--no-git", "--quiet", "--output-dir",
                            os.path.join(blocker, "sub"))
        self.assertEqual(code, 1)
        self.assertIn("cannot write", json.loads(out)["error"])


class ScopeReportTests(unittest.TestCase):
    def test_scope_report_counts(self):
        with tempfile.TemporaryDirectory() as d:
            _tree(d, {
                "src/a.py": "a = 1\n",
                "src/b.py": "b = 2\n",
                "src/app.min.js": "var x=1;\n",
                "src/big.py": "x = 1\n" * 40,
                "src/gen/x.generated.ts": "export const x = 1;\n",
                "legacy/old.py": "o = 1\n",
                "legacy/old2.py": "o = 2\n",
                "only/kept.py": "k = 1\n",
            })
            kw = dict(use_git=False, max_file_bytes=100, exclude=["legacy/", "**/*.generated.ts"])
            r = scan(d, **kw)
            rep = r["summary"]["scope_report"]
            self.assertEqual(rep["scanned"], r["summary"]["files_scanned"])
            self.assertEqual(rep["skipped_by_reason"], {"minified": 1, "too_large": 1})
            self.assertEqual(rep["excluded_by_config"], 2)  # legacy/ (once) + the generated file
            r = scan(d, scope=["src"], **kw)
            rep = r["summary"]["scope_report"]
            self.assertEqual(rep["scanned"], 2)
            self.assertEqual(rep["excluded_by_config"], 1)
            r = scan(d, use_git=False, include=["only/**"])
            rep = r["summary"]["scope_report"]
            self.assertEqual(rep["scanned"], 1)
            self.assertEqual(rep["excluded_by_config"], 7)
            self.assertEqual(rep["skipped_by_reason"], {})


class WalkerTestNameTests(unittest.TestCase):  # Task 16 (d)
    def test_rspec_files_are_tests(self):
        from dfscan.walker import is_test_file, is_test_filename
        for rel in ("app/models/user_spec.rb", "lib/user.spec.rb", "user_spec.rb"):
            self.assertTrue(is_test_filename(rel), rel)
            self.assertTrue(is_test_file(rel), rel)
        for rel in ("app/models/spec.rb", "app/models/inspector.rb", "lib/user_spec_helper.rb"):
            self.assertFalse(is_test_filename(rel), rel)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "lib"))
            for name in ("user_spec.rb", "user.rb"):
                with open(os.path.join(d, "lib", name), "w", encoding="utf-8") as fh:
                    fh.write("class User\nend\n")
            tests = {f.rel: f.is_test for f in walk(d)}
        self.assertEqual(tests, {"lib/user_spec.rb": True, "lib/user.rb": False})


if __name__ == "__main__":
    unittest.main()
