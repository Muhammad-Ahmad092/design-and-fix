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

Several models: `--model` may be repeated (or comma-separated); every case runs
once per model and skill-results.md has one row per case x model. The headline
`**N / M cases pass.**` counts all those rows.

Clients: `--client claude|codex|cursor` (default claude). Codex runs
`codex exec --sandbox workspace-write` with the skill staged under
`.agents/skills/` in the temp folder; Cursor runs `cursor-agent -p --force
--sandbox enabled --plugin-dir <staged plugin>`. See evals/README.md, "Clients",
for the sources and for the safety controls each client lacks. A non-claude full
run writes evals/skill-results-<client>.md (never overwrites the Claude rows);
combine files with --merge-results.

Usage: python evals/run.py [--client C] [--case ID] [--claude PATH] [--model M ...]
                           [--keep] [--timeout SECONDS] [--bare]
       python evals/run.py --merge-results FILE [FILE ...]
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
# Optional keys:
#   must_output_contain [str]   case-sensitive strings the transcript must hold.
#   must_not_output_line [str]  compliance markers: no transcript line, and no line of any
#                               new or changed file, may be just the marker plus punctuation
#                               or emphasis (`X`, **X**, "X.", ...). A mention inside a
#                               longer sentence (reporting an injection) is fine.
#   allowed_new_paths [glob]    files the run may create (default docs/architecture/** and
#                               docs/adr/**); any other new file fails the case.
#   must_contain_any {glob: [str]}  case-insensitive; at least one needle per glob.
#   timeout int                 per-case seconds cap (min with --timeout).


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
    copy_files {dest: repo-relative source}, line_replace {path: {line, text}},
    generate_files {dir, count, per_dir, ext, text}: write `count` small files under
    `dir` at run time (so a huge repo is not committed).
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
    gen = setup.get("generate_files")
    if gen:
        generate_files(workdir, gen)


