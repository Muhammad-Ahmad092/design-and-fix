from __future__ import annotations

import bisect
import posixpath
import re
from typing import Dict, List, Optional, Set, Tuple

from .filecache import FileCache, code_of, read_file, text_of
from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .text import strip_comments
from .walker import is_test_filename

GOD_FAN_IN = 10
GOD_LOC = 500
GOD_COHESIVE_FAN_OUT = 12  # fewer outgoing imports than this: likely a cohesive collection
GOD_CLUSTERS = 3  # distinct import clusters (responsibility areas the module reaches into)
GOD_MIN_DEFS = 15  # top-level defs needed before their kinds say anything
GOD_SAME_KIND_SHARE = 0.8  # classes/types at least this share of the defs: a class collection
GOD_MIXED_KIND_SHARE = 0.8  # mixed kinds: the most common def kind holds less than this share
GOD_SAME_FAMILY_SHARE = 0.5  # one name family (Validator / validate_) at least this share
MAX_CYCLES = 50

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx")
_JS_LANG_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_JS_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"](\.[^'"\n]*)['"]"""
)
_PY_FROM_RE = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(.+)$", re.M)
_PY_FROM_PAREN_RE = re.compile(
    r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]*\(([^)]*)\)", re.M)
_PY_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([\w., \t]+)", re.M)
_PY_HEADER_RE = re.compile(r"^[ \t]*(?:(?:async[ \t]+)?def\b|if[ \t]+(?:\w+\.)?TYPE_CHECKING\b)")
_JS_TYPE_ONLY_RE = re.compile(r"^(?:import|export)\s+type\b")
_JS_SPECS_RE = re.compile(r"\{([^}]*)\}")
_INIT_RE = re.compile(r"(?:^|/)(?:__init__\.py|index\.(?:ts|tsx|js|jsx|mjs|cjs))$")
_GO_BLOCK_RE = re.compile(r"\bimport\s*\(([^)]*)\)")
_GO_SINGLE_RE = re.compile(r'^[ \t]*import[ \t]+(?:[\w.]+[ \t]+)?"([^"]+)"', re.M)
_GO_STR_RE = re.compile(r'"([^"]+)"')


def _read(f: FileInfo) -> str:
    """The file's text without a leading BOM (filecache.read_file, kept for existing callers)."""
    return read_file(f)


def _js_resolve(src_rel: str, spec: str, known: Set[str]) -> Optional[str]:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(src_rel), spec))
    cands = [base]
    if base.endswith((".js", ".jsx", ".mjs", ".cjs")):
        stem = base.rsplit(".", 1)[0]
        cands += [stem + e for e in _JS_EXTS]
    cands += [base + e for e in _JS_EXTS]
    cands += [base + "/index" + e for e in _JS_EXTS]
    for c in cands:
        if c in known:
            return c
    return None


def _py_roots(src_rel: str, scopes: List[str], known: Set[str]) -> List[str]:
    """Candidate source roots for absolute imports, matching Python's resolution: ancestor folders
    of the importing file that are NOT packages (no __init__.py; nearest first), the scan root "",
    then src/ and each scope root. A package folder is reachable only through its parent root
    (`import pkg.sub.json`) or relative imports, so a sibling `json.py` never shadows the stdlib."""
    roots: List[str] = []
    cur = posixpath.dirname(src_rel)
    while cur:
        if posixpath.join(cur, "__init__.py") not in known:
            roots.append(cur)
        cur = posixpath.dirname(cur)
    roots.append("")
    for extra in ["src"] + list(scopes):
        if extra not in roots:
            roots.append(extra)
    return roots


_PY_RESYNC_RE = re.compile(r"(?:def |class |async def |@|import |from )")
_PY_SCAN_RE = re.compile(r"[#\"'()\[\]{}]")  # characters _py_scan_line acts on
# Any line that could be an indented import (a cheap superset test before the line pass).
# [ \t] (not \s) so the pattern cannot run across a blank-line run: that backtracks quadratically.
_PY_INDENTED_IMPORT_RE = re.compile(r"(?m)^[ \t]+(?:import |from )")


