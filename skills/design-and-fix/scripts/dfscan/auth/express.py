"""Express adapter: router/app middleware, mounts and the callers of route functions.

Middleware applies by receiver, line and path: `x.use([path,] mw...)` covers routes registered
later on `x` whose path (relative to `x`) is under `path`. The path relative to each receiver is
carried up the mount chain (`app.use('/api', router)` turns router path /orders into /api/orders
on `app`); when a mount path is not a literal the path becomes unknown and a path-scoped or
`.unless(...)`-exempted middleware makes the route unresolved."""
from __future__ import annotations

import bisect
import re
from typing import List, Optional, Set, Tuple

from ..imports import _js_resolve
from .core import (_LOCAL, _MAX_DEPTH, PROTECTED, UNRESOLVED, AuthVerdict, Resolver,
                   _combine, _rooted, _word, join_path, js_receivers, parse_use,
                   use_applies)

_JS_APP_RE = re.compile(r"^(?:await\s+)?express\s*\(")
_JS_ROUTER_RE = re.compile(r"(?:^|[.\s])Router\s*\(")
_JS_EXPORT = (r"module\.exports\s*=\s*%s\b|export\s+default\s+%s\b|exports\.\w+\s*=\s*%s\b"
              r"|export\s*\{[^}]*\b%s\b|export\s+(?:const|let|var)\s+%s\b"
              r"|module\.exports\s*=\s*\{[^}]*\b%s\b")
_JS_FUNC_HEADS = (
    re.compile(r"\bfunction\s*(\w*)\s*\(([^)]*)\)"),
    re.compile(r"(?:(?:const|let|var)\s+(\w+)\s*=\s*)?(?:async\s*)?\(([^)]*)\)\s*(?::\s*[\w<>\[\]]+\s*)?=>"),
    re.compile(r"(?:(?:const|let|var)\s+(\w+)\s*=\s*)?(?:async\s+)?(\w+)\s*=>"),
)
_EXPORT_NAMES_RES = (
    re.compile(r"\bmodule\.exports\s*=\s*([A-Za-z_$][\w$]*)\s*;?\s*$", re.M),
    re.compile(r"\bexport\s+default\s+([A-Za-z_$][\w$]*)\s*;?\s*$", re.M),
    re.compile(r"\b(?:module\.)?exports\.\w+\s*=\s*([A-Za-z_$][\w$]*)\s*;?\s*$", re.M),
    re.compile(r"\bexport\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)"),
)
_EXPORT_GROUP_RE = re.compile(r"\b(?:module\.exports\s*=|export)\s*\{([^}]*)\}")

Path = Optional[str]


_FuncHeads = Tuple[List[int], List[List[Tuple[str, str]]]]


def _js_func_heads(lines: List[str]) -> _FuncHeads:
    """Every function header of a file, scanned once: (sorted line indexes, per line the
    (parameter text, function name or "") of each header in _JS_FUNC_HEADS match order)."""
    idxs: List[int] = []
    heads: List[List[Tuple[str, str]]] = []
    for j, ln in enumerate(lines):
        hits = []
        for rx in _JS_FUNC_HEADS:
            for m in rx.finditer(ln):
                name = m.group(1) or ""
                if not name:
                    em = re.search(r"(?:const|let|var)\s+(\w+)\s*=", ln)
                    name = em.group(1) if em else ""
                hits.append((m.group(2) or "", name))
        if hits:
            idxs.append(j)
            heads.append(hits)
    return idxs, heads


def _js_param_func(lines: List[str], idx: int, var: str,
                   heads: Optional[_FuncHeads] = None) -> Optional[Tuple[int, str]]:
    """(header line index, function name or "") of the nearest function at most 400 lines above
    lines[idx] that takes `var` as a parameter. `heads` is _js_func_heads(lines), passed in so a
    file with thousands of routes is scanned for headers once."""
    w = _word(var)
    idxs, per_line = heads if heads is not None else _js_func_heads(lines)
    lo, hi = bisect.bisect_left(idxs, idx - 399), bisect.bisect_right(idxs, idx)
    for k in range(hi - 1, lo - 1, -1):
        for params, name in per_line[k]:
            if w.search(params):
                return idxs[k], name
    return None


