from __future__ import annotations

import posixpath
import re
from typing import Callable, Dict, List, Optional, Set, Tuple

from .auth import (UNPROTECTED, UNRESOLVED, DjangoViews, Resolver, call_args, django_route_parts,
                   django_view_ref, is_health_path, split_args)
from .auth import auth_use as _auth_use
from .auth import blank_strings as _blank_strings
from .auth import code_text as _code_text
from .auth.core import (PROTECTED, _js_bindings, file_code_text, has_auth_word, js_receivers,
                        js_router_decls, routes_spec, use_applies, use_info)
from .auth.go import GoScopes
from .auth.nest import _NEST_GUARD_AUTH_RE
from .auth import has_auth as _has_auth
from .auth import kind_of as _kind
from .auth import split_lines as _lines
from .filecache import FileCache
from .frameworks import FRAMEWORK_EXTS, find_framework_routes
from .imports import _read
from .model import FileInfo, Signal
from .registry import RuleMeta, register

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_Q = r"""['"`]"""
_NEST_CTRL_RE = re.compile(r"@Controller\(\s*(?:" + _Q + r"([^'\"`]*)" + _Q + r")?")
_NEST_RE = re.compile(r"@(Get|Post|Put|Patch|Delete)\(\s*(?:" + _Q + r"([^'\"`]*)" + _Q + r")?(.*)")
# Route calls whose arguments may span lines: the path and the remaining arguments are read
# from the balanced call text (auth.call_args), so multi-line registrations match single-line ones.
_PY_HEAD_RE = re.compile(r"^\s*@(\w+)\.(get|post|put|patch|delete|route)\(")
_PATH_ARG_RE = re.compile(r"\s*(['\"`])([^'\"`]*)\1(.*)", re.S)
# Where an inline JS handler starts: its body is not read for auth markers.
_HANDLER_START_RE = re.compile(r"=>|\bfunction\b|\basync\b")
# A chained call split over lines: `app` alone on one line, `.post('/x', h)` on the next.
_JS_CHAIN_VERB_RE = re.compile(r"^\s*\.(get|post|put|patch|delete)\(")
_JS_USE_CALL_RE = re.compile(r"\b(\w+)\s*\.\s*use\s*\(")
_JS_CHAIN_LINK_RE = re.compile(r"\s*\.\s*(get|post|put|patch|delete|all)\s*\(")
_BARE_NAME_RE = re.compile(r"^[\w$.]+$")
_GO_RECV = r"(?:r|g|e|app|api|mux|router|group|v\d+|\w*[Gg]roup|\w*[Rr]outer)"
_GO_GROUP_RE = re.compile(r"\b(\w+)\s*:?=\s*(\w+)\.Group\(\s*\"([^\"]*)\"([^\n]*)")
_CHI_WITH = r"\.With\((.*?)\)\.(Get|Post|Put|Patch|Delete)\(\s*\"(/[^\"]*)\"(.*)"
_GO_HANDLE_RE = re.compile(r"\bhttp\.HandleFunc\(\s*\"([^\"]*)\"(.*)")
_METHODS_RE = re.compile(r"methods\s*=\s*[\[(]([^\])]*)[\])]", re.I)
_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s")

# Server entry points (they anchor service-level signals), matched on text with comments and
# string contents blanked.
_ENTRY_RES = {
    "py": re.compile(r"\buvicorn\.run\(|(?<![\w.])(?:app|application|server)\.run\(|\bweb\.run_app\("),
    "js": re.compile(r"(?<![\w.])(?:app|server|httpServer|httpsServer|fastify|application|srv|instance)"
                     r"\.listen\(|\bNestFactory\.create\w*\(|\bcreateServer\(|\bBun\.serve\("),
    "go": re.compile(r"\bListenAndServe(?:TLS)?\(|\bgin\.(?:Default|New)\(\)|\becho\.New\(\)"
                     r"|\bfiber\.New\("),
}
# Framework app factories count only in a main/app/server/index-style file.
_FACTORY_RES = {
    "py": re.compile(r"=\s*(?:FastAPI|Flask|Starlette|Quart|Sanic)\("),
    "js": re.compile(r"=\s*(?:express|fastify|Fastify)\(\s*\)|\bnew\s+(?:Koa|Hono)\("),
}
_ENTRY_STEMS = {"main", "app", "server", "index", "application", "__main__", "run"}
_MANAGE_RE = re.compile(r"\bexecute_from_command_line\(")
_DOCKER_ENTRY_RE = re.compile(r"(?im)^[ \t]*(?:CMD|ENTRYPOINT|EXPOSE)\b")


def _is_comment(stripped: str) -> bool:
    return stripped.startswith(("//", "#", "/*", "*"))