def generate_files(workdir: str, gen: dict) -> None:
    """Write gen["count"] small files under workdir/gen["dir"], gen["per_dir"] per folder."""
    base = os.path.join(workdir, *gen["dir"].split("/"))
    per_dir = max(1, int(gen.get("per_dir", 200)))
    ext = gen.get("ext", ".py")
    text = gen.get("text", "VALUE = {n}\n")
    for n in range(int(gen["count"])):
        folder = os.path.join(base, "pkg_%04d" % (n // per_dir))
        if n % per_dir == 0:
            os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "mod_%05d%s" % (n, ext)), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(text.format(n=n))


def _matches(workdir: str, pattern: str) -> List[str]:
    return glob.glob(os.path.join(workdir, *pattern.split("/")))


def _read(path: str) -> str:
    """File text; "" for a directory, a dangling symlink or an unreadable file."""
    if os.path.isdir(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


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
                          timeout=120, encoding="utf-8", errors="replace", env=_child_env())
    failures = []  # type: List[str]
    if proc.returncode != 0:
        for line in proc.stdout.strip().splitlines()[:20]:
            failures.append("lint %s: %s" % (label, line))
        if not proc.stdout.strip():
            failures.append("lint %s: exit code %d" % (label, proc.returncode))
    return failures


def run_failures(case: dict, result: RunResult, client: str = "claude") -> List[str]:
    """Failures from the client run itself: exit code, timeout, transcript checks."""
    failures = []  # type: List[str]
    if result.timed_out:
        failures.append("%s timed out" % client)
    elif result.returncode != 0:
        failures.append("%s exited with code %s" % (client, result.returncode))
    if not result.output.strip():
        failures.append("%s printed no output" % client)
    for needle in case.get("must_output_contain") or []:
        if needle not in result.output:
            failures.append("transcript lacks %r" % needle)
    for needle in case.get("must_not_output_line") or []:
        if any(is_marker_line(ln, needle) for ln in result.output.splitlines()):
            failures.append("transcript prints forbidden line %r" % needle)
    return failures


DEFAULT_ALLOWED_NEW = ["docs/architecture/**", "docs/adr/**"]


def _glob_re(pattern: str):
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        c = pattern[i]
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def workdir_files(workdir: str, baseline: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """sha256 per entry under workdir, keyed by POSIX relative path.

    `baseline` (a previous workdir_files result) lets snapshot() skip .pyc and
    __pycache__ entries that already existed; new ones are still reported."""
    abs_base = None
    if baseline is not None:
        abs_base = {os.path.abspath(os.path.join(workdir, *rel.split("/"))): d
                    for rel, d in baseline.items()}
    out = {}  # type: Dict[str, str]
    for path, digest in snapshot([workdir], abs_base).items():
        rel = os.path.relpath(path, workdir).replace(os.sep, "/")
        out[rel] = digest
    return out


def is_marker_line(line: str, marker: str) -> bool:
    return re.fullmatch(r"[\W_]*" + re.escape(marker) + r"[\W_]*", line.strip()) is not None


def new_file_failures(case: dict, workdir: str, baseline: Dict[str, str]) -> List[str]:
    """New files outside allowed_new_paths, and marker lines in any new or changed file."""
    failures = []  # type: List[str]
    allowed = [_glob_re(g) for g in case.get("allowed_new_paths") or DEFAULT_ALLOWED_NEW]
    markers = case.get("must_not_output_line") or []
    for rel, digest in sorted(workdir_files(workdir, baseline).items()):
        is_new = rel not in baseline
        if is_new and not any(a.match(rel) for a in allowed):
            failures.append("unexpected new file: %s" % rel)
        if (is_new or baseline[rel] != digest) and markers:
            for line in _read(os.path.join(workdir, *rel.split("/"))).splitlines():
                for marker in markers:
                    if is_marker_line(line, marker):
                        failures.append("%s prints forbidden line %r" % (rel, marker))
                        break
    return failures


def grade(case: dict, workdir: str, result: Optional[RunResult] = None,
          baseline: Optional[Dict[str, str]] = None, client: str = "claude") -> List[str]:
    """Return a list of failure messages; empty means the case passed.

    Paths in must_* keys are workdir-relative POSIX paths and may use glob
    wildcards (for example docs/architecture/history/*/01-architecture-issues.md).
    must_not_contain is case-insensitive. With `result`, the claude run's exit
    code, timeout and transcript (must_output_contain) are graded too. With `baseline`
    (workdir_files() taken after setup), new files outside the allowed set fail.
    """
    failures = run_failures(case, result, client) if result is not None else []
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
    for pattern, needles in (case.get("must_contain_any") or {}).items():
        files = _matches(workdir, pattern)
        if not files:
            failures.append("cannot check contents, missing: %s" % pattern)
            continue
        text = "\n".join(_read(f) for f in files).lower()
        if not any(n.lower() in text for n in needles):
            failures.append("%s lacks any of %r" % (pattern, needles))
    if baseline is not None:
        failures += new_file_failures(case, workdir, baseline)
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
def _is_link(path: str) -> bool:
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _entry_digest(path: str) -> str:
    """Digest of one entry. A symlink (file, directory or dangling) is recorded as
    its target text, never followed; an unreadable file as a marker, never raised."""
    if _is_link(path):
        try:
            target = os.readlink(path)
        except OSError:
            target = "?"
        return "symlink:" + target
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 16), b""):
                h.update(block)
    except OSError as exc:
        return "unreadable:%s" % type(exc).__name__
    return h.hexdigest()


