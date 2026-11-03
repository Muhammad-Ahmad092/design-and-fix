#!/usr/bin/env python3
"""Headless skill-eval runner for design-and-fix.

Copies each case's setup into a temp dir, runs the installed `claude` CLI there
with a staged copy of this plugin loaded, then grades the produced files and the
transcript deterministically (no LLM judge). Paid: every case is one real
`claude -p` call.

Command used per case (cwd = the temp working folder):

    claude -p "<prompt>" --plugin-dir <staged plugin> --permission-mode acceptEdits
           --allowedTools <allowed_tools()...>
           [--permission-prompts none]
           --setting-sources project,local --strict-mcp-config
           --mcp-config <empty-mcp.json> [--bare] [--model <model>]

`--plugin-dir` loads the plugin for this session only (no install, no settings
change). Permissions are not bypassed:
  - File writes: `acceptEdits` accepts edits only inside the working directory
    (the temp folder). There are no `Write`/`Edit` allow rules, so a write to any
    other path needs a permission prompt, and in `-p` mode nobody can answer it
    (`--permission-prompts none` makes that an automatic deny when the CLI has it).
  - Bash: only `python`/`python3` on the staged scan_repo.py, lint_output.py and
    compare_audits.py (absolute-path prefix rules; no `python -c`). On Windows the
    Git Bash spelling (`/c/...`) is allowed as well.
  - Read, Glob, Grep and Skill are allowed everywhere (read-only).
Tamper check: the repo's skills/, .claude-plugin/, evals/skill-cases/, this file
and the staged plugin are hashed before and after each claude run; any change
fails the case, so an agent cannot pass by editing its grader. The residual risk
is that the agent can still read any file the user can and could write outside
these paths if the CLI's permission checks failed; use a throwaway CI runner for
real isolation.

Isolation (best effort; each flag is used only if `claude --help` lists it):
  - `--setting-sources project,local` skips the user's ~/.claude settings
    (allow rules, hooks, env); the temp folder has no project settings.
  - `--strict-mcp-config` with an empty `--mcp-config` skips the user's MCP
    servers.
  - `--bare` (opt-in, needs ANTHROPIC_API_KEY) also skips CLAUDE.md discovery
    and plugin hooks.
NOT isolated: managed/enterprise policy settings, the user's installed plugins
and skills, CLAUDE.md files in ~/.claude and in parent dirs of the temp folder
(unless --bare), auth, and the network. The isolation in effect is written into
skill-results.md. Allow rules match the literal command text, so the model must
call the scripts by absolute path as the skill does.

A case fails when claude exits non-zero, times out or prints nothing, in
addition to its file checks. On timeout the whole process tree is killed (the
Windows npm `claude.cmd` shim included). On Windows, a `.cmd`/`.bat` shim routes
arguments through cmd.exe, which mangles `"` and `&`; for those the prompt is
sent on stdin instead.

Usage: python evals/run.py [--case ID] [--claude PATH] [--model M] [--keep]
                           [--timeout SECONDS] [--bare]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, NamedTuple, Optional

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS_DIR)
CASES_DIR = os.path.join(EVALS_DIR, "skill-cases")
RESULTS_FILE = os.path.join(EVALS_DIR, "skill-results.md")
LINT = os.path.join(REPO, "skills", "design-and-fix", "scripts", "lint_output.py")
DEFAULT_TIMEOUT = 1200

REQUIRED_KEYS = ("id", "prompt", "setup", "must_exist", "must_not_exist",
                 "must_contain", "must_not_contain", "lint", "lint_repo")
# Optional: must_output_contain [str] - case-sensitive strings the transcript must hold.


class RunResult(NamedTuple):
    returncode: Optional[int]
    output: str
    timed_out: bool


def load_cases(cases_dir: str = CASES_DIR) -> List[dict]:
    cases = []
    for path in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as fh:
            cases.append(json.load(fh))
    return cases


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def prepare_workdir(case: dict, workdir: str, repo: str = REPO) -> None:
    """Build the case's starting folder inside `workdir` (must exist, be empty).

    setup keys: fixture (repo-relative dir or null), preexisting {path: text},
    copy_files {dest: repo-relative source}, line_replace {path: {line, text}}.
    """
    setup = case.get("setup") or {}
    fixture = setup.get("fixture")
    if fixture:
        shutil.copytree(os.path.join(repo, *fixture.split("/")), workdir,
                        dirs_exist_ok=True)
    for rel, content in (setup.get("preexisting") or {}).items():
        _write(os.path.join(workdir, *rel.split("/")), content)
    for dest, src in (setup.get("copy_files") or {}).items():
        target = os.path.join(workdir, *dest.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(repo, *src.split("/")), target)
    for rel, spec in (setup.get("line_replace") or {}).items():
        path = os.path.join(workdir, *rel.split("/"))
        with open(path, "r", encoding="utf-8", newline="") as fh:
            lines = fh.read().split("\n")
        lines[spec["line"] - 1] = spec["text"]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines))


def _matches(workdir: str, pattern: str) -> List[str]:
    return glob.glob(os.path.join(workdir, *pattern.split("/")))


def _read(path: str) -> str:
    if os.path.isdir(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


_LINE_NEEDLE = re.compile(r"^(.+):(\d+)$")
# A path needle may follow a folder separator but not a longer file name.
_LEFT = r"(?<![\w.-])"


def contains(text: str, needle: str) -> bool:
    """Match a needle in text.

    A `path:N` needle matches `path:N` not followed by another digit (so
    `a.ts:14` does not match `a.ts:140`), or an evidence range `path:A-B` with
    A <= N <= B. The path must start at a word boundary or after a `/` or `\\`
    (so `foo.py:3` does not match `barfoo.py:3`). Other needles are substrings.
    """
    m = _LINE_NEEDLE.match(needle)
    if not m:
        return needle in text
    path, line = re.escape(m.group(1)), int(m.group(2))
    if re.search(_LEFT + path + ":" + m.group(2) + r"(?!\d)", text):
        return True
    for r in re.finditer(_LEFT + path + r":(\d+)-(\d+)", text):
        if int(r.group(1)) <= line <= int(r.group(2)):
            return True
    return False


def _lint(target: str, label: str, workdir: str, repo_flag: bool) -> List[str]:
    cmd = [sys.executable, LINT, target]
    if repo_flag:
        cmd += ["--repo", workdir]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=120, encoding="utf-8", errors="replace")
    failures = []  # type: List[str]
    if proc.returncode != 0:
        for line in proc.stdout.strip().splitlines()[:20]:
            failures.append("lint %s: %s" % (label, line))
        if not proc.stdout.strip():
            failures.append("lint %s: exit code %d" % (label, proc.returncode))
    return failures


def run_failures(case: dict, result: RunResult) -> List[str]:
    """Failures from the claude run itself: exit code, timeout, transcript checks."""
    failures = []  # type: List[str]
    if result.timed_out:
        failures.append("claude timed out")
    elif result.returncode != 0:
        failures.append("claude exited with code %s" % result.returncode)
    if not result.output.strip():
        failures.append("claude printed no output")
    for needle in case.get("must_output_contain") or []:
        if needle not in result.output:
            failures.append("transcript lacks %r" % needle)
    return failures


def grade(case: dict, workdir: str, result: Optional[RunResult] = None) -> List[str]:
    """Return a list of failure messages; empty means the case passed.

    Paths in must_* keys are workdir-relative POSIX paths and may use glob
    wildcards (for example docs/architecture/history/*/01-architecture-issues.md).
    must_not_contain is case-insensitive. With `result`, the claude run's exit
    code, timeout and transcript (must_output_contain) are graded too.
    """
    failures = run_failures(case, result) if result is not None else []
    for pattern in case.get("must_exist", []):
        if not _matches(workdir, pattern):
            failures.append("missing: %s" % pattern)
    for pattern in case.get("must_not_exist", []):
        if _matches(workdir, pattern):
            failures.append("should not exist: %s" % pattern)
    for pattern, needles in (case.get("must_contain") or {}).items():
        files = _matches(workdir, pattern)
        if not files:
            failures.append("cannot check contents, missing: %s" % pattern)
            continue
        text = "\n".join(_read(f) for f in files)
        for needle in needles:
            if not contains(text, needle):
                failures.append("%s lacks %r" % (pattern, needle))
    for pattern, needles in (case.get("must_not_contain") or {}).items():
        text = "\n".join(_read(f) for f in _matches(workdir, pattern)).lower()
        for needle in needles:
            if needle.lower() in text:
                failures.append("%s contains forbidden %r" % (pattern, needle))
    if case.get("lint"):
        target = os.path.join(workdir, "docs", "architecture")
        if not os.path.isdir(target):
            failures.append("lint: docs/architecture does not exist")
        else:
            failures += _lint(target, "docs/architecture", workdir, bool(case.get("lint_repo")))
        adr = os.path.join(workdir, "docs", "adr")
        if os.path.isdir(adr):
            failures += _lint(adr, "docs/adr", workdir, False)
    return failures


# ---------------------------------------------------------------- tamper check
def snapshot(paths: List[str]) -> Dict[str, str]:
    """sha256 of every file under paths (files or dirs), keyed by absolute path.

    __pycache__ folders and .pyc files are skipped (the grader's own lint run
    creates them)."""
    out = {}  # type: Dict[str, str]

    def add(path: str) -> None:
        if path.endswith(".pyc"):
            return
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 16), b""):
                h.update(block)
        out[os.path.abspath(path)] = h.hexdigest()

    for p in paths:
        if os.path.isfile(p):
            add(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                add(os.path.join(root, name))
    return out


def changed_files(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def guarded_paths(plugin_dir: Optional[str] = None, repo: str = REPO) -> List[str]:
    paths = [os.path.join(repo, "skills"), os.path.join(repo, ".claude-plugin"),
             os.path.join(repo, "evals", "skill-cases"), os.path.join(repo, "evals", "run.py")]
    if plugin_dir:
        paths.append(plugin_dir)
    return paths


# ---------------------------------------------------------------- command
SCRIPT_NAMES = ("scan_repo.py", "lint_output.py", "compare_audits.py")
# No Write/Edit rules: acceptEdits already accepts edits inside the working
# directory, and a bare rule would allow writes to any path.
BASE_TOOLS = ["Read", "Glob", "Grep", "Skill"]
ISOLATION_FLAGS = ("--setting-sources", "--strict-mcp-config", "--mcp-config",
                   "--permission-prompts")
OPTIONAL_FLAGS = ("--bare",)
EMPTY_MCP = '{"mcpServers": {}}'


def git_bash_path(path: str) -> Optional[str]:
    """Windows drive path (drive C, folders a and b) -> `/c/a/b`, the Git Bash
    spelling; None for other paths."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not m:
        return None
    return "/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/"))


def allowed_tools(repo: str = REPO, nt: Optional[bool] = None) -> List[str]:
    """Base tools plus Bash rules limited to the bundled scripts (absolute paths)."""
    nt = (os.name == "nt") if nt is None else nt
    scripts = os.path.join(repo, "skills", "design-and-fix", "scripts")
    prefixes = {scripts.replace("\\", "/"), scripts}
    if nt:
        gb = git_bash_path(scripts)
        if gb:
            prefixes.add(gb)
    tools = list(BASE_TOOLS)
    for interp in ("python", "python3"):
        for d in sorted(prefixes):
            for name in SCRIPT_NAMES:
                sep = "\\" if "\\" in d else "/"
                path = "%s%s%s" % (d, sep, name)
                # SKILL.md quotes the script path; allow both spellings.
                tools.append("Bash(%s %s *)" % (interp, path))
                tools.append("Bash(%s \"%s\" *)" % (interp, path))
    return tools


ALLOWED_TOOLS = allowed_tools()


def is_cmd_shim(claude: str) -> bool:
    return claude.lower().endswith((".cmd", ".bat"))


def supported_flags(claude: str) -> set:
    """Isolation and optional flags that `claude --help` lists; empty set if help fails."""
    try:
        proc = subprocess.run([claude, "--help"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return set()
    return {f for f in ISOLATION_FLAGS + OPTIONAL_FLAGS
            if re.search(re.escape(f) + r"(?![\w-])", proc.stdout or "")}


def write_empty_mcp(directory: str) -> str:
    path = os.path.join(directory, "empty-mcp.json")
    _write(path, EMPTY_MCP + "\n")
    return path


def build_command(claude: str, prompt: Optional[str], model: Optional[str] = None,
                  repo: str = REPO, flags: Optional[set] = None,
                  mcp_config: str = "empty-mcp.json", bare: bool = False) -> List[str]:
    """Build the argv. prompt=None omits it (caller sends it on stdin).

    flags: flags the CLI supports (default: all of ISOLATION_FLAGS). `--bare` is
    added only when bare=True and the CLI lists it.
    """
    flags = set(ISOLATION_FLAGS) if flags is None else flags
    cmd = [claude, "-p"]
    if prompt is not None:
        cmd.append(prompt)
    cmd += ["--plugin-dir", repo, "--permission-mode", "acceptEdits"]
    if "--permission-prompts" in flags:
        cmd += ["--permission-prompts", "none"]
    if "--setting-sources" in flags:
        cmd += ["--setting-sources", "project,local"]
    if "--strict-mcp-config" in flags and "--mcp-config" in flags:
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
    if bare and "--bare" in flags:
        cmd.append("--bare")
    if model:
        cmd += ["--model", model]
    # Variadic option last so it cannot swallow other arguments.
    cmd += ["--allowedTools"] + allowed_tools(repo)
    return cmd


def isolation_summary(flags: set, bare: bool) -> str:
    parts = ["writes limited to the temp working folder (acceptEdits, no Write/Edit rules)"]
    if "--permission-prompts" in flags:
        parts.append("unanswerable prompts denied")
    if "--setting-sources" in flags:
        parts.append("user settings skipped")
    if "--strict-mcp-config" in flags and "--mcp-config" in flags:
        parts.append("MCP servers skipped")
    parts.append("CLAUDE.md and hooks skipped (--bare)" if bare and "--bare" in flags
                 else "user CLAUDE.md, plugins and skills still loaded")
    return "; ".join(parts)


def stage_plugin(dest: str, repo: str = REPO) -> str:
    """Copy the plugin (manifest + skills) to dest/plugin and return that path.

    The Bash allow rules name the bundled scripts by absolute path; a repo path with
    spaces gets quoted by the model and no longer matches, so runs use a staged copy
    under the temp folder (normally space-free).
    """
    plugin = os.path.join(dest, "plugin")
    for name in (".claude-plugin", "skills"):
        shutil.copytree(os.path.join(repo, name), os.path.join(plugin, name),
                        ignore=shutil.ignore_patterns("__pycache__"))
    if " " in plugin:
        print("warning: staged plugin path has spaces; script allow rules may not match",
              file=sys.stderr)
    return plugin


# ---------------------------------------------------------------- process control
def _decode(data) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def popen_group_kwargs() -> dict:
    """Start the child in its own process group so the whole tree can be killed."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill proc and every descendant (cmd.exe shim -> node on Windows)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_process(cmd: List[str], cwd: str, stdin_text: Optional[str],
                timeout: float) -> RunResult:
    """Run cmd; on timeout kill its process tree and return the partial output."""
    proc = subprocess.Popen(cmd, cwd=cwd,
                            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", **popen_group_kwargs())
    try:
        out, _ = proc.communicate(stdin_text, timeout=timeout)
        return RunResult(proc.returncode, _decode(out), False)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired as exc:
            out = _decode(exc.stdout)
            print("warning: claude output pipe still open after kill (pid %d)" % proc.pid,
                  file=sys.stderr)
        return RunResult(proc.returncode, "%s\n[TIMEOUT after %ds]\n" % (_decode(out), timeout),
                         True)
    except BaseException:
        # Ctrl-C or any other error: do not leave a paid run going in the background.
        kill_tree(proc)
        raise


def run_claude(claude: str, case: dict, workdir: str, timeout: int,
               model: Optional[str] = None, flags: Optional[set] = None,
               mcp_config: str = "empty-mcp.json", repo: str = REPO,
               bare: bool = False) -> RunResult:
    """Run one case; never raises on timeout."""
    via_stdin = is_cmd_shim(claude)
    cmd = build_command(claude, None if via_stdin else case["prompt"], model,
                        repo=repo, flags=flags, mcp_config=mcp_config, bare=bare)
    return run_process(cmd, workdir, case["prompt"] if via_stdin else None, timeout)


def remove_tree(path: str, attempts: int = 5) -> bool:
    """rmtree with retries (a killed child may still hold files on Windows)."""
    for i in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(0.5 * (i + 1))
    print("warning: could not remove %s" % path, file=sys.stderr)
    return False


def cli_version(claude: str) -> str:
    try:
        proc = subprocess.run([claude, "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60,
                              encoding="utf-8", errors="replace")
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def plugin_version(repo: str = REPO) -> str:
    try:
        with open(os.path.join(repo, ".claude-plugin", "plugin.json"),
                  "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("version", "unknown"))
    except (OSError, ValueError):
        return "unknown"


# ---------------------------------------------------------------- results
PASS_LINE_RE = re.compile(r"\*\*(\d+) / (\d+) cases pass\.\*\*")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_results(rows: List[Dict[str, str]], model: str, cli: str,
                   plugin: str, date: str, isolation: str = "") -> str:
    """skill-results.md. The first `N / M` in the file is the pass line, which the
    README skill-evals block must repeat (tests/test_repo_docs.py checks it)."""
    passed = sum(1 for r in rows if r["result"] == "PASS")
    out = ["# Skill eval results", "",
           "**%d / %d cases pass.**" % (passed, len(rows)), "",
           "Generated by `python evals/run.py`, all cases in one run. Model: %s. CLI: %s. "
           "Plugin: %s. Date: %s." % (_cell(model), _cell(cli), _cell(plugin), date)]
    if isolation:
        out += ["", "Isolation: %s." % _cell(isolation)]
    out += ["", "| Case | Result | Model | CLI version | Plugin version | Date | Failures |",
            "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], r["result"], _cell(model), _cell(cli), _cell(plugin), date,
            _cell(r["failures"])))
    out += ["", "History and notes on earlier runs: [evals/README.md](README.md#skill-eval-history)."]
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run design-and-fix skill evals headlessly.")
    ap.add_argument("--case", help="run only this case id (e.g. 01-audit-monorepo or 01)")
    ap.add_argument("--claude", default=None, help="path to the claude CLI")
    ap.add_argument("--model", default=None, help="model alias or id passed to claude")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help="seconds per case (default %d)" % DEFAULT_TIMEOUT)
    ap.add_argument("--keep", action="store_true", help="keep temp working folders")
    ap.add_argument("--bare", action="store_true",
                    help="pass --bare (skips CLAUDE.md and hooks; needs ANTHROPIC_API_KEY)")
    args = ap.parse_args(argv)

    claude = args.claude or shutil.which("claude")
    if not claude:
        print("error: claude CLI not found; pass --claude PATH", file=sys.stderr)
        return 2
    if args.bare and not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: --bare needs ANTHROPIC_API_KEY (bare mode reads no OAuth login)",
              file=sys.stderr)
        return 2
    cases = load_cases()
    if args.case:
        cases = [c for c in cases
                 if c["id"] == args.case or c["id"].startswith(args.case + "-")]
        if not cases:
            print("error: no case matches %r" % args.case, file=sys.stderr)
            return 2

    flags = supported_flags(claude)
    missing = [f for f in ISOLATION_FLAGS if f not in flags]
    if missing:
        print("warning: claude lacks %s; run is less isolated" % ", ".join(missing),
              file=sys.stderr)
    if args.bare and "--bare" not in flags:
        print("warning: claude lacks --bare; running without it", file=sys.stderr)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs_dir = os.path.join(EVALS_DIR, "runs", stamp)
    os.makedirs(runs_dir, exist_ok=True)
    rows = []  # type: List[Dict[str, str]]
    cfg_dir = tempfile.mkdtemp(prefix="dfeval-cfg-")
    try:
        mcp_config = write_empty_mcp(cfg_dir)
        plugin_dir = stage_plugin(cfg_dir)
        guarded = guarded_paths(plugin_dir)
        for case in cases:
            workdir = tempfile.mkdtemp(prefix="dfeval-%s-" % case["id"])
            try:
                prepare_workdir(case, workdir)
                before = snapshot(guarded)
                result = run_claude(claude, case, workdir, args.timeout, args.model,
                                    flags, mcp_config, repo=plugin_dir, bare=args.bare)
                tampered = changed_files(before, snapshot(guarded))
                _write(os.path.join(runs_dir, case["id"] + ".txt"), result.output)
                failures = grade(case, workdir, result)
                if tampered:
                    failures.insert(0, "grader or plugin files changed during the run: %s"
                                    % ", ".join(tampered[:10]))
            finally:
                if args.keep:
                    print("kept: %s" % workdir)
                else:
                    remove_tree(workdir)
            rows.append({"id": case["id"], "result": "FAIL" if failures else "PASS",
                         "failures": "; ".join(failures)})
            print("%-28s %s" % (case["id"], rows[-1]["result"]))
            for f in failures:
                print("    - %s" % f)
    finally:
        remove_tree(cfg_dir)
    date = datetime.date.today().isoformat()
    text = render_results(rows, args.model or "default", cli_version(claude),
                          plugin_version(), date, isolation_summary(flags, args.bare))
    if not args.case:
        _write(RESULTS_FILE, text)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    print("%d/%d passed" % (passed, len(rows)))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