def _def_params(lines: List[str], k: int) -> str:
    """Parameter list of the def starting at lines[k] (parens balanced, comments dropped)."""
    text = "\n".join(re.sub(r"\s#.*$", "", ln) for ln in lines[k:k + 12])
    start = text.find("(")
    if start < 0:
        return ""
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
    return text[start:]


def _py_decorators(lines: List[str]) -> Dict[int, int]:
    """First line -> last line of every decorator (a multi-line decorator spans several lines)."""
    out: Dict[int, int] = {}
    i = 0
    while i < len(lines):
        s = lines[i].lstrip()
        if s.startswith("@"):
            end = i
            if "(" in s:
                end = call_args(lines, i, lines[i].index("("), max_lines=20)[1]
            out[i] = end
            i = end + 1
        else:
            i += 1
    return out


def _middleware_text(args: str) -> str:
    """Route-call arguments after the path, as auth evidence: string contents blanked, an inline
    handler (from =>, function or async on) dropped, and a bare trailing handler name dropped, so
    `app.post('/superusers', createSuperuser)` is not read as protected."""
    cut = _HANDLER_START_RE.split(args, 1)
    parts = [a for a in split_args(_blank_strings(cut[0]).strip().lstrip(",")) if a]
    if len(cut) == 1 and parts and _BARE_NAME_RE.match(parts[-1]):
        parts = parts[:-1]
    return ", ".join(parts)


def _py_stack_auth(lines: List[str], idx: int, blocks: Dict[int, int],
                   ends: Dict[int, int]) -> bool:
    """Decorators directly above/below the route decorator, plus the def signature.
    `ends` maps each decorator's last line to its first (built once per file)."""
    j = idx - 1
    while j in ends:
        start = ends[j]
        if _has_auth(_blank_strings(" ".join(lines[start:j + 1]))):
            return True
        j = start - 1
    k = blocks.get(idx, idx) + 1
    while k in blocks:
        if _has_auth(_blank_strings(" ".join(lines[k:blocks[k] + 1]))):
            return True
        k = blocks[k] + 1
    if k < len(lines) and _DEF_RE.match(lines[k]):
        return _has_auth(_def_params(lines, k))
    return False


def _nest_deco_auth(line: str) -> bool:
    """Whether one NestJS decorator line applies auth: string contents never count, Swagger
    @Api* decorators (@ApiBearerAuth documents, it does not enforce) never do, and
    @UseGuards(...) counts only for auth-like guard names (not ThrottlerGuard)."""
    text = _blank_strings(line.strip())
    if re.match(r"@Api\w*", text):
        return False
    m = re.match(r"@UseGuards\s*\((.*)", text)
    if m:
        return bool(_NEST_GUARD_AUTH_RE.search(m.group(1)))
    return _has_auth(text)


def _nest_stack_auth(lines: List[str], idx: int) -> bool:
    j = idx - 1
    while j >= 0 and lines[j].strip().startswith("@"):
        if _nest_deco_auth(lines[j]):
            return True
        j -= 1
    # Class-level decorators above the enclosing class declaration.
    c = idx - 1
    while c >= 0 and not _CLASS_RE.match(lines[c]):
        c -= 1
    c -= 1
    while c >= 0 and lines[c].strip().startswith("@"):
        if _nest_deco_auth(lines[c]):
            return True
        c -= 1
    return False


def _route(method: str, path: str, f: FileInfo, line: int, framework: str, auth: bool) -> dict:
    return {"method": method, "path": path, "file": f.rel, "line": line,
            "framework": framework, "auth_detected": auth}


def _match_paren(text: str, pos: int) -> int:
    """Index of the bracket closing the one at text[pos] (string contents skipped), or -1."""
    depth = 0
    quote = ""
    k = pos
    while k < len(text):
        c = text[k]
        if quote:
            if c == "\\":
                k += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return -1


def _route_chain(lines: List[str], idx: int, col: int) -> Optional[Tuple[str, List[Tuple[str, str]]]]:
    """`x.route('/p').post(mw, h).put(h)` starting with the `(` at lines[idx][col] (the chain may
    span lines): (path, [(verb, call argument text)]), or None when the path is not a literal."""
    text = "\n".join(lines[idx:idx + 40])
    close = _match_paren(text, col)
    if close < 0:
        return None
    pm = _PATH_ARG_RE.match(text[col + 1:close])
    if not pm or pm.group(3).strip():
        return None
    verbs: List[Tuple[str, str]] = []
    k = close + 1
    while True:
        m = _JS_CHAIN_LINK_RE.match(text, k)
        if not m:
            break
        end = _match_paren(text, m.end() - 1)
        if end < 0:
            break
        if m.group(1) != "all":
            verbs.append((m.group(1), text[m.end():end]))
        k = end + 1
    return pm.group(2), verbs