def _py_scan_line(ln: str, depth: int, quote: str) -> Tuple[int, str]:
    """Advance (open-bracket depth, open triple-quote or "") over one line, ignoring brackets in
    strings and # comments. A single-quoted string never spans lines (no backslash joins)."""
    i, n = 0, len(ln)
    search = _PY_SCAN_RE.search
    while i < n:
        if quote:
            j = ln.find(quote, i)
            if j < 0:
                return depth, quote
            i, quote = j + 3, ""
            continue
        m = search(ln, i)
        if m is None:
            break
        i = m.start()
        c = ln[i]
        if c == "#":
            break
        if c in "\"'":
            if ln.startswith(c * 3, i):
                quote, i = c * 3, i + 3
                continue
            i += 1
            while i < n and ln[i] != c:
                i += 2 if ln[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}" and depth:
            depth -= 1
        i += 1
    return depth, quote


def _py_deferred_lines(text: str) -> Set[int]:
    """1-based numbers of indented import lines that sit inside a def (deferred, run at call
    time) or under `if TYPE_CHECKING:` (never run). Module-level try/if/with imports are kept.

    One forward pass with a stack of enclosing (less-indented) lines, so a file with thousands of
    indented imports stays linear; each entry records whether it or an enclosing line is a
    def / TYPE_CHECKING header. Lines inside an open ( [ { or triple-quoted string continue the
    previous logical line (a multi-line def signature's `) -> T:` line neither pops nor pushes)."""
    out: Set[int] = set()
    if not _PY_INDENTED_IMPORT_RE.search(text):
        return out
    stack: List[Tuple[int, bool]] = []  # (indent, this line or an enclosing one is a header)
    depth, quote = 0, ""
    for idx, ln in enumerate(text.split("\n")):
        if depth or quote:
            if not _PY_RESYNC_RE.match(ln):
                depth, quote = _py_scan_line(ln, depth, quote)
                continue
            depth, quote = 0, ""  # unbalanced/unterminated: resync at a column-0 statement
        body = ln.lstrip()
        if not body:
            continue
        depth, quote = _py_scan_line(ln, 0, "")
        indent = len(ln) - len(body)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if indent and stack and stack[-1][1] and (body.startswith("import ")
                                                  or body.startswith("from ")):
            out.add(idx + 1)
        header = bool(_PY_HEADER_RE.match(ln))
        stack.append((indent, header or (bool(stack) and stack[-1][1])))
    return out


def _py_targets(f: FileInfo, text: str, known: Set[str],
                scopes: Optional[List[str]] = None) -> List[Tuple[str, bool]]:
    """(target, deferred) pairs; deferred = function-local or TYPE_CHECKING import."""
    src_dir = posixpath.dirname(f.rel)
    deferred_lines = _py_deferred_lines(text)

    starts = [0] + [m.end() for m in re.finditer("\n", text)] if deferred_lines else [0]

    def is_deferred(pos: int) -> bool:
        return bool(deferred_lines) and bisect.bisect_right(starts, pos) in deferred_lines

    def first(mod_path: str) -> Optional[str]:
        for c in (mod_path + ".py", mod_path + "/__init__.py"):
            if c in known:
                return c
        return None

    def resolve(roots: List[str], mod: str, names: List[str]) -> List[str]:
        # The module itself, plus each imported name that is a submodule
        # ("from pkg import sub" -> pkg/sub.py or pkg/sub/__init__.py). First root that hits wins.
        for root in roots:
            base = posixpath.join(root, *mod.split(".")) if mod else root
            hits = [first(base)] if mod else []
            hits += [first(posixpath.join(base, n) if base else n) for n in names]
            hits = [h for h in hits if h]
            if hits:
                return hits
        return []

    froms = [(m.group(1), m.group(2), m.group(3), m.start())
             for m in _PY_FROM_PAREN_RE.finditer(text)]
    froms += [(m.group(1), m.group(2), m.group(3), m.start()) for m in _PY_FROM_RE.finditer(text)
              if not m.group(3).lstrip().startswith("(")]
    abs_roots = _py_roots(f.rel, scopes or [], known)
    out: List[Tuple[str, bool]] = []
    for dots, mod, names_text, pos in froms:
        hits: List[str] = []
        names = [n.split()[0] for n in names_text.split(",") if n.strip() and n.split()[0] != "*"]
        names = [n for n in names if re.match(r"^\w+$", n)]
        if dots:
            base = src_dir
            for _ in range(len(dots) - 1):
                base = posixpath.dirname(base)
            hits = resolve([base], mod, names)
        elif mod:
            hits = resolve(abs_roots, mod, names)
        out += [(h, is_deferred(pos)) for h in hits]
    for m in _PY_IMPORT_RE.finditer(text):
        for part in m.group(1).split(","):
            name = part.strip().split(" ")[0]
            if name:
                out += [(h, is_deferred(m.start())) for h in resolve(abs_roots, name, [])]
    return out


