from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Dict, Iterable, List, Optional

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
SCRIPTS = os.path.join(REPO, "skills", "design-and-fix", "scripts")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def run_cli(*args, env: Optional[Dict[str, str]] = None):
    """Run scan_repo.py as a subprocess; return (exit_code, stdout).

    `env` entries are added to the current environment."""
    full_env = dict(os.environ, **env) if env else None
    proc = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "scan_repo.py")] + list(args),
        capture_output=True, text=True, cwd=REPO, env=full_env,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout


# --- Markdown helpers (skill docs, templates, examples) ---------------------

RERUN_ONLY = " (re-run only)"
PREFIX_MATCH_HEADERS = ("Architecture Health Score",)

_FILE_ROW = re.compile(r"^\|\s*`([^`]+\.md)`\s*\|\s*([^|]+?)\s*\|\s*$")


def headings(md: str) -> List[str]:
    """Level-2 heading texts in order, ignoring headings inside fenced code blocks."""
    out = []
    in_fence = False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            out.append(line[3:].strip())
    return out


def _header_rows(rules_md: str) -> Dict[str, List[str]]:
    rows = {}
    for line in rules_md.splitlines():
        m = _FILE_ROW.match(line.strip())
        if m:
            rows[m.group(1)] = [h.strip() for h in m.group(2).split(",") if h.strip()]
    return rows


def expected_headers(rules_md: str) -> Dict[str, List[str]]:
    """Standard headers per file from formatting-rules.md, with the re-run-only marker stripped."""
    return {f: [h[: -len(RERUN_ONLY)] if h.endswith(RERUN_ONLY) else h for h in hs]
            for f, hs in _header_rows(rules_md).items()}


def rerun_only_headers(rules_md: str) -> Dict[str, List[str]]:
    """Headers marked "(re-run only)" per file (marker stripped); these may be omitted."""
    return {f: [h[: -len(RERUN_ONLY)] for h in hs if h.endswith(RERUN_ONLY)]
            for f, hs in _header_rows(rules_md).items()}


def header_mismatch(actual: List[str], expected: List[str],
                    optional: Iterable[str] = ()) -> Optional[str]:
    """None if `actual` equals `expected` in order (optional headers may be absent);
    headers in PREFIX_MATCH_HEADERS match by prefix. Otherwise a description."""
    def same(a: str, e: str) -> bool:
        return a.startswith(e) if e in PREFIX_MATCH_HEADERS else a == e

    opt = set(optional)
    i = 0
    for e in expected:
        if i < len(actual) and same(actual[i], e):
            i += 1
        elif e not in opt:
            return "expected %r at position %d, got %r" % (
                e, i, actual[i] if i < len(actual) else None)
    if i != len(actual):
        return "unexpected extra headers: %r" % (actual[i:],)
    return None


def subscore(severities: Iterable[str]) -> int:
    """Category sub-score from the rubric lookup table, given its issues' severities."""
    sev = list(severities)
    crit, high, med = sev.count("Critical"), sev.count("High"), sev.count("Medium")
    if crit:
        return {1: 3, 2: 2}.get(crit, 1)
    if high >= 3:
        return 3
    if high == 2:
        return 4
    if high == 1:
        return 6 if med <= 2 else 5
    if med:
        return 8 if med <= 2 else 7
    return 9 if sev else 10


def overall_score(subscores: Iterable[int]) -> float:
    """Mean of assessed sub-scores, rounded half up to one decimal."""
    vals = list(subscores)
    return int(sum(vals) * 10.0 / len(vals) + 0.5) / 10.0


def overall_band(score: float) -> str:
    if score >= 8.0:
        return "Healthy"
    if score >= 6.0:
        return "Stable with risks"
    if score >= 4.0:
        return "Fragile"
    return "Unsafe"
