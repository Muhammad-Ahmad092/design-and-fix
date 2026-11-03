from __future__ import annotations

import posixpath
import re
from typing import Dict, List, Optional, Set, Tuple

from .frameworks import FRAMEWORK_EXTS, find_framework_routes, global_auth_policy
from .imports import _read
from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .text import strip_comments

AUTH_MARKERS = re.compile(
    r"auth|requireauth|authenticate|jwt|protect|passport|login_required|"
    r"permission_required|get_current_user|current_user|isauthenticated|"
    r"guard|verifytoken|bearer",
    re.I,
)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HEALTH_RE = re.compile(
    r"(^|/)(health|healthz|health[-_]?checks?|ready|readyz|readiness|live|livez|liveness|alive"
    r"|ping|heartbeat)/?$", re.I)
# `status` and `up` (the Rails 7.1 / Laravel 11 default health route) are health routes only at
# the top level (/status, /api/status, /api/v1/status, /up); /orders/:id/status is a resource.
STATUS_RE = re.compile(r"^/?(?:api/)?(?:v\d+/)?(?:status|up)/?$", re.I)
# Endpoints that are public by design (unauth-write-route never flags them). Matched on whole
# path segments of the route path only (never the handler name or a comment on the line).
PUBLIC_AUTH_RE = re.compile(
    r"(?:^|/)(?:login|logout|signin|signup|sign-in|sign-up|register|token|refresh|password-reset"
    r"|reset-password|forgot-password|password-recovery|verify-email|session|sessions|passwords"
    r"|sign_in|sign_up)(?:/|$)", re.I)


def is_health_path(path: str) -> bool:
    p = path.rstrip("/") or "/"
    return bool(HEALTH_RE.search(p) or STATUS_RE.match(p))

_Q = r"""['"`]"""
_JS_RE = re.compile(
    r"\b(?:app|router|server|r)\.(get|post|put|patch|delete)\(\s*(" + _Q + r")([^'\"`]*)\2(.*)")
_NEST_CTRL_RE = re.compile(r"@Controller\(\s*(?:" + _Q + r"([^'\"`]*)" + _Q + r")?")
_NEST_RE = re.compile(r"@(Get|Post|Put|Patch|Delete)\(\s*(?:" + _Q + r"([^'\"`]*)" + _Q + r")?(.*)")
_PY_VERB_RE = re.compile(r"^\s*@(\w+)\.(get|post|put|patch|delete)\(\s*(['\"])([^'\"]*)\3(.*)")
_PY_ROUTE_RE = re.compile(r"^\s*@(\w+)\.route\(\s*(['\"])([^'\"]*)\2(.*)")
_DJANGO_RE = re.compile(r"(?<![\w.])(?:re_)?path\(\s*r?(['\"])([^'\"]*)\1(.*)")
_GO_RECV = r"(?:r|g|e|app|api|mux|router|group|v\d+|\w*[Gg]roup|\w*[Rr]outer)"
_GO_GROUP_RE = re.compile(r"\b(\w+)\s*:?=\s*(\w+)\.Group\(\s*\"([^\"]*)\"([^\n]*)")
_GO_HANDLE_RE = re.compile(r"\bhttp\.HandleFunc\(\s*\"([^\"]*)\"(.*)")
_USE_RE = re.compile(r"\b(\w+)\.[Uu]se\((.*)")
# Strategy registration protects nothing: passport.use(new JwtStrategy(...)) or
# x.use('jwt', new Strategy(...)).
_STRATEGY_ARG_RE = re.compile(r"\s*(?:new\s|(['\"`])[^'\"`]*\1\s*,\s*new\s)")
_METHODS_RE = re.compile(r"methods\s*=\s*[\[(]([^\])]*)[\])]", re.I)
_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s")
_TRIPLE_RE = re.compile(r"(?s)(\"\"\"|''')(.*?)\1")

