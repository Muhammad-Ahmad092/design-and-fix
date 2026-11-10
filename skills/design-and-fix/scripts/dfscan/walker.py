from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .ignore import GitIgnore
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
    r"|(_spec\.rb$)"  # RSpec (x.spec.rb is covered by .spec.*)
)
_TEST_DIRS = {"tests", "test", "__tests__"}
# Example/demo programs: listed and scanned, but route and folder rules skip them.
EXAMPLE_DIRS = {"examples", "example", "_examples", "_example", "demo", "demos", "samples"}


def _globs(patterns: Tuple[str, ...]) -> "re.Pattern[str]":
    """One regex matching like any(fnmatch.fnmatchcase(name, p) for p in patterns)."""
    return re.compile("|".join(fnmatch.translate(p) for p in patterns))


# fnmatch.fnmatch normcases name and pattern (case-insensitive on Windows): same here.
_MANIFEST_RE = _globs(tuple(os.path.normcase(p) for p in MANIFEST_PATTERNS))
_CONFIG_RE = _globs(CONFIG_PATTERNS)


def is_manifest(name: str) -> bool:
    return bool(_MANIFEST_RE.match(os.path.normcase(name)))


def is_config(name: str) -> bool:
    if name in CONFIG_EXCLUDE:
        return False
    return bool(_CONFIG_RE.match(name.lower()))


# Infra files (spec 9.3): walked with is_infra=True and no LOC, read only by the infra rules.
# Dot folders are skipped by the walk except these, and inside them only infra files are listed.
INFRA_DOT_DIRS = {".github", ".circleci"}
_K8S_WORKLOAD_RE = re.compile(r"(?m)^[ \t]*kind:[ \t]*[\"']?(?:Deployment|StatefulSet|DaemonSet)[\"']?[ \t]*(?:#[^\n]*)?$")


def is_infra_name(rel: str) -> bool:
    """Dockerfile, Dockerfile.*, *.dockerfile, .github/workflows/*.yml, .gitlab-ci.yml,
    .circleci/config.yml (k8s YAML is recognised by content, see is_k8s_workload)."""
    parts = rel.replace("\\", "/").split("/")
    low = parts[-1].lower()
    if low == "dockerfile" or low.endswith(".dockerfile"):
        return True
    if low.startswith("dockerfile.") and not low.endswith((".md", ".txt", ".dockerignore")):
        return True
    if low == ".gitlab-ci.yml":
        return True
    if len(parts) >= 2 and parts[-2] == ".circleci" and low in ("config.yml", "config.yaml"):
        return True
    return (len(parts) >= 3 and parts[-3] == ".github" and parts[-2] == "workflows"
            and low.endswith((".yml", ".yaml")))


def is_k8s_workload(text: str) -> bool:
    """YAML declaring a Deployment, StatefulSet or DaemonSet (`kind:` line)."""
    return bool(_K8S_WORKLOAD_RE.search(text))


def is_test_filename(rel: str) -> bool:
    """Test by file name only (test_x.py, x_test.go, x.test.ts, x.spec.js), ignoring folders."""
    return bool(_TEST_NAME.search(rel.replace("\\", "/").rsplit("/", 1)[-1]))


def is_test_file(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if _TEST_NAME.search(parts[-1]):
        return True
    return any(p in _TEST_DIRS for p in parts[:-1])


# Reverse-DNS package roots: `com/example/shop` is a Java/Kotlin package, not a demo folder.
_PACKAGE_ROOTS = {"com", "org", "net", "io", "dev", "edu", "gov"}


def is_example_file(rel: str) -> bool:
    """True when a folder in `rel` (not the file name) is an examples/demo folder.

    A segment right after a reverse-DNS package root (com/example/...) is a package name.
    """
    parts = rel.replace("\\", "/").split("/")[:-1]
    return any(p in EXAMPLE_DIRS and not (i and parts[i - 1] in _PACKAGE_ROOTS)
               for i, p in enumerate(parts))


def normalize_scope(entry: str) -> str:
    s = entry.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    s = s.strip("/")
    return "" if s == "." else s


SECRET_FILE_PATTERNS = ("*.pem", "*.key", "id_rsa*", "*.p12")
_SECRET_RE = _globs(SECRET_FILE_PATTERNS)
MINIFIED_AVG_LINE = 1000
GENERATED_MARKERS = ("@generated", "DO NOT EDIT")
_CHECK_EVERY = 64
_PROGRESS_EVERY = 500


def is_secret_file(name: str) -> bool:
    """Private-key style files, read only by the hardcoded-secret rule (public .pub excluded)."""
    low = name.lower()
    return not low.endswith(".pub") and bool(_SECRET_RE.match(low))


@dataclass
class WalkResult:
    files: List[FileInfo] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)  # {"path", "reason"}
    partial: bool = False
    invalid_gitignore: List[str] = field(default_factory=list)  # root .gitignore lines git ignores
    excluded: List[str] = field(default_factory=list)  # dropped by config exclude/include globs


