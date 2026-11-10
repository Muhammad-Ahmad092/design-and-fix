"""Flask and FastAPI adapters: before_request hooks, router/app dependencies and the
include_router chain."""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from .core import (_LOCAL, _MAX_DEPTH, PROTECTED, UNRESOLVED, AuthVerdict, Resolver,
                   _combine, _py_block, _rooted, blank_strings, call_args, has_auth,
                   split_args)
from .django_views import _DEF_HEAD_RE

def _py_receiver(ctx: Resolver, route: dict) -> Optional[str]:
    lines = ctx.lines(route["file"])
    idx = route["line"] - 1
    m = re.match(r"^\s*@(\w+)\.", lines[idx]) if 0 <= idx < len(lines) else None
    return m.group(1) if m else None


# A rejection in a before_request hook body: abort(401/403), `return ..., 401`, status=403,
# raise Unauthorized/Forbidden, redirect to a login view.
_REJECT_RE = re.compile(
    r"\b40[13]\b|\babort\s*\(\s*(?:\w+\.)*(?:UNAUTHORIZED|FORBIDDEN)\b"
    r"|\braise\s+(?:\w+\.)*(?:Unauthorized|Forbidden|PermissionDenied|AuthenticationFailed"
    r"|NotAuthenticated|AuthError|AuthenticationError)\w*"
    r"|\bredirect\s*\([^)]*\blog_?in\b|\bunauthorized\s*\(")
_FLASK_APP_RE = re.compile(r"=\s*(?:flask\.)?Flask\(")


def _flask_app_seen(ctx: Resolver, rel: str) -> bool:
    """Whether a Flask(...) app is constructed in the scanned files of rel's app unit
    (computed once per scan and per unit)."""
    files: List[str] = ctx.memo("flask_app_files", lambda: [  # type: ignore[assignment]
        f.rel for f in ctx.of_kind("py") if _FLASK_APP_RE.search(ctx.raw(f.rel))])
    unit = ctx.app_unit(rel)
    return ctx.memo("flask_app_seen:%r" % (unit,),  # type: ignore[return-value]
                    lambda: any(ctx.same_app(rel, f) for f in files))


def _flask_hooks(ctx: Resolver) -> List[Tuple[str, str, int, bool]]:
    """(file, receiver, line idx, rejects) of before_request hooks whose body checks auth.
    `rejects` is False for a hook that reads the user or token but never rejects the request
    (no abort / 401 / 403 / raise Unauthorized / redirect to login): it may only load the
    user, so it does not prove protection."""
    def build() -> List[Tuple[str, str, int, bool]]:
        out: List[Tuple[str, str, int, bool]] = []
        for f in ctx.of_kind("py"):
            if "before_" not in ctx.raw(f.rel):
                continue
            lines = ctx.lines(f.rel)
            for i, ln in enumerate(lines):
                m = re.match(r"^\s*@(\w+)\.before_(?:app_)?request\b", ln)
                fn_idx = None
                if m:
                    k = i + 1
                    while k < len(lines) and not _DEF_HEAD_RE.match(lines[k]):
                        k += 1
                    fn_idx = k if k < len(lines) and k < i + 10 else None
                else:
                    m = re.search(r"\b(\w+)\.before_(?:app_)?request\(\s*(\w+)\s*\)", ln)
                    if m:
                        rx = re.compile(r"^\s*(?:async\s+)?def\s+%s\s*\(" % re.escape(m.group(2)))
                        fn_idx = next((k for k, t in enumerate(lines) if rx.match(t)), None)
                if m is None or fn_idx is None:
                    continue
                body = lines[i] + " " + " ".join(lines[i + 1:fn_idx + 1]) + " " + " ".join(
                    t for _, t in _py_block(lines, fn_idx, 60))
                rejects = bool(_REJECT_RE.search(body))
                if has_auth(body) or rejects:
                    out.append((f.rel, m.group(1), i, rejects))
        return out
    return ctx.memo("flask_hooks", build)  # type: ignore[return-value]