# Server entry points (spec R2-fp3), matched on text with comments and string contents blanked.
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


def _has_auth(text: str) -> bool:
    return bool(AUTH_MARKERS.search(text))


def _auth_use(ln: str) -> Optional[str]:
    """Receiver of an `x.use(auth...)` / `g.Use(AuthMiddleware())` call on this line, or None.
    Strategy registration (`passport.use(...)`, `x.use(new Strategy(...))`) is not middleware."""
    m = _USE_RE.search(ln)
    if not m or m.group(1).lower() == "passport" or _STRATEGY_ARG_RE.match(m.group(2)):
        return None
    return m.group(1) if _has_auth(m.group(2)) else None


def _kind(f: FileInfo) -> Optional[str]:
    if f.lang is None:
        return None
    if f.ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        return "js"
    if f.ext == ".py":
        return "py"
    if f.ext == ".go":
        return "go"
    return None


def _blank(m: "re.Match[str]") -> str:
    return m.group(1) + re.sub(r"[^\n]", " ", m.group(2)) + m.group(1)


def _code_text(text: str, kind: str) -> str:
    """Comment-stripped text (line numbers kept); Python triple-quoted strings (docstrings)
    are blanked too, so examples in docstrings are not read as routes or entry points."""
    lang = "Python" if kind == "py" else "JavaScript"
    out = strip_comments(text, lang)
    return _TRIPLE_RE.sub(_blank, out) if kind == "py" else out


def _lines(text: str) -> List[str]:
    """Split on "\\n" only (the scanner's line numbering), dropping a trailing "\\r"."""
    return [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]


def _blank_strings(text: str) -> str:
    """Blank the contents of single-line string literals ('...', "...", `...`)."""
    return re.sub(r"(['\"`])(?:\\.|(?!\1)[^\\\n])*\1", lambda m: m.group(1) * 2, text)


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


def _py_stack_auth(lines: List[str], idx: int) -> bool:
    """Decorators directly above/below the route line, plus the def signature."""
    j = idx - 1
    while j >= 0 and lines[j].strip().startswith("@"):
        if _has_auth(lines[j]):
            return True
        j -= 1
    k = idx + 1
    while k < len(lines) and lines[k].strip().startswith("@"):
        if _has_auth(lines[k]):
            return True
        k += 1
    if k < len(lines) and _DEF_RE.match(lines[k]):
        return _has_auth(_def_params(lines, k))
    return False


def _nest_stack_auth(lines: List[str], idx: int) -> bool:
    j = idx - 1
    while j >= 0 and lines[j].strip().startswith("@"):
        if _has_auth(lines[j]):
            return True
        j -= 1
    # Class-level decorators above the enclosing class declaration.
    c = idx - 1
    while c >= 0 and not _CLASS_RE.match(lines[c]):
        c -= 1
    c -= 1
    while c >= 0 and lines[c].strip().startswith("@"):
        if _has_auth(lines[c]):
            return True
        c -= 1
    return False


def _route(method: str, path: str, f: FileInfo, line: int, framework: str, auth: bool) -> dict:
    return {"method": method, "path": path, "file": f.rel, "line": line,
            "framework": framework, "auth_detected": auth}


def _js_routes(f: FileInfo, lines: List[str]) -> List[dict]:
    out: List[dict] = []
    use_auth = False
    prefix = ""
    for i, ln in enumerate(lines):
        if _is_comment(ln.strip()):
            continue
        if _auth_use(ln):
            use_auth = True
        m = _NEST_CTRL_RE.search(ln)
        if m:
            prefix = (m.group(1) or "").strip("/")
        m = _JS_RE.search(ln)
        if m:
            out.append(_route(m.group(1).upper(), m.group(3), f, i + 1, "express",
                              use_auth or _has_auth(m.group(4))))
            continue
        m = _NEST_RE.search(ln)
        if m:
            sub = (m.group(2) or "").strip("/")
            path = "/" + "/".join(p for p in (prefix, sub) if p)
            out.append(_route(m.group(1).upper(), path, f, i + 1, "nestjs",
                              _nest_stack_auth(lines, i) or _has_auth(m.group(3))))
    return out


