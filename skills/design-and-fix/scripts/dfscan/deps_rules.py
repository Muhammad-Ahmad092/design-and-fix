from __future__ import annotations

import json
import os
import posixpath
import re
from typing import Dict, List, Optional, Tuple

from .filecache import FileCache
from .imports import _read
from .json_utils import check_json_safety
from .model import FileInfo, Signal
from .registry import RuleMeta, register

# Dependency hygiene (spec 9.4). Offline only: manifests are read, nothing is looked up.
# Manifests in test and example folders are skipped. Lockfiles are looked up on disk (the walk
# does not list them), in the manifest folder and its ancestors up to the scan root.

NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
             "bun.lockb", "bun.lock")
LOCKS = {
    "package.json": NPM_LOCKS,
    "Pipfile": ("Pipfile.lock",),
    "Gemfile": ("Gemfile.lock",),
    "composer.json": ("composer.lock",),
    "go.mod": ("go.sum",),
}


def _root_of(fi: FileInfo) -> str:
    depth = fi.rel.count("/") + 1
    root = fi.abs
    for _ in range(depth):
        root = os.path.dirname(root)
    return root


def _has_lock(fi: FileInfo, names: Tuple[str, ...], climb: bool) -> bool:
    """A lockfile next to the manifest or (climb=True: workspaces) in an ancestor below the root."""
    root = os.path.normcase(os.path.abspath(_root_of(fi)))
    cur = os.path.dirname(fi.abs)
    while True:
        if any(os.path.exists(os.path.join(cur, n)) for n in names):
            return True
        if not climb or os.path.normcase(os.path.abspath(cur)) == root:
            return False
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


class MalformedManifest(ValueError):
    """A JSON manifest that does not parse (bad syntax, or nesting too deep for the decoder)."""


def _json(text: str) -> Optional[dict]:
    """The manifest's top-level object, None when it is not an object; MalformedManifest when the
    JSON does not parse."""
    try:
        check_json_safety(text)
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise MalformedManifest(type(exc).__name__) from None
    return data if isinstance(data, dict) else None


def rule_missing_lockfile(fi: FileInfo, text: str) -> List[Signal]:
    """Manifest with dependencies and no lockfile: package.json (npm/yarn/pnpm/bun lock, also in a
    workspace root above), Pipfile, Gemfile, composer.json, go.mod with requires.

    False-positive risks: published libraries often leave the lockfile out on purpose (a
    package.json with main/exports and no "private": true emits low confidence; a Gemfile next to a
    *.gemspec and a composer.json of "type": "library" are skipped). A lockfile that exists on
    disk but is git-ignored counts as present. requirements.txt and pyproject.toml are not
    checked (Python lockfile tools vary).
    """
    name = posixpath.basename(fi.rel)
    conf = "medium"
    if name == "package.json":
        data = _json(text)
        if data is None:
            return []
        deps = [k for k in ("dependencies", "devDependencies", "optionalDependencies")
                if isinstance(data.get(k), dict) and data[k]]
        if not deps:
            return []
        if not data.get("private") and any(k in data for k in ("main", "exports", "module")):
            conf = "low"
    elif name == "Gemfile":
        folder = os.path.dirname(fi.abs)
        try:
            if any(n.endswith(".gemspec") for n in os.listdir(folder)):
                return []
        except OSError:
            return []
        if not re.search(r"(?m)^[ \t]*(?:gem|gemspec)\b", text):
            return []
    elif name == "composer.json":
        data = _json(text)
        if data is None or data.get("type") == "library":
            return []
        if not any(isinstance(data.get(k), dict) and data[k] for k in ("require", "require-dev")):
            return []
    elif name == "Pipfile":
        if not re.search(r"(?ms)^\[(?:dev-)?packages\]\s*\n\s*[A-Za-z0-9\"']", text):
            return []
    elif name == "go.mod":
        if not re.search(r"(?m)^require\b", text):
            return []
    else:
        return []
    if _has_lock(fi, LOCKS[name], climb=name in ("package.json", "go.mod")):
        return []
    return [Signal("missing-lockfile", "Maintainability", "Medium", fi.rel, 1,
                   "%s without %s" % (name, " / ".join(LOCKS[name][:3])), conf)]