def flask_adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    rel = route["file"]
    var = _py_receiver(ctx, route)
    if var is None:
        return _LOCAL
    kind, orel, oidx, ctor, _ = ctx.py_origin(rel, var, "Flask|Blueprint")
    if kind == "missing":
        return AuthVerdict(UNRESOLVED, "%s is imported from %s, which is not in the scanned files "
                                       "(its before_request hooks are not seen)" % (var, orel))
    hooks = _flask_hooks(ctx)
    oname = ""
    if kind == "local":
        m = re.match(r"^\s*(\w+)", ctx.lines(orel)[oidx])
        oname = m.group(1) if m else ""
    # Hooks that apply: on this receiver (or its origin), or app-wide on a Flask app of the
    # route's own app or service unit (another service's hook never protects this one).
    applies = []
    for hrel, hvar, i, rejects in hooks:
        if (hrel == rel and hvar == var) or (hrel == orel and hvar == oname):
            applies.append(("before_request auth hook at %s:%d" % (hrel, i + 1), rejects))
    for hrel, hvar, i, rejects in hooks:
        if ctx.same_app(rel, hrel) and ctx.py_origin(hrel, hvar, "Flask")[0] == "local":
            applies.append(("app-wide before_request auth hook at %s:%d" % (hrel, i + 1),
                            rejects))
    for where, rejects in applies:
        if rejects:
            return AuthVerdict(PROTECTED, where)
    if applies:
        return AuthVerdict(UNRESOLVED, "%s reads auth state but never rejects the request "
                                       "(no abort(401/403), 401 response or raise "
                                       "Unauthorized seen); it may only load the user"
                           % applies[0][0])
    if kind == "local" and ctor == "Blueprint":
        if not _flask_app_seen(ctx, rel):
            return AuthVerdict(UNRESOLVED, "the Flask app that registers blueprint %s is not in the "
                                           "scanned files (app-wide before_request hooks not seen)"
                               % var)
    return _LOCAL


def _fastapi_includes(ctx: Resolver, def_rel: str, name: str) -> List[Tuple[str, int, str, str]]:
    """(file, line idx, receiver, remaining args) of include_router calls that mount router
    `name` defined in `def_rel`."""
    out: List[Tuple[str, int, str, str]] = []

    def is_def(importer: str, module: str) -> bool:
        target = ctx.py_module_file(importer, module)
        return target == def_rel

    for f in ctx.of_kind("py"):
        if "include_router" not in ctx.raw(f.rel):
            continue
        lines = ctx.lines(f.rel)
        binds = ctx.py_binds(f.rel)
        for i, ln in enumerate(lines):
            for m in re.finditer(r"\b(\w+)\.include_router\(", ln):
                args = split_args(call_args(lines, i, m.end() - 1)[0])
                if not args:
                    continue
                ref = args[0]
                hit = False
                mm = re.match(r"^(\w+)\.(\w+)$", ref)
                if mm and mm.group(2) == name and mm.group(1) in binds:
                    module, sub = binds[mm.group(1)]
                    if sub:  # `from app.api import items` / `from . import items`
                        module = module + sub if module.endswith(".") else module + "." + sub
                    hit = is_def(f.rel, module)
                elif re.match(r"^\w+$", ref):
                    if f.rel == def_rel and ref == name:
                        hit = True
                    elif ref in binds and binds[ref][1] == name:
                        hit = is_def(f.rel, binds[ref][0])
                if hit:
                    out.append((f.rel, i, m.group(1), ", ".join(args[1:])))
    return out


def _fastapi_chain(ctx: Resolver, rel: str, var: str, depth: int,
                   seen: Set[Tuple[str, str]]) -> AuthVerdict:
    if depth > _MAX_DEPTH or (rel, var) in seen:
        return AuthVerdict(UNRESOLVED, "include_router chain too deep or cyclic at %s" % rel)
    seen = seen | {(rel, var)}
    kind, orel, oidx, ctor, args = ctx.py_origin(rel, var, "FastAPI|APIRouter")
    if kind == "missing":
        return AuthVerdict(UNRESOLVED, "%s is imported from %s, which is not in the scanned "
                                       "files" % (var, orel))
    if kind == "none":
        return _LOCAL
    if has_auth(blank_strings(args)):
        return AuthVerdict(PROTECTED, "%s(dependencies=...) at %s:%d" % (ctor, orel, oidx + 1))
    if ctor == "FastAPI":
        return _LOCAL
    name = var
    m = re.match(r"^\s*(\w+)", ctx.lines(orel)[oidx])
    if m:
        name = m.group(1)
    incs = _fastapi_includes(ctx, orel, name)
    if not incs:
        return AuthVerdict(UNRESOLVED, "router %s (%s) is not included by any scanned file"
                           % (name, orel))
    verdicts = []
    for frel, i, recv, rest in incs:
        where = "included at %s:%d" % (frel, i + 1)
        if has_auth(blank_strings(rest)):
            verdicts.append(AuthVerdict(PROTECTED, where + " with auth dependencies"))
        else:
            verdicts.append(_rooted(_fastapi_chain(ctx, frel, recv, depth + 1, seen), where))
    return _combine(verdicts)


def fastapi_adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    var = _py_receiver(ctx, route)
    if var is None:
        return _LOCAL
    return ctx.memo("fastapi:%s:%s" % (route["file"], var),  # type: ignore[return-value]
                    lambda: _fastapi_chain(ctx, route["file"], var, 0, set()))