def _py_routes(f: FileInfo, lines: List[str], text: str) -> List[dict]:
    out: List[dict] = []
    fw = "fastapi" if "fastapi" in text.lower() else "flask"
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            continue
        m = _PY_VERB_RE.match(ln)
        if m:
            auth = _py_stack_auth(lines, i) or _has_auth(m.group(5))
            out.append(_route(m.group(2).upper(), m.group(4), f, i + 1, fw, auth))
            continue
        m = _PY_ROUTE_RE.match(ln)
        if m:
            auth = _py_stack_auth(lines, i) or _has_auth(m.group(4))
            mm = _METHODS_RE.search(m.group(4))
            methods = re.findall(r"[A-Za-z]+", mm.group(1)) if mm else []
            for meth in [x.upper() for x in methods] or ["GET"]:
                out.append(_route(meth, m.group(3), f, i + 1, fw, auth))
            continue
        m = _DJANGO_RE.search(ln)
        if m:
            out.append(_route("ANY", m.group(2), f, i + 1, "django", _has_auth(m.group(3))))
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
    up_re = re.compile(r"\b(" + recv + r")\.(GET|POST|PUT|PATCH|DELETE)\(\s*\"(/[^\"]*)\"(.*)")
    chi_re = re.compile(r"\b(" + recv + r")\.(Get|Post|Put|Patch|Delete)\(\s*\"(/[^\"]*)\"(.*)")
    state: Dict[str, Tuple[str, str, bool]] = {}  # var -> (parent, prefix, group middleware auth)
    used: Set[str] = set()  # receivers with a prior `x.Use(auth...)`

    def info(var: str) -> Tuple[str, str, bool]:
        return state.get(var) or first.get(var) or ("", "", False)

    def authed(var: str) -> bool:
        """Group middleware or Use(auth) on the receiver or any parent group."""
        for _ in range(16):
            parent, _, mw = info(var)
            if mw or var in used:
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
        receiver = _auth_use(ln)
        if receiver:
            used.add(receiver)
        for rx, fw in ((up_re, upper_fw), (chi_re, "chi")):
            m = rx.search(ln)
            if m:
                path = _join(info(m.group(1))[1], m.group(3))
                out.append(_route(m.group(2).upper(), path, f, i + 1, fw,
                                  authed(m.group(1)) or _has_auth(m.group(4))))
                break
        else:
            m = _GO_HANDLE_RE.search(ln)
            if m:
                out.append(_route("ANY", m.group(1), f, i + 1, "net/http",
                                  bool(used) or _has_auth(m.group(2))))
    return out


def find_routes(files: List[FileInfo]) -> List[dict]:
    """Route inventory. Read from comment-stripped text (Python docstrings blanked) so routes
    shown as examples in comments or docstrings are not listed. Java/Kotlin, C#, Ruby and PHP
    files go to frameworks.find_framework_routes (Spring, ASP.NET Core, Rails, Laravel)."""
    routes: List[dict] = []
    for f in files:
        if f.is_test:
            continue
        kind = _kind(f)
        if kind is None:
            if f.lang is not None and f.ext in FRAMEWORK_EXTS:
                routes.extend(find_framework_routes(f, _read(f), files))
            continue
        text = _read(f)
        # Stripping only blanks text, so a file with no route candidate in its raw lines has
        # none after stripping either: skip the (slower) strip for those files.
        if _routes_in(f, kind, _lines(text), text):
            routes.extend(_routes_in(f, kind, _lines(_code_text(text, kind)), text))
    return routes