def _js_routes(f: FileInfo, lines: List[str]) -> List[dict]:
    """Express and NestJS routes of a JS/TS file. Express receivers are app/server/r/*router and
    names bound to express() or a Router in the file (core.js_receivers); `x.route(path)` chains
    give one route per chained verb. auth_detected: a marker in the route call's middleware
    (core.has_auth_word, string contents and the trailing handler dropped) or a prior
    `x.use(auth)` on the same receiver that covers the route's path (core.use_applies)."""
    out: List[dict] = []
    text = "\n".join(lines)
    recv = js_receivers(text)
    head_re = re.compile(r"\b(" + recv + r")\.(get|post|put|patch|delete)\(")
    chain_recv_re = re.compile(r"\b(" + recv + r")\s*$")
    route_re = re.compile(r"(?:\b(" + recv + r")\s*)?\.\s*route\s*\(")
    routers = frozenset(js_router_decls(text)) | frozenset(
        n for n, spec in _js_bindings(text) if spec.startswith(".") and routes_spec(spec))
    uses: Dict[str, list] = {}  # receiver -> UseInfo of each prior x.use(auth...)
    prefix = ""

    def authed(r: str, path: str, mw: str) -> bool:
        return any(use_applies(u, path) == PROTECTED for u in uses.get(r, ())) or \
            has_auth_word(_middleware_text(mw))

    for i, ln in enumerate(lines):
        if _is_comment(ln.strip()):
            continue
        if ".use" in ln or ". use" in ln:
            for um in _JS_USE_CALL_RE.finditer(ln):
                info = use_info(um.group(1), call_args(lines, i, um.end() - 1)[0], routers)
                if info is not None:
                    uses.setdefault(um.group(1), []).append(info)
        m = _NEST_CTRL_RE.search(ln)
        if m:
            prefix = (m.group(1) or "").strip("/")
        m = head_re.search(ln)
        r, verb = (m.group(1), m.group(2)) if m else ("", "")
        if m is None and i > 0:
            cm = chain_recv_re.search(lines[i - 1])
            m = _JS_CHAIN_VERB_RE.match(ln) if cm else None
            if m and cm:
                r, verb = cm.group(1), m.group(1)
        pm = _PATH_ARG_RE.match(call_args(lines, i, m.end() - 1)[0]) if m else None
        if m and pm:
            out.append(_route(verb.upper(), pm.group(2), f, i + 1, "express",
                              authed(r, pm.group(2), pm.group(3))))
            continue
        rm = route_re.search(ln) if "route" in ln else None
        if rm:
            r = rm.group(1) or ""
            if not r and i > 0 and not ln[:rm.start()].strip():
                cm = chain_recv_re.search(lines[i - 1])
                r = cm.group(1) if cm else ""
            chain = _route_chain(lines, i, rm.end() - 1) if r else None
            if chain:
                for v, args in chain[1]:
                    out.append(_route(v.upper(), chain[0], f, i + 1, "express",
                                      authed(r, chain[0], ", " + args)))
                continue
        m = _NEST_RE.search(ln)
        if m:
            sub = (m.group(2) or "").strip("/")
            path = "/" + "/".join(p for p in (prefix, sub) if p)
            out.append(_route(m.group(1).upper(), path, f, i + 1, "nestjs",
                              _nest_stack_auth(lines, i) or _has_auth(_blank_strings(m.group(3)))))
    return out


def _py_routes(f: FileInfo, lines: List[str], text: str,
               views: Callable[[], DjangoViews]) -> List[dict]:
    out: List[dict] = []
    fw = "fastapi" if "fastapi" in text.lower() else "flask"
    blocks = _py_decorators(lines)
    ends = {e: b for b, e in blocks.items()}
    drf_router = "Router(" in text
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            continue
        m = _PY_HEAD_RE.match(ln)
        pm = _PATH_ARG_RE.match(call_args(lines, i, m.end() - 1)[0]) if m else None
        if m and pm and pm.group(1) != "`":
            rest = pm.group(3)
            auth = _py_stack_auth(lines, i, blocks, ends) or _has_auth(_blank_strings(rest))
            if m.group(2) != "route":
                out.append(_route(m.group(2).upper(), pm.group(2), f, i + 1, fw, auth))
                continue
            mm = _METHODS_RE.search(rest)
            methods = re.findall(r"[A-Za-z]+", mm.group(1)) if mm else []
            for meth in [x.upper() for x in methods] or ["GET"]:
                out.append(_route(meth, pm.group(2), f, i + 1, fw, auth))
            continue
        parts = django_route_parts(lines, i)
        if parts is None or (parts[0] == "register" and not drf_router):
            continue
        out.extend(_django_routes(f, i, parts, views))
    return out