def _js_chain(ctx: Resolver, rel: str, var: str, idx: int, depth: int,
              seen: Set[Tuple[str, str]], path: Path) -> AuthVerdict:
    """Auth applying to receiver `var` at lines[idx] of `rel` for a route at `path` (relative to
    `var`; None when unknown), beyond the route's own line."""
    if depth > _MAX_DEPTH or (rel, var) in seen:
        return AuthVerdict(UNRESOLVED, "router mount chain too deep or cyclic at %s" % rel)
    seen = seen | {(rel, var)}
    hit = ctx.js_auth_before(rel, var, idx, path)
    if hit is not None and hit[1] == PROTECTED:
        return AuthVerdict(PROTECTED, "%s.use(auth) at %s:%d" % (var, rel, hit[0] + 1))
    v = _js_chain_up(ctx, rel, var, idx, depth, seen, path)
    if hit is not None and v.state != PROTECTED:
        # A path-scoped or exempting use(auth) on this receiver that cannot be matched to the
        # route: neither protected nor unprotected.
        return AuthVerdict(UNRESOLVED, "%s.use(auth) at %s:%d is limited to a mount path or has "
                                       "exempt paths; whether it covers the route is not resolved"
                           % (var, rel, hit[0] + 1))
    return v


def _js_chain_up(ctx: Resolver, rel: str, var: str, idx: int, depth: int,
                 seen: Set[Tuple[str, str]], path: Path) -> AuthVerdict:
    lines = ctx.lines(rel)
    decls = ctx.js_decls(rel, var)
    prior = [d for d in decls if d[0] <= idx]
    decl = (prior[-1] if prior else decls[0]) if decls else None
    if decl is not None:
        rhs = decl[1]
        if _JS_ROUTER_RE.search(rhs):
            return ctx.memo("js_router:%s:%s:%s" % (rel, var, path),  # type: ignore[return-value]
                            lambda: _js_router(ctx, rel, var, depth, seen, path))
        if _JS_APP_RE.match(rhs):
            return _LOCAL
        req = re.match(r"require\(\s*['\"]([^'\"]+)['\"]", rhs)
        if req:
            return _js_module(ctx, rel, var, req.group(1), path)
    for name, spec in ctx.js_bindings(rel):
        if name == var:
            return _js_module(ctx, rel, var, spec, path)
    fn = _js_param_func(lines, idx, var, ctx.memo(  # type: ignore[arg-type]
        "js_func_heads:%s" % rel, lambda: _js_func_heads(lines)))
    if fn is not None:
        return ctx.memo("js_callers:%s:%s:%d:%s" % (rel, var, fn[0], path),  # type: ignore
                        lambda: _js_callers(ctx, rel, var, fn, depth, seen, path))
    return _LOCAL


def _export_names(text: str) -> Set[str]:
    out: Set[str] = set()
    for rx in _EXPORT_NAMES_RES:
        out.update(rx.findall(text))
    for m in _EXPORT_GROUP_RE.finditer(text):
        for part in m.group(1).split(","):
            bits = part.replace(":", " as ").split()
            if bits:
                out.add(bits[0])
    return out