def snapshot(paths: List[str], baseline: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """sha256 of every entry under paths (files or dirs), keyed by absolute path.

    Symlinks are entries (their target text), directory symlinks included, and are
    never followed. .pyc files and __pycache__ folders are skipped only when they
    already existed in `baseline` (the grader's own lint run creates some); the
    baseline value is carried over so they do not show as changed. A new .pyc or
    __pycache__/x.md is reported like any other new file."""
    out = {}  # type: Dict[str, str]
    base = baseline or {}

    def in_cache(path: str) -> bool:
        parts = os.path.abspath(path).replace("\\", "/").split("/")
        return path.endswith(".pyc") or "__pycache__" in parts

    def add(path: str) -> None:
        key = os.path.abspath(path)
        if in_cache(path) and key in base:
            out[key] = base[key]
            return
        out[key] = _entry_digest(path)

    for p in paths:
        if _is_link(p) or os.path.isfile(p):
            add(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs.sort()
            for d in list(dirs):
                full = os.path.join(root, d)
                if _is_link(full):
                    dirs.remove(d)
                    add(full)
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


# ---------------------------------------------------------------- clients
CLIENTS = {"claude": ("claude", "Claude Code"),
           "codex": ("codex", "Codex"),
           "cursor": ("cursor-agent", "Cursor")}
SKILL_NAME = "design-and-fix"


def find_client(client: str, override: Optional[str] = None) -> Optional[str]:
    """Path of the client's CLI; Cursor's binary is `cursor-agent` (current docs
    also call it `agent`)."""
    if override:
        return override
    names = [CLIENTS[client][0]] + (["agent"] if client == "cursor" else [])
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def build_command_for(client: str, prompt: Optional[str], plugin_dir: str,
                      model: Optional[str] = None, exe: Optional[str] = None,
                      flags: Optional[set] = None, mcp_config: str = "empty-mcp.json",
                      bare: bool = False) -> List[str]:
    """argv (no shell; the prompt is one element, or omitted when None so the caller
    sends it on stdin) for one headless run of `client` in the temp folder.

    claude: see build_command. codex: `codex exec` in a workspace-write sandbox, no
    approval prompts; the skill is read from `.agents/skills/` in the temp folder
    (stage_codex_skill). cursor: `cursor-agent -p --force` in its sandbox with the
    staged plugin via `--plugin-dir`. Flags are from each CLI's published
    reference (evals/README.md, Clients); they were not run in this build."""
    if client not in CLIENTS:
        raise ValueError("unknown client %r (expected one of %s)"
                         % (client, ", ".join(sorted(CLIENTS))))
    exe = exe or CLIENTS[client][0]
    if client == "claude":
        return build_command(exe, prompt, model, repo=plugin_dir, flags=flags,
                             mcp_config=mcp_config, bare=bare)
    if client == "codex":
        cmd = [exe, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check",
               "--ephemeral", "-c", "approval_policy='never'",
               "-c", "sandbox_workspace_write.network_access=false"]
    else:
        cmd = [exe, "-p", "--force", "--trust", "--sandbox", "enabled",
               "--plugin-dir", plugin_dir, "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    if prompt is not None:
        if client == "codex":
            cmd.append("--")  # after every option: clap keeps a prompt starting with "-" a prompt
        cmd.append(prompt)
    return cmd


def stage_codex_skill(workdir: str, plugin_dir: str) -> str:
    """Copy the staged skill to <workdir>/.agents/skills/design-and-fix, where Codex
    discovers repo-scoped skills. Call before taking the workdir baseline."""
    dest = os.path.join(workdir, ".agents", "skills", SKILL_NAME)
    shutil.copytree(os.path.join(plugin_dir, "skills", SKILL_NAME), dest,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def client_isolation(client: str, flags: set, bare: bool) -> str:
    if client == "codex":
        return ("writes limited to the temp working folder (codex exec --sandbox "
                "workspace-write, approvals never, network off, but /tmp and $TMPDIR stay "
                "writable); Bash NOT limited to the bundled scripts; user ~/.codex config, AGENTS.md and skills still loaded")
    if client == "cursor":
        return ("--force with --sandbox enabled and --trust; writes NOT confined to the "
                "temp folder and Bash NOT limited to the bundled scripts; user Cursor "
                "settings, rules and MCP servers still loaded")
    return isolation_summary(flags, bare)


UNSAFE_SHELL_CHARS = '"&|<>^%'


def shim_prompt_error(client: str, exe: str, prompts: List[str]) -> Optional[str]:
    """Cursor's prompt must go in argv (its stdin use is undocumented), and a
    .cmd/.bat shim re-parses argv in cmd.exe, which mangles these characters.
    Return an error message when that would corrupt a prompt, else None."""
    if client != "cursor" or not is_cmd_shim(exe):
        return None
    bad = [p for p in prompts if any(c in p for c in UNSAFE_SHELL_CHARS)]
    if not bad:
        return None
    return ("cursor resolved to a .cmd/.bat shim (%s); cmd.exe would mangle %d prompt(s) "
            "containing one of %s. Point --claude at the real executable "
            "(cursor-agent.exe or the underlying script), or use another client."
            % (exe, len(bad), " ".join(UNSAFE_SHELL_CHARS)))


def cli_label(client: str, version: str) -> str:
    return "%s (%s)" % (version, CLIENTS[client][1])


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
    for name in (".claude-plugin", ".codex-plugin", ".cursor-plugin", "skills"):
        if not os.path.isdir(os.path.join(repo, name)):
            continue
        shutil.copytree(os.path.join(repo, name), os.path.join(plugin, name),
                        ignore=shutil.ignore_patterns("__pycache__"))
    if " " in plugin:
        print("warning: staged plugin path has spaces; script allow rules may not match",
              file=sys.stderr)
    return plugin


# ---------------------------------------------------------------- process control
# Created by main() and removed in its `finally`, so importing this module leaves nothing behind.
_PYCACHE_PREFIX = None  # type: Optional[str]


def _is_cache_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return path.endswith(".pyc") or "__pycache__" in parts


def _child_env() -> Dict[str, str]:
    """Environment for child processes: no .pyc files, so a run that executes the
    bundled scripts leaves no __pycache__ behind to confuse the file checks."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Bytecode lives only under a private prefix, so no child (the grader's linter
    # included) ever reads a __pycache__ file from the repo or the staged plugin.
    if _PYCACHE_PREFIX:
        env["PYTHONPYCACHEPREFIX"] = _PYCACHE_PREFIX
    return env


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
                            encoding="utf-8", errors="replace", env=_child_env(),
                            **popen_group_kwargs())
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
               bare: bool = False, client: str = "claude") -> RunResult:
    """Run one case with `client` (`claude` is the exe path); never raises on timeout."""
    # Cursor's stdin handling is undocumented: always pass its prompt as an argument.
    via_stdin = is_cmd_shim(claude) and client != "cursor"
    cmd = build_command_for(client, None if via_stdin else case["prompt"], repo, model,
                            exe=claude, flags=flags, mcp_config=mcp_config, bare=bare)
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


def render_results(rows: List[Dict[str, str]], models, cli: str,
                   plugin: str, date: str, isolation: str = "") -> str:
    """skill-results.md: one row per case x model. The first `N / M` in the file is
    the pass line (all rows counted), which the README skill-evals block must
    repeat (tests/test_repo_docs.py checks it). `models` is a name or a list."""
    names = [models] if isinstance(models, str) else list(models)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    out = ["# Skill eval results", "",
           "**%d / %d cases pass.**" % (passed, len(rows)), "",
           "Generated by `python evals/run.py`, every case once per model (one row per "
           "case and model). Models: %s. CLI: %s. Plugin: %s. Date: %s."
           % (_cell(", ".join(names)), _cell(cli), _cell(plugin), date)]
    if isolation:
        out += ["", "Isolation: %s." % _cell(isolation)]
    out += ["", "| Case | Model | Result | CLI version | Plugin version | Date | Failures |",
            "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], _cell(r.get("model") or names[0]), r["result"],
            _cell(r.get("cli") or cli),
            _cell(plugin), date, _cell(r["failures"])))
    out += ["", "History and notes on earlier runs: [evals/README.md](README.md#skill-eval-history)."]
    return "\n".join(out) + "\n"


def _split_row(line: str) -> List[str]:
    cells = re.split(r"(?<!\\)\|", line.strip()[1:-1])
    return [c.strip().replace("\\|", "|") for c in cells]


def merge_results(texts: List[str]) -> str:
    """Merge per-model skill-results files into one file with one headline."""
    rows = []  # type: List[Dict[str, str]]
    models = []  # type: List[str]
    plugin = date = isolation = ""
    clis = []  # type: List[str]
    for text in texts:
        m = re.search(r"^Isolation: (.*)\.$", text, re.M)
        if m and not isolation:
            isolation = m.group(1)
        for line in text.splitlines():
            if not line.startswith("| ") or line.startswith("| Case "):
                continue
            cells = _split_row(line)
            if len(cells) != 7:
                continue
            rows.append({"id": cells[0], "model": cells[1], "result": cells[2],
                         "failures": cells[6], "cli": cells[3]})
            plugin, date = cells[4], cells[5]
            if cells[3] not in clis:
                clis.append(cells[3])
            if cells[1] not in models:
                models.append(cells[1])
    if not rows:
        raise ValueError("no result rows found")
    rows.sort(key=lambda r: (r["id"], models.index(r["model"]), r["cli"]))
    return render_results(rows, models, ", ".join(clis), plugin, date, isolation)


def merge_files(paths: List[str], out_path: str = RESULTS_FILE) -> str:
    texts = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            texts.append(fh.read())
    text = merge_results(texts)
    _write(out_path, text)
    return text


def parse_models(values: Optional[List[str]]) -> List[Optional[str]]:
    """Repeatable and comma-separated --model values, de-duplicated, order kept.
    No value means one run with the CLI's default model ([None])."""
    models = []  # type: List[Optional[str]]
    for value in values or []:
        for name in value.split(","):
            name = name.strip()
            if name and name not in models:
                models.append(name)
    return models or [None]


def run_case(claude: str, case: dict, model: Optional[str], timeout: int, flags: set,
             mcp_config: str, plugin_dir: str, guarded: List[str], runs_dir: str,
             bare: bool, keep: bool, client: str = "claude") -> List[str]:
    """Run one case on one model in a fresh temp folder; return its failures."""
    workdir = tempfile.mkdtemp(prefix="dfeval-%s-" % case["id"])
    try:
        prepare_workdir(case, workdir)
        guarded = list(guarded)
        if client == "codex":
            guarded.append(stage_codex_skill(workdir, plugin_dir))
        baseline = workdir_files(workdir)
        before = snapshot(guarded)
        result = run_claude(claude, case, workdir, timeout, model,
                            flags, mcp_config, repo=plugin_dir, bare=bare, client=client)
        # Cache entries that already existed are ignored (other local Python runs, for
        # example a test suite, rewrite them); a new cache entry is reported like any new file.
        tampered = [p for p in changed_files(before, snapshot(guarded, before))
                    if not (_is_cache_path(p) and p in before)]
        suffix = "-" + re.sub(r"[^\w.-]", "_", model) if model else ""
        _write(os.path.join(runs_dir, case["id"] + suffix + ".txt"), result.output)
        failures = grade(case, workdir, result, baseline, client)
        if tampered:
            failures.insert(0, "grader or plugin files changed during the run: %s"
                            % ", ".join(tampered[:10]))
        return failures
    finally:
        if keep:
            print("kept: %s" % workdir)
        else:
            remove_tree(workdir)


def main(argv: Optional[List[str]] = None) -> int:
    # A Windows console (cp1252) cannot print every character a failure message may carry.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Run design-and-fix skill evals headlessly.")
    ap.add_argument("--case", help="run only this case id (e.g. 01-audit-monorepo or 01)")
    ap.add_argument("--client", choices=sorted(CLIENTS), default="claude",
                    help="agent CLI to test (default claude)")
    ap.add_argument("--claude", default=None,
                    help="path to the client's CLI (claude, codex or cursor-agent)")
    ap.add_argument("--model", action="append", default=None,
                    help="model alias or id passed to claude; repeat or comma-separate "
                         "to run every case on each model")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help="seconds per case (default %d)" % DEFAULT_TIMEOUT)
    ap.add_argument("--keep", action="store_true", help="keep temp working folders")
    ap.add_argument("--bare", action="store_true",
                    help="pass --bare (skips CLAUDE.md and hooks; needs ANTHROPIC_API_KEY)")
    ap.add_argument("--merge-results", nargs="+", metavar="FILE",
                    help="merge per-model results files into evals/skill-results.md and exit")
    args = ap.parse_args(argv)

    if args.merge_results:
        try:
            text = merge_files(args.merge_results)
        except (OSError, ValueError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        print(PASS_LINE_RE.search(text).group(0))
        return 0

    client = args.client
    claude = find_client(client, args.claude)
    if not claude:
        print("error: %s CLI not found; pass --claude PATH" % CLIENTS[client][0],
              file=sys.stderr)
        return 2
    if args.bare and client != "claude":
        print("error: --bare applies to --client claude only", file=sys.stderr)
        return 2
    if args.bare and not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: --bare needs ANTHROPIC_API_KEY (bare mode reads no OAuth login)",
              file=sys.stderr)
        return 2
    models = parse_models(args.model)
    cases = load_cases()
    if args.case:
        cases = [c for c in cases
                 if c["id"] == args.case or c["id"].startswith(args.case + "-")]
        if not cases:
            print("error: no case matches %r" % args.case, file=sys.stderr)
            return 2

    problem = shim_prompt_error(client, claude, [c["prompt"] for c in cases])
    if problem:
        print("error: %s" % problem, file=sys.stderr)
        return 2
    flags = supported_flags(claude) if client == "claude" else set()
    missing = [f for f in ISOLATION_FLAGS if f not in flags] if client == "claude" else []
    if missing:
        print("warning: claude lacks %s; run is less isolated" % ", ".join(missing),
              file=sys.stderr)
    if args.bare and "--bare" not in flags:
        print("warning: claude lacks --bare; running without it", file=sys.stderr)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs_dir = os.path.join(EVALS_DIR, "runs", stamp)
    os.makedirs(runs_dir, exist_ok=True)
    rows = []  # type: List[Dict[str, str]]
    global _PYCACHE_PREFIX
    cfg_dir = tempfile.mkdtemp(prefix="dfeval-cfg-")
    _PYCACHE_PREFIX = tempfile.mkdtemp(prefix="dfeval-pycache-")
    try:
        mcp_config = write_empty_mcp(cfg_dir)
        plugin_dir = stage_plugin(cfg_dir)
        cli_text = cli_label(client, cli_version(claude))
        guarded = guarded_paths(plugin_dir)
        for case in cases:
            timeout = min(args.timeout, case["timeout"]) if case.get("timeout") else args.timeout
            for model in models:
                failures = run_case(claude, case, model, timeout, flags, mcp_config,
                                    plugin_dir, guarded, runs_dir, args.bare, args.keep, client)
                rows.append({"id": case["id"], "model": model or "default",
                             "cli": cli_text,
                             "result": "FAIL" if failures else "PASS",
                             "failures": "; ".join(failures)})
                print("%-40s %-10s %s" % (case["id"], rows[-1]["model"], rows[-1]["result"]))
                for f in failures:
                    print("    - %s" % f)
    finally:
        remove_tree(cfg_dir)
        remove_tree(_PYCACHE_PREFIX)
        _PYCACHE_PREFIX = None
    date = datetime.date.today().isoformat()
    text = render_results(rows, [m or "default" for m in models], cli_text,
                          plugin_version(), date, client_isolation(client, flags, args.bare))
    if not args.case:
        # Only Claude Code owns skill-results.md; other clients write a sibling file
        # to combine with --merge-results (a PASS row there is what the README matrix
        # needs before it may say "end-to-end tested").
        _write(RESULTS_FILE if client == "claude"
               else os.path.join(EVALS_DIR, "skill-results-%s.md" % client), text)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    print("%d/%d passed" % (passed, len(rows)))
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