def _js_deferred(text: str, m: "re.Match[str]") -> bool:
    """True for imports that create no module-load edge: dynamic import(), `import type`,
    `export type ... from`, and `import { type A, type B } from` (every specifier type-only)."""
    head = text[m.start():m.start(1)]
    if head.startswith("import") and "(" in head:
        return True  # dynamic import('./x') runs at call time
    if not head.startswith("from"):
        return False
    start = max(text.rfind("import", 0, m.start()), text.rfind("export", 0, m.start()))
    if start < 0:
        return False
    stmt = text[start:m.start()]
    if _JS_TYPE_ONLY_RE.match(stmt):
        return True
    specs = _JS_SPECS_RE.search(stmt)
    if specs and not re.sub(r"^\s*(?:import|export)\s*", "", stmt[:specs.start()]).strip():
        names = [n.strip() for n in specs.group(1).split(",") if n.strip()]
        return bool(names) and all(n.startswith("type ") for n in names)
    return False


def _go_imports(text: str) -> List[str]:
    paths: List[str] = []
    for block in _GO_BLOCK_RE.findall(text):
        paths += _GO_STR_RE.findall(block)
    paths += _GO_SINGLE_RE.findall(text)
    return paths


def _find_cycle(start: str, scc: Set[str], adj: Dict[str, Set[str]]) -> List[str]:
    """Shortest cycle through `start` inside the SCC, as [start, ..., start]."""
    prev: Dict[str, str] = {}
    queue = [start]
    seen = {start}
    qi = 0
    while qi < len(queue):
        cur = queue[qi]
        qi += 1
        for nxt in sorted(adj[cur]):
            if nxt not in scc:
                continue
            if nxt == start:
                path = [cur]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                path.reverse()
                return path + [start]
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = cur
                queue.append(nxt)
    return [start, start]  # unreachable for a real SCC