def _routes_in(f: FileInfo, kind: str, lines: List[str], text: str) -> List[dict]:
    if kind == "js":
        return _js_routes(f, lines)
    if kind == "py":
        return _py_routes(f, lines, text)
    return _go_routes(f, lines, text)


# ---------------------------------------------------------------- services (R2-fp3)
Entry = Tuple[str, int, bool]  # (file, line, anchor-worthy code file)


def find_services(files: List[FileInfo]) -> Dict[str, Entry]:
    """Service root folder ("." for the scan root) -> its entry point.

    A folder is a service root when it holds a server entry point: app.listen/uvicorn.run/app.run/
    http.ListenAndServe/gin.Default()/NestFactory.create, a framework app factory (FastAPI(),
    Flask(...), express()) in a main/app/server/index file, a Django manage.py, or a Dockerfile with
    CMD/ENTRYPOINT/EXPOSE. Test and example files never count.
    """
    found: Dict[str, List[Tuple[int, str, int, bool]]] = {}

    def add(f: FileInfo, line: int, code: bool) -> None:
        folder = posixpath.dirname(f.rel) or "."
        found.setdefault(folder, []).append((0 if code else 1, f.rel, line, code))

    for f in files:
        if f.is_test or f.is_example:
            continue
        name = posixpath.basename(f.rel)
        if name == "Dockerfile":
            m = _DOCKER_ENTRY_RE.search(_read(f))
            if m:
                add(f, 0, False)
            continue
        kind = _kind(f)
        if kind is None:
            continue
        raw = _read(f)
        stem = name.rsplit(".", 1)[0]
        factory = _FACTORY_RES.get(kind) if stem in _ENTRY_STEMS else None
        if name == "manage.py":
            if _MANAGE_RE.search(raw):
                add(f, 0, False)
            continue
        if not (_ENTRY_RES[kind].search(raw) or (factory and factory.search(raw))):
            continue
        code = _blank_strings(_code_text(raw, kind))
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


def _auth_use_files(files: List[FileInfo]) -> Set[str]:
    """Files that apply auth middleware (`x.use(auth...)` / `v1.Use(AuthMiddleware...)`)."""
    out: Set[str] = set()
    for f in files:
        kind = _kind(f)
        if f.is_test or f.is_example or kind not in ("js", "go"):
            continue
        raw = _read(f)
        if not re.search(r"\.[Uu]se\(", raw):
            continue
        for ln in _lines(_code_text(raw, kind)):
            if _auth_use(ln):
                out.add(f.rel)
                break
    return out


def _global_auth_files(files: List[FileInfo]) -> List[str]:
    """Files with a Spring Security filter chain or an ASP.NET Core global auth policy."""
    return sorted(f.rel for f in files
                  if not f.is_test and not f.is_example and f.ext in (".java", ".kt", ".cs")
                  and global_auth_policy(f, _read(f)))


