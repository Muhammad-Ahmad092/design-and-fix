"""Gin/Echo/chi adapter: Use/Group middleware, followed into route functions via their callers."""
from __future__ import annotations

import posixpath
import re
from bisect import bisect_right
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from .core import (_LOCAL, _MAX_DEPTH, PROTECTED, UNRESOLVED, AuthVerdict, Resolver, _combine,
                   _rooted, auth_use, blank_strings, call_args, has_auth, split_args)

_GO_FUNC_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)")
_GO_ROUTE_RECV_RE = re.compile(
    r"\b(\w+)\.(?:GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete|With)\(")
_GO_GROUP_ASSIGN_RE = re.compile(r"\b(\w+)\s*:?=\s*(\w+)\.Group\(")


# A func declaration or literal header: optional method receiver, optional name, parameters.
_GO_ANY_FUNC_RE = re.compile(r"\bfunc\b\s*(?:\(([^()]*)\)\s*)?(\w+)?\s*\(([^()]*)\)")
_GO_DECL_RE = re.compile(r"\b(\w+(?:\s*,\s*\w+)*)\s*:=|\bvar\s+(\w+)")
_GO_CALL_RE = re.compile(r"(?<![\w.])(?:(\w+)\.)?(\w+)\(")
_GO_IMPORT_LINE_RE = re.compile(r"^\s*(?:import\s+)?(\w+|\.|_)?\s*\"([^\"]+)\"")


def _go_names(params: str) -> Set[str]:
    """Names bound by a Go parameter list (`r chi.Router`, `a, b int`, `h *Handler`)."""
    out = set()
    for part in params.split(","):
        bits = part.split()
        if bits and re.match(r"^\w+$", bits[0]):
            out.add(bits[0])
    return out


class GoScopes:
    """Lexical scopes of a Go file's functions (declarations and literals), so a name bound in a
    closure (`r.Group(func(r chi.Router) {...})`) or in another function is not the outer one.
    Brace depth is tracked on string-blanked lines; a line belongs to the functions open at its
    start."""

    def __init__(self, lines: List[str]) -> None:
        self.names: List[Set[str]] = []   # per function: parameters, receiver and := / var names
        self.parent: List[int] = []
        self.inner: List[int] = []        # per line: innermost function open at its start (-1)
        stack: List[int] = []             # open braces: function index or -1 for a block
        for ln in lines:
            cur = next((f for f in reversed(stack) if f >= 0), -1)
            self.inner.append(cur)
            code = blank_strings(ln) if ('"' in ln or "`" in ln or "'" in ln) else ln
            heads = [(m.end(), _go_names(m.group(1) or "") | _go_names(m.group(3) or ""))
                     for m in _GO_ANY_FUNC_RE.finditer(code)] if "func" in code else []
            if cur >= 0 and ("=" in code or "var" in code):
                for m in _GO_DECL_RE.finditer(code):
                    names = m.group(1) or m.group(2)
                    self.names[cur].update(x.strip() for x in names.split(","))
            if "{" not in code and "}" not in code:
                continue
            for pos, c in enumerate(code):
                if c == "{":
                    k = next((j for j, hd in enumerate(heads) if hd[0] <= pos), -1)
                    if k >= 0:
                        self.names.append(set(heads[k][1]))
                        self.parent.append(next((f for f in reversed(stack) if f >= 0), -1))
                        stack.append(len(self.names) - 1)
                        del heads[k]
                    else:
                        stack.append(-1)
                elif c == "}" and stack:
                    stack.pop()

    def scope_of(self, idx: int, name: str) -> int:
        """Index of the innermost function open at lines[idx] that binds `name`, else -1."""
        f = self.inner[idx] if 0 <= idx < len(self.inner) else -1
        while f >= 0:
            if name in self.names[f]:
                return f
            f = self.parent[f]
        return -1


class _GoFile(NamedTuple):
    """Per-file prefix data, built once: group assignments, Use(auth) calls, func headers."""
    groups: List[Tuple[int, str, str, bool]]  # (line idx, var, parent, group middleware auth)
    uses: List[Tuple[int, str]]               # (line idx, receiver of x.Use(auth...))
    funcs: List[int]                          # line indexes of func headers
    scopes: GoScopes


