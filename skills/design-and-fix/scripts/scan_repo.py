#!/usr/bin/env python3
"""
scan_repo.py - helper for Mode 2 (Existing Project) audits.

Thin CLI over the stdlib-only `dfscan` package. Reports detected stack,
frameworks, candidate folders (recursive sizing) and, as analyzers land,
import graph, routes, risky-pattern signals and git hotspots.

Usage:
    python3 scan_repo.py <path> [--scope DIR]... [--list-depth N] [--top N]
                         [--no-git] [--since 12.months] [--format json|markdown]
                         [--folders-only] [--no-gitignore] [--quiet] [--verbose]
                         [--config PATH] [--fail-on LEVEL]

Output: JSON (default), Markdown or SARIF 2.1.0 to stdout. Errors: {"error": "..."}.
Exit codes: 0 ok, 1 runtime error, 2 usage/config error, 3 --fail-on threshold exceeded.
Settings come from <path>/.design-and-fix.json (see docs/configuration.md); CLI flags win.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable, List, Optional

if sys.version_info < (3, 9):  # actionable message instead of a SyntaxError/ImportError traceback
    print(json.dumps({"error": "scan_repo.py needs Python 3.9 or newer (found %d.%d); "
                               "run it with python3.9+" % sys.version_info[:2]}))
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dfscan import config as dfconfig  # noqa: E402
from dfscan.registry import SEVERITIES  # noqa: E402
from dfscan.report import render_markdown, scan  # noqa: E402
from dfscan.sarif import to_sarif  # noqa: E402
from dfscan.walker import normalize_scope  # noqa: E402


def _error(msg: str, code: int = 1) -> int:
    print(json.dumps({"error": msg}))
    return code


def _fail_level(text: str) -> str:
    for level in dfconfig.FAIL_LEVELS:
        if level.lower() == text.lower():
            return level
    raise argparse.ArgumentTypeError("invalid level %r (use %s)" % (
        text, ", ".join(dfconfig.FAIL_LEVELS)))


def _non_negative(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid number %r (use a whole number, 0 or more)" % text)
    if value < 0:
        raise argparse.ArgumentTypeError("%d is negative (use a whole number, 0 or more)" % value)
    return value


def _exceeds(result: dict, level: str) -> bool:
    if level == "none":
        return False
    limit = SEVERITIES.index(level)  # lower index = more severe
    return any(SEVERITIES.index(s["severity_hint"]) <= limit for s in result["signals"])


def _make_progress(quiet: bool) -> Optional[Callable[[str], None]]:
    """Progress lines go to stderr only: first once the scan passes 5 s, then every ~2 s.

    DF_PROGRESS_AFTER / DF_PROGRESS_EVERY (seconds) override the thresholds (used by tests).
    """
    if quiet:
        return None
    after = float(os.environ.get("DF_PROGRESS_AFTER", "5"))
    every = float(os.environ.get("DF_PROGRESS_EVERY", "2"))
    start = time.monotonic()
    last = [None]  # type: List[Optional[float]]

    def progress(msg: str) -> None:
        now = time.monotonic()
        if now - start < after or (last[0] is not None and now - last[0] < every):
            return
        last[0] = now
        print("scan: %s (%.0fs elapsed)" % (msg, now - start), file=sys.stderr, flush=True)

    return progress


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="Repo root to scan")
    parser.add_argument("--scope", action="append", default=[],
                        help="Limit scan to a subfolder (repeatable)")
    parser.add_argument("--list-depth", type=_non_negative, default=2,
                        help="Folder depth reported as candidates (default: 2)")
    parser.add_argument("--max-depth", type=_non_negative, default=None,
                        help="Deprecated alias for --list-depth")
    parser.add_argument("--top", type=_non_negative, default=15,
                        help="Max candidate folders to return (default: 15)")
    parser.add_argument("--no-git", action="store_true", help="Do not run git log")
    parser.add_argument("--since", default="12.months", help="Git history window")
    parser.add_argument("--format", choices=("json", "markdown", "sarif"), default="json")
    parser.add_argument("--folders-only", action="store_true",
                        help="Fast scope discovery: stack, frameworks and candidate folders only")
    parser.add_argument("--no-gitignore", action="store_true",
                        help="Scan files matched by the root .gitignore too")
    parser.add_argument("--quiet", action="store_true", help="No progress lines on stderr")
    parser.add_argument("--verbose", action="store_true",
                        help="Add per-stage and per-rule timing to notes")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="Config file (default: <path>/.design-and-fix.json if present)")
    parser.add_argument("--fail-on", type=_fail_level, default=None, metavar="LEVEL",
                        help="Exit 3 when any signal is at or above Critical|High|Medium|Low "
                             "(default: never fails; overrides the config file)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # non-ASCII paths on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        return _error("not a directory: %s (check the path)" % args.path)
    try:
        os.listdir(root)
    except OSError as exc:
        return _error("cannot read %s: %s; check the folder permissions or pass a readable path"
                      % (args.path, exc.strerror or exc))

    try:
        cfg = dfconfig.load_config(root, args.config)
    except dfconfig.ConfigError as exc:
        return _error("config error: %s" % exc.message, 2)
    fail_on = args.fail_on or cfg.fail_on

    scope = []
    for entry in args.scope:
        # Absolute or relative, resolve against the root; `..` that leaves the root is an error.
        full = os.path.normpath(os.path.join(root, entry))
        try:
            rel = os.path.relpath(full, root)
        except ValueError:  # different drive on Windows
            rel = ".."
        if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
            return _error("scope outside root: %s" % entry)
        s = normalize_scope(rel)
        if s:
            scope.append(s)
    for s in scope:
        if not os.path.isdir(os.path.join(root, *s.split("/"))):
            return _error("scope not found: %s" % s)

    list_depth = args.list_depth
    notes = []
    if args.max_depth is not None:
        list_depth = args.max_depth
        notes.append("--max-depth is deprecated; use --list-depth")

    try:
        result = scan(root, scope or None, list_depth, args.top,
                      not args.no_git, args.since, args.folders_only,
                      config_hash=dfconfig.config_hash(cfg),
                      respect_gitignore=not args.no_gitignore,
                      max_files=cfg.limits["max_files"],
                      timeout_seconds=cfg.limits["timeout_seconds"],
                      progress=_make_progress(args.quiet), verbose=args.verbose or cfg.verbose,
                      max_file_bytes=cfg.limits["max_file_bytes"],
                      exclude=cfg.exclude, include=cfg.include, rule_config=cfg.rules)
    except Exception as exc:  # never a traceback
        return _error("scan failed: %s" % exc)
    result["notes"].extend(notes)

    if args.format == "markdown":
        print(render_markdown(result), end="")
    elif args.format == "sarif":
        print(json.dumps(to_sarif(result), indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 3 if _exceeds(result, fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