def _django_routes(f: FileInfo, i: int, parts: Tuple[str, str, str],
                   views: Callable[[], DjangoViews]) -> List[dict]:
    """Routes of one urls.py `path(...)` or DRF `router.register(...)` call: one per HTTP method
    the view handles (resolved through the scanned view classes/functions), else method ANY."""
    kind, path, rest = parts
    line_auth = _has_auth(rest)
    if kind == "register":
        m = re.match(r"\s*,\s*([\w.]+)", rest)
        ref = ("class", m.group(1).split(".")[-1], ()) if m else None
    else:
        ref = django_view_ref(rest)
    view = views().lookup(ref, f.rel) if ref else None
    if view is None or not view.methods:
        return [_route("ANY", path, f, i + 1, "django", line_auth)]
    out = []
    for meth in view.methods:
        p = _join(path, "<pk>") if kind == "register" and meth in ("PUT", "PATCH", "DELETE") else path
        out.append(_route(meth, p, f, i + 1, "django",
                          line_auth or bool(view.auth) or meth in view.method_auth))
    return out


def _join(prefix: str, sub: str) -> str:
    if not prefix:
        return sub
    return prefix.rstrip("/") + "/" + sub.lstrip("/") if sub else prefix


def _go_routes(f: FileInfo, lines: List[str], text: str) -> List[dict]:
    out: List[dict] = []
    upper_fw = "echo" if "labstack/echo" in text else "gin"
    # Router groups declared in this file: `v := r.Group("/api", mw...)` prefixes routes on `v`.
    # Assignments apply in line order, so a variable reused in another function resolves to its
    # latest prior assignment (a use before any assignment falls back to the first one).
    groups: Dict[int, List[Tuple[str, str, str, bool]]] = {}
    first: Dict[str, Tuple[str, str, bool]] = {}  # var -> (parent, prefix, group middleware auth)
    for i, ln in enumerate(lines):
        for m in _GO_GROUP_RE.finditer(ln):
            g = (m.group(1), m.group(2), m.group(3), _has_auth(m.group(4)))
            groups.setdefault(i, []).append(g)
            if g[0] not in first:
                parent_prefix = first[g[1]][1] if g[1] in first else ""
                first[g[0]] = (g[1], _join(parent_prefix, g[2]), g[3])
    recv = _GO_RECV
    if first:
        recv = r"(?:" + "|".join(re.escape(v) for v in sorted(first)) + "|" + _GO_RECV + ")"
    # Route calls; the path and the other arguments come from the balanced (maybe multi-line)
    # call text: r.POST(\n "/orders",\n create,\n) matches like the single-line form.
    up_re = re.compile(r"\b(" + recv + r")\.(GET|POST|PUT|PATCH|DELETE)\(")
    chi_re = re.compile(r"\b(" + recv + r")\.(Get|Post|Put|Patch|Delete)\(")
    with_re = re.compile(r"\b(" + recv + r")" + _CHI_WITH)  # r.With(mw...).Post("/x", h)
    state: Dict[str, Tuple[str, str, bool]] = {}  # var -> (parent, prefix, group middleware auth)
    # Receivers with a prior `x.Use(auth...)`, with the function scope binding the name there
    # (a closure parameter shadowing `r`, or another function's `r`, is another receiver).
    scopes = GoScopes(lines)
    used: Dict[str, Set[int]] = {}

    def info(var: str) -> Tuple[str, str, bool]:
        return state.get(var) or first.get(var) or ("", "", False)

    def authed(var: str, at: int) -> bool:
        """Group middleware or Use(auth) on the receiver or any parent group."""
        for _ in range(16):
            parent, _, mw = info(var)
            if mw or scopes.scope_of(at, var) in used.get(var, ()):
                return True
            if not parent or parent == var:
                return False
            var = parent
        return False

    for i, ln in enumerate(lines):
        if _is_comment(ln.strip()):
            continue
        for var, parent, path, mw in groups.get(i, []):
            state[var] = (parent, _join(info(parent)[1], path), mw)
        receiver = _auth_use(ln) if "se(" in ln else None
        if receiver:
            used.setdefault(receiver, set()).add(scopes.scope_of(i, receiver))
        m = with_re.search(ln)
        if m:
            path = _join(info(m.group(1))[1], m.group(4))
            out.append(_route(m.group(3).upper(), path, f, i + 1, "chi", authed(m.group(1), i)
                              or _has_auth(m.group(2)) or _has_auth(_middleware_text(m.group(5)))))
            continue
        for rx, fw in ((up_re, upper_fw), (chi_re, "chi")):
            m = rx.search(ln)
            pm = _PATH_ARG_RE.match(call_args(lines, i, m.end() - 1)[0]) if m else None
            if m and pm and pm.group(1) == '"' and pm.group(2).startswith("/"):
                path = _join(info(m.group(1))[1], pm.group(2))
                out.append(_route(m.group(2).upper(), path, f, i + 1, fw, authed(m.group(1), i)
                                  or _has_auth(_middleware_text(pm.group(3)))))
                break
        else:
            m = _GO_HANDLE_RE.search(ln)
            if m:
                out.append(_route("ANY", m.group(1), f, i + 1, "net/http",
                                  _has_auth(m.group(2))))
    return out