def _js_module(ctx: Resolver, rel: str, var: str, spec: str, path: Path) -> AuthVerdict:
    """`var` is required/imported from `spec`: auth applied in that module to the object it
    exports (`app.use(auth); module.exports = app`). use(auth) on another receiver there
    (`adminRouter.use(auth)`) does not cover `var`; when what the module exports cannot be tied
    to a receiver, auth used there makes the route unresolved."""
    if not spec.startswith((".", "@/", "~/", "#", "/")):
        return _LOCAL  # a package (express itself, a framework helper): nothing to follow
    target = _js_resolve(rel, spec, ctx.known) if spec.startswith(".") else None
    if target is None:
        return AuthVerdict(UNRESOLVED, "%s is imported from %s, which is not in the scanned "
                                       "files" % (var, spec))
    uses = ctx.js_auth_uses(target)
    if not uses:
        return _LOCAL
    text = "\n".join(ctx.lines(target))
    exported = _export_names(text)
    bound = set()
    for name in exported:
        for _, rhs in ctx.js_decls(target, name):
            if _JS_APP_RE.match(rhs) or _JS_ROUTER_RE.search(rhs):
                bound.add(name)
    if not bound:
        i, recv, _ = uses[0]
        return AuthVerdict(UNRESOLVED, "%s.use(auth) at %s:%d; whether it applies to %s (what %s "
                                       "exports) is not resolved" % (recv, target, i + 1, var, spec))
    unresolved = None
    for i, recv, info in uses:
        if recv not in bound:
            continue
        st = use_applies(info, path)
        if st == PROTECTED:
            return AuthVerdict(PROTECTED, "%s.use(auth) at %s:%d" % (recv, target, i + 1))
        if st == UNRESOLVED and unresolved is None:
            unresolved = AuthVerdict(UNRESOLVED, "%s.use(auth) at %s:%d is limited to a mount path "
                                                 "or has exempt paths; whether it covers the route "
                                                 "is not resolved" % (recv, target, i + 1))
    return unresolved or _LOCAL


_Mount = Tuple[str, int, str, str, str]  # (file, line idx, receiver, args, router name or "")


def _js_mounts(ctx: Resolver, rel: str, var: str) -> List[_Mount]:
    """Every `x.use([path,] [mw...,] router)` mount of router `var` defined in `rel` (cached)."""
    def build() -> List[_Mount]:
        mounts: List[_Mount] = []
        names = [(rel, var)] + ctx.js_imports().get(rel, [])
        for frel, name in names:
            w = _word(name)
            for i, recv, args, blank in ctx.js_uses_blank(frel):
                if recv != name and name in blank and w.search(blank):
                    mounts.append((frel, i, recv, args, name))
        # inline app.use('/x', require('./routes/x'))
        mounts += [(f, i, recv, args, "") for f, i, recv, args in ctx.js_inline_mounts().get(rel, [])]
        return mounts
    return ctx.memo("js_mounts:%s:%s" % (rel, var), build)  # type: ignore[return-value]


def _js_router(ctx: Resolver, rel: str, var: str, depth: int,
               seen: Set[Tuple[str, str]], path: Path) -> AuthVerdict:
    """A Router: protected when every mount `x.use([path,] [auth...,] router)` applies auth."""
    mounts = _js_mounts(ctx, rel, var)
    if not mounts:
        if re.search(_JS_EXPORT.replace("%s", re.escape(var)), "\n".join(ctx.lines(rel))):
            return AuthVerdict(UNRESOLVED, "router %s in %s is exported but not mounted in the "
                                           "scanned files" % (var, rel))
        return _LOCAL
    verdicts = []
    for frel, i, recv, args, name in mounts:
        routers = ctx.js_router_names(frel) | ({name} if name else set())
        info = parse_use(args, frozenset(routers))
        where = "mounted at %s:%d" % (frel, i + 1)
        if info.auth:
            # Middleware in the mount call runs for every route of the router (an unless()
            # exemption list still applies).
            st = use_applies(info._replace(scope=None, scope_known=True), path)
            if st == PROTECTED:
                verdicts.append(AuthVerdict(PROTECTED, where + " with auth middleware"))
                continue
            if st == UNRESOLVED:
                verdicts.append(AuthVerdict(UNRESOLVED, where + " with auth middleware that has "
                                                        "exempt paths not matched to the route"))
                continue
        if not info.scope_known or path is None:
            up: Path = None
        elif info.scope is None:
            up = path
        elif len(info.scope) == 1:
            up = join_path(info.scope[0], path)
        else:
            up = None
        verdicts.append(_rooted(_js_chain(ctx, frel, recv, i, depth + 1, seen, up), where))
    return _combine(verdicts)