def _go_file(ctx: Resolver, rel: str) -> _GoFile:
    def build() -> _GoFile:
        lines = ctx.lines(rel)
        groups: List[Tuple[int, str, str, bool]] = []
        uses: List[Tuple[int, str]] = []
        funcs: List[int] = []
        for i, ln in enumerate(lines):
            if "Group(" in ln:
                for m in _GO_GROUP_ASSIGN_RE.finditer(ln):
                    args = split_args(call_args(lines, i, m.end() - 1)[0])
                    groups.append((i, m.group(1), m.group(2), any(has_auth(a) for a in args[1:])))
            if "se(" in ln:
                recv = auth_use(ln)
                if recv:
                    uses.append((i, recv))
            if "func" in ln and _GO_FUNC_RE.match(ln):
                funcs.append(i)
        return _GoFile(groups, uses, funcs, GoScopes(lines))
    return ctx.memo("go_file:" + rel, build)  # type: ignore[return-value]


def go_authed(ctx: Resolver, rel: str, idx: int, var: str) -> Optional[str]:
    """Evidence when `var` at lines[idx] has auth: a prior `x.Use(auth)` on it or a parent group,
    or auth middleware in a `Group(path, mw...)` that created it or a parent."""
    gf = _go_file(ctx, rel)
    sc = gf.scopes
    v, at = var, idx
    for _ in range(16):
        scope = sc.scope_of(at, v)
        for i, recv in gf.uses:
            if i >= idx:
                break
            if recv == v and sc.scope_of(i, v) == scope:
                return "%s.Use(auth) at line %d" % (v, i + 1)
        p = None
        for i, gv, par, mw in gf.groups:
            if i >= at:
                break
            if gv == v and sc.scope_of(i, v) == scope:
                p = (par, mw, i)
        if p is None:
            return None
        if p[1]:
            return "Group middleware at line %d" % (p[2] + 1)
        if p[0] == v:
            return None
        v, at = p[0], p[2]
    return None


def _go_root(ctx: Resolver, rel: str, idx: int, var: str) -> str:
    """Follow in-file `v := parent.Group(...)` assignments up to the root receiver."""
    gf = _go_file(ctx, rel)
    v, at = var, idx
    for _ in range(16):
        scope = gf.scopes.scope_of(at, v)
        p = None
        for i, gv, par, _ in gf.groups:
            if i >= at:
                break
            if gv == v and gf.scopes.scope_of(i, v) == scope:
                p = (par, i)
        if p is None or p[0] == v:
            return v
        v, at = p
    return v


def _go_chain(ctx: Resolver, rel: str, var: str, idx: int, depth: int,
              seen: Set[Tuple[str, str]]) -> AuthVerdict:
    if depth > _MAX_DEPTH or (rel, var) in seen:
        return AuthVerdict(UNRESOLVED, "route registration chain too deep or cyclic at %s" % rel)
    seen = seen | {(rel, var)}
    lines = ctx.lines(rel)
    ev = go_authed(ctx, rel, idx, var)
    if ev:
        return AuthVerdict(PROTECTED, "%s (%s)" % (ev, rel))
    root = _go_root(ctx, rel, idx, var)
    funcs = _go_file(ctx, rel).funcs
    k = bisect_right(funcs, idx) - 1  # the enclosing func header (the nearest one above)
    if k < 0:
        return _LOCAL
    m = _GO_FUNC_RE.match(lines[funcs[k]])
    params = [p.strip().split()[0] for p in m.group(2).split(",") if p.strip()]
    if root not in params:
        return _LOCAL
    func, pos = m.group(1), params.index(root)
    return ctx.memo("go_callers:%s:%s:%d" % (rel, func, pos),  # type: ignore[return-value]
                    lambda: _go_callers(ctx, rel, func, pos, root, depth, seen))


def _go_package(text: str) -> str:
    m = re.search(r"(?m)^[ \t]*package\s+(\w+)", text)
    return m.group(1) if m else ""