def find_routes(files: List[FileInfo], cache: Optional[FileCache] = None) -> List[dict]:
    """Route inventory. Read from comment-stripped text (Python docstrings blanked) so routes
    shown as examples in comments or docstrings are not listed. Java/Kotlin, C#, Ruby and PHP
    files go to frameworks.find_framework_routes (Spring, ASP.NET Core, Rails, Laravel).
    Django routes take their HTTP methods from the view class or function (DRF generics and
    viewsets, handler methods, @api_view/@require_http_methods); unresolved views stay ANY.
    `cache` is the scan's file cache (files are read directly without one); with it, whether
    each JS/TS/Python/Go file has routes is recorded for has_routes."""
    routes: List[dict] = []
    index: List[DjangoViews] = []
    read = cache.text if cache is not None else _read

    def views() -> DjangoViews:
        if not index:
            index.append(DjangoViews(files, read, None if cache is None else
                                     lambda f: _lines(file_code_text(f, "py", cache))))
        return index[0]

    for f in files:
        if f.is_test:
            continue
        kind = _kind(f)
        if kind is None:
            if f.lang is not None and f.ext in FRAMEWORK_EXTS:
                routes.extend(find_framework_routes(f, read(f), files, cache))
            continue
        found = _file_routes(f, kind, read(f), views, cache)
        routes.extend(found)
        if cache is not None:
            cache.memo(("has_routes", f.abs), lambda: bool(found))
    return routes


def _file_routes(f: FileInfo, kind: str, text: str, views: Callable[[], DjangoViews],
                 cache: Optional[FileCache]) -> List[dict]:
    # Stripping only blanks text, so a file with no route candidate in its raw lines has
    # none after stripping either: skip the (slower) strip for those files.
    if not _routes_in(f, kind, _lines(text), text, views):
        return []
    code = file_code_text(f, kind, cache) if cache is not None else _code_text(text, kind)
    return _routes_in(f, kind, _lines(code), text, views)


class _NoViews:
    """A Django view index that resolves nothing (every Django route stays method ANY)."""

    def lookup(self, ref: object, near: str) -> None:
        return None


def has_routes(files: List[FileInfo], cache: Optional[FileCache] = None) -> bool:
    """bool(find_routes(files)), reusing the per-file answers find_routes recorded in `cache`.

    Whether a JS/TS/Python/Go file has routes does not depend on the other files (Django view
    resolution only changes a route's methods, never whether it is listed), so it is decided
    per file; Spring/ASP.NET/Rails/Laravel files are parsed with `files` as find_routes does."""
    read = cache.text if cache is not None else _read
    for f in files:
        if f.is_test:
            continue
        kind = _kind(f)
        if kind is None:
            if (f.lang is not None and f.ext in FRAMEWORK_EXTS
                    and find_framework_routes(f, read(f), files, cache)):
                return True
            continue

        def compute(f: FileInfo = f, kind: str = kind) -> bool:
            return bool(_file_routes(f, kind, read(f), lambda: _NoViews(), cache))  # type: ignore

        if cache.memo(("has_routes", f.abs), compute) if cache is not None else compute():
            return True
    return False


def _routes_in(f: FileInfo, kind: str, lines: List[str], text: str,
               views: Callable[[], DjangoViews]) -> List[dict]:
    if kind == "js":
        return _js_routes(f, lines)
    if kind == "py":
        return _py_routes(f, lines, text, views)
    return _go_routes(f, lines, text)


# ------------------------------------------------- services (one per server entry point)
Entry = Tuple[str, int, bool]  # (file, line, anchor-worthy code file)


def find_services(files: List[FileInfo], cache: Optional[FileCache] = None) -> Dict[str, Entry]:
    """Service root folder ("." for the scan root) -> its entry point.

    A folder is a service root when it holds a server entry point: app.listen/uvicorn.run/app.run/
    http.ListenAndServe/gin.Default()/NestFactory.create, a framework app factory (FastAPI(),
    Flask(...), express()) in a main/app/server/index file, a Django manage.py, or a Dockerfile with
    CMD/ENTRYPOINT/EXPOSE. Test and example files never count. `cache` is the scan's file cache.
    """
    read = cache.text if cache is not None else _read
    found: Dict[str, List[Tuple[int, str, int, bool]]] = {}

    def add(f: FileInfo, line: int, code: bool) -> None:
        folder = posixpath.dirname(f.rel) or "."
        found.setdefault(folder, []).append((0 if code else 1, f.rel, line, code))

    for f in files:
        if f.is_test or f.is_example:
            continue
        name = posixpath.basename(f.rel)
        if name == "Dockerfile":
            m = _DOCKER_ENTRY_RE.search(read(f))
            if m:
                add(f, 0, False)
            continue
        kind = _kind(f)
        if kind is None:
            continue
        raw = read(f)
        stem = name.rsplit(".", 1)[0]
        factory = _FACTORY_RES.get(kind) if stem in _ENTRY_STEMS else None
        if name == "manage.py":
            if _MANAGE_RE.search(raw):
                add(f, 0, False)
            continue
        if not (_ENTRY_RES[kind].search(raw) or (factory and factory.search(raw))):
            continue
        code = _blank_strings(file_code_text(f, kind, cache) if cache is not None
                              else _code_text(raw, kind))
        hits = [m.start() for m in _ENTRY_RES[kind].finditer(code)]
        if factory:
            hits += [m.start() for m in factory.finditer(code)]
        if hits:
            add(f, code.count("\n", 0, min(hits)) + 1, True)
    return {folder: (e[1], e[2], e[3]) for folder, entries in found.items()
            for e in [min(entries)]}