def _js_caller_sites(ctx: Resolver, rel: str, fn: Tuple[int, str]) -> List[Tuple[str, int, str]]:
    """(file, line idx, first argument) of the calls of the function at `fn` in `rel`: by name in
    the file and in files importing it, and `require('./rel')(arg)` (indexed once per scan)."""
    def build() -> List[Tuple[str, int, str]]:
        head, name = fn
        calls: List[Tuple[str, int, str]] = []
        names = ([(rel, name)] if name else []) + ctx.js_imports().get(rel, [])
        for frel, n in names:
            if not n:
                continue
            rx = re.compile(r"(?<![\w$.])" + re.escape(n) + r"\s*\(\s*(\w+)")
            for i, ln in enumerate(ctx.lines(frel)):
                if (frel == rel and i == head) or n not in ln:
                    continue
                for m in rx.finditer(ln):
                    calls.append((frel, i, m.group(1)))
        calls += ctx.js_require_calls().get(rel, [])
        return calls
    return ctx.memo("js_caller_sites:%s:%d" % (rel, fn[0]), build)  # type: ignore[return-value]


def _js_callers(ctx: Resolver, rel: str, var: str, fn: Tuple[int, str], depth: int,
                seen: Set[Tuple[str, str]], path: Path) -> AuthVerdict:
    """`var` is a parameter of a function (`module.exports = (app) => {...}`): follow its callers."""
    calls = _js_caller_sites(ctx, rel, fn)
    if not calls:
        return AuthVerdict(UNRESOLVED, "%s is a parameter of a function in %s whose caller is not "
                                       "in the scanned files" % (var, rel))
    return _combine([_rooted(_js_chain(ctx, frel, arg, i, depth + 1, seen, path),
                             "passed in at %s:%d" % (frel, i + 1)) for frel, i, arg in calls])


def _route_res(ctx: Resolver, rel: str) -> Tuple["re.Pattern[str]", "re.Pattern[str]",
                                                  "re.Pattern[str]"]:
    """(route call, chain receiver at a line end, `.route(` call) regexes of a JS file."""
    def build() -> Tuple["re.Pattern[str]", "re.Pattern[str]", "re.Pattern[str]"]:
        recv = js_receivers("\n".join(ctx.lines(rel)))
        return (re.compile(r"\b(" + recv + r")\.(?:get|post|put|patch|delete)\("),
                re.compile(r"\b(" + recv + r")\s*$"),
                re.compile(r"(?:\b(" + recv + r")\s*)?\.\s*route\s*\("))
    return ctx.memo("js_route_res:" + rel, build)  # type: ignore[return-value]


_JS_CHAIN_VERB_RE = re.compile(r"^\s*\.(?:get|post|put|patch|delete)\(")


def route_receiver(ctx: Resolver, rel: str, idx: int) -> Optional[str]:
    """Receiver of the Express route registered at lines[idx]: `x.post(`, `x` alone on the line
    above a `.post(` line, or `x.route('/p')` (same line or `x` on the line above)."""
    lines = ctx.lines(rel)
    call_re, chain_re, route_re = _route_res(ctx, rel)
    m = call_re.search(lines[idx])
    if m:
        return m.group(1)
    if idx > 0 and _JS_CHAIN_VERB_RE.match(lines[idx]):
        m = chain_re.search(lines[idx - 1])
        if m:
            return m.group(1)
    m = route_re.search(lines[idx])
    if m:
        if m.group(1):
            return m.group(1)
        if idx > 0 and not lines[idx][:m.start()].strip():
            m = chain_re.search(lines[idx - 1])
            if m:
                return m.group(1)
    return None


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    rel = route["file"]
    lines = ctx.lines(rel)
    idx = route["line"] - 1
    if not 0 <= idx < len(lines):
        return _LOCAL
    var = route_receiver(ctx, rel, idx)
    if var is None:
        return _LOCAL
    return _js_chain(ctx, rel, var, idx, 0, set(), route.get("path") or "/")

