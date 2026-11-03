from __future__ import annotations

import bisect
import posixpath
import re
from typing import Dict, List, Optional, Set, Tuple

from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .text import strip_comments
from .walker import is_test_filename

GOD_FAN_IN = 10
GOD_LOC = 500
GOD_COHESIVE_FAN_OUT = 12  # fewer outgoing imports than this: likely a cohesive collection
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
_GO_SINGLE_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
_GO_STR_RE = re.compile(r'"([^"]+)"')


def _read(f: FileInfo) -> str:
    try:
        with open(f.abs, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return ""
    # A UTF-8 BOM is not text: dropping it keeps line numbers and lets line 1 hold a comment.
    return text[1:] if text.startswith("\ufeff") else text


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


def _py_deferred_lines(text: str) -> Set[int]:
    """1-based numbers of indented import lines that sit inside a def (deferred, run at call
    time) or under `if TYPE_CHECKING:` (never run). Module-level try/if/with imports are kept.

    One forward pass with a stack of enclosing (less-indented) lines, so a file with thousands of
    indented imports stays linear; each entry records whether it or an enclosing line is a
    def / TYPE_CHECKING header."""
    out: Set[int] = set()
    stack: List[Tuple[int, bool]] = []  # (indent, this line or an enclosing one is a header)
    for idx, ln in enumerate(text.split("\n")):
        body = ln.lstrip()
        if not body:
            continue
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


def build_graph(files: List[FileInfo], scopes: Optional[List[str]] = None) -> dict:
    """Import graph of JS/TS, Python and Go files; imports are read from comment-stripped text.
    `scopes` are extra source roots for absolute Python imports."""
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
        text = _read(f)
        if not text:
            continue
        text = strip_comments(text, f.lang or "")
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

    # God-module candidates: total fan-in triggers; test files (by file name, so library folders
    # named test/ still count) are never candidates. Low non-test fan-in only lowers confidence.
    gods = [
        {"file": r, "fan_in": fan_in[r], "fan_out": len(adj[r]), "loc": by_rel[r].loc,
         "fan_in_non_test": fan_in_prod[r]}
        for r in nodes
        if not is_test_filename(r) and fan_in[r] >= GOD_FAN_IN and by_rel[r].loc >= GOD_LOC
    ]
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
        out.append(Signal(
            "god-module", "Maintainability", "Medium", g["file"], 1,
            "fan-in %d (non-test %d), fan-out %d, %d LOC" % (g["fan_in"], prod, g["fan_out"], g["loc"]),
            "low" if g["fan_out"] < GOD_COHESIVE_FAN_OUT or prod < GOD_FAN_IN else "medium"))
    return out



register(RuleMeta(
    "import-cycle", "3", "System Design", "High", "low",
    "Circular import chain between modules (Python, JS/TS relative imports, Go).",
    "Only module-load imports form cycles: Python imports inside a def or under if TYPE_CHECKING, "
    "TS import type / { type X } and dynamic import() are ignored (they still count for fan-in). "
    "A cycle through a package __init__.py or JS index.* barrel is reported as Medium with low "
    "confidence and via_init (often a re-export). Literal relative import()/require() are resolved, "
    "but JS/TS alias and tsconfig paths are missed (only relative specifiers), require() inside a "
    "function is still counted, and Python dynamic importlib imports are not resolved.",
    "Break the cycle by extracting shared code or inverting a dependency.",
    ["Python", "JavaScript", "TypeScript", "Go"]))
register(RuleMeta(
    "god-module", "4", "Maintainability", "Medium", "low",
    "Module (not a test file by name) with fan-in of at least 10 and at least 500 LOC.",
    "Files named like tests (test_*.py, *_test.go, *.test.ts, *.spec.*) are never candidates; "
    "modules in folders named test/ (library code such as django/test/utils.py) and test fixture "
    "modules (tests/models.py) are. Low confidence when fewer than 10 importers are non-test "
    "files (fan-in mostly from tests) or fan-out is below 12: cohesive collections of same-kind classes or "
    "functions (validators, widgets, filters) match the size and fan-in thresholds without being "
    "god modules. Intentional shared utility, types or constants modules match; fan-in counts only "
    "resolved imports (see import-cycle limitations).",
    "Split the module by responsibility.",
    ["Python", "JavaScript", "TypeScript", "Go"]))