def _service_of(rel: str, roots: List[str]) -> Optional[str]:
    """Deepest service root containing `rel` (roots sorted deepest first)."""
    d = posixpath.dirname(rel)
    for root in roots:
        if root == "." or d == root or d.startswith(root + "/"):
            return root
    return None


def route_signals(routes: List[dict], files: List[FileInfo],
                  cache: Optional[FileCache] = None) -> List[Signal]:
    """unauth-write-route and no-health-endpoint signals.

    unauth-write-route: each write route gets an auth.Resolver verdict. "unprotected" and
    "unresolved" (auth inherited from somewhere the scan cannot follow, for example a router
    mounted or a settings module outside the scanned files) are reported; the code emits medium
    and low, but the registry ceiling (low) caps both, so `uncertain_reason` on an unresolved
    route is what tells them apart. "protected" and "public" routes are not reported.

    False-positive guards: routes in example/demo folders are skipped; public-by-design paths
    (login, signup, token, password reset, health, signed webhooks) are skipped; Spring Security
    filter chains, ASP.NET Core global policies, Rails controller filters and Laravel controller
    middleware are resolved by the auth adapters like every other framework; a top-level /status or /up counts as health but /x/:id/status does not;
    no-health-endpoint fires once per service (a folder with a server entry point and its own
    routes), anchored on the entry-point file, never on libraries.
    """
    by_rel = {f.rel: f for f in files}
    routes = [r for r in routes if not getattr(by_rel.get(r["file"]), "is_example", False)]
    signals: List[Signal] = []
    if not routes:
        return signals
    services = find_services(files, cache)
    roots = sorted(services, key=lambda r: (-(0 if r == "." else r.count("/") + 1), r))
    resolver = Resolver(files, _read, services, cache)
    for r in routes:
        if r["method"] not in WRITE_METHODS:
            continue
        verdict = resolver.resolve(r)
        if verdict.state not in (UNPROTECTED, UNRESOLVED):
            continue
        snippet = "%s %s" % (r["method"], r["path"])
        if verdict.state == UNRESOLVED:
            signals.append(Signal("unauth-write-route", "Security", "Critical", r["file"], r["line"],
                                  snippet, "low", uncertain_reason=verdict.evidence))
        else:
            signals.append(Signal("unauth-write-route", "Security", "Critical", r["file"], r["line"],
                                  snippet, "medium"))
    by_service: Dict[str, List[dict]] = {}
    for r in routes:
        svc = _service_of(r["file"], roots)
        if svc is not None:
            by_service.setdefault(svc, []).append(r)
    for svc in sorted(by_service):
        rs = by_service[svc]
        if any(is_health_path(r["path"]) for r in rs):
            continue
        entry_file, entry_line, code = services[svc]
        if code:
            anchor = (entry_file, entry_line)
        else:
            first = min(rs, key=lambda r: (r["file"].count("/"), r["file"], r["line"]))
            anchor = (first["file"], first["line"])
        signals.append(Signal("no-health-endpoint", "Observability", "Medium", anchor[0], anchor[1],
                              "no health endpoint in service " + svc, "medium"))
    return signals