def _go_callers(ctx: Resolver, rel: str, func: str, pos: int, var: str, depth: int,
                seen: Set[Tuple[str, str]]) -> AuthVerdict:
    pkg = _go_package(ctx.raw(rel))
    rel_dir = posixpath.dirname(rel)
    calls: List[Tuple[str, int, str]] = []
    for frel, i, qual, col in _go_call_index(ctx).get(func, []):
        if qual:
            # pkg.Func(...) or alias.Func(...): the qualifier must name this package (by its
            # package name, or an import whose path ends in its folder or package name).
            ipath = _go_imports(ctx, frel).get(qual)
            if ipath is not None:
                if ipath.rstrip("/").rsplit("/", 1)[-1] not in (pkg, posixpath.basename(rel_dir)):
                    continue
            elif qual != pkg:
                continue
        elif posixpath.dirname(frel) != rel_dir:
            continue
        args = split_args(call_args(ctx.lines(frel), i, col)[0])
        if len(args) > pos:
            calls.append((frel, i, args[pos]))
    if not calls:
        return AuthVerdict(UNRESOLVED, "route function %s (%s) receives %s from a caller not in the "
                                       "scanned files" % (func, rel, var))
    verdicts = []
    for frel, i, arg in calls:
        where = "passed in at %s:%d" % (frel, i + 1)
        m = re.match(r"^(\w+)\.Group\((.*)\)$", arg, re.S)
        if m:
            gargs = split_args(m.group(2))
            if any(has_auth(a) for a in gargs[1:]):
                verdicts.append(AuthVerdict(PROTECTED, where + " (Group middleware)"))
                continue
            arg = m.group(1)
        if re.match(r"^\w+$", arg):
            verdicts.append(_rooted(_go_chain(ctx, frel, arg, i, depth + 1, seen), where))
        else:
            verdicts.append(AuthVerdict(UNRESOLVED, "router argument %r %s is not followed"
                                        % (arg[:40], where)))
    return _combine(verdicts)


def _go_call_index(ctx: Resolver) -> Dict[str, List[Tuple[str, int, str, int]]]:
    """Function name -> (file, line idx, package qualifier or "", column of "(") of every call
    in the scanned Go files (func header lines skipped), built once per scan: a route function's
    callers are looked up here instead of rescanning every file per function."""
    def build() -> Dict[str, List[Tuple[str, int, str, int]]]:
        out: Dict[str, List[Tuple[str, int, str, int]]] = {}
        for f in ctx.of_kind("go"):
            for i, ln in enumerate(ctx.lines(f.rel)):
                if "(" not in ln or _GO_FUNC_RE.match(ln):
                    continue
                for m in _GO_CALL_RE.finditer(ln):
                    out.setdefault(m.group(2), []).append((f.rel, i, m.group(1) or "", m.end() - 1))
        return out
    return ctx.memo("go_call_index", build)  # type: ignore[return-value]


def _go_imports(ctx: Resolver, rel: str) -> Dict[str, str]:
    """Package name or alias -> import path of a Go file's imports (an unaliased import is named
    by the last element of its path)."""
    def build() -> Dict[str, str]:
        out: Dict[str, str] = {}
        text = ctx.raw(rel)
        blocks = re.findall(r"(?ms)^[ \t]*import[ \t]*\((.*?)\)", text)
        singles = re.findall(r"(?m)^[ \t]*import[ \t]+((?:\w+[ \t]+)?\"[^\"\n]+\")", text)
        for ln in [x for b in blocks for x in b.split("\n")] + singles:
            m = _GO_IMPORT_LINE_RE.match(ln)
            if not m:
                continue
            alias = m.group(1)
            if alias in (".", "_"):
                continue
            out[alias or m.group(2).rstrip("/").rsplit("/", 1)[-1]] = m.group(2)
        return out
    return ctx.memo("go_imports:" + rel, build)  # type: ignore[return-value]


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    rel = route["file"]
    lines = ctx.lines(rel)
    idx = route["line"] - 1
    m = _GO_ROUTE_RECV_RE.search(lines[idx]) if 0 <= idx < len(lines) else None
    if not m:
        return _LOCAL
    return _go_chain(ctx, rel, m.group(1), idx, 0, set())
