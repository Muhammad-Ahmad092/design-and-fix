from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional

from .model import FileInfo

UNAVAILABLE_NOTE = "git history unavailable: hotspots skipped"


def parse_git_log(text: str) -> Dict[str, int]:
    """Count commits per path from `git log --name-only --pretty=format:` output."""
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        path = line.strip().replace("\\", "/")
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def _find_repo_root(root: str) -> Optional[str]:
    cur = os.path.abspath(root)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def compute_hotspots(root: str, files: List[FileInfo], since: str = "12.months",
                     enabled: bool = True, top: int = 15) -> dict:
    result: dict = {"available": False, "since": since, "files": [], "note": None}
    if not enabled:
        result["note"] = "git hotspots disabled (--no-git)"
        return result
    root = os.path.abspath(root)
    repo_root = _find_repo_root(root)
    if shutil.which("git") is None or repo_root is None:
        result["note"] = UNAVAILABLE_NOTE
        return result
    try:
        proc = subprocess.run(
            ["git", "-C", root, "-c", "core.quotePath=false", "log",
             "--since=%s" % since, "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        counts = parse_git_log(proc.stdout)
        # git log paths are repo-root-relative; map to scan-root-relative.
        prefix = os.path.relpath(root, repo_root).replace(os.sep, "/")
        prefix = "" if prefix == "." else prefix + "/"
        by_rel = {f.rel: f for f in files if f.lang is not None}
        rows = []
        for path, n in counts.items():
            if prefix:
                if not path.startswith(prefix):
                    continue
                path = path[len(prefix):]
            fi = by_rel.get(path)
            if fi is None:
                continue
            rows.append({"file": path, "commits": n, "loc": fi.loc, "score": n * fi.loc})
        rows.sort(key=lambda r: (-r["score"], r["file"]))
        result["files"] = rows[:top]
        result["available"] = True
    except Exception:
        result["available"] = False
        result["files"] = []
        result["note"] = UNAVAILABLE_NOTE
    return result