register(RuleMeta(
    "unauth-write-route", "7", "Security", "Critical", "low",
    "Write route (POST/PUT/PATCH/DELETE) with no detected authentication. Routes whose auth is "
    "inherited from somewhere the scan cannot follow carry uncertain_reason. Always low confidence (the rule ceiling).",
    "Auth is detected by substring match on markers (auth, jwt, protect, guard, verify_token, "
    "current_*user, ...), so words like author, protect or guard can over-detect auth; string "
    "contents and a bare trailing handler name in the route call are not read. Express "
    "route-call middleware and Express/Go use() arguments match auth only as a whole word or "
    "camelCase part and read callee names, not call arguments (authValidation, authorController, "
    "a mounted authRouter or routes module, cors({allowedHeaders: ['Authorization']}) and "
    "passport.initialize() do not count). Framework "
    "adapters (dfscan/auth/) follow auth beyond the route line: Express x.use(auth) before "
    "the route on the same receiver (a path argument limits it to routes under that path; a "
    "path the scan cannot read or an express-jwt .unless() exemption list makes the route "
    "unresolved unless a literal list exempts it), router-level router.use(auth), and where a "
    "router is mounted (app.use(path, "
    "auth, router), through require/import, index-file routers and module.exports = (app) => "
    "{...} callers; passport.use(...) and x.use(new Strategy(...)) register strategies and do "
    "not count; use(auth) in an imported module counts only on the object it exports); NestJS "
    "@UseGuards with an auth-like guard name (not ThrottlerGuard) or another auth decorator on "
    "the handler or controller (@Api* Swagger decorators never count) and global guards of the "
    "route's own app (app.useGlobalGuards, APP_GUARD with an auth-like guard name; a guard in "
    "another app is ignored and one in a shared folder makes the route unresolved; "
    "@Public()-style decorators "
    "then mark a route public); Flask before_request hooks that check auth (blueprint or "
    "app-wide); FastAPI Depends in the route, the def signature, APIRouter(dependencies=...), "
    "include_router(..., dependencies=...) up the include chain and FastAPI(dependencies=...); "
    "Django/DRF view mixins, decorators (on the class, a handler or dispatch(); "
    "@login_not_required opts out of LoginRequiredMiddleware) and permission_classes (AllowAny "
    "is public, any other permission or ownership class counts as a check), REST_FRAMEWORK "
    "DEFAULT_PERMISSION_CLASSES and LoginRequiredMiddleware in settings; Gin/Echo/chi "
    "x.Use(auth) on the same receiver in the same function or closure (a closure parameter "
    "that shadows r is another receiver), Group(path, auth...) and r.With(auth), followed into "
    "route functions through their callers (pkg.Register(v1.Group(...)) after v1.Use(auth); "
    "import aliases resolved). Django routes take their "
    "methods from the view (DRF generics/viewsets, handler methods, @api_view, "
    "@require_http_methods; @action extra routes are not listed); unresolved views and views "
    "wrapped in another call stay method ANY and are never flagged. A route is unresolved "
    "(uncertain_reason) when its router is exported but mounted in no scanned file, "
    "its router or app is imported from a file outside the scan, its route function's caller is "
    "not scanned, a view's base class is not scanned, Django settings or the NestJS "
    "bootstrap/modules or the Flask app are outside the scan, or auth middleware is applied in "
    "another file of the same service and which routes it covers is not resolved. Multi-line "
    "registrations are read (Express app<newline>.post(...) chains, Go r.POST(<newline>\"/x\", "
    "...)); Express receivers are app, server, r, *router and names bound to express() or a "
    "Router in the file, and x.route(path).post(...).put(...) chains are listed. Inline handler bodies, dynamic route loaders, gateway auth and in-handler checks "
    "(if (!req.user) ...) are not read. Routes whose LAST path segment is login, logout, "
    "signin/sign-in, signup/sign-up, register, token, refresh, password-reset, reset-password, "
    "password/reset, forgot-password, password-recovery, verify-email (or their _ spellings), "
    "session(s), passwords (Rails 8 generated auth), sign_in/sign_up (or whose second-to-last "
    "segment is one of these and the last is a parameter or a "
    "token/confirm/verify/callback-style step, or that end in password/reset), routes ending in "
    "a token parameter after a reset/confirm/verify/activate/password segment "
    "(reset/<uidb64>/<token>/) are public by design and skipped (a registration handler "
    "mounted at / is still flagged; refresh-token(s) counts like refresh); register, token, "
    "refresh, session(s) and passwords count "
    "only after nothing, an API/version segment or an auth-like segment (auth, oauth, users, "
    "account, token, jwt ...), and a path parameter before the segment or another resource "
    "segment (/devices/register, /users/:id/token) makes the route unresolved instead; nothing "
    "under an admin segment is public except its login/logout. Path prefixes applied in another "
    "file (an Express mount path, APIRouter(prefix=...)) are not joined for this check. A POST "
    "to a webhook path with no path parameter is public when a signature is verified "
    "(constructEvent, verify*(), compare_digest, timingSafeEqual ...; not createHmac) in its "
    "inline handler, or anywhere in the file for a provider path below the webhook segment "
    "(/webhooks/stripe). Write routes never get the health exemption. "
    "Spring, ASP.NET Core, Rails and Laravel use framework markers at the route: "
    "@PreAuthorize/@Secured/@RolesAllowed, [Authorize] ([AllowAnonymous] wins) or "
    "RequireAuthorization(), before_action :authenticate.../:authorize.../:require_admin, "
    "CanCanCan load_and_authorize_resource/authorize_resource and Pundit after_action "
    ":verify_authorized in the controller, its included concerns or a parent controller "
    "(resolved through enclosing `module` blocks, then top-level; only:/except: read), and "
    "->middleware('auth...') on the route or an enclosing Route group ('auth...:optional' "
    "does not count). Their adapters add: Spring Security filter chains "
    "(authorizeHttpRequests/authorizeRequests/authorizeExchange, Java and Kotlin DSL) read as "
    "ordered matchers, first match wins (permitAll()/anonymous() public; authenticated(), "
    "hasRole(...), access(...) protected; same-file String/String[] constants resolved; "
    "web.ignoring() public; securityMatcher scopes a chain; regexMatchers and chains that "
    "disagree are unresolved, as is a route no matcher covers); ASP.NET Core FallbackPolicy "
    "(every endpoint), a global AuthorizeFilter or MapControllers().RequireAuthorization() "
    "(controllers), with [AllowAnonymous]/.AllowAnonymous() public under them or a class-level "
    "[Authorize]; Laravel controller middleware ($this->middleware('auth')->only/except(...) "
    "and Laravel 11 HasMiddleware new Middleware('auth', only:/except:), parents followed) and "
    "the middleware of the group that loads the route file (RouteServiceProvider, a requiring "
    "route file, Kernel $middlewareGroups). Rails `resources` write routes whose scanned "
    "controller (with its scanned parents and included modules, followed 3 levels through "
    "app/controllers, app/helpers, app/models/concerns and lib) defines no such action are not "
    "listed; a controller with no methods, metaprogramming (method_missing, define_method, "
    "inherit_resources, alias), an unscanned parent, an include that is not scanned or an "
    "include line naming several modules keeps them (Rails framework modules define none). "
    "Security configuration applies only within the route's own app (the folder with its "
    "pom.xml, build.gradle, *.csproj, composer.json or Gemfile) or service; a Spring filter "
    "chain found only in another module makes the route unresolved. ASP.NET Core, Rails and "
    "Laravel actions named register/login/signup/password-reset are public by design (never "
    "under an admin path, controller or namespace), and "
    "(Spring, ASP.NET Core, Laravel) so is a POST to /users (/accounts, /members; never under "
    "an admin segment) beside an unauthenticated /users/login in the same file when its handler "
    "is registration-like: such an action name, an Auth/Account/Registration controller, or the "
    "controller serving that login itself (never an Admin-named controller). Unresolved for these frameworks: a Spring app whose Spring Security "
    "configuration (or @SpringBootApplication class) is not scanned, an ASP.NET Core app whose "
    "Program.cs/Startup.cs is not scanned, a Rails controller (or a parent) not scanned, a "
    "Laravel route file loaded by nothing scanned or whose controller is not scanned. "
    "Class-level Spring/ASP.NET prefixes and auth come from the innermost class whose body "
    "contains the handler; multi-line Rails `{ }` blocks and Laravel route files with "
    "unbalanced brackets may be misread; Spring method-security annotations on a superclass "
    "or interface, ASP.NET policies set through conventions or a custom "
    "IAuthorizationPolicyProvider, Laravel middleware added in bootstrap/app.php "
    "withMiddleware(), and prefixes applied in another file are not seen. Routes in "
    "examples/demo folders are skipped.",
    "Require authentication and authorisation on every write route.",
    ["Python", "JavaScript", "TypeScript", "Go", "Java", "Kotlin", "C#", "Ruby", "PHP"]))
