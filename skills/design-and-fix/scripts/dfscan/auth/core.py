"""Core of auth resolution for unauth-write-route, for every route of the inventory (routes.py
and frameworks.py).

`resolve(route, files, read)` answers one question for a route from the inventory: is it
protected, public by design, unprotected, or unresolved (security inherited from somewhere this
scan cannot follow)? Each framework has one adapter, looked up by the route's `framework` name in
`ADAPTERS`, so another framework is one more entry there.

Adapters follow the auth that applies to a route beyond its own line: the router or app it is
registered on, where that router is mounted (Express `x.use(...)`, FastAPI `include_router`, a Go
route function's callers), framework-wide hooks (Flask `before_request`, NestJS global guards,
Django `MIDDLEWARE` and DRF `DEFAULT_PERMISSION_CLASSES`, Spring Security filter chains, ASP.NET
Core global policies, Laravel controller middleware and route-file groups). An adapter returns
"unresolved" when it sees signs of central auth it cannot follow: a router or app factory
imported from a file that is not scanned, a router never mounted in the scanned files, a route
function whose caller is not scanned, Django settings, a Spring Security configuration, an
ASP.NET Core startup, a Rails controller or a Laravel RouteServiceProvider outside the scanned
files.
"""
from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Callable, Dict, List, NamedTuple, Optional, Tuple

from ..filecache import FileCache, read_file
from ..imports import _js_resolve
from ..model import FileInfo
from ..text import strip_comments

if TYPE_CHECKING:  # pragma: no cover
    from .django_views import DjangoViews

PROTECTED = "protected"
PUBLIC = "public"
UNPROTECTED = "unprotected"
UNRESOLVED = "unresolved"


class AuthVerdict(NamedTuple):
    state: str      # protected | public | unprotected | unresolved
    evidence: str   # what was inspected (the uncertain_reason for "unresolved")


Read = Callable[[FileInfo], str]

# ---------------------------------------------------------------- markers and public paths
AUTH_MARKERS = re.compile(
    r"auth|requireauth|authenticate|jwt|protect|passport|login_required|"
    r"permission_required|current_\w*user|isauthenticated|"
    r"guard|verify_?token|bearer",
    re.I,
)
# Stricter marker test for middleware names and call text (Express/Go use(), route-call
# middleware): `auth` must be a whole word or a camelCase/snake_case part followed by no lower-case
# letter, so authValidation, authorController and authRouter do not count while auth('x'),
# requireAuth, isAuthenticated, AuthMiddleware() and verify_token do.
_AUTH_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9])auth0?(?![A-Za-z0-9])|authenticat|authoriz|jwt|passport|bearer|protect|guard"
    r"|login_?required|permission_?required|current_\w*user|verify_?token", re.I)
_AUTH_CAMEL_RE = re.compile(
    r"(?<=[a-z0-9])Auth(?![a-z])|(?i:auth)(?:Middleware|Guard|Required|Handler|Filter|Check|Mw|Hook)")
_USE_RE = re.compile(r"\b(\w+)\.[Uu]se\((.*)")
# Strategy registration protects nothing: passport.use(new JwtStrategy(...)) or
# x.use('jwt', new Strategy(...)).
_STRATEGY_ARG_RE = re.compile(r"\s*(?:new\s|(['\"`])[^'\"`]*\1\s*,\s*new\s)")

HEALTH_RE = re.compile(
    r"(^|/)(health|healthz|health[-_]?checks?|ready|readyz|readiness|live|livez|liveness|alive"
    r"|ping|heartbeat)/?$", re.I)
# `status` and `up` (the Rails 7.1 / Laravel 11 default health route) are health routes only at
# the top level (/status, /api/status, /api/v1/status, /up); /orders/:id/status is a resource.
STATUS_RE = re.compile(r"^/?(?:api/)?(?:v\d+/)?(?:status|up)/?$", re.I)
# Endpoints that are public by design: one whole path segment, matched only at the end of the
# route path (see is_public_path; never the handler name or a comment on the line).
PUBLIC_AUTH_RE = re.compile(
    r"^(?:login|logout|signin|signup|sign-in|sign-up|register|token|refresh|refresh[-_]tokens?|password[-_]reset"
    r"|reset[-_]password|forgot[-_]password|password[-_]recovery|verify[-_]email|session|sessions"
    r"|passwords|sign_in|sign_up)$", re.I)
# A segment that may follow a public one: /login/access-token, /passwords/:token, /signup/confirm.
_PUBLIC_TAIL_RE = re.compile(
    r"^(?:[:*].*|<.*>|\{.*\}|[\w-]*(?:token|confirm|verify|callback|done|new|request|email)[\w-]*)$",
    re.I)
_PASSWORD_PAIR_RE = re.compile(r"(?:^|/)password/(?:reset|forgot|recover|recovery)$", re.I)
# A token parameter as the final segment after reset/confirm/verify/activate/password segments
# (only parameters in between): a password-reset or email-confirmation link such as
# reset/<uidb64>/<token>/ or /users/confirm/:token.
_TOKEN_LINK_RE = re.compile(
    r"(?:^|/)[\w-]*(?:reset|confirm|verif|activat|password)[\w-]*/(?:(?:[:<{][^/]*)/)*"
    r"(?:<(?:\w+:)?token>|\{token\}|:token)$", re.I)
_WEBHOOK_RE = re.compile(r"(?:^|/)(?:webhooks?|hooks)(?:/|$)", re.I)
_WEBHOOK_LAST_RE = re.compile(r"(?:^|/)(?:webhooks?|hooks)/?$", re.I)
# Verification of an incoming signed request (not the signing of outgoing deliveries: createHmac).
_VERIFY_RE = re.compile(
    r"construct_?event|\bverify\w*\s*\(|\.verify\w*\s*\(|compare_digest|timingSafeEqual"
    r"|hmac\.Equal|hash_equals|MessageDigest\.isEqual|\bsvix\b|RequestValidator"
    r"|validate_?(?:signature|webhook|request)\w*\s*\(", re.I)
# Where the next route registration starts (ends the window read for an inline handler).
_ROUTE_START_RE = re.compile(
    r"^\s*@\w+(?:\.\w+)*\.(?:get|post|put|patch|delete|route|api_route)\s*\("
    r"|^\s*@(?:Get|Post|Put|Patch|Delete|Request)(?:Mapping)?\b|^\s*\[Http|\bRoute::"
    r"|\b(?:app|router|server|r|api|bp|blueprint|e|g|mux|\w*[Rr]outer|\w*[Gg]roup)\s*\.\s*"
    r"(?:get|post|put|patch|delete|route|all|GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch"
    r"|Delete|HandleFunc)\s*\(")
