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


if __name__ == "__main__":
    unittest.main()
