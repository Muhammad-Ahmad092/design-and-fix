#!/usr/bin/env python3
"""
report_quality.py - deterministic quality checks for an AI-written audit (no LLM judge).

Usage:
    python evals/report_quality.py AUDIT_DIR --repo REPO

Failures (one line each; exit 1 when any):
  - an Evidence path does not exist in the repo (file or folder);
  - an Evidence line (or the end of a range) is beyond the end of the file;
  - a `confirmed` issue has no parsable Evidence;
  - prose (Issue, Root cause, Consequence) names a file in backticks, or a route like
    `POST /items`, that does not exist in the repo (not when the sentence says it is absent:
    "no `package-lock.json`"; routes: the static prefix must appear in some
    source file); Fix and Verify may propose new files, so they are not checked;
  - an issue ID reused with a different title in `AUDIT_DIR/history/*` (title similarity below
    compare_audits.FUZZY_TITLE_MIN).

Tables are parsed with the project's parsers (compare_audits, lint_output).
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO_ROOT, "skills", "design-and-fix", "scripts")
for _p in (HERE, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import compare_audits as ca  # noqa: E402
from lint_output import evidence_path_problem, parse_evidence  # noqa: E402
from verify_precision import read_audit_tables  # noqa: E402

PROSE_COLS = ("issue", "root cause", "consequence")
FILE_EXTS = {"py", "js", "ts", "tsx", "jsx", "mjs", "cjs", "go", "java", "kt", "rb", "php", "cs",
             "rs", "json", "yml", "yaml", "toml", "md", "sql", "sh", "xml", "gradle", "lock",
             "tf", "properties", "html", "css", "txt", "mod", "sum"}
NOT_FILES = {"res", "req", "response", "request", "ctx", "c", "r", "w", "resp", "data", "body",
             "result", "err", "json"}
SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_TEXT = 512 * 1024
_TICK_RE = re.compile(r"`([^`\n]+)`")
_FILE_TOKEN_RE = re.compile(r"^(?:\./)?([\w@.\-]+(?:/[\w@.\-]+)*)(?::\d+(?:-\d+)?)?$")
_ABSENT_RE = re.compile(r"(?<![A-Za-z])(?:no|missing|without|absent|lacks?|lacking|not)(?![A-Za-z])[^`]{0,40}$", re.I)
_ROUTE_RE = re.compile(r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S*)$")


class Repo:
    """Lazy view of the repo: file list, line counts and (bounded) source text."""

    def __init__(self, root: str):
        self.root = os.path.realpath(root)
        self.files = []  # type: List[str]
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, fn), self.root).replace(os.sep, "/")
                self.files.append(rel)
        self._text = None  # type: Optional[str]

    def resolve(self, rel: str) -> Optional[str]:
        full = os.path.realpath(os.path.join(self.root, *rel.split("/")))
        if full != self.root and not full.startswith(self.root + os.sep):
            return None
        return full if os.path.exists(full) else None

    def line_count(self, rel: str) -> int:
        full = self.resolve(rel)
        if not full or not os.path.isfile(full):
            return 0
        with open(full, encoding="utf-8", errors="replace", newline="") as fh:
            return len(fh.read().splitlines())

    def has_file_like(self, token: str) -> bool:
        """Exact path, or any repo path ending in `/token` (prose often omits the folder)."""
        if self.resolve(token):
            return True
        return any(f == token or f.endswith("/" + token) for f in self.files)

    def all_text(self) -> str:
        if self._text is None:
            parts = []
            for rel in self.files:
                if rel.rsplit(".", 1)[-1].lower() not in FILE_EXTS:
                    continue
                full = os.path.join(self.root, *rel.split("/"))
                try:
                    if os.path.getsize(full) > MAX_TEXT:
                        continue
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        parts.append(fh.read())
                except OSError:
                    continue
            self._text = "\n".join(parts)
        return self._text


def _static_prefix(path: str) -> str:
    m = re.search(r"[:<{*\[(]", path)
    prefix = path[:m.start()] if m else path
    return prefix.rstrip("/")


def _file_tokens(cell: str) -> List[str]:
    out = []  # type: List[str]
    for hit in _TICK_RE.finditer(cell):
        tok = hit.group(1)
        m = _FILE_TOKEN_RE.match(tok.strip())
        if not m or "." not in m.group(1):
            continue
        if _ABSENT_RE.search(cell[:hit.start()]):
            continue   # "no `package-lock.json`": naming an absent file is the point
        path = m.group(1)
        stem, _, ext = path.rsplit("/", 1)[-1].rpartition(".")
        if ext.lower() not in FILE_EXTS or not stem:
            continue
        if "/" not in path and stem.lower() in NOT_FILES:
            continue
        if path not in out:
            out.append(path)
    return out


def _routes(cell: str) -> List[str]:
    out = []  # type: List[str]
    for tok in _TICK_RE.findall(cell):
        m = _ROUTE_RE.match(tok.strip())
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", title.lower())).strip()


def _history_titles(audit_dir: str) -> Dict[str, List[Tuple[str, str]]]:
    """issue id -> [(label, title)] over the current audit and every history/* run."""
    out = {}  # type: Dict[str, List[Tuple[str, str]]]
    runs = [("current", audit_dir)]
    hist = os.path.join(audit_dir, "history")
    if os.path.isdir(hist):
        runs += [("history/" + d, os.path.join(hist, d)) for d in sorted(os.listdir(hist))
                 if os.path.isdir(os.path.join(hist, d))]
    for label, path in runs:
        for issue in ca.parse_issues(path):
            out.setdefault(issue.id, []).append((label, issue.title))
    return out


def check(audit_dir: str, repo: str) -> List[str]:
    """Deterministic failures for the audit in `audit_dir` against the checkout at `repo`."""
    r = Repo(repo)
    failures = []  # type: List[str]
    for issue in read_audit_tables(audit_dir):
        where = "%s %s" % (issue["_file"], issue["id"])
        evidence = parse_evidence(issue.get("evidence", ""))
        if ca.clean_cell(issue.get("status", "")).lower() == "confirmed" and not evidence:
            failures.append("%s: confirmed issue has no Evidence" % where)
        for path, first, last, token in evidence:
            problem = evidence_path_problem(path)
            if problem:
                failures.append("%s: Evidence path %r %s" % (where, path, problem))
                continue
            rel = path.replace("\\", "/")
            full = r.resolve(rel)
            if full is None:
                failures.append("%s: Evidence %r does not exist in the repo" % (where, token))
            elif first and os.path.isfile(full) and max(first, last) > r.line_count(rel):
                failures.append("%s: Evidence %r is beyond the end of the file (%d lines)"
                                % (where, token, r.line_count(rel)))
        for col in PROSE_COLS:
            cell = issue.get(col, "")
            for tok in _file_tokens(cell):
                if not r.has_file_like(tok):
                    failures.append("%s: %s names file %r that does not exist" % (where, col, tok))
            for route in _routes(cell):
                prefix = _static_prefix(route)
                if prefix and prefix not in r.all_text():
                    failures.append("%s: %s names route %r not found in any source file"
                                    % (where, col, route))
    for iid, entries in sorted(_history_titles(audit_dir).items()):
        base_label, base_title = entries[0]
        for label, title in entries[1:]:
            ratio = difflib.SequenceMatcher(None, _norm(base_title), _norm(title)).ratio()
            if ratio < ca.FUZZY_TITLE_MIN:
                failures.append("%s reused with a different title: %r (%s) vs %r (%s)"
                                % (iid, base_title, base_label, title, label))
    return failures


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic AI audit quality checks")
    ap.add_argument("audit_dir")
    ap.add_argument("--repo", required=True)
    args = ap.parse_args(argv)
    for p in (args.audit_dir, args.repo):
        if not os.path.isdir(p):
            print("report_quality: not a folder: %s" % p, file=sys.stderr)
            return 2
    failures = check(args.audit_dir, args.repo)
    for f in failures:
        print(f)
    if not failures:
        print("report_quality: no failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
