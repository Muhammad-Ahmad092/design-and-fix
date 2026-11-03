from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

from helpers import REPO

EVALS = os.path.join(REPO, "evals")
if EVALS not in sys.path:
    sys.path.insert(0, EVALS)

import run  # noqa: E402


def _put(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class GradeTests(unittest.TestCase):
    CASE = {
        "must_exist": ["docs/a.md", "docs/h/*/b.md"],
        "must_not_exist": ["docs/bad.md"],
        "must_contain": {"docs/a.md": ["alpha"]},
        "must_not_contain": {"docs/a.md": ["forbidden"]},
        "lint": False, "lint_repo": False,
    }

    def test_grade_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as good:
            _put(good, "docs/a.md", "alpha beta")
            _put(good, "docs/h/2026/b.md", "x")
            self.assertEqual(run.grade(self.CASE, good), [])
        with tempfile.TemporaryDirectory() as bad:
            _put(bad, "docs/a.md", "forbidden only")
            _put(bad, "docs/bad.md", "x")
            joined = "\n".join(run.grade(self.CASE, bad))
            self.assertIn("missing: docs/h/*/b.md", joined)
            self.assertIn("should not exist: docs/bad.md", joined)
            self.assertIn("lacks 'alpha'", joined)
            self.assertIn("forbidden", joined)

    def test_line_needle_matches_evidence_range(self):
        self.assertTrue(run.contains("see src/refund.ts:7-13", "refund.ts:8"))
        self.assertTrue(run.contains("src/refund.ts:8", "refund.ts:8"))
        self.assertFalse(run.contains("src/refund.ts:9-13", "refund.ts:8"))
        self.assertFalse(run.contains("src/other.ts:1-20", "refund.ts:8"))

    def test_line_needle_is_anchored(self):
        # A line needle must not match a longer line number or a longer file name.
        self.assertFalse(run.contains("src/charge.ts:140", "charge.ts:14"))
        self.assertFalse(run.contains("src/charge.ts:142-150", "charge.ts:14"))
        self.assertFalse(run.contains("app.py:91", "app.py:9"))
        self.assertFalse(run.contains("barfoo.py:1-9", "foo.py:3"))
        self.assertFalse(run.contains("barfoo.py:3", "foo.py:3"))
        self.assertTrue(run.contains("services/payments/charge.ts:14 and more", "charge.ts:14"))
        self.assertTrue(run.contains("(charge.ts:14)", "charge.ts:14"))
        self.assertTrue(run.contains("src\\charge.ts:14", "charge.ts:14"))
        self.assertTrue(run.contains("services/catalog/app.py:9-11", "services/catalog/app.py:9"))
        self.assertTrue(run.contains("plain text", "plain"))

    def test_must_not_contain_ignores_case(self):
        case = dict(self.CASE, must_exist=[], must_not_exist=[], must_contain={},
                    must_not_contain={"docs/a.md": ["replace PostgreSQL"]})
        with tempfile.TemporaryDirectory() as tmp:
            _put(tmp, "docs/a.md", "We should Replace PostgreSQL soon")
            self.assertTrue(any("forbidden" in f for f in run.grade(case, tmp)))

    def test_run_result_graded(self):
        case = dict(self.CASE, must_exist=[], must_not_exist=["docs/bad.md"], must_contain={},
                    must_not_contain={}, must_output_contain=["PATCH"])
        with tempfile.TemporaryDirectory() as tmp:
            ok = run.RunResult(0, "Use PATCH for a partial update.", False)
            self.assertEqual(run.grade(case, tmp, ok), [])
            # A crash, login failure or timeout writes no files; it must not pass.
            self.assertIn("claude exited with code 1",
                          run.grade(case, tmp, run.RunResult(1, "Error: not logged in", False)))
            self.assertIn("claude timed out",
                          run.grade(case, tmp, run.RunResult(None, "PATCH\n[TIMEOUT]", True)))
            self.assertIn("claude printed no output",
                          run.grade(case, tmp, run.RunResult(0, "  ", False)))
            self.assertIn("transcript lacks 'PATCH'",
                          run.grade(case, tmp, run.RunResult(0, "Use PUT.", False)))

    def test_every_case_has_a_positive_check(self):
        for case in run.load_cases():
            positive = (case["must_exist"] or case["must_contain"]
                        or case.get("must_output_contain"))
            self.assertTrue(positive, case["id"])

    def test_adr_folder_is_linted(self):
        case = dict(self.CASE, must_exist=[], must_not_exist=[], must_contain={},
                    must_not_contain={}, lint=True)
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs", "architecture"))
            _put(tmp, "docs/adr/0001-bad.md", "# not an ADR\n\n> Format: 1.0.2\n")
            self.assertTrue(any(f.startswith("lint docs/adr:") for f in run.grade(case, tmp)))

    def test_lint_flag_fails_without_output_folder(self):
        case = dict(self.CASE, must_exist=[], must_not_exist=[], must_contain={},
                    must_not_contain={}, lint=True)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(any("lint" in f for f in run.grade(case, tmp)))


class CaseFileTests(unittest.TestCase):
    def test_case_files_valid(self):
        cases = run.load_cases()
        self.assertEqual(len(cases), 5)
        for case in cases:
            for key in run.REQUIRED_KEYS:
                self.assertIn(key, case, "%s lacks %s" % (case.get("id"), key))
            self.assertIn("fixture", case["setup"])
            self.assertIn("preexisting", case["setup"])
            self.assertIsInstance(case["must_contain"], dict)
            fixture = case["setup"]["fixture"]
            if fixture:
                self.assertTrue(os.path.isdir(os.path.join(REPO, *fixture.split("/"))))
            for src in case["setup"].get("copy_files", {}).values():
                self.assertTrue(os.path.isfile(os.path.join(REPO, *src.split("/"))))

    def test_prepare_workdir_case_05(self):
        case = next(c for c in run.load_cases() if c["id"].startswith("05"))
        with tempfile.TemporaryDirectory() as tmp:
            run.prepare_workdir(case, tmp)
            self.assertTrue(os.path.isfile(os.path.join(
                tmp, "docs", "architecture", "01-architecture-issues.md")))
            with open(os.path.join(tmp, "services", "catalog", "app.py"),
                      encoding="utf-8") as fh:
                self.assertIn("%s", fh.read().split("\n")[20])

    def test_build_command_is_list_with_plugin_dir(self):
        cmd = run.build_command("claude", "hi", "sonnet")
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertIn("--plugin-dir", cmd)
        self.assertIn("sonnet", cmd)

    def test_command_restricts_bash_and_isolates(self):
        cmd = run.build_command("claude", "hi")
        rules = cmd[cmd.index("--allowedTools") + 1:]
        for bad in ("Bash(python:*)", "Bash(python3:*)", "Bash", "Bash(*)"):
            self.assertNotIn(bad, cmd)
        bash = [r for r in rules if r.startswith("Bash(")]
        self.assertTrue(bash)
        for r in bash:
            self.assertRegex(r, r"^Bash\(python3? .+[/\\]skills[/\\]design-and-fix[/\\]scripts[/\\]"
                                r"(scan_repo|lint_output|compare_audits)\.py\"? \*\)$")
        for flag in ("--setting-sources", "--strict-mcp-config", "--mcp-config"):
            self.assertIn(flag, cmd)

    def test_missing_flags_and_stdin_prompt(self):
        cmd = run.build_command("claude", None, flags=set())
        self.assertNotIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[:3], ["claude", "-p", "--plugin-dir"])
        self.assertTrue(run.is_cmd_shim("C:\\x\\claude.CMD"))
        self.assertFalse(run.is_cmd_shim("claude.exe"))


    def test_no_unscoped_write_rules(self):
        # Bare Write/Edit rules would allow writes anywhere; acceptEdits limits
        # writes to the working directory instead.
        cmd = run.build_command("claude", "hi")
        rules = cmd[cmd.index("--allowedTools") + 1:]
        for r in rules:
            self.assertFalse(r.startswith(("Write", "Edit", "MultiEdit", "NotebookEdit")), r)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
        self.assertNotIn("--bare", cmd)
        self.assertIn("--bare", run.build_command("claude", "hi", flags={"--bare"}, bare=True))
        self.assertNotIn("--bare", run.build_command("claude", "hi", flags=set(), bare=True))

    def test_git_bash_spelling_on_windows(self):
        self.assertEqual(run.git_bash_path("C:\\Users\\a b\\x"), "/c/Users/a b/x")
        self.assertIsNone(run.git_bash_path("/tmp/x"))
        rules = run.allowed_tools("C:\\t\\plugin", nt=True)
        self.assertIn("Bash(python /c/t/plugin/skills/design-and-fix/scripts/scan_repo.py *)", rules)
        self.assertFalse(any("/c/" in r for r in run.allowed_tools("C:\\t\\plugin", nt=False)))


