from __future__ import annotations

import fnmatch
import os
import re
from typing import List, Optional

from .model import FileInfo

IGNORE_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".next", ".cache", "coverage",
    ".idea", ".vscode", "out",
}

MAX_FILE_BYTES = 1_048_576

# Extension -> language label (code files only).
CODE_EXTS = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".ex": "Elixir", ".cs": "C#",
    ".sql": "SQL", ".proto": "Protocol Buffers (gRPC likely)",
    ".graphql": "GraphQL",
}

# Manifests are always listed (stack detection needs them).
MANIFEST_PATTERNS = (
    "package.json", "pnpm-lock.yaml", "yarn.lock", "requirements.txt",
    "pyproject.toml", "Pipfile", "go.mod", "Cargo.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "mix.exs",
    "*.csproj", "*.fsproj", "*.sln", "docker-compose.yml",
    "docker-compose.yaml", "Dockerfile",
)

# Config files are listed for the hardcoded-secret rule only (no LOC, not code).
CONFIG_PATTERNS = (
    ".env", ".env.*", "*.yml", "*.yaml", "*.json", "*.toml", "*.ini", "*.properties",
)
CONFIG_EXCLUDE = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "composer.lock",
    "Cargo.lock", "go.sum", ".env.example", ".env.sample", ".env.template",
}

_TEST_NAME = re.compile(
    r"(^test_.*\.py$)|(_test\.py$)|(_test\.go$)|(\.test\.(js|ts|jsx|tsx)$)|(\.spec\.[^/]+$)"
)
_TEST_DIRS = {"tests", "test", "__tests__"}


def is_manifest(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in MANIFEST_PATTERNS)


def is_config(name: str) -> bool:
    if name in CONFIG_EXCLUDE:
        return False
    return any(fnmatch.fnmatchcase(name.lower(), p) for p in CONFIG_PATTERNS)


def is_test_file(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if _TEST_NAME.search(parts[-1]):
        return True
    return any(p in _TEST_DIRS for p in parts[:-1])


def normalize_scope(entry: str) -> str:
    s = entry.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    s = s.strip("/")
    return "" if s == "." else s


def _is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True


def _count_lines(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def walk(root: str, scope: Optional[List[str]] = None) -> List[FileInfo]:
    """Full-depth walk. Oversized or binary code files are not listed at all."""
    root = os.path.abspath(root)
    scopes = [normalize_scope(s) for s in (scope or [])]
    scopes = [s for s in scopes if s]
    start_dirs = [os.path.join(root, *s.split("/")) for s in scopes] or [root]
    out: List[FileInfo] = []
    seen = set()
    for start in start_dirs:
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")
            )
            for fname in sorted(filenames):
                full = os.path.join(dirpath, fname)
                if full in seen:
                    continue
                seen.add(full)
                ext = os.path.splitext(fname)[1].lower()
                lang = CODE_EXTS.get(ext)
                manifest = is_manifest(fname)
                config = lang is None and is_config(fname)
                if lang is None and not manifest and not config:
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if config and size > MAX_FILE_BYTES:
                    if not manifest:
                        continue
                    config = False
                loc = 0
                if lang is not None:
                    if size > MAX_FILE_BYTES or _is_binary(full):
                        continue
                    counted = _count_lines(full)
                    if counted is None:
                        continue
                    loc = counted
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                out.append(FileInfo(rel, full, ext, lang, loc, size, is_test_file(rel), config))
    return out


def candidate_folders(files: List[FileInfo], list_depth: int = 2, top: int = 15) -> List[dict]:
    """Folders at depth 1..list_depth with code anywhere beneath (recursive sums)."""
    agg = {}  # folder -> [file_count, loc]
    for f in files:
        if f.lang is None:
            continue
        parts = f.rel.split("/")[:-1]
        if not parts:
            keys = ["."]
        else:
            keys = ["/".join(parts[:i]) for i in range(1, min(len(parts), list_depth) + 1)]
        for k in keys:
            entry = agg.setdefault(k, [0, 0])
            entry[0] += 1
            entry[1] += f.loc
    rows = [{"path": k, "file_count": v[0], "approx_loc": v[1]} for k, v in agg.items()]
    rows.sort(key=lambda r: (-r["approx_loc"], r["path"]))
    return rows[:top]