# ---------------------------------------------------------------- unpinned-dependency
_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")
_BOUNDED_RE = re.compile(r"==|~=|<|===")
_NPM_SKIP = ("workspace:", "file:", "link:", "portal:", "patch:", "catalog:", "git", "http", "github:")


def _unpinned_spec(spec: str) -> bool:
    """True for a PEP 440 specifier with no upper bound (bare name, >=, >, !=)."""
    spec = spec.strip()
    return not spec or not _BOUNDED_RE.search(spec)


def _requirements(fi: FileInfo, text: str) -> List[Signal]:
    out = []
    for i, raw in enumerate(text.split("\n")):
        ln = raw.split("#", 1)[0] if raw.lstrip().startswith("#") else re.sub(r"\s#.*$", "", raw)
        ln = re.sub(r"\s--?\w[\w-]*(?:[= ]\S+)?", "", ln.split(";", 1)[0]).strip()
        if not ln or ln.startswith(("-", ".", "/")) or "://" in ln or " @ " in ln or "@" in ln:
            continue
        m = _REQ_NAME_RE.match(ln)
        if m and _unpinned_spec(m.group(3)):
            out.append(Signal("unpinned-dependency", "Maintainability", "Low", fi.rel, i + 1,
                              raw.strip(), "medium"))
    return out


def _npm_unpinned(value: str) -> Optional[str]:
    """Category for an unsafe npm range, or None. `*`/latest/empty -> Security; >= without < ->
    Maintainability; ^ and ~ are fine."""
    v = value.strip()
    if v.startswith("npm:"):
        # npm:pkg@range or npm:@scope/pkg@range; no version after the name means latest.
        rest = v[4:]
        at = rest.rfind("@")
        v = rest[at + 1:] if at > 0 else ""
    if v.startswith(_NPM_SKIP):
        return None
    if v in ("", "*", "latest", "x", "X") or v.lower() in ("next",):
        return "Security"
    if re.match(r"^>=?\s*\d", v) and "<" not in v and "||" not in v:
        return "Maintainability"
    return None


_PAIR_RE = re.compile(r"\"((?:[^\"\\\n]|\\.)*)\"\s*:\s*\"((?:[^\"\\\n]|\\.)*)\"")


def _pair_lines(text: str, start: int) -> Dict[Tuple[str, str], int]:
    """First line of each `"key": "value"` string pair at or after `start` (one forward pass)."""
    out: Dict[Tuple[str, str], int] = {}
    line = text.count("\n", 0, start) + 1
    pos = start
    for m in _PAIR_RE.finditer(text, start):
        line += text.count("\n", pos, m.start())
        pos = m.start()
        try:
            key = (json.loads('"%s"' % m.group(1)), json.loads('"%s"' % m.group(2)))
        except ValueError:
            continue
        out.setdefault(key, line)
    return out


def _package_json(fi: FileInfo, text: str) -> List[Signal]:
    data = _json(text)
    if data is None:
        return []
    out = []
    everywhere: Optional[Dict[Tuple[str, str], int]] = None
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        sm = re.search(r"\"%s\"\s*:" % section, text)
        after: Optional[Dict[Tuple[str, str], int]] = None
        for name, value in deps.items():
            if not isinstance(value, str):
                continue
            cat = _npm_unpinned(value)
            if cat is None:
                continue
            if after is None:
                after = _pair_lines(text, sm.start() if sm else 0)
            line = after.get((name, value))
            if line is None:
                if everywhere is None:
                    everywhere = _pair_lines(text, 0)
                line = everywhere.get((name, value), 1)
            out.append(Signal("unpinned-dependency", cat, "Low", fi.rel, line,
                              '"%s": "%s"' % (name, value),
                              "high" if cat == "Security" else "medium"))
    return out