register(RuleMeta(
    "no-health-endpoint", "5", "Observability", "Medium", "medium",
    "Service (folder with a server entry point and its own routes) with no health, ready, live, "
    "ping or heartbeat endpoint (or a top-level /status or /up).",
    "Only folders with a recognised server entry point (app.listen, uvicorn.run, app.run, "
    "http.ListenAndServe, gin.Default(), NestFactory.create, an app factory in a main/app/server/"
    "index file, manage.py, a file named exactly Dockerfile with CMD/EXPOSE) count as services, so "
    "serverless handlers, a Flask create_app() factory in __init__.py, Dockerfile variants "
    "(Dockerfile.prod, *.dockerfile), Procfile/gunicorn-only setups and routers mounted by an "
    "unrecognised entry point are missed; libraries never get it. Health "
    "checks served by a sidecar, platform probe or prefix-mounted router (path joined outside the "
    "file) are not seen. Spring, ASP.NET Core, Rails and Laravel apps count only when a "
    "Dockerfile marks the folder as a service (their own entry points are not recognised), and "
    "Spring Boot Actuator health endpoints are not seen. Routes in examples/demo folders are "
    "skipped.",
    "Expose /health and /ready endpoints for orchestrators.",
    ["Python", "JavaScript", "TypeScript", "Go", "Java", "Kotlin", "C#", "Ruby", "PHP"]))