def route_signals(routes: List[dict], files: List[FileInfo]) -> List[Signal]:
    """unauth-write-route and no-health-endpoint signals.

    False-positive guards: routes in example/demo folders are skipped; routes whose PATH has a
    whole login/signup/register/token/password-reset/... segment are public by design (handler
    names and comments are not read); auth middleware applied in another file of the same service
    lowers confidence (outside any service it applies to its own file only); a Spring Security
    filter chain or ASP.NET Core global policy lowers confidence for Java/Kotlin/C# routes; a
    top-level /status or /up counts as health but /x/:id/status does not; no-health-endpoint fires once per service (a folder with a server
    entry point and its own routes), anchored on the entry-point file, never on libraries.
    """
    by_rel = {f.rel: f for f in files}
    routes = [r for r in routes if not getattr(by_rel.get(r["file"]), "is_example", False)]
    signals: List[Signal] = []
    if not routes:
        return signals
    services = find_services(files)
    roots = sorted(services, key=lambda r: (-(0 if r == "." else r.count("/") + 1), r))
    unauth = [r for r in routes if r["method"] in WRITE_METHODS and not r["auth_detected"]
              and not PUBLIC_AUTH_RE.search(r["path"])]
    use_by_service: Dict[str, List[str]] = {}
    if unauth and services:
        for rel in sorted(_auth_use_files(files)):
            svc = _service_of(rel, roots)
            if svc is not None:
                use_by_service.setdefault(svc, []).append(rel)
    policies: List[str] = []
    if any(r["file"].endswith((".java", ".kt", ".cs")) for r in unauth):
        policies = _global_auth_files(files)
    for r in unauth:
        # Cross-file middleware only lowers confidence inside one service; outside any service
        # (no entry point found) a .use(auth) applies to its own file only.
        svc = _service_of(r["file"], roots)
        elsewhere = [u for u in use_by_service.get(svc, []) if u != r["file"]] if svc else []
        snippet = "%s %s" % (r["method"], r["path"])
        # A Spring/ASP.NET global auth policy lowers confidence for routes of that ecosystem in
        # the same service (anywhere in the scan when no service is recognised).
        policy = [p for p in policies if r["file"].endswith((".java", ".kt", ".cs"))
                  and p.endswith(".cs") == r["file"].endswith(".cs")
                  and (svc is None or _service_of(p, roots) == svc)]
        if policy:
            signals.append(Signal("unauth-write-route", "Security", "Critical", r["file"], r["line"],
                                  snippet + " (global auth policy in %s)" % policy[0], "low"))
        elif elsewhere:
            signals.append(Signal("unauth-write-route", "Security", "Critical", r["file"], r["line"],
                                  snippet + " (auth middleware applied in %s)" % elsewhere[0], "low"))
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
    "unauth-write-route", "6", "Security", "Critical", "low",
    "Write route (POST/PUT/PATCH/DELETE) with no detected authentication.",
    "Auth is detected by substring match on markers (auth, jwt, protect, guard, ...), so words "
    "like author, protect or guard can over-detect auth; one x.use(auth...) marks later routes in "
    "that file as authed (Go: later routes on that receiver and its groups; Gin/Echo "
    "Group(path, middleware) auth covers the group's routes; passport.use(...) and "
    "x.use(new Strategy(...)) register strategies and do not count), and auth middleware applied in another file of the same service (a "
    "folder with a recognised entry point) lowers the signal to low confidence (which routes it "
    "covers is not resolved; outside a service a .use applies to its own file only); gateway-level auth is "
    "not seen; Django routes are method ANY and never flagged. Routes whose path has a whole "
    "segment login, logout, signin/sign-in, signup/sign-up, register, token, refresh, "
    "password-reset, reset-password, forgot-password, password-recovery, verify-email, "
    "session(s), passwords (Rails 8 generated auth) or sign_in/sign_up are "
    "treated as public by design and skipped (a registration handler mounted at / is still "
    "flagged). "
    "Spring, ASP.NET Core, Rails and Laravel use framework markers instead: @PreAuthorize/"
    "@Secured/@RolesAllowed, [Authorize] ([AllowAnonymous] wins) or RequireAuthorization(), "
    "before_action :authenticate... in the controller or a parent controller (resolved through "
    "enclosing `module` blocks, then top-level), and ->middleware('auth...') on the route or an "
    "enclosing Route group. Class-level Spring/ASP.NET prefixes and auth come from the innermost "
    "class whose body contains the handler; multi-line Rails `{ }` blocks and Laravel route "
    "files with unbalanced brackets may be misread. A Spring Security filter "
    "chain or ASP.NET Core global policy (FallbackPolicy, RequireAuthenticatedUser, "
    "MapControllers().RequireAuthorization()) lowers Java/Kotlin/C# routes to low confidence "
    "(permitAll() matchers are not read); Laravel controller-constructor middleware and prefixes "
    "applied in another file are not seen. "
    "Routes in examples/demo folders are skipped.",
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