_TOML_TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _pyproject(fi: FileInfo, text: str) -> List[Signal]:
    out = []
    table = ""
    in_deps = False
    # A distributable package ([build-system]) declares lower bounds on purpose: its [project]
    # dependencies are not checked.
    library = re.search(r"(?m)^[ \t]*\[build-system\]", text) is not None
    for i, raw in enumerate(text.split("\n")):
        ln = re.sub(r"\s#.*$", "", raw).strip()
        if ln.startswith("#"):
            continue
        m = _TOML_TABLE_RE.match(ln)
        if m:
            table, in_deps = m.group(1).strip(), False
            continue
        if table == "project" and not library and re.match(r"dependencies\s*=\s*\[", ln):
            in_deps = True
            ln = ln.split("[", 1)[1]
        if in_deps:
            for q in re.findall(r"\"([^\"]+)\"|'([^']+)'", ln):
                req = (q[0] or q[1]).split(";", 1)[0].strip()
                rm = _REQ_NAME_RE.match(req)
                if rm and "@" not in req and rm.group(3).strip() and _unpinned_spec(rm.group(3)):
                    out.append(Signal("unpinned-dependency", "Maintainability", "Low", fi.rel,
                                      i + 1, raw.strip(), "low"))
            # The list ends at a `]` outside quotes ("uvicorn[standard]" does not end it).
            if "]" in re.sub(r"\"[^\"]*\"|'[^']*'", "", ln):
                in_deps = False
            continue
        if table in ("tool.poetry.dependencies", "tool.poetry.dev-dependencies") or (
                table.startswith("tool.poetry.group.") and table.endswith(".dependencies")):
            pm = re.match(r"([A-Za-z0-9._-]+)\s*=\s*"
                          r"(?:\"([^\"]*)\"|\{.*?version\s*=\s*\"([^\"]*)\")", ln)
            if not pm or pm.group(1).lower() == "python":
                continue
            value = (pm.group(2) if pm.group(2) is not None else pm.group(3) or "").strip()
            if value in ("*", "", "latest"):
                out.append(Signal("unpinned-dependency", "Security", "Low", fi.rel, i + 1,
                                  raw.strip(), "medium"))
            elif _unpinned_spec(value) and not value.startswith(("^", "~")) and value[:1] in "><!":
                out.append(Signal("unpinned-dependency", "Maintainability", "Low", fi.rel, i + 1,
                                  raw.strip(), "low"))
    return out


_DOCS_DIRS = {"docs", "doc"}
_PY_LIBRARY_TABLE_RE = re.compile(r"(?m)^[ \t]*\[(?:project|build-system)\][ \t]*\r?$")
_SETUP_CFG_METADATA_RE = re.compile(r"(?m)^[ \t]*\[metadata\][ \t]*\r?$")


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(200000)
    except OSError:
        return ""


def _python_library_root(folder: str) -> bool:
    """A distributable Python package is declared in this folder: setup.py, setup.cfg with
    [metadata], or pyproject.toml with [project] or [build-system]."""
    if os.path.isfile(os.path.join(folder, "setup.py")):
        return True
    if _SETUP_CFG_METADATA_RE.search(_read_file(os.path.join(folder, "setup.cfg"))):
        return True
    return _PY_LIBRARY_TABLE_RE.search(_read_file(os.path.join(folder, "pyproject.toml"))) is not None


def _library_docs_requirements(fi: FileInfo) -> bool:
    """requirements.txt under a docs/ or doc/ folder of a Python library (a library marker in an
    ancestor of the docs folder, up to the scan root): documentation-build tooling, not deployed."""
    parts = fi.rel.split("/")[:-1]
    if not any(p.lower() in _DOCS_DIRS for p in parts):
        return False
    root = os.path.normcase(os.path.abspath(_root_of(fi)))
    cur = os.path.dirname(os.path.dirname(fi.abs))
    for _ in range(len(parts)):
        if _python_library_root(cur):
            return True
        if os.path.normcase(os.path.abspath(cur)) == root:
            break
        cur = os.path.dirname(cur)
    return False


