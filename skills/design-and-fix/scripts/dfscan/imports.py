from __future__ import annotations

import posixpath
import re
from typing import Dict, List, Optional, Set

from .model import FileInfo, Signal
from .text import strip_comments

GOD_FAN_IN = 10
GOD_LOC = 500
MAX_CYCLES = 50

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx")
_JS_LANG_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_JS_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"](\.[^'"\n]*)['"]"""
)
_PY_FROM_RE = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(.+)$", re.M)
_PY_FROM_PAREN_RE = re.compile(
    r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]*\(([^)]*)\)", re.M)
_PY_IMPORT_RE =re.compile(r"^[ \t]*import[ \t]+([\w., \t]+)", re.M)
_GO_BLOCK_RE = re.compile(r"\bimport\s*\(([^)]*)\)")
_GO_SINGLE_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
_GO_STR_RE = re.compile(r'"([^"]+)"')


def _read(f: FileInfo) -> str:
    try:
        with open(f.abs, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


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


def _py_targets(f: FileInfo, text: str, known: Set[str],
                scopes: Optional[List[str]] = None) -> List[str]:
    src_dir = posixpath.dirname(f.rel)

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

    froms = [(m.group(1), m.group(2), m.group(3)) for m in _PY_FROM_PAREN_RE.finditer(text)]
    froms += [(m.group(1), m.group(2), m.group(3)) for m in _PY_FROM_RE.finditer(text)
              if not m.group(3).lstrip().startswith("(")]
    abs_roots = _py_roots(f.rel, scopes or [], known)
    out: List[str] = []
    for dots, mod, names_text in froms:
        names = [n.split()[0] for n in names_text.split(",") if n.strip() and n.split()[0] != "*"]
        names = [n for n in names if re.match(r"^\w+$", n)]
        if dots:
            base = src_dir
            for _ in range(len(dots) - 1):
                base = posixpath.dirname(base)
            out += resolve([base], mod, names)
        elif mod:
            out += resolve(abs_roots, mod, names)
    for m in _PY_IMPORT_RE.finditer(text):
        for part in m.group(1).split(","):
            name = part.strip().split(" ")[0]
            if name:
                out += resolve(abs_roots, name, [])
    return out


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
    for f in code:
        if f.ext not in _JS_LANG_EXTS and f.ext not in (".py", ".go"):
            continue
        text = _read(f)
        if not text:
            continue
        text = strip_comments(text, f.lang or "")
        targets: List[str] = []
        if f.ext in _JS_LANG_EXTS:
            for m in _JS_RE.finditer(text):
                t = _js_resolve(f.rel, m.group(1), known)
                if t:
                    targets.append(t)
        elif f.ext == ".py":
            targets = _py_targets(f, text, known, scopes)
        else:
            for imp in _go_imports(text):
                for folder in go_folders:
                    if imp == folder or imp.endswith("/" + folder):
                        targets += go_dirs[folder]
                        break
        for t in targets:
            if t != f.rel:
                adj[f.rel].add(t)

    fan_in: Dict[str, int] = {r: 0 for r in adj}
    for dsts in adj.values():
        for d in dsts:
            fan_in[d] += 1

    nodes = sorted(adj)
    comps = sorted(sorted(c) for c in _sccs(nodes, adj) if len(c) >= 2)
    cycles = [_find_cycle(c[0], set(c), adj) for c in comps][:MAX_CYCLES]

    gods = [
        {"file": r, "fan_in": fan_in[r], "fan_out": len(adj[r]), "loc": by_rel[r].loc}
        for r in nodes
        if fan_in[r] >= GOD_FAN_IN and by_rel[r].loc >= GOD_LOC
    ]
    gods.sort(key=lambda g: (-g["fan_in"], g["file"]))
    return {
        "modules": len(nodes),
        "edges": sum(len(v) for v in adj.values()),
        "cycles": cycles,
        "god_modules": gods,
    }


def graph_signals(graph: dict) -> List[Signal]:
    out: List[Signal] = []
    for cyc in graph["cycles"]:
        out.append(Signal("import-cycle", "System Design", "High", cyc[0], 1,
                          " -> ".join(cyc), "high"))
    for g in graph["god_modules"]:
        out.append(Signal(
            "god-module", "Maintainability", "Medium", g["file"], 1,
            "fan-in %d, fan-out %d, %d LOC" % (g["fan_in"], g["fan_out"], g["loc"]),
            "medium"))
    return out
