#!/usr/bin/env python3
"""
scan_repo.py - helper for Mode 2 (Existing Project) audits.

Thin CLI over the stdlib-only `dfscan` package. Reports detected stack,
frameworks, candidate folders (recursive sizing) and, as analyzers land,
import graph, routes, risky-pattern signals and git hotspots.

Usage:
    python3 scan_repo.py <path> [--scope DIR]... [--list-depth N] [--top N]
                         [--no-git] [--since 12.months] [--format json|markdown]
                         [--folders-only]

Output: JSON (default) to stdout. Errors: {"error": "..."} and exit code 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dfscan.report import render_markdown, scan  # noqa: E402
from dfscan.walker import normalize_scope  # noqa: E402


def _error(msg: str) -> int:
    print(json.dumps({"error": msg}))
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="Repo root to scan")
    parser.add_argument("--scope", action="append", default=[],
                        help="Limit scan to a subfolder (repeatable)")
    parser.add_argument("--list-depth", type=int, default=2,
                        help="Folder depth reported as candidates (default: 2)")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="Deprecated alias for --list-depth")
    parser.add_argument("--top", type=int, default=15,
                        help="Max candidate folders to return (default: 15)")
    parser.add_argument("--no-git", action="store_true", help="Do not run git log")
    parser.add_argument("--since", default="12.months", help="Git history window")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--folders-only", action="store_true",
                        help="Fast scope discovery: stack, frameworks and candidate folders only")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # non-ASCII paths on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        return _error("not a directory: %s" % args.path)

    scope = []
    for entry in args.scope:
        if os.path.isabs(entry):  # absolute path inside root -> root-relative
            try:
                rel = os.path.relpath(os.path.abspath(entry), root)
            except ValueError:  # different drive on Windows
                rel = ".."
            if rel == ".." or rel.startswith(".." + os.sep):
                return _error("scope outside root: %s" % entry)
            entry = rel
        s = normalize_scope(entry)
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
                      not args.no_git, args.since, args.folders_only)
    except Exception as exc:  # never a traceback
        return _error("scan failed: %s" % exc)
    result["notes"].extend(notes)

    if args.format == "markdown":
        print(render_markdown(result), end="")
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