def rule_unpinned_dependency(fi: FileInfo, text: str) -> List[Signal]:
    """Dependency with no upper bound: requirements.txt bare names / >= / > ranges, package.json
    `*` / latest / empty / >= without <, pyproject [project] dependencies with >= only, Poetry `*`.
    `*` / latest are reported in category Security; ^ and ~ ranges are fine.

    False-positive risks: libraries deliberately declare lower bounds only (pyproject [project]
    dependencies are skipped when the file has a [build-system] table, otherwise reported; bare
    names there are not flagged); a constraints
    file or lockfile next to requirements.txt that pins versions is not read. peerDependencies are
    skipped. Requirements included with -r / -c and other requirement file names are not read.
    A requirements.txt under docs/ or doc/ in a Python library (setup.py, setup.cfg [metadata] or
    pyproject [project]/[build-system] above the docs folder) is documentation tooling and is
    skipped; docs requirements of an application are still read.
    """
    name = posixpath.basename(fi.rel)
    if name == "requirements.txt":
        return [] if _library_docs_requirements(fi) else _requirements(fi, text)
    if name == "package.json":
        return _package_json(fi, text)
    if name == "pyproject.toml":
        return _pyproject(fi, text)
    return []


MANIFESTS = set(LOCKS) | {"requirements.txt", "pyproject.toml"}


def run_deps_rules(files: List[FileInfo], notes: Optional[List[str]] = None,
                   cache: Optional[FileCache] = None) -> List[Signal]:
    """missing-lockfile and unpinned-dependency over manifests outside test/example folders.

    A JSON manifest that does not parse (package.json, composer.json) is skipped, with a note
    appended to `notes` when given."""
    out: List[Signal] = []
    for fi in files:
        if fi.is_test or fi.is_example or posixpath.basename(fi.rel) not in MANIFESTS:
            continue
        text = (cache.text(fi) if cache is not None else _read(fi)).replace("\r\n", "\n")
        try:
            sigs = rule_missing_lockfile(fi, text) + rule_unpinned_dependency(fi, text)
        except MalformedManifest as exc:
            if notes is not None:
                notes.append("deps rules skipped %s: malformed JSON (%s)" % (fi.rel, exc))
            continue
        out += sigs
    return out


_DEPS_LANGS = ["Node.js", "Python", "Ruby", "PHP", "Go"]
for _m in [
    RuleMeta("missing-lockfile", "2", "Maintainability", "Medium", "medium",
             "Manifest with dependencies but no lockfile (package.json, Pipfile, Gemfile, "
             "composer.json, go.mod).",
             "Lockfiles are looked up on disk next to the manifest (package.json and go.mod also "
             "in ancestor folders up to the scan root, for workspaces), so a git-ignored lockfile "
             "counts as present. Publishable npm packages (main/exports, not private) emit low "
             "confidence; gems with a gemspec and composer libraries are skipped. requirements.txt, "
             "pyproject.toml and Cargo.toml are not checked. A package.json or composer.json that is "
             "not valid JSON is skipped with a note.",
             "Commit the lockfile and install with the frozen/ci command in CI.", _DEPS_LANGS),
    RuleMeta("unpinned-dependency", "4", "Maintainability", "Low", "low",
             "Dependency without an upper bound: requirements.txt bare names or >= ranges, "
             "package.json `*`/latest/>= ranges, pyproject >= ranges and Poetry `*` "
             "(`*`/latest are reported as Security).",
             "^ and ~ ranges are treated as fine. pyproject [project] dependencies are skipped "
             "when the file has [build-system] (a distributable package uses lower bounds on "
             "purpose), otherwise reported; bare names there are not flagged. "
             "Constraints files, lockfiles that pin versions anyway, -r includes, other "
             "requirement file names, Pipfile, Gemfile, Cargo.toml and peerDependencies are not "
             "read. A requirements.txt under docs/ or doc/ of a Python library (setup.py, setup.cfg "
             "[metadata] or pyproject [project]/[build-system] above the docs folder) is skipped as "
             "documentation tooling; docs requirements of an application are still reported, but a "
             "Poetry or other packaged application that declares [build-system] or [project] is "
             "treated as a library, so its docs requirements are skipped too. An npm "
             "alias without a version (npm:pkg) counts as latest. A package.json that is not valid "
             "JSON is skipped with a note. No vulnerability lookup. Always low confidence (the rule "
             "ceiling; measured precision cannot raise it).",
             "Pin exact versions (or bounded ranges) and rely on a lockfile for transitive pins.",
             ["Node.js", "Python"]),
]:
    register(_m)