class TamperTests(unittest.TestCase):
    def test_snapshot_detects_changes_and_skips_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            _put(tmp, "skills/lint.py", "x")
            _put(tmp, "cases/01.json", "{}")
            paths = [os.path.join(tmp, "skills"), os.path.join(tmp, "cases", "01.json")]
            before = run.snapshot(paths)
            _put(tmp, "skills/__pycache__/lint.cpython-39.pyc", "junk")
            self.assertEqual(run.changed_files(before, run.snapshot(paths)), [])
            _put(tmp, "skills/lint.py", "always pass")
            _put(tmp, "skills/new.py", "y")
            changed = run.changed_files(before, run.snapshot(paths))
            self.assertEqual([os.path.basename(p) for p in changed], ["lint.py", "new.py"])

    def test_guarded_paths_cover_grader_and_plugin(self):
        paths = run.guarded_paths("/tmp/plugin")
        for needle in ("skills", ".claude-plugin", "skill-cases", "run.py", "/tmp/plugin"):
            self.assertTrue(any(p.endswith(needle) for p in paths), needle)


class ProcessTests(unittest.TestCase):
    def test_run_process_returns_code_and_output(self):
        res = run.run_process([sys.executable, "-c", "print('hi'); raise SystemExit(3)"],
                              tempfile.gettempdir(), None, 60)
        self.assertEqual((res.returncode, res.output.strip(), res.timed_out), (3, "hi", False))

    def test_run_process_stdin(self):
        res = run.run_process([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                              tempfile.gettempdir(), "patch", 60)
        self.assertEqual(res.output.strip(), "PATCH")

    def test_timeout_kills_tree_and_keeps_partial_output(self):
        # The child starts a grandchild that holds stdout open (like cmd.exe -> node).
        child = ("import subprocess, sys, time; print('started', flush=True); "
                 "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                 "time.sleep(60)")
        start = run.time.time()
        res = run.run_process([sys.executable, "-c", child], tempfile.gettempdir(), None, 3)
        self.assertLess(run.time.time() - start, 45)
        self.assertTrue(res.timed_out)
        self.assertIn("started", res.output)
        self.assertIn("[TIMEOUT after 3s]", res.output)

    def test_decode_bytes(self):
        self.assertEqual(run._decode(b"caf\xc3\xa9"), "caf\u00e9")
        self.assertEqual(run._decode(None), "")


class ResultsTests(unittest.TestCase):
    ROWS = [{"id": "01-a", "result": "PASS", "failures": ""},
            {"id": "02-b", "result": "FAIL", "failures": "x | y; app.py:3/4 lacks 'z'"}]

    def test_pass_line_is_first_ratio(self):
        text = run.render_results(self.ROWS, "default", "2.1.312 (Claude Code)", "1.0.2",
                                  "2026-11-03", "user settings skipped")
        # Same regex as tests/test_repo_docs.py uses for the README check.
        self.assertEqual(re.search(r"(\d+)\s*/\s*(\d+)", text).groups(), ("1", "2"))
        self.assertRegex(text, run.PASS_LINE_RE)
        self.assertIn("Isolation: user settings skipped.", text)
        row = [line for line in text.splitlines() if line.startswith("| 02-b")][0]
        self.assertEqual(row.count("|") - row.count("\\|"), 8)


class WorkflowTests(unittest.TestCase):
    def test_workflow_dispatch_only(self):
        path = os.path.join(REPO, ".github", "workflows", "skill-evals.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        on_block = text.split("\njobs:")[0]
        self.assertIn("workflow_dispatch", on_block)
        for trigger in ("push", "pull_request", "schedule"):
            self.assertNotIn(trigger + ":", on_block)
        self.assertIn("\npermissions:", text)
        self.assertIn("contents: read", text)
        self.assertIn("claude-code@2.1.312", text)
        self.assertIn("retention-days:", text)
        self.assertIn("ANTHROPIC_API_KEY", text)
        self.assertIn("python evals/run.py", text)


if __name__ == "__main__":
    unittest.main()