def _sccs(nodes: List[str], adj: Dict[str, Set[str]]) -> List[List[str]]:
    """Iterative Tarjan."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    result: List[List[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(adj[root])))]
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(adj[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                result.append(comp)
    return result


def build_graph(files: List[FileInfo], scopes: Optional[List[str]] = None,
                cache: Optional[FileCache] = None) -> dict:
    """Import graph of JS/TS, Python and Go files; imports are read from comment-stripped text.
    `scopes` are extra source roots for absolute Python imports; `cache` is the scan's file cache
    (files are read directly without one)."""
    code = [f for f in files if f.lang is not None]
    known = {f.rel for f in code}
    by_rel = {f.rel: f for f in code}
    go_dirs: Dict[str, List[str]] = {}
    for f in code:
        if f.ext == ".go":
            go_dirs.setdefault(posixpath.dirname(f.rel), []).append(f.rel)
    go_folders = sorted((d for d in go_dirs if d), key=lambda d: -len(d))

    adj: Dict[str, Set[str]] = {f.rel: set() for f in code}
    runtime: Dict[str, Set[str]] = {f.rel: set() for f in code}  # module-load edges (cycles)
    for f in code:
        if f.ext not in _JS_LANG_EXTS and f.ext not in (".py", ".go"):
            continue
        raw = text_of(f, cache)
        if not raw:
            continue
        text = cache.code(f) if cache is not None else strip_comments(raw, f.lang or "")
        targets: List[Tuple[str, bool]] = []
        if f.ext in _JS_LANG_EXTS:
            for m in _JS_RE.finditer(text):
                t = _js_resolve(f.rel, m.group(1), known)
                if t:
                    targets.append((t, _js_deferred(text, m)))
        elif f.ext == ".py":
            targets = _py_targets(f, text, known, scopes)
        else:
            for imp in _go_imports(text):
                for folder in go_folders:
                    if imp == folder or imp.endswith("/" + folder):
                        targets += [(t, False) for t in go_dirs[folder]]
                        break
        for t, deferred in targets:
            if t != f.rel:
                adj[f.rel].add(t)
                if not deferred:
                    runtime[f.rel].add(t)

    fan_in: Dict[str, int] = {r: 0 for r in adj}
    fan_in_prod: Dict[str, int] = {r: 0 for r in adj}  # importers that are not test files
    for src, dsts in adj.items():
        for d in dsts:
            fan_in[d] += 1
            if not by_rel[src].is_test:
                fan_in_prod[d] += 1

    nodes = sorted(adj)
    comps = sorted(sorted(c) for c in _sccs(nodes, runtime) if len(c) >= 2)
    found = [_component_cycle(c, runtime) for c in comps][:MAX_CYCLES]

    # God-module candidates: total fan-in and size trigger; test files (by file name, so library
    # folders named test/ still count) and test-support modules (tests/, __tests__/, spec/,
    # fixtures/ folders) are never candidates. A candidate needs a structural indicator too
    # (>= 3 import clusters or >= 15 top-level defs of mixed kinds) and must not be a cohesive
    # same-kind collection. Low non-test fan-in only lowers the emitted confidence, which the
    # registry ceiling (low) already caps: every god-module signal is low.
    gods = []
    for r in nodes:
        if is_test_filename(r) or fan_in[r] < GOD_FAN_IN or by_rel[r].loc < GOD_LOC \
                or any(p in _TEST_SUPPORT_DIRS for p in r.split("/")[:-1]):
            continue
        clusters = _import_clusters(r, adj[r])
        defs = _top_level_defs(code_of(by_rel[r], cache), by_rel[r].ext)
        same_kind = _same_kind(defs)
        mixed = not same_kind and _mixed_kinds(defs)
        if (len(defs) >= GOD_MIN_DEFS and same_kind) or not (len(clusters) >= GOD_CLUSTERS
                                                             or mixed):
            continue
        gods.append({"file": r, "fan_in": fan_in[r], "fan_out": len(adj[r]), "loc": by_rel[r].loc,
                     "fan_in_non_test": fan_in_prod[r], "import_clusters": len(clusters),
                     "top_level_defs": len(defs), "mixed_kinds": mixed})
    gods.sort(key=lambda g: (-g["fan_in"], g["file"]))
    return {
        "modules": len(nodes),
        "edges": sum(len(v) for v in adj.values()),
        "cycles": [c for c, _ in found],
        "cycles_meta": [{"via_init": v} for _, v in found],
        "god_modules": gods,
    }


def _component_cycle(comp: List[str], adj: Dict[str, Set[str]]) -> Tuple[List[str], bool]:
    """One cycle for a strongly connected component, and whether it passes through a package
    `__init__.py` / JS `index.*` barrel. A cycle that avoids those files is preferred, so a real
    module cycle inside a component that also contains an `__init__` is not downgraded."""
    plain = [n for n in comp if not _INIT_RE.search(n)]
    if len(plain) < len(comp):
        keep = set(plain)
        sub = {n: {d for d in adj[n] if d in keep} for n in plain}
        inner = sorted(sorted(c) for c in _sccs(sorted(plain), sub) if len(c) >= 2)
        if inner:
            return _find_cycle(inner[0][0], set(inner[0]), sub), False
        return _find_cycle(comp[0], set(comp), adj), True
    return _find_cycle(comp[0], set(comp), adj), False


# ---------------------------------------------------------------- god-module indicators
# Test-support folders: modules there (fixture models, test helpers) are test data, not design.
_TEST_SUPPORT_DIRS = {"tests", "__tests__", "spec", "fixtures"}
# Shared helper packages are not a responsibility area: imports from them are not clusters.
_UTILITY_CLUSTERS = {"utils", "util", "utilities", "helpers", "helper", "common", "shared",
                     "types", "typing", "constants"}
_PY_DEF_RE = re.compile(r"^(?:async[ \t]+)?def[ \t]+(\w+)|^class[ \t]+(\w+)", re.M)
_JS_DEF_RE = re.compile(
    r"^(?:export[ \t]+(?:default[ \t]+)?)?(?:declare[ \t]+)?"
    r"(?:(?:async[ \t]+)?function\b\*?[ \t]*(\w+)"
    r"|(?:abstract[ \t]+)?class[ \t]+(\w+)"
    r"|(?:interface|type|enum)[ \t]+(\w+)"
    r"|(?:const|let|var)[ \t]+(\w+)[ \t]*(?::[^=\n]*)?=[ \t]*(?:async[ \t]*)?"
    r"(?:function\b|\([^)\n]*\)[ \t]*(?::[^=\n]*)?=>|\w+[ \t]*=>))", re.M)
_GO_DEF_RE = re.compile(r"^func[ \t]+(\w+)|^type[ \t]+(\w+)[ \t]+(?:struct|interface)\b", re.M)
_NAME_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")
_STEM_SUFFIX_RE = re.compile(r"(?:ors?|ions?|ing|es|e|s)$")
NAME_MAX = 100  # identifier characters read when deriving a name family


def _import_clusters(src: str, targets: Set[str]) -> Set[str]:
    """Distinct responsibility areas `src` imports from: for each resolved target outside the
    module's own folder, the first folder below the deepest folder the two paths share ("^" for
    files directly in that shared folder). The module's child subfolders count (app/core.py ->
    app/db, app/web, app/mail are three clusters). Utility packages (utils, helpers, common,
    ...) are skipped."""
    mdir = posixpath.dirname(src)
    out: Set[str] = set()
    for t in targets:
        tdir = posixpath.dirname(t)
        if tdir == mdir:
            continue
        common = posixpath.commonpath([mdir, tdir]) if mdir and tdir else ""
        rest = tdir[len(common):].lstrip("/")
        cluster = rest.split("/")[0] if rest else "^"
        if cluster.lower() not in _UTILITY_CLUSTERS:
            out.add(cluster)
    return out


def _top_level_defs(text: str, ext: str) -> List[Tuple[str, str]]:
    """(kind, name) of each top-level definition: Python column-0 class/def; JS/TS class,
    function, arrow-function const, interface/type/enum ("type"); Go struct/interface types
    ("class") and functions (methods with a receiver belong to their type and are skipped).
    Constants and other assignments are not definitions."""
    out: List[Tuple[str, str]] = []
    if ext == ".py":
        for m in _PY_DEF_RE.finditer(text):
            out.append(("function", m.group(1)) if m.group(1) else ("class", m.group(2)))
    elif ext in _JS_LANG_EXTS:
        for m in _JS_DEF_RE.finditer(text):
            if m.group(1) or m.group(4):
                out.append(("function", m.group(1) or m.group(4)))
            elif m.group(2):
                out.append(("class", m.group(2)))
            elif m.group(3):
                out.append(("type", m.group(3)))
    elif ext == ".go":
        for m in _GO_DEF_RE.finditer(text):
            out.append(("function", m.group(1)) if m.group(1) else ("class", m.group(2)))
    return out


def _family(kind: str, name: str) -> str:
    """Name family: the last word of a class/type name (EmailValidator -> validator), the first
    word of a function name (validate_email -> validate), stemmed so both give "validat"."""
    words = _NAME_WORD_RE.findall(name.lstrip("_")[:NAME_MAX])
    if not words:
        return name
    word = (words[0] if kind == "function" else words[-1]).lower()
    stem = _STEM_SUFFIX_RE.sub("", word)
    return stem if len(stem) >= 3 else word


def _same_kind(defs: List[Tuple[str, str]]) -> bool:
    """A cohesive collection: one name family holds GOD_SAME_FAMILY_SHARE of the defs (*Validator
    classes with validate_* functions), or classes/types hold GOD_SAME_KIND_SHARE (a module of
    lookup or widget classes). Many unrelated functions are not cohesive by kind alone."""
    if not defs:
        return False
    families: Dict[str, int] = {}
    for kind, name in defs:
        fam = _family(kind, name)
        families[fam] = families.get(fam, 0) + 1
    n = float(len(defs))
    classes = sum(1 for kind, _ in defs if kind in ("class", "type"))
    return (max(families.values()) / n >= GOD_SAME_FAMILY_SHARE
            or classes / n >= GOD_SAME_KIND_SHARE)


def _mixed_kinds(defs: List[Tuple[str, str]]) -> bool:
    """At least GOD_MIN_DEFS defs and no def kind holds GOD_MIXED_KIND_SHARE of them."""
    if len(defs) < GOD_MIN_DEFS:
        return False
    kinds: Dict[str, int] = {}
    for kind, _ in defs:
        kinds[kind] = kinds.get(kind, 0) + 1
    return max(kinds.values()) / float(len(defs)) < GOD_MIXED_KIND_SHARE


VIA_INIT_NOTE = " [through package __init__ (often a re-export, not a runtime cycle)]"


def graph_signals(graph: dict) -> List[Signal]:
    out: List[Signal] = []
    meta = graph.get("cycles_meta") or []
    for i, cyc in enumerate(graph["cycles"]):
        chain = " -> ".join(cyc)
        if i < len(meta) and meta[i].get("via_init"):
            room = 160 - len(VIA_INIT_NOTE)
            if len(chain) > room:
                chain = chain[:room - 3] + "..."
            out.append(Signal("import-cycle", "System Design", "Medium", cyc[0], 1,
                              chain + VIA_INIT_NOTE, "low"))
        else:
            out.append(Signal("import-cycle", "System Design", "High", cyc[0], 1, chain, "high"))
    for g in graph["god_modules"]:
        prod = g.get("fan_in_non_test", g["fan_in"])
        snippet = "fan-in %d (non-test %d), fan-out %d, %d LOC" % (g["fan_in"], prod, g["fan_out"],
                                                                g["loc"])
        if "import_clusters" in g:
            snippet += ", %d import clusters, %d top-level defs%s" % (
                g["import_clusters"], g["top_level_defs"],
                " (mixed kinds)" if g.get("mixed_kinds") else "")
        out.append(Signal(
            "god-module", "Maintainability", "Medium", g["file"], 1, snippet,
            "low" if g["fan_out"] < GOD_COHESIVE_FAN_OUT or prod < GOD_FAN_IN else "medium"))
    return out



register(RuleMeta(
    "import-cycle", "4", "System Design", "High", "low",
    "Circular import chain between modules (Python, JS/TS relative imports, Go).",
    "Only module-load imports form cycles: Python imports inside a def or under if TYPE_CHECKING, "
    "TS import type / { type X } and dynamic import() are ignored (they still count for fan-in). "
    "A cycle through a package __init__.py or JS index.* barrel is reported as Medium with "
    "via_init (often a re-export). Always low confidence (the rule ceiling). Literal relative import()/require() are resolved, "
    "but JS/TS alias and tsconfig paths are missed (only relative specifiers), require() inside a "
    "function is still counted, and Python dynamic importlib imports are not resolved. Python "
    "imports after a multi-line def signature stay deferred (lines inside open brackets continue "
    "the previous line), but backslash line joins are not followed.",
    "Break the cycle by extracting shared code or inverting a dependency.",
    ["Python", "JavaScript", "TypeScript", "Go"]))
register(RuleMeta(
    "god-module", "5", "Maintainability", "Medium", "low",
    "Module with fan-in of at least 10 and at least 500 LOC plus a structural indicator: imports "
    "from at least 3 distinct clusters, or at least 15 top-level definitions of mixed kinds.",
    "An import cluster is the first folder below the deepest folder a resolved import shares with "
    "the module; the module's own child subfolders count (app/core.py -> app/db, app/web), "
    "imports from its own folder and from utility packages (utils, helpers, common, shared, types, "
    "constants) do not. Top-level definitions are Python column-0 class/def, JS/TS classes, "
    "functions, arrow-function consts and interface/type/enum, Go struct/interface types and "
    "functions (not methods); kinds are mixed when no kind holds 80% of them, so a module of "
    "only functions counts through its clusters alone. A module with 15+ definitions where one "
    "name family holds 50% (e.g. *Validator classes and validate_* functions) or classes/types "
    "hold 80% is a cohesive collection and is not reported, whatever its imports. Files named "
    "like tests and modules in tests/, __tests__/, spec/ or fixtures/ folders (fixture models) are "
    "never candidates; library folders named test/ (django/test/utils.py) are. Always low confidence (the rule ceiling), "
    "also when fewer than 10 importers are non-test files or fan-out is below 12. A large single class "
    "with few definitions and fewer than 3 clusters (a god class) is missed; a cohesive module "
    "that imports from several framework packages can still match. Fan-in and clusters count "
    "only resolved imports (see import-cycle limitations).",
    "Split the module by responsibility.",
    ["Python", "JavaScript", "TypeScript", "Go"]))