WRITE_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))


_SIGNUP_COLLECTION_RE = re.compile(r"(?:^|/)(?:users?|accounts?|members?)$", re.I)
_LOGIN_SEG_RE = re.compile(r"/(?:login|signin|sign_in|sign-in)$", re.I)
_ADMIN_SEG_RE = re.compile(r"(?:^|/)admin(?:/|$)", re.I)
_ADMIN_NAME_RE = re.compile(r"Admin|(?:^|[\\/:._])admin")
# Public-by-design segments that are also ordinary resource words (devices/register,
# cache/refresh, users/:id/token): public only right after nothing, an API or version segment or
# an auth-like segment.
_WEAK_PUBLIC_RE = re.compile(
    r"^(?:register|token|refresh|refresh[-_]tokens?|session|sessions|passwords)$", re.I)
_PUBLIC_PREV_RE = re.compile(
    r"^(?:api|rest|v\d+(?:\.\d+)*|auth|oauth2?|users?|accounts?|me|identity|iam|token|jwt)$", re.I)
_PARAM_SEG_RE = re.compile(r"^(?:[:*].*|<.*>|\{.*\}|\[.*\])$")
_ADMIN_LOGIN_RE = re.compile(r"^(?:login|logout|signin|sign-in|sign_in)$", re.I)
_AUTH_CONTROLLER_RE = re.compile(r"^(?:Auth|Authentication|Account|Accounts|Registration"
                                 r"|Registrations|Register|Signup|SignUp)(?:Controller|Api)?$")
_APP_MANIFESTS = ("pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "Gemfile",
                  "artisan")


_PUBLIC_ACTION_RE = re.compile(
    r"^(?:register|signup|sign_up|signin|sign_in|login|log_in|logout|log_out"
    r"|(?:send_)?(?:password_reset|reset_password|forgot_password)(?:_link|_email)?)$")