def _inside(root_real: str, path: str) -> bool:
    try:
        real = os.path.realpath(path)
        return os.path.commonpath([root_real, real]) == root_real
    except ValueError:  # different drive
        return False


def _is_link_dir(full_d: str, parent_real: str, name: str) -> bool:
    """True for a symlink, an NTFS junction or any directory whose real path is not
    <real parent>/<name> (os.path.islink is False for junctions, and os.walk descends into them)."""
    if os.path.islink(full_d):
        return True
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    if isjunction is not None and isjunction(full_d):
        return True
    real = os.path.realpath(full_d)
    return os.path.normcase(real) != os.path.normcase(os.path.join(parent_real, name))


def walk_result(root: str, scope: Optional[List[str]] = None, respect_gitignore: bool = True,
                max_files: int = 50000, deadline: Optional[float] = None,
                progress: Optional[Callable[[str], None]] = None,
                max_file_bytes: int = MAX_FILE_BYTES, exclude: Optional[List[str]] = None,
                include: Optional[List[str]] = None) -> WalkResult:
    """Full-depth walk. Never follows directory symlinks or NTFS junctions (so no loops); those
    pointing outside the root are reported as symlink_outside.

    Unreadable, oversized, binary, minified and generated files are not listed; each is recorded in
    `skipped` with a reason (too_large, binary, minified, generated, permission, symlink_outside,
    max_files). The root `.gitignore` is applied unless `respect_gitignore` is false. The walk stops
    with `partial` set when `max_files` files are listed or `deadline` (time.perf_counter) passes.
    Files above `max_file_bytes` are skipped as too_large. `exclude` globs (gitignore syntax) drop
    matching files and folders (recorded in `excluded`, a folder once, not in `skipped`); when `include`
    globs are given only matching files are listed (the others are recorded in `excluded` too).
    """
    root = os.path.abspath(root)
    root_real = os.path.realpath(root)
    gi = GitIgnore.from_root(root) if respect_gitignore else None
    ex = GitIgnore(exclude) if exclude else None
    inc = GitIgnore(include) if include else None
    scopes = [normalize_scope(s) for s in (scope or [])]
    scopes = [s for s in scopes if s]
    start_dirs = [os.path.join(root, *s.split("/")) for s in scopes] or [root]
    res = WalkResult()
    if gi is not None:
        res.invalid_gitignore = list(gi.invalid)
    seen = set()

    def skip(rel: str, reason: str) -> None:
        res.skipped.append({"path": rel, "reason": reason})

    prefix = root if root.endswith(os.sep) else root + os.sep

    def rel_of(full: str) -> str:
        # Walked paths are root + os.sep + names: slice them (relpath is slow). Anything else,
        # or a path with an empty, "." or ".." part, goes through relpath.
        if full.startswith(prefix):
            rest = full[len(prefix):]
            parts = rest.split(os.sep)
            if rest and all(p not in ("", ".", "..") for p in parts):
                return "/".join(parts)
        return os.path.relpath(full, root).replace(os.sep, "/")

    def on_error(err: OSError) -> None:
        if isinstance(err, PermissionError):
            skip(rel_of(err.filename) if err.filename else ".", "permission")

    processed = 0
    for start in start_dirs:
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False, onerror=on_error):
            keep = []
            parent_real = os.path.realpath(dirpath) if dirnames else ""
            for d in sorted(dirnames):
                if d in IGNORE_DIRS or (d.startswith(".") and d not in INFRA_DOT_DIRS):
                    continue
                full_d = os.path.join(dirpath, d)
                rel_d = rel_of(full_d)
                if _is_link_dir(full_d, parent_real, d):
                    # Links and junctions are never followed (no loops); outside ones are reported.
                    if not _inside(root_real, full_d):
                        skip(rel_d, "symlink_outside")
                    continue
                if gi is not None and gi.matches(rel_d, True):
                    continue
                if ex is not None and ex.matches(rel_d, True):
                    res.excluded.append(rel_d)  # a folder counts once
                    continue
                keep.append(d)
            dirnames[:] = keep
            for fname in sorted(filenames):
                full = os.path.join(dirpath, fname)
                if full in seen:
                    continue
                seen.add(full)
                processed += 1
                if processed % _CHECK_EVERY == 0:
                    if deadline is not None and time.perf_counter() > deadline:
                        res.partial = True
                        return res
                if progress is not None and processed % _PROGRESS_EVERY == 0:
                    progress("walking: %d files" % processed)
                ext = os.path.splitext(fname)[1].lower()
                lang = CODE_EXTS.get(ext)
                manifest = is_manifest(fname)
                secret = is_secret_file(fname)
                config = lang is None and not secret and is_config(fname)
                low = fname.lower()
                junk = low.endswith(".map") or low.endswith(".min.js")
                rel = rel_of(full)
                infra = lang is None and not secret and is_infra_name(rel)
                if (lang is None and not manifest and not config and not secret and not junk
                        and not infra):
                    continue
                if not infra and any(p in INFRA_DOT_DIRS for p in rel.split("/")[:-1]):
                    continue  # dot folders are walked for their infra files only
                if gi is not None and gi.matches(rel, False):
                    continue
                if ex is not None and ex.matches(rel, False):
                    res.excluded.append(rel)
                    continue
                if inc is not None and not inc.matches(rel, False):
                    res.excluded.append(rel)
                    continue
                if os.path.islink(full) and not _inside(root_real, full):
                    skip(rel, "symlink_outside")
                    continue
                if junk:
                    skip(rel, "minified")
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if (config or secret or infra) and size > max_file_bytes:
                    if not manifest:
                        skip(rel, "too_large")
                        continue
                    config = False
                    infra = False
                loc = 0
                if lang is not None:
                    if size > max_file_bytes:
                        skip(rel, "too_large")
                        continue
                    try:
                        with open(full, "rb") as fh:
                            data = fh.read()
                    except PermissionError:
                        skip(rel, "permission")
                        continue
                    except OSError:
                        continue
                    if b"\x00" in data[:8192]:
                        skip(rel, "binary")
                        continue
                    text = data.decode("utf-8", errors="ignore")
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                    loc = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
                    if loc and len(text) / loc > MINIFIED_AVG_LINE:
                        skip(rel, "minified")
                        continue
                    head = text.split("\n", 5)[:5]
                    if any(m in line for line in head for m in GENERATED_MARKERS):
                        skip(rel, "generated")
                        continue
                elif secret or config or infra:
                    yaml = ext in (".yml", ".yaml") and not infra
                    try:
                        with open(full, "rb") as fh:
                            head_bytes = fh.read() if yaml else fh.read(1)
                    except PermissionError:
                        skip(rel, "permission")
                        continue
                    except OSError:
                        continue
                    if yaml and is_k8s_workload(head_bytes.decode("utf-8", errors="ignore")):
                        infra = True
                if len(res.files) >= max_files:
                    skip(rel, "max_files")
                    res.partial = True
                    return res
                res.files.append(FileInfo(rel, full, ext, lang, loc, size, is_test_file(rel), config,
                                          is_example_file(rel), secret, infra))
    return res


def walk(root: str, scope: Optional[List[str]] = None, **kwargs) -> List[FileInfo]:
    """Compatibility wrapper: the listed files of `walk_result`."""
    return walk_result(root, scope, **kwargs).files


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