def is_public_action(name: str) -> bool:
    """A controller action named like an authentication endpoint (register, login, signUp,
    resetPassword ...): public by design."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name or "").lower()
    return bool(_PUBLIC_ACTION_RE.match(snake))


def admin_context(route: dict, extra: Optional[dict] = None) -> bool:
    """The route sits under an `admin` path segment or an Admin controller/namespace."""
    if _ADMIN_SEG_RE.search(route.get("path", "") or ""):
        return True
    extra = extra or {}
    return any(_ADMIN_NAME_RE.search(extra.get(k, "") or "") for k in ("controller", "namespace"))


def public_action(route: dict, extra: dict) -> bool:
    """An authentication action (is_public_action) that is not under an admin path, controller
    or namespace: public by design. Admin\\DeviceController@register is an admin write."""
    return is_public_action(extra.get("action", "")) and not admin_context(route, extra)


def has_auth(text: str) -> bool:
    return bool(AUTH_MARKERS.search(text))


def has_auth_word(text: str) -> bool:
    """has_auth with whole-word `auth` (see _AUTH_WORD_RE): for middleware names and call text."""
    return bool(_AUTH_WORD_RE.search(text) or _AUTH_CAMEL_RE.search(text))


class UseInfo(NamedTuple):
    """What an `x.use(...)` call applies. `scope` is the literal mount path(s) (None: every path);
    `scope_known` is False when the first argument is a path this scan cannot read (a variable,
    a template or a regex). `unless` holds express-jwt `.unless({ path: [...] })` literal paths;
    `unless_known` is False when an unless() is present but not readable."""
    auth: bool
    scope: Optional[Tuple[str, ...]]
    scope_known: bool
    unless: Tuple[str, ...]
    unless_known: bool


_STR_LIT_RE = re.compile(r"""^(['"`])((?:\\.|(?!\1)[^\\])*)\1$""", re.S)
_PATHISH_NAME_RE = re.compile(r"(?:path|prefix|url|base|mount|root|route)s?$", re.I)
_ROUTER_NAME_RE = re.compile(r"(?:router|routes|routers|controller|controllers)$", re.I)
_ROUTES_SEG_RE = re.compile(r"^(?:routes?|routers?|controllers?|api|views?|pages?)$", re.I)
_REQUIRE_ARG_RE = re.compile(r"""^require\(\s*(['"`])([^'"`]*)\1\s*\)$""")
_UNLESS_RE = re.compile(r"\.\s*unless\s*\(")


def _str_lit(arg: str) -> Optional[str]:
    """The value of a plain string literal argument (no `${...}`), else None."""
    m = _STR_LIT_RE.match(arg.strip())
    if not m or "${" in m.group(2):
        return None
    return m.group(2)


def routes_spec(spec: str) -> bool:
    """A require/import specifier that names a routes/router/controller module."""
    segs = [x for x in spec.lower().split("/") if x not in ("", ".", "..")]
    if not segs:
        return False
    stem = re.sub(r"\.(?:[cm]?js|jsx|tsx?)$", "", segs[-1])
    return any(_ROUTES_SEG_RE.match(x) for x in segs[:-1]) or bool(
        _ROUTER_NAME_RE.search(stem) or re.search(r"[.\-_](?:routes?|router)$", stem))


def _middleware_auth(arg: str, routers: frozenset) -> bool:
    """Whether one use() argument is auth middleware: a call or name whose callee matches the
    markers (string contents never count), never a mounted router or a passport set-up call."""
    raw = arg.strip()
    a = blank_strings(raw).strip()
    if not a:
        return False
    if a.startswith("["):
        return any(_middleware_auth(x, routers) for x in split_args(raw.strip()[1:-1]))
    req = _REQUIRE_ARG_RE.match(raw)
    if req:
        spec = req.group(2)
        if routes_spec(spec) or ("require:" + spec) in routers:
            return False
        return has_auth_word(re.sub(r"[/.]+", " ", spec))
    if re.match(r"new\s", a):
        return False
    if a.startswith(("(", "function", "async")) or "=>" in a:
        return has_auth(a)  # an inline middleware function: its (string-blanked) body
    m = re.match(r"[\w$.]+", a)
    if not m:
        return has_auth_word(a)
    callee = m.group(0)
    if re.match(r"^passport\.(?:initialize|session)$", callee):
        return False
    rest = a[m.end():].lstrip()
    if not rest:  # a bare name: a router mounted without a path, or a middleware function
        if callee in routers or _ROUTER_NAME_RE.search(callee.split(".")[-1]):
            return False
        return has_auth_word(callee)
    # A call (or a chain of calls): the callee and later links, never the call arguments.
    links = [callee] + re.findall(r"\)\s*\.\s*([\w$]+)", a)
    return any(has_auth_word(x) for x in links if x != "unless")


def parse_use(args: str, routers: frozenset = frozenset()) -> UseInfo:
    """Read the argument text of an `x.use(...)` call (see UseInfo)."""
    parts = split_args(args)
    scope: Optional[Tuple[str, ...]] = None
    known = True
    if parts:
        first = parts[0].strip()
        lit = _str_lit(first)
        if lit is not None:
            scope, parts = (lit,), parts[1:]
        elif first.startswith("[") and first.endswith("]"):
            items = split_args(first[1:-1])
            lits = [_str_lit(x) for x in items]
            if items and all(x is not None for x in lits):
                scope, parts = tuple(x for x in lits if x is not None), parts[1:]
            elif items and all(re.match(r"^['\"`/]", x.strip()) for x in items):
                known, parts = False, parts[1:]
        elif re.match(r"^['\"`]", first) or (first.startswith("/") and len(parts) > 1):
            known, parts = False, parts[1:]  # a template string, a concatenation or a regex
        elif len(parts) > 1 and re.match(r"^[\w$.]+$", first) and \
                _PATHISH_NAME_RE.search(first.split(".")[-1]):
            known, parts = False, parts[1:]  # app.use(prefix, auth)
        elif len(parts) > 1 and re.match(r"^[\w$.]+\s*\+", blank_strings(first)):
            known, parts = False, parts[1:]
    auth = any(_middleware_auth(p, routers) for p in parts)
    unless: Tuple[str, ...] = ()
    unless_known = True
    m = _UNLESS_RE.search(args)
    if m and auth:
        inner = call_args([args], 0, m.end() - 1, max_lines=1)[0]
        pm = re.search(r"\bpath\s*:\s*(\[.*?\]|['\"`][^'\"`]*['\"`])", inner, re.S)
        if pm:
            val = pm.group(1)
            items = split_args(val[1:-1]) if val.startswith("[") else [val]
            lits = [_str_lit(x) for x in items]
            if all(x is not None for x in lits):
                unless = tuple(x for x in lits if x is not None)
            else:
                unless_known = False
        else:
            unless_known = False
    return UseInfo(auth, scope, known, unless, unless_known)


def use_info(recv: str, args: str, routers: frozenset = frozenset()) -> Optional[UseInfo]:
    """UseInfo of `recv.use(args)` when it applies auth middleware; None for strategy
    registration (`passport.use(...)`, `x.use(new Strategy(...))`) or no auth."""
    if recv.lower() == "passport" or _STRATEGY_ARG_RE.match(args):
        return None
    info = parse_use(args, routers)
    return info if info.auth else None


def auth_use(ln: str, routers: frozenset = frozenset()) -> Optional[str]:
    """Receiver of an `x.use(auth...)` / `g.Use(AuthMiddleware())` call on this line, or None.
    Strategy registration (`passport.use(...)`, `x.use(new Strategy(...))`), string contents,
    a mounted router (`app.use('/auth', authRouter)`, a name in `routers`) and non-auth calls
    such as cors({allowedHeaders: ['Authorization']}) are not auth middleware."""
    m = _USE_RE.search(ln)
    if not m:
        return None
    args = call_args([ln], 0, m.start(2) - 1, max_lines=1)[0]
    return m.group(1) if use_info(m.group(1), args, routers) else None


def _segs(path: str) -> List[str]:
    return [x for x in path.strip().split("/") if x]


def path_under(path: str, prefix: str) -> bool:
    """Whether route path `path` is at or below mount path `prefix` (a `:param` segment of the
    prefix matches any one segment)."""
    pre = _segs(prefix)
    segs = _segs(path)
    if len(pre) > len(segs):
        return False
    return all(p.startswith(":") or p == "*" or p == s for p, s in zip(pre, segs))


def join_path(prefix: str, sub: str) -> str:
    """Mount path + route path ("/api" + "/orders/:id")."""
    return "/" + "/".join(_segs(prefix) + _segs(sub))


def use_applies(info: UseInfo, path: Optional[str]) -> Optional[str]:
    """PROTECTED when the middleware covers a route at `path` (relative to the receiver), None
    when it does not, UNRESOLVED when that cannot be decided (an unknown mount path or exemption
    list, or the route path relative to the receiver is unknown)."""
    if info.unless or not info.unless_known:
        if path is None or not info.unless_known:
            return UNRESOLVED
        if any(_segs(path) == _segs(u) for u in info.unless):
            return None
    if info.scope is None:
        return PROTECTED if info.scope_known else UNRESOLVED
    if path is None:
        return UNRESOLVED
    return PROTECTED if any(path_under(path, s) for s in info.scope) else None


def public_path_verdict(path: str) -> Optional[AuthVerdict]:
    """PUBLIC when the path is an authentication endpoint by design: the last segment is a
    login/signup/token/... segment, or the one before it is and the last is a parameter or a
    token/confirm-style step; password/reset pairs; a final token parameter after a
    reset/confirm/verify/activate/password segment. Earlier segments never count, so
    /register/admin is not public. Never under an `admin` segment (None). UNRESOLVED ("named
    like an auth endpoint") when a path parameter comes before the segment (/users/:id/token)
    or a resource-word segment (register, token, refresh, session(s), passwords) follows
    something other than nothing, an API/version or an auth-like segment (/devices/register)."""
    p = path.strip().rstrip("/")
    segs = [x for x in p.split("/") if x]
    if not segs:
        return None
    n = len(segs)
    hit: Optional[int] = None
    if PUBLIC_AUTH_RE.match(segs[-1]):
        hit = n - 1
    elif n >= 2 and PUBLIC_AUTH_RE.match(segs[-2]) and _PUBLIC_TAIL_RE.match(segs[-1]):
        hit = n - 2
    elif _PASSWORD_PAIR_RE.search(p):
        hit = n - 2
    elif _TOKEN_LINK_RE.search(p):
        hit = -1
    if hit is None:
        return None
    if _ADMIN_SEG_RE.search(p) and not (hit >= 0 and _ADMIN_LOGIN_RE.match(segs[hit])):
        return None  # an admin write (an admin panel's own login/logout stays public)
    if hit >= 0:
        before = segs[:hit]
        weak = bool(_WEAK_PUBLIC_RE.match(segs[hit]))
        if any(_PARAM_SEG_RE.match(x) for x in before) or (
                weak and before and not _PUBLIC_PREV_RE.match(before[-1])):
            return AuthVerdict(UNRESOLVED, "%s is named like an auth endpoint but sits under a "
                                           "resource; whether it is public is not resolved" % path)
    return AuthVerdict(PUBLIC, "authentication endpoint (public by design)")


def is_public_path(path: str) -> bool:
    """public_path_verdict(path) is PUBLIC."""
    v = public_path_verdict(path)
    return v is not None and v.state == PUBLIC


def is_health_path(path: str) -> bool:
    p = path.rstrip("/") or "/"
    return bool(HEALTH_RE.search(p) or STATUS_RE.match(p))


# ---------------------------------------------------------------- source helpers
_TRIPLE_RE = re.compile(r"(?s)(\"\"\"|''')(.*?)\1")
_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def kind_of(f: FileInfo) -> Optional[str]:
    if f.lang is None:
        return None
    if f.ext in _JS_EXTS:
        return "js"
    if f.ext == ".py":
        return "py"
    if f.ext == ".go":
        return "go"
    return None


def _blank(m: "re.Match[str]") -> str:
    return m.group(1) + re.sub(r"[^\n]", " ", m.group(2)) + m.group(1)


def code_text(text: str, kind: str) -> str:
    """Comment-stripped text (line numbers kept); Python triple-quoted strings (docstrings)
    are blanked too, so examples in docstrings are not read as routes or entry points."""
    lang = "Python" if kind == "py" else "JavaScript"
    out = strip_comments(text, lang)
    return _TRIPLE_RE.sub(_blank, out) if kind == "py" else out


def file_code_text(f: FileInfo, kind: str, cache: Optional[FileCache] = None) -> str:
    """code_text of a file's text, through the scan's file cache when given."""
    if cache is None:
        return code_text(read_file(f), kind)
    if kind != "py":
        return cache.code(f, "JavaScript")
    return cache.derived(f, "code_text_py",  # type: ignore[return-value]
                         lambda: _TRIPLE_RE.sub(_blank, cache.code(f, "Python")))


def split_lines(text: str) -> List[str]:
    """Split on "\\n" only (the scanner's line numbering), dropping a trailing "\\r"."""
    return [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]


def blank_strings(text: str) -> str:
    """Blank the contents of single-line string literals ('...', "...", `...`)."""
    return re.sub(r"(['\"`])(?:\\.|(?!\1)[^\\\n])*\1", lambda m: m.group(1) * 2, text)


def call_args(lines: List[str], idx: int, col: int, max_lines: int = 15) -> Tuple[str, int]:
    """Text inside the parentheses opened at lines[idx][col] (lines joined with a space; string
    contents do not count as brackets) and the index of the line holding the closing paren.
    An unclosed call returns what was read within `max_lines` lines."""
    depth = 0
    parts: List[str] = []
    quote = ""
    for j in range(idx, min(len(lines), idx + max_lines)):
        ln = lines[j]
        start = col if j == idx else 0
        buf: List[str] = []
        k = start
        while k < len(ln):
            c = ln[k]
            if quote:
                if c == "\\":
                    buf.append(ln[k:k + 2])
                    k += 2
                    continue
                if c == quote:
                    quote = ""
            elif c in "'\"`":
                quote = c
            elif c in "([{":
                depth += 1
                if depth == 1 and j == idx and k == start:
                    k += 1
                    continue
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    parts.append("".join(buf))
                    return " ".join(parts), j
            buf.append(c)
            k += 1
        if quote in "'\"":
            quote = ""  # single-line strings end at the line end
        parts.append("".join(buf))
    return " ".join(parts), min(len(lines), idx + max_lines) - 1


def split_args(text: str) -> List[str]:
    """Top-level comma-separated arguments of a call's inner text."""
    out: List[str] = []
    depth = 0
    cur: List[str] = []
    quote = ""
    for c in text:
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(c)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _word(name: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])")


def _combine(verdicts: List[AuthVerdict]) -> AuthVerdict:
    """All paths to a route must be protected: any unprotected path wins, then unresolved."""
    for state in (UNPROTECTED, UNRESOLVED):
        for v in verdicts:
            if v.state == state:
                return v
    return verdicts[0]


# "Nothing beyond the route's own file was found": the resolver turns it into a final verdict
# (auth middleware applied in another file of the same service makes it unresolved).
_LOCAL = AuthVerdict(UNPROTECTED, "")
_MAX_DEPTH = 6


def _rooted(v: AuthVerdict, where: str) -> AuthVerdict:
    """A chain step that reached a root with no auth: definitely unprotected through `where`."""
    if v is _LOCAL or (v.state == UNPROTECTED and not v.evidence):
        return AuthVerdict(UNPROTECTED, "%s with no auth before it" % where)
    return v


# ---------------------------------------------------------------- Python helpers
_PY_ASSIGN = r"^\s*%s\s*(?::[^=\n]*)?=\s*(?:[\w.]+\.)?(%s)\s*\("
_PY_FROM_RE = re.compile(r"^[ \t]*from\s+([.\w]+)\s+import\s+(\([^)]*\)|[^\n]+)", re.M)
_PY_IMPORT_RE = re.compile(r"^[ \t]*import\s+([\w.]+)\s+as\s+(\w+)", re.M)


def _py_binds(text: str) -> Dict[str, Tuple[str, str]]:
    """Local name -> (module, imported name or "" for a module import)."""
    out: Dict[str, Tuple[str, str]] = {}
    for m in _PY_FROM_RE.finditer(text):
        for part in m.group(2).strip("()").replace("\n", " ").split(","):
            bits = part.split()
            if not bits or bits[0] == "*":
                continue
            local = bits[2] if len(bits) == 3 and bits[1] == "as" else bits[0]
            out[local] = (m.group(1), bits[0])
    for m in _PY_IMPORT_RE.finditer(text):
        out[m.group(2)] = (m.group(1), "")
    return out


def _py_module_path(importer: str, module: str) -> str:
    """Root-relative path stem (no extension) of a dotted, possibly relative, module."""
    if module.startswith("."):
        dots = len(module) - len(module.lstrip("."))
        base = posixpath.dirname(importer)
        for _ in range(dots - 1):
            base = posixpath.dirname(base)
        rest = module.lstrip(".").replace(".", "/")
        return posixpath.join(base, rest) if rest else base
    return module.replace(".", "/")


def _indent(ln: str) -> int:
    return len(ln) - len(ln.lstrip())


def _py_block(lines: List[str], idx: int, limit: int = 400) -> List[Tuple[int, str]]:
    """Lines of the indented block under the header at lines[idx] (header continuation lines of
    a multi-line signature are included)."""
    base = _indent(lines[idx])
    out: List[Tuple[int, str]] = []
    j = idx + 1
    # Skip the rest of a multi-line header (up to the line ending with ":").
    if not lines[idx].rstrip().endswith(":"):
        while j < len(lines) and j < idx + 15 and not lines[j - 1].rstrip().endswith(":"):
            j += 1
    while j < len(lines) and j < idx + limit:
        ln = lines[j]
        if ln.strip() and _indent(ln) <= base:
            break
        out.append((j, ln))
        j += 1
    return out


def _decorators_above(lines: List[str], idx: int) -> str:
    """Decorator text directly above lines[idx] (multi-line decorators included)."""
    ends: Dict[int, int] = {}  # last line of a decorator -> its first line
    i = max(0, idx - 80)
    while i < idx:
        s = lines[i].strip()
        if not s.startswith("@"):
            i += 1
            continue
        end = i
        if "(" in s:
            end = call_args(lines, i, lines[i].index("("), max_lines=20)[1]
        ends[end] = i
        i = end + 1
    out: List[str] = []
    j = idx - 1
    while j in ends:
        start = ends[j]
        out.insert(0, " ".join(x.strip() for x in lines[start:j + 1]))
        j = start - 1
    return " ".join(out)


# ---------------------------------------------------------------- the resolver
class Resolver:
    """Per-scan auth resolution (file texts and indexes cached). `resolve(route)` is the
    entry point; adapters receive the resolver and the route."""

    def __init__(self, files: List[FileInfo], read: Read,
                 services: Optional[Dict[str, object]] = None,
                 cache: Optional[FileCache] = None) -> None:
        """`services` is routes.find_services(files) when the caller already has it. With the
        scan's file `cache`, file texts and their code lines are held there (bounded) instead
        of in the resolver, and `read` is not used."""
        self.files = [f for f in files if not f.is_test]
        self.all_files = files  # the scan's list, shared with frameworks.route_details' cache
        self._given_services = services
        self.cache = cache
        self.read = cache.text if cache is not None else read
        self.by_rel = {f.rel: f for f in self.files}
        self.known = set(self.by_rel)
        self._raw: Dict[str, str] = {}
        self._lines: Dict[str, List[str]] = {}
        self._memo: Dict[str, object] = {}

    # -- file access
    def raw(self, rel: str) -> str:
        if self.cache is not None:
            f = self.by_rel.get(rel)
            return self.cache.text(f) if f is not None else ""
        if rel not in self._raw:
            f = self.by_rel.get(rel)
            self._raw[rel] = self.read(f) if f is not None else ""
        return self._raw[rel]

    def lines(self, rel: str) -> List[str]:
        if self.cache is not None:
            f = self.by_rel.get(rel)
            if f is None:
                return split_lines("")
            k = kind_of(f)
            return self.cache.derived(  # type: ignore[return-value]
                f, "auth_lines", lambda: split_lines(file_code_text(f, k, self.cache) if k
                                                     else self.cache.text(f)))
        if rel not in self._lines:
            f = self.by_rel.get(rel)
            k = kind_of(f) if f is not None else None
            raw = self.raw(rel)
            self._lines[rel] = split_lines(code_text(raw, k) if k else raw)
        return self._lines[rel]

    def of_kind(self, kind: str) -> List[FileInfo]:
        return [f for f in self.files if kind_of(f) == kind and not f.is_example]

    def memo(self, key: str, build: Callable[[], object]) -> object:
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]

    # -- entry point
    def resolve(self, route: dict) -> AuthVerdict:
        path = route.get("path", "")
        named = public_path_verdict(path)
        if named is not None and named.state == PUBLIC:
            return named
        # A health probe is a GET: write routes never get the health exemption (resolve is
        # called for write routes; /orders/:id/ready or a router's PUT /status is a resource).
        if route.get("method") not in WRITE_METHODS and is_health_path(path):
            return AuthVerdict(PUBLIC, "health endpoint")
        if self._signed_webhook(route):
            return AuthVerdict(PUBLIC, "webhook verified by signature in %s" % route["file"])
        v = self._resolve(route)
        if named is not None and v.state not in (PROTECTED, PUBLIC):
            return named  # named like an auth endpoint under a resource: unresolved, never silent
        return v

    def _resolve(self, route: dict) -> AuthVerdict:
        fw = route.get("framework", "")
        adapter = ADAPTERS.get(fw)
        if route.get("auth_detected") and fw not in _ADAPTER_FIRST:
            return AuthVerdict(PROTECTED, "auth at the route")
        v = adapter(self, route) if adapter is not None else _LOCAL
        if route.get("auth_detected"):
            # Express/Go: the adapter names the use() line that protects the route; otherwise
            # the auth is in the route call itself.
            return v if v.state == PROTECTED else AuthVerdict(PROTECTED, "auth at the route")
        if v is _LOCAL or (v.state == UNPROTECTED and not v.evidence):
            return self._local(route)
        return v

    def _signed_webhook(self, route: dict) -> bool:
        """A POST to a webhook path with no parameters whose handler verifies a signature: in
        the route's own window (an inline handler), or anywhere in the file when the path names
        a provider below the webhook segment (/webhooks/stripe). A collection path ending in
        /webhooks is the shape of webhook-management CRUD, so it needs the check at the route."""
        path = route.get("path", "")
        if route.get("method") != "POST" or not _WEBHOOK_RE.search(path):
            return False
        if any(_PARAM_SEG_RE.match(x) for x in _segs(path)):
            return False
        lines = self.lines(route["file"])
        idx = route.get("line", 0) - 1
        if 0 <= idx < len(lines):
            window = [lines[idx]]
            head = True  # the route's own decorator stack (Python) right below its line
            for ln in lines[idx + 1:idx + 60]:
                if head and ln.lstrip().startswith("@"):
                    window.append(ln)
                    continue
                head = False
                if _ROUTE_START_RE.search(ln):
                    break
                window.append(ln)
            if _VERIFY_RE.search("\n".join(window)):
                return True
        if _WEBHOOK_LAST_RE.search(path):
            return False
        return bool(_VERIFY_RE.search("\n".join(lines)))

    def _local(self, route: dict) -> AuthVerdict:
        """Nothing beyond the route's file applies. Auth middleware applied in another file of
        the same service (a folder with a recognised entry point) might cover it: unresolved."""
        svc_of, use_by_service = self.memo("services", self._services)  # type: ignore[misc]
        svc = svc_of(route["file"])
        elsewhere = [u for u in use_by_service.get(svc, []) if u != route["file"]] if svc else []
        if elsewhere:
            return AuthVerdict(UNRESOLVED, "auth middleware applied in %s; which routes it covers "
                                           "is not resolved" % elsewhere[0])
        return AuthVerdict(UNPROTECTED, "no auth at the route, its router or its app")

    def _services(self) -> Tuple[Callable[[str], Optional[str]], Dict[str, List[str]]]:
        from ..routes import _service_of, find_services  # routes imports this package
        services = self._given_services
        if services is None:
            services = find_services(self.files)
        roots = sorted(services, key=lambda r: (-(0 if r == "." else r.count("/") + 1), r))
        use_by_service: Dict[str, List[str]] = {}
        for f in sorted(self.files, key=lambda x: x.rel):
            kind = kind_of(f)
            if f.is_example or kind not in ("js", "go"):
                continue
            if not re.search(r"\.[Uu]se\(", self.raw(f.rel)):
                continue
            if any(auth_use(ln) for ln in self.lines(f.rel)) if kind == "go" else \
                    self.js_auth_uses(f.rel):
                svc = _service_of(f.rel, roots)
                if svc is not None:
                    use_by_service.setdefault(svc, []).append(f.rel)
        return (lambda rel: _service_of(rel, roots)), use_by_service

    # -- JavaScript (Express)
    def js_imports(self) -> Dict[str, List[Tuple[str, str]]]:
        """Target file -> [(importing file, local name)] for require/import bindings."""
        def build() -> Dict[str, List[Tuple[str, str]]]:
            out: Dict[str, List[Tuple[str, str]]] = {}
            for f in self.of_kind("js"):
                text = "\n".join(self.lines(f.rel))
                for name, spec in _js_bindings(text):
                    target = _js_resolve(f.rel, spec, self.known) if spec.startswith(".") else None
                    if target:
                        out.setdefault(target, []).append((f.rel, name))
            return out
        return self.memo("js_imports", build)  # type: ignore[return-value]

    def js_uses(self, rel: str) -> List[Tuple[int, str, str]]:
        """(line index, receiver, argument text) of every `x.use(...)` call in a JS file."""
        def build() -> List[Tuple[int, str, str]]:
            lines = self.lines(rel)
            out: List[Tuple[int, str, str]] = []
            for i, ln in enumerate(lines):
                for m in re.finditer(r"\b(\w+)\s*\.\s*use\s*\(", ln):
                    out.append((i, m.group(1), call_args(lines, i, m.end() - 1)[0]))
            return out
        return self.memo("js_uses:" + rel, build)  # type: ignore[return-value]

    def js_decls(self, rel: str, var: str) -> List[Tuple[int, str]]:
        """(line index, right-hand side) of every `var = ...` declaration in a JS file (cached)."""
        def build() -> List[Tuple[int, str]]:
            rx = re.compile(_JS_DECL % re.escape(var))
            out: List[Tuple[int, str]] = []
            for i, ln in enumerate(self.lines(rel)):
                if var in ln:
                    m = rx.search(ln)
                    if m:
                        out.append((i, m.group(1).strip()))
            return out
        return self.memo("js_decls:%s:%s" % (rel, var), build)  # type: ignore[return-value]

    def js_bindings(self, rel: str) -> List[Tuple[str, str]]:
        return self.memo("js_bindings:" + rel,  # type: ignore[return-value]
                         lambda: _js_bindings("\n".join(self.lines(rel))))

    def js_uses_blank(self, rel: str) -> List[Tuple[int, str, str, str]]:
        """js_uses with each argument text's string contents blanked (blank_strings), cached."""
        return self.memo("js_uses_blank:" + rel,  # type: ignore[return-value]
                         lambda: [(i, recv, args, blank_strings(args))
                                  for i, recv, args in self.js_uses(rel)])

    def js_inline_mounts(self) -> Dict[str, List[Tuple[str, int, str, str]]]:
        """Target file -> (file, line index, receiver, args without the require) of every inline
        `x.use('/p', require('./routes/p'))` mount in the scanned JS files, in file order."""
        def build() -> Dict[str, List[Tuple[str, int, str, str]]]:
            out: Dict[str, List[Tuple[str, int, str, str]]] = {}
            for f in self.of_kind("js"):
                for i, recv, args in self.js_uses(f.rel):
                    for spec in re.findall(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", args):
                        target = _js_resolve(f.rel, spec, self.known) if spec.startswith(".") else None
                        if target is not None:
                            out.setdefault(target, []).append(
                                (f.rel, i, recv, re.sub(r"require\([^)]*\)", "", args)))
            return out
        return self.memo("js_inline_mounts", build)  # type: ignore[return-value]

    def js_router_names(self, rel: str) -> frozenset:
        """Names in a JS file bound to a Router (`x = express.Router()`) or to a required/imported
        routes module, plus "require:<spec>" for inline-required Router files: mounting one is
        not auth middleware (`app.use('/auth', authRouter)`)."""
        def is_router_file(spec: str) -> bool:
            target = _js_resolve(rel, spec, self.known)
            return target is not None and bool(js_router_decls("\n".join(self.lines(target))))

        def build() -> frozenset:
            text = "\n".join(self.lines(rel))
            names = set(js_router_decls(text))
            for name, spec in self.js_bindings(rel):
                if spec.startswith(".") and (routes_spec(spec) or is_router_file(spec)):
                    names.add(name)
            # Inline mounts: app.use('/auth', require('./auth')) where ./auth is a Router.
            for spec in set(re.findall(r"require\(\s*['\"](\.[^'\"]*)['\"]\s*\)", text)):
                if is_router_file(spec):
                    names.add("require:" + spec)
            return frozenset(names)
        return self.memo("js_router_names:" + rel, build)  # type: ignore[return-value]

    def js_auth_uses(self, rel: str) -> List[Tuple[int, str, UseInfo]]:
        """(line index, receiver, UseInfo) of every `x.use(...)` in a JS file that applies auth
        middleware (use_info), cached."""
        def build() -> List[Tuple[int, str, UseInfo]]:
            routers = self.js_router_names(rel)
            out = []
            for i, recv, args in self.js_uses(rel):
                info = use_info(recv, args, routers)
                if info is not None:
                    out.append((i, recv, info))
            return out
        return self.memo("js_auth_uses:" + rel, build)  # type: ignore[return-value]

    def js_auth_before(self, rel: str, var: str, idx: int,
                       path: Optional[str] = None) -> Optional[Tuple[int, str]]:
        """(line index, PROTECTED | UNRESOLVED) of the first `var.use(auth)` before lines[idx]
        that covers a route at `path` (relative to `var`; None when unknown); UNRESOLVED when the
        only candidates are mounted on a path or exempt paths this scan cannot match."""
        unresolved: Optional[int] = None
        for i, recv, info in self.js_auth_uses(rel):
            if i >= idx or recv != var:
                continue
            st = use_applies(info, path)
            if st == PROTECTED:
                return i, PROTECTED
            if st == UNRESOLVED and unresolved is None:
                unresolved = i
        return (unresolved, UNRESOLVED) if unresolved is not None else None

    def js_require_calls(self) -> Dict[str, List[Tuple[str, int, str]]]:
        """Target file -> (file, line index, first argument) of every `require('./x')(arg)` call
        in the scanned JS files, built once (route functions' callers are looked up here)."""
        def build() -> Dict[str, List[Tuple[str, int, str]]]:
            out: Dict[str, List[Tuple[str, int, str]]] = {}
            rx = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\(\s*(\w+)")
            for f in self.of_kind("js"):
                for i, ln in enumerate(self.lines(f.rel)):
                    if "require(" not in ln:
                        continue
                    for m in rx.finditer(ln):
                        if not m.group(1).startswith("."):
                            continue
                        target = _js_resolve(f.rel, m.group(1), self.known)
                        if target is not None:
                            out.setdefault(target, []).append((f.rel, i, m.group(2)))
            return out
        return self.memo("js_require_calls", build)  # type: ignore[return-value]

    # -- Python
    def py_binds(self, rel: str) -> Dict[str, Tuple[str, str]]:
        return self.memo("py_binds:" + rel,  # type: ignore[return-value]
                         lambda: _py_binds("\n".join(self.lines(rel))))

    def py_module_file(self, importer: str, module: str) -> Optional[str]:
        stem = _py_module_path(importer, module)
        return self.memo("py_mod:%s:%s" % (posixpath.dirname(importer), stem),  # type: ignore
                         lambda: self._py_file(stem, importer))

    def _py_tails(self) -> Dict[str, List[str]]:
        """Last one and last two path segments -> known files ending so (sorted)."""
        def build() -> Dict[str, List[str]]:
            out: Dict[str, List[str]] = {}
            for r in sorted(self.known):
                parts = r.split("/")
                out.setdefault(parts[-1], []).append(r)
                if len(parts) >= 2:
                    out.setdefault("/".join(parts[-2:]), []).append(r)
            return out
        return self.memo("py_tails", build)  # type: ignore[return-value]

    def _py_file(self, stem: str, importer: str = "") -> Optional[str]:
        """The file of an absolute or relative module stem. For an absolute import from a source
        root (src/, backend/, services/a/...), the candidate in the importer's app/service unit
        wins, then the one sharing the longest folder prefix with the importer, then the shortest
        path, then path order (deterministic: never set or hash order)."""
        idir = posixpath.dirname(importer).split("/") if importer else []
        unit = self.app_unit(importer) if importer else None
        for cand in (stem + ".py", stem + "/__init__.py"):
            if cand in self.known:
                return cand
            parts = cand.split("/")
            tail = "/".join(parts[-2:])
            hits = [r for r in self._py_tails().get(tail, []) if r.endswith("/" + cand)]
            if hits:
                def key(r: str) -> Tuple[int, int, int, str]:
                    rdir = posixpath.dirname(r).split("/")
                    shared = 0
                    for a, b in zip(idir, rdir):
                        if a != b:
                            break
                        shared += 1
                    same = 0 if importer and self.app_unit(r) == unit else 1
                    return same, -shared, len(r), r
                return min(hits, key=key)
        return None

    def py_origin(self, rel: str, var: str, ctors: str) -> Tuple[str, str, int, str, str]:
        """Where `var` (used in `rel`) is created by one of `ctors` (regex alternation):
        ("local", file, line index, ctor, call args), ("missing", module, -1, "", name) when it is
        imported from a file not in the scan, or ("none", "", -1, "", ""). Cached."""
        return self.memo("py_origin:%s:%s:%s" % (rel, var, ctors),  # type: ignore[return-value]
                         lambda: self._py_origin(rel, var, ctors))

    def _py_origin(self, rel: str, var: str, ctors: str) -> Tuple[str, str, int, str, str]:
        rx = re.compile(_PY_ASSIGN % (re.escape(var), ctors))
        lines = self.lines(rel)
        for i, ln in enumerate(lines):
            m = rx.match(ln)
            if m:
                return "local", rel, i, m.group(1), call_args(lines, i, m.end() - 1)[0]
        bind = self.py_binds(rel).get(var)
        if bind is None:
            return "none", "", -1, "", ""
        module, name = bind
        target = self.py_module_file(rel, module)
        if target is None:
            return "missing", module, -1, "", name
        rx = re.compile(_PY_ASSIGN % (re.escape(name), ctors))
        tlines = self.lines(target)
        for i, ln in enumerate(tlines):
            m = rx.match(ln)
            if m:
                return "local", target, i, m.group(1), call_args(tlines, i, m.end() - 1)[0]
        return "none", "", -1, "", ""

    # -- app / service units
    def app_unit(self, rel: str) -> Optional[str]:
        """Deepest folder holding `rel` that is an app root (a pom.xml, build.gradle, *.csproj,
        composer.json, Gemfile or artisan beside it) or a service root (routes.find_services);
        "" is the scan root. None when no such folder contains `rel`."""
        def build() -> set:
            from ..routes import find_services  # routes imports this package
            roots = set()
            for f in self.files:
                name = posixpath.basename(f.rel)
                if name in _APP_MANIFESTS or name.endswith(".csproj"):
                    roots.add(posixpath.dirname(f.rel))
            services = self._given_services
            if services is None:
                services = find_services(self.files)
            roots |= {"" if r == "." else r for r in services}
            return roots
        roots = self.memo("app_units", build)
        cache: Dict[str, Optional[str]] = self.memo("app_unit_of", dict)  # type: ignore
        if rel not in cache:
            d = posixpath.dirname(rel)
            while d not in roots and d:  # type: ignore[operator]
                d = posixpath.dirname(d)
            cache[rel] = d if d in roots else None  # type: ignore[operator]
        return cache[rel]

    def same_app(self, route_rel: str, other_rel: str) -> bool:
        """Whether configuration in other_rel can apply to a route in route_rel: both in the same
        app or service unit (anywhere in the scan when the route belongs to no unit)."""
        unit = self.app_unit(route_rel)
        return unit is None or self.app_unit(other_rel) == unit

    # -- Spring, ASP.NET Core, Rails, Laravel (dfscan/frameworks.py)
    def framework_routes(self, rel: str) -> List[dict]:
        """frameworks.route_details of one file: its routes with adapter details (cached)."""
        def build() -> List[dict]:
            from ..frameworks import route_details
            f = self.by_rel.get(rel)
            return (route_details(f, self.raw(rel), self.all_files, self.cache)
                    if f is not None else [])
        return self.memo("fw_routes:" + rel, build)  # type: ignore[return-value]

    def route_extra(self, route: dict) -> dict:
        """Adapter details of an inventory route (controller, action, [AllowAnonymous] ...)."""
        def build() -> Dict[Tuple[int, str, str], dict]:
            out: Dict[Tuple[int, str, str], dict] = {}
            for r in self.framework_routes(route["file"]):
                out.setdefault((r["line"], r["method"], r["path"]), r.get("_extra") or {})
            return out
        index = self.memo("fw_extra:" + route["file"], build)
        return index.get((route["line"], route["method"], route["path"]), {})  # type: ignore

    def signup_route(self, route: dict) -> bool:
        """The sign-up endpoint, public by design: a POST to a user collection (/users,
        /accounts, /members, never under an `admin` segment) whose file also serves an
        unauthenticated <collection>/login, handled by a registration-like handler: an action
        named like one (register, signUp ...), an Auth/Account/Registration/Signup controller,
        or the controller that serves that login route itself (a users controller doing its own
        login). Admin-named controllers never count."""
        if route["method"] != "POST":
            return False
        p = route["path"].rstrip("/")
        if not _SIGNUP_COLLECTION_RE.search(p) or _ADMIN_SEG_RE.search(p):
            return False
        extra = self.route_extra(route)
        ctrl = re.split(r"[\\:.]", extra.get("controller", "") or "")[-1]
        if "admin" in ctrl.lower():
            return False
        logins = self.memo("fw_logins:" + route["file"], lambda: {
            (r["path"].rstrip("/").rsplit("/", 1)[0],
             (r.get("_extra") or {}).get("controller", ""))
            for r in self.framework_routes(route["file"])
            if _LOGIN_SEG_RE.search(r["path"].rstrip("/")) and not r["auth_detected"]})
        if not any(lp == p for lp, _ in logins):  # type: ignore[union-attr]
            return False
        if is_public_action(extra.get("action", "")) or _AUTH_CONTROLLER_RE.search(ctrl):
            return True
        return bool(extra.get("controller")) and \
            (p, extra.get("controller")) in logins  # type: ignore[operator]

    # -- Django
    def django_views(self) -> "DjangoViews":
        from .django_views import DjangoViews  # django_views imports this module
        return self.memo("django_views",  # type: ignore[return-value]
                         lambda: DjangoViews(self.files, self.read,
                                             lambda f: self.lines(f.rel)))

    def django_settings(self) -> List[str]:
        def build() -> List[str]:
            rx = re.compile(r"(?m)^(?:INSTALLED_APPS|MIDDLEWARE|REST_FRAMEWORK|ROOT_URLCONF)\s*[+]?=")
            return [f.rel for f in self.of_kind("py") if rx.search(self.raw(f.rel))]
        return self.memo("django_settings", build)  # type: ignore[return-value]


# Frameworks whose adapter names the use() line protecting a route; their inventory auth flag
# (a prior same-receiver use(auth) or Group middleware, or a marker in the route call) is only
# the fallback evidence "auth at the route".
_ADAPTER_FIRST = frozenset(("express", "gin", "echo", "chi"))

_JS_ROUTER_DECL_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*(?::\s*[\w.<>]+\s*)?=\s*(?:new\s+)?"
    r"(?:[\w$]+\s*\.\s*|require\(\s*['\"][^'\"]+['\"]\s*\)\s*\.\s*)?Router\s*\(")
_JS_APP_DECL_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*(?::\s*[\w.<>]+\s*)?=\s*(?:await\s+)?express\s*\(\s*\)")
# Receivers whose .get/.post/... calls are Express routes in any file (the Go adapter accepts
# \w*[Rr]outer the same way); names bound to express() or a Router in the file are added.
JS_RECV_DEFAULT = r"app|server|r|\w*[Rr]outer"


def js_router_decls(text: str) -> List[str]:
    """Names declared as a Router in JS text (`const api = express.Router()`)."""
    return _JS_ROUTER_DECL_RE.findall(text)


def js_receivers(text: str) -> str:
    """Regex alternation of the route receivers of a JS file (JS_RECV_DEFAULT plus names bound
    to express() or a Router in it), so `const api = express.Router(); api.post(...)` is a route
    while an `api.post(...)` HTTP client call in a frontend file is not."""
    names = sorted(set(js_router_decls(text)) | set(_JS_APP_DECL_RE.findall(text)))
    return "|".join([re.escape(n) for n in names] + [JS_RECV_DEFAULT])


_JS_DECL = r"(?:\b(?:const|let|var)\s+|^\s*|\bthis\.)%s\s*(?::\s*[\w.<>\[\]]+\s*)?=(?!=)\s*(.*)"
_JS_REQ_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+|\{[^}]*\})\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)")
_JS_IMPORT_RE = re.compile(
    r"\bimport\s+(?:type\s+)?(\w+)?\s*,?\s*(\{[^}]*\}|\*\s+as\s+\w+)?\s*from\s*['\"]([^'\"]+)['\"]")


def _js_names(group: str) -> List[str]:
    group = group.strip()
    if group.startswith("*"):
        return [group.split()[-1]]
    out = []
    for part in group.strip("{}").split(","):
        bits = part.replace(":", " as ").split()
        if bits:
            out.append(bits[-1])
    return out


def _js_bindings(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for m in _JS_REQ_RE.finditer(text):
        out += [(n, m.group(2)) for n in _js_names(m.group(1))]
    for m in _JS_IMPORT_RE.finditer(text):
        if m.group(1):
            out.append((m.group(1), m.group(3)))
        if m.group(2):
            out += [(n, m.group(3)) for n in _js_names(m.group(2))]
    return out


# Framework name (the route's "framework") -> adapter, filled in by the package __init__
# (one module per framework family). Frameworks without an entry use the route's own
# auth_detected flag and the same-service middleware check only.
ADAPTERS: Dict[str, Callable[["Resolver", dict], AuthVerdict]] = {}


def resolve(route: dict, files: List[FileInfo], read: Read) -> AuthVerdict:
    """Auth verdict for one route of the inventory (see the module docstring). For many routes
    of one scan, build a `Resolver(files, read)` once and call its `resolve`."""
    return Resolver(files, read).resolve(route)
