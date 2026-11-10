from __future__ import annotations

import json
import posixpath
import re
from typing import Callable, Dict, List, Optional, Set, Tuple

from .auth.core import file_code_text
from .filecache import FileCache, text_of
from .imports import _read
from .json_utils import check_json_safety
from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .routes import _code_text, _kind, find_services, is_health_path
from .text import strip_comments

# API Design rules (spec 9.1). They read the route inventory from routes.find_routes and the
# comment-stripped source of the route files. Handler bodies are seen only when the handler is
# written inline at the route (JS/Go: the route call's parentheses; Python: the decorated def);
# a handler passed by name (`router.get('/x', listX)`) is not followed, so it is never flagged.

HANDLER_MAX_LINES = 60
# Route files of the framework analyzers (Spring, ASP.NET Core, Rails, Laravel). Only
# no-version-prefix and openapi-drift use their routes; the handler-body rules read JS/TS/Python/Go.
FRAMEWORK_EXTS = (".java", ".kt", ".cs", ".rb", ".php")
WRITE_BODY_METHODS = {"POST", "PUT", "PATCH"}


def _lines(text: str) -> List[str]:
    return text.split("\n")


def _js_go_window(lines: List[str], idx: int) -> str:
    """Text of the route call starting on lines[idx]: from the line to the matching `)`."""
    text = "\n".join(lines[idx:idx + HANDLER_MAX_LINES])
    start = text.find("(")
    if start < 0:
        return lines[idx]
    depth = 0
    for pos in range(start, len(text)):
        ch = text[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[:pos + 1]
    return text


def _py_window(lines: List[str], idx: int) -> str:
    """Decorator line through the end of the decorated def (by indentation)."""
    k = idx + 1
    while k < len(lines) and (lines[k].strip().startswith("@") or not lines[k].strip()):
        k += 1
    if k >= len(lines) or not re.match(r"^\s*(?:async\s+)?def\s", lines[k]):
        return lines[idx]
    base = len(lines[k]) - len(lines[k].lstrip())
    end = k + 1
    while end < len(lines) and end - idx < HANDLER_MAX_LINES:
        ln = lines[end]
        if ln.strip() and len(ln) - len(ln.lstrip()) <= base and not ln.lstrip().startswith(")"):
            break
        end += 1
    return "\n".join(lines[idx:end])


class _Source:
    """Comment-stripped text of route files, cached."""

    def __init__(self, files: List[FileInfo], file_cache: Optional[FileCache] = None) -> None:
        self.by_rel = {f.rel: f for f in files}
        self.cache: Dict[str, List[str]] = {}
        self.file_cache = file_cache

    def lines(self, rel: str) -> List[str]:
        if rel not in self.cache:
            f = self.by_rel.get(rel)
            kind = _kind(f) if f is not None else None
            if kind and self.file_cache is not None:
                self.cache[rel] = _lines(file_code_text(f, kind, self.file_cache))  # type: ignore
            else:
                self.cache[rel] = _lines(_code_text(_read(f), kind)) if kind else []
        return self.cache[rel]

    def any_text(self, f: FileInfo) -> str:
        """Comment-stripped text of a JS/TS/Python/Go or Java/Kotlin/C#/Ruby/PHP file, else ''."""
        if _kind(f):
            return "\n".join(self.lines(f.rel))
        if f.lang is not None and f.ext in FRAMEWORK_EXTS:
            if self.file_cache is not None:
                return self.file_cache.code(f)
            return strip_comments(_read(f), f.lang)
        return ""

    def window(self, route: dict) -> str:
        lines = self.lines(route["file"])
        idx = route["line"] - 1
        if not 0 <= idx < len(lines):
            return ""
        if route["file"].endswith(".py"):
            return _py_window(lines, idx)
        return _js_go_window(lines, idx)


def _usable(routes: List[dict], files: List[FileInfo]) -> List[dict]:
    by_rel = {f.rel: f for f in files}
    return [r for r in routes if r["file"] in by_rel and not by_rel[r["file"]].is_example
            and not by_rel[r["file"]].is_test]


def _route_sig(rule: str, sev: str, r: dict, note: str, conf: str) -> Signal:
    return Signal(rule, "API Design", sev, r["file"], r["line"],
                  "%s %s: %s" % (r["method"], r["path"], note), conf)


# ---------------------------------------------------------------- list-route-no-pagination
_PARAM_SEG_RE = re.compile(r"^(?::\w+|\{[^}]*\}|<[^>]*>|\*)$")
_FULL_QUERY_RE = re.compile(
    r"\.findAll\(\s*\)|\.find\(\s*(?:\{\s*\})?\s*\)|\.findMany\(\s*\)|\.objects\.all\(\)"
    r"|\bquery\([A-Z]\w*\)(?:\.\w+\([^()]*\))*?\.all\(\)|\.scalars\(\)\.all\(\)"
    r"|\.Find\(&\w+\)|\bSELECT\s+\*\s+FROM\s+\w+\s*['\"`]", re.I)
_PAGINATION_RE = re.compile(
    r"limit|offset|cursor|\bpage|per_?page|page_?size|\btake\b|\bskip\b|paginat|\bfirst\b"
    r"|\bafter\b|\bbefore\b|\[\s*:\s*\d+\s*\]|\bslice\(|\bLimit\(|\bOffset\(", re.I)


def _is_collection_path(path: str) -> bool:
    segs = [s for s in path.strip("/").split("/") if s]
    if not segs or is_health_path(path):
        return False
    last = segs[-1]
    return not _PARAM_SEG_RE.match(last) and re.match(r"^[a-z][a-z_-]*s$", last) is not None \
        and not last.endswith(("ss", "status"))


def rule_list_route_no_pagination(routes: List[dict], src: _Source) -> List[Signal]:
    """GET route on a plural collection path (no trailing :id) whose inline handler returns a full
    query (findAll(), find({}), findMany(), objects.all(), query(X)....all(), gorm Find(&x),
    SELECT * FROM t) and mentions no limit/offset/cursor/page/take/skip.

    False-positive risks: small bounded tables (countries, roles) are flagged; pagination applied
    by middleware or a repository helper is not seen. Handlers passed by name are not read
    (missed, never flagged); queries built in a service function are missed.
    """
    out = []
    for r in routes:
        if r["method"] != "GET" or not _is_collection_path(r["path"]):
            continue
        win = src.window(r)
        if _FULL_QUERY_RE.search(win) and not _PAGINATION_RE.search(win):
            out.append(_route_sig("list-route-no-pagination", "Medium", r,
                                  "returns every row with no pagination", "medium"))
    return out


# ---------------------------------------------------------------- unvalidated-body
_BODY_RE = re.compile(
    r"\breq\.body\b|\brequest\.(?:json|get_json\(|data\b|form\b)|\bctx\.request\.body\b"
    r"|\bc\.(?:Bind|BindJSON|ShouldBind\w*)\(|json\.NewDecoder\(\s*r\.Body\s*\)")
_VALIDATED_RE = re.compile(
    r"(?<!JSON)\.(?:safe)?[pP]arse(?:Async)?\(|\bvalidate\w*\(|\bValidate\w*\(|\bcelebrate\("
    r"|\bJoi\.|\byup\.|\bz\.|checkSchema|validationResult|\bbody\(\s*['\"]|plainToClass|plainToInstance"
    r"|\.load\(|\bSchema\(|\bmodel_validate|\bparse_obj\(|\b\w+Schema\b|\bajv\b|\bassert\w*\("
    r"|\babort\(\s*4\d\d|status\(\s*4\d\d\)|status_code\s*=\s*4\d\d|\b4(?:00|22)\b"
    r"|ValidationError|BadRequest|constructEvent|signature", re.I)
_GO_TAGS_RE = re.compile(r"`[^`]*\b(?:binding|validate):\"[^\"]+\"[^`]*`")
_VALIDATOR_IMPORT_RE = re.compile(
    r"['\"](?:zod|joi|@hapi/joi|yup|celebrate|express-validator|class-validator|ajv|superstruct"
    r"|valibot|@sinclair/typebox)['\"]|\bfrom\s+(?:pydantic|marshmallow|cerberus|voluptuous"
    r"|jsonschema)\b|\bimport\s+(?:pydantic|marshmallow|jsonschema)\b"
    r"|go-playground/validator")


def rule_unvalidated_body(routes: List[dict], src: _Source) -> List[Signal]:
    """POST/PUT/PATCH route whose inline handler reads the request body (req.body, request.json,
    request.get_json(), request.data/form, c.Bind*/c.ShouldBind*, json.NewDecoder(r.Body)) with no
    validation in sight: zod/joi/yup/celebrate/express-validator/class-validator/ajv calls,
    marshmallow .load, pydantic model_validate/parse_obj, validate(...) calls, a 400/422 check, or
    (Go) `binding:`/`validate:` struct tags in the file. FastAPI/Nest typed body params never read
    the raw body and are not flagged.

    False-positive risks: validation middleware mounted in another file (app.use(validator)) or
    done in a called service is not seen; when the file imports a validation library the code
    emits low instead of medium, but the measured ceiling (low) caps every signal of this rule
    at low. Handlers passed by name are not read (missed).
    """
    out = []
    raw_cache: Dict[str, str] = {}
    for r in routes:
        if r["method"] not in WRITE_BODY_METHODS:
            continue
        win = src.window(r)
        if not _BODY_RE.search(win) or _VALIDATED_RE.search(win):
            continue
        if r["file"] not in raw_cache:
            raw_cache[r["file"]] = "\n".join(src.lines(r["file"]))
        whole = raw_cache[r["file"]]
        if r["file"].endswith(".go") and _GO_TAGS_RE.search(whole):
            continue
        conf = "low" if _VALIDATOR_IMPORT_RE.search(whole) else "medium"
        out.append(_route_sig("unvalidated-body", "High", r, "body used without validation", conf))
    return out


# ---------------------------------------------------------------- mixed-error-shapes
_KEYS = ("error", "errors", "message", "detail", "msg")
_JS_ERR_RE = re.compile(
    r"\.status\(\s*(?:[45]\d\d|HttpStatus\.\w+)\s*\)\s*\.(json|send)\(\s*(\{[^{}]*\}|['\"`])")
_PY_ERR_RE = re.compile(
    r"\breturn\s+(?:jsonify\()?(\{[^{}]*\})\)?\s*,\s*[45]\d\d\b"
    r"|JSONResponse\(\s*(?:content\s*=\s*)?(\{[^{}]*\})\s*,\s*status_code\s*=\s*[45]\d\d"
    r"|\b(HTTPException)\(\s*(?:status_code\s*=\s*)?[45]\d\d")
_GO_ERR_RE = re.compile(
    r"\bc\.JSON\(\s*(?:http\.Status(?!OK|Created|Accepted|NoContent)\w+|[45]\d\d)\s*,\s*"
    r"(?:gin\.H|map\[string\]\w+|echo\.Map)\{([^{}]*)\}|\bhttp\.(Error)\(")


def _shape(obj: str) -> Optional[str]:
    keys = [k for k in _KEYS if re.search(r"[{,]\s*['\"]?%s['\"]?\s*:" % k, obj)]
    return "+".join(keys) if keys else None


def _error_shapes(f: FileInfo, lines: List[str]) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    text = "\n".join(lines)
    if f.rel.endswith(".py"):
        for m in _PY_ERR_RE.finditer(text):
            shape = "detail" if m.group(3) else _shape(m.group(1) or m.group(2) or "")
            if shape:
                out.append((shape, text.count("\n", 0, m.start()) + 1))
    elif f.rel.endswith(".go"):
        for m in _GO_ERR_RE.finditer(text):
            shape = "string" if m.group(2) else _shape("{" + (m.group(1) or "") + "}")
            if shape:
                out.append((shape, text.count("\n", 0, m.start()) + 1))
    else:
        for m in _JS_ERR_RE.finditer(text):
            body = m.group(2)
            shape = "string" if body[0] in "'\"`" else _shape(body)
            if shape:
                out.append((shape, text.count("\n", 0, m.start()) + 1))
    return out


def _compatible(a: str, b: str) -> bool:
    return bool(set(a.split("+")) & set(b.split("+")))


def rule_mixed_error_shapes(routes: List[dict], files: List[FileInfo], src: _Source,
                            services: Dict[str, tuple]) -> List[Signal]:
    """Within one service, 4xx/5xx error bodies with incompatible shapes: objects keyed error /
    errors / message / detail / msg with no key in common, or a plain string next to an object.
    One signal per service, on the first response whose shape differs from the most common one.

    False-positive risks: only error responses with an explicit 4xx/5xx status in the same call
    are read (res.status(4xx).json/send, Flask/FastAPI return/JSONResponse/HTTPException, gin/echo
    c.JSON, http.Error); errors produced by a central error handler are not seen, so a service
    that is consistent through its handler but has one hand-written response is flagged.
    """
    roots = _service_roots(services)
    found: Dict[str, List[Tuple[str, str, int]]] = {}
    route_files = sorted({r["file"] for r in routes})
    for rel in route_files:
        f = src.by_rel.get(rel)
        if f is None:
            continue
        svc = _service_of(rel, roots)
        for shape, line in _error_shapes(f, src.lines(rel)):
            found.setdefault(svc, []).append((shape, rel, line))
    out = []
    for svc in sorted(found):
        items = found[svc]
        counts: Dict[str, int] = {}
        for shape, _, _ in items:
            counts[shape] = counts.get(shape, 0) + 1
        main = max(sorted(counts), key=lambda s: counts[s])
        odd = [x for x in items if not _compatible(x[0], main)]
        if not odd:
            continue
        shape, rel, line = odd[0]
        summary = ", ".join("%s x%d" % (s, counts[s]) for s in sorted(counts))
        out.append(Signal("mixed-error-shapes", "API Design", "Medium", rel, line,
                          "error shapes in service %s: %s" % (svc, summary), "medium"))
    return out


def _service_roots(services: Dict[str, tuple]) -> List[str]:
    """Service roots, deepest first."""
    return sorted(services, key=lambda r: (-(0 if r == "." else r.count("/") + 1), r))


def _service_of(rel: str, roots: List[str]) -> str:
    d = posixpath.dirname(rel)
    for root in roots:
        if root == "." or d == root or d.startswith(root + "/"):
            return root
    return posixpath.dirname(rel) or "."


# ---------------------------------------------------------------- no-version-prefix
VERSION_MIN_ROUTES = 3
# /v1, /v2.1 and template segments such as ASP.NET's v{version:apiVersion} or Spring's v{ver}.
_VERSION_SEG_RE = re.compile(r"(?:^|/)v(?:\d+(?:\.\d+)?|\{[^}]*\})(?:/|$)", re.I)
_VERSION_LITERAL_RE = re.compile(
    r"['\"`]/?(?:[\w-]+/)*v(?:\d+(?:\.\d+)?|\{[^}'\"`]*\})/?['\"`/]", re.I)
_VERSION_HEADER_RE = re.compile(
    r"api-version|accept-version|x-api-version|enableVersioning|@Version\(|versioning|apiVersion"
    r"|\bvnd\.[\w.-]*\bv\d"  # media-type versioning: application/vnd.acme.v1+json
    r"|\bnamespace\s*\(?\s*:v\d+\b|\bscope\s*\(?\s*:?['\"]?/?v\d+\b|\bmodule\s+(?:\w+::)*V\d+\b"
    # Rails namespace/scope/module V1; PHP/C# namespace ...\V1 / ....V1; Java package ....v1
    r"|\b(?:namespace|package)\s+[\w\\.]*[\\.]V\d+\b", re.I)


def rule_no_version_prefix(routes: List[dict], files: List[FileInfo], src: _Source,
                           services: Dict[str, tuple]) -> List[Signal]:
    """Service with at least 3 non-health routes, none under a /vN or v{...} segment, and no
    versioning anywhere in its code (JS/TS/Python/Go and Java/Kotlin/C#/Ruby/PHP files): a "/v1"
    or "/api/v2" string literal, e.g. a mount prefix; Api-Version / Accept-Version header
    handling; Nest enableVersioning; ASP.NET [ApiVersion]; media-type versioning (vnd.x.v1);
    Rails namespace/scope/module V1; a V1 namespace or v1 package. One signal per service, on its
    entry point. Low confidence.

    False-positive risks: internal services, BFFs and server-rendered sites do not need a version
    prefix; versioning done at the gateway or by host name is not seen.
    """
    by_svc: Dict[str, List[dict]] = {}
    roots = _service_roots(services)
    for r in routes:
        if is_health_path(r["path"]):
            continue
        by_svc.setdefault(_service_of(r["file"], roots), []).append(r)
    out = []
    for svc in sorted(by_svc):
        rs = by_svc[svc]
        if svc not in services or len(rs) < VERSION_MIN_ROUTES:
            continue
        if any(_VERSION_SEG_RE.search(r["path"]) for r in rs):
            continue
        prefix = "" if svc == "." else svc + "/"
        code = [f for f in files if f.rel.startswith(prefix) and not f.is_test
                and (_kind(f) or (f.lang is not None and f.ext in FRAMEWORK_EXTS))]
        if any(_VERSION_LITERAL_RE.search(t) or _VERSION_HEADER_RE.search(t)
               for t in (src.any_text(f) for f in code)):
            continue
        entry_file, entry_line, is_code = services[svc]
        if not is_code:
            first = min(rs, key=lambda r: (r["file"].count("/"), r["file"], r["line"]))
            entry_file, entry_line = first["file"], first["line"]
        out.append(Signal("no-version-prefix", "API Design", "Low", entry_file, entry_line,
                          "%d routes in service %s, none versioned" % (len(rs), svc), "low"))
    return out


# ---------------------------------------------------------------- openapi-drift
SPEC_NAMES = ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml",
              "swagger.yml")
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_JSON_KEY_RE = re.compile(r'\s*("(?:[^"\\]|\\.)*")\s*:')


class MalformedSpec(ValueError):
    """The spec file cannot be read as OpenAPI (bad JSON, too deeply nested, wrong types)."""


def _norm(path: str) -> str:
    p = re.sub(r"\{[^}]*\}|:\w+|<[^>]*>|\*", "{}", path.strip())
    p = "/" + p.strip("/")
    return p


def _json_key_lines(lines: List[str]) -> Dict[str, int]:
    """First line of each JSON object key that starts a line (one pass)."""
    out: Dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = _JSON_KEY_RE.match(ln)
        if not m:
            continue
        try:
            key = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(key, str) and key not in out:
            out[key] = i + 1
    return out


def _parse_spec(text: str, json_file: bool) -> Tuple[Dict[str, Tuple[int, Set[str]]], List[str]]:
    """({path: (line, methods)}, base paths) from an OpenAPI/Swagger document.

    Raises MalformedSpec for JSON that does not parse (including nesting too deep for the
    decoder); a document that parses but has no `paths` object gives no paths."""
    paths: Dict[str, Tuple[int, Set[str]]] = {}
    bases: List[str] = []
    lines = text.split("\n")
    if json_file:
        try:
            check_json_safety(text)
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise MalformedSpec(type(exc).__name__) from None
        if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
            return paths, bases
        key_lines = _json_key_lines(lines)
        for p, ops in data["paths"].items():
            methods = {m.upper() for m in ops if m in _HTTP_METHODS} if isinstance(ops, dict) else set()
            paths[p] = (key_lines.get(p, 1), methods)
        if isinstance(data.get("basePath"), str):
            bases.append(data["basePath"])
        servers = data.get("servers")
        if isinstance(servers, list):
            for s in servers:
                if isinstance(s, dict) and isinstance(s.get("url"), str):
                    bases.append(s["url"])
    else:
        in_paths = False
        in_servers = False
        cur: Optional[str] = None
        path_ind = -1
        for i, ln in enumerate(lines):
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            ind = len(ln) - len(ln.lstrip())
            if ind == 0:
                in_paths = ln.startswith("paths:")
                in_servers = ln.startswith("servers:")
                cur = None
                bm = re.match(r"^basePath:\s*['\"]?([^'\"\s]+)", ln)
                if bm:
                    bases.append(bm.group(1))
                continue
            if in_servers:  # only server urls are bases (not info.license.url and the like)
                m = re.match(r"^\s*-?\s*url:\s*['\"]?([^'\"\s]+)", ln)
                if m:
                    bases.append(m.group(1))
                continue
            if not in_paths:
                continue
            pm = re.match(r"^\s*['\"]?(/[^'\":]*)['\"]?\s*:", ln)
            if pm and (path_ind < 0 or ind == path_ind):
                path_ind = ind
                cur = pm.group(1)
                paths[cur] = (i + 1, set())
                continue
            mm = re.match(r"^\s*(get|post|put|patch|delete)\s*:", ln)
            if mm and cur is not None and ind > path_ind:
                paths[cur][1].add(mm.group(1).upper())
    norm_bases = []
    for b in bases:
        b = re.sub(r"^[a-z]+://[^/]+", "", b).rstrip("/")
        if b and b.startswith("/"):
            norm_bases.append(b)
    return paths, norm_bases


def _strip_base(path: str, bases: List[str]) -> str:
    for b in sorted(bases, key=len, reverse=True):
        if path == b or path.startswith(b + "/"):
            return path[len(b):] or "/"
    return path


def _has_literal_seg(path: str) -> bool:
    return any(seg and seg != "{}" for seg in path.split("/"))


def _matches(code_path: str, spec_path: str) -> bool:
    """Equal, or one path is the other plus a prefix of whole segments (a prefix mounted in
    another file shortens the code path; a spec that omits a mount prefix shortens the spec path).
    The shorter path must have a literal segment, so `/{}` never matches as a suffix."""
    if code_path == spec_path:
        return True
    if code_path.endswith(spec_path):
        return _has_literal_seg(spec_path)
    if spec_path.endswith(code_path):
        return _has_literal_seg(code_path)
    return False


def _shared_segments(a: str, b: str) -> int:
    n = 0
    for x, y in zip(posixpath.dirname(a).split("/"), posixpath.dirname(b).split("/")):
        if x != y or not x:
            break
        n += 1
    return n


def _spec_owners(routes: List[dict], specs: List[FileInfo],
                 services: Dict[str, tuple]) -> Dict[str, List[dict]]:
    """Spec rel -> the routes it describes. A route belongs to the deepest spec in one of its
    ancestor folders; otherwise to the nearest spec of its own service (docs/openapi.yaml next to
    src/); otherwise to the nearest spec that sits outside every service."""
    roots = _service_roots(services)

    def svc(rel: str) -> Optional[str]:
        d = posixpath.dirname(rel)
        for root in roots:
            if root == "." or d == root or d.startswith(root + "/"):
                return root
        return None

    folders = sorted(((posixpath.dirname(f.rel), f) for f in specs),
                     key=lambda x: (-len(x[0]), x[0]))
    spec_svc = {f.rel: svc(f.rel) for f in specs}
    owned: Dict[str, List[dict]] = {}
    for r in routes:
        owner = next((f for folder, f in folders
                      if not folder or r["file"].startswith(folder + "/")), None)
        if owner is None:
            mine = svc(r["file"])
            cands = [f for f in specs if spec_svc[f.rel] == mine] if mine is not None else []
            cands = cands or [f for f in specs if spec_svc[f.rel] is None]
            if cands:
                owner = min(cands, key=lambda f: (-_shared_segments(f.rel, r["file"]),
                                                  f.rel.count("/"), f.rel))
        if owner is not None:
            owned.setdefault(owner.rel, []).append(r)
    return owned


def rule_openapi_drift(routes: List[dict], files: List[FileInfo],
                       services: Optional[Dict[str, tuple]] = None,
                       notes: Optional[List[str]] = None,
                       cache: Optional[FileCache] = None) -> List[Signal]:
    """When openapi/swagger .json/.yaml exists: code routes missing from the spec, and spec paths
    with no code route. A route is checked against the deepest spec in an ancestor folder, else
    the nearest spec in its service (docs/, api/, spec/ ... next to the code), else the nearest
    spec outside every service. Path parameters are compared by position
    ({id} = :id = <id>), servers/basePath prefixes are stripped, and a path whose trailing whole
    segments equal the other counts as a match (prefix mounted elsewhere, in either direction).
    ASP.NET Core (.cs) routes are compared case-insensitively.

    False-positive risks: routes the scanner cannot see (unsupported frameworks, dynamic paths)
    show up as spec paths with no route. A spec sharing no route with the code (a client spec for
    a third-party API) is ignored, as are health routes missing from the spec. A malformed JSON
    spec is skipped with a note.
    """
    specs = [f for f in files if posixpath.basename(f.rel).lower() in SPEC_NAMES
             and not f.is_test and not f.is_example]
    if not specs:
        return []
    out: List[Signal] = []
    owned = _spec_owners(routes, specs, services if services is not None else find_services(files))
    for f in specs:
        rs = owned.get(f.rel, [])
        if not rs:
            continue
        try:
            paths, bases = _parse_spec(text_of(f, cache).replace("\r\n", "\n"), f.ext == ".json")
        except MalformedSpec as exc:
            if notes is not None:
                notes.append("openapi-drift skipped %s: malformed spec (%s)" % (f.rel, exc))
            continue
        if not paths:
            continue
        spec_norm = {_norm(p): (p, line, methods) for p, (line, methods) in paths.items()}
        by_len = sorted(spec_norm, key=lambda s: (-len(s), s))
        folded: Dict[str, List[str]] = {}
        for s in by_len:
            folded.setdefault(s.lower(), []).append(s)
        matched_specs: Set[str] = set()
        missing: List[dict] = []
        # A route listed as ANY may have a POST twin at the same line (Spring @RequestMapping
        # with no method): the ANY entry speaks for both.
        any_at = {(r["file"], r["line"], r["path"]) for r in rs if r["method"] == "ANY"}
        for r in rs:
            if r["method"] != "ANY" and (r["file"], r["line"], r["path"]) in any_at:
                continue
            code = _norm(_strip_base(_norm(r["path"]), bases))
            if r["file"].endswith(".cs"):  # ASP.NET Core routing is case-insensitive
                hits = folded.get(code.lower()) or [
                    s for s in by_len if _matches(code.lower(), s.lower())]
            else:
                hits = [code] if code in spec_norm else [s for s in by_len if _matches(code, s)]
            if not hits:
                if not is_health_path(r["path"]):
                    missing.append(r)
                continue
            matched_specs.update(hits)
            methods = set().union(*(spec_norm[h][2] for h in hits))
            if r["method"] not in ("ANY",) and methods and r["method"] not in methods:
                missing.append(r)
        if not matched_specs:
            continue  # nothing in common: the spec describes another API
        for r in missing:
            out.append(Signal("openapi-drift", "API Design", "Medium", r["file"], r["line"],
                              "%s %s not in %s" % (r["method"], r["path"], f.rel), "medium"))
        for s in sorted(spec_norm):
            if s not in matched_specs:
                p, line, _ = spec_norm[s]
                out.append(Signal("openapi-drift", "API Design", "Medium", f.rel, line,
                                  "spec path %s has no route in code" % p, "medium"))
    return out


# ---------------------------------------------------------------- runner
def run_api_rules(files: List[FileInfo], routes: List[dict],
                  notes: Optional[List[str]] = None,
                  cache: Optional[FileCache] = None,
                  enabled: Optional[Callable[[str], bool]] = None) -> List[Signal]:
    """Every API Design rule over the route inventory (test and example routes skipped).

    The handler-body rules (list-route-no-pagination, unvalidated-body, mixed-error-shapes) read
    JS/TS/Python/Go route files only; no-version-prefix and openapi-drift also take the Spring,
    ASP.NET Core, Rails and Laravel routes. `notes` (optional) collects skipped-input notes.
    `enabled` (optional, rule id -> bool): openapi-drift (off by default, and the only rule here
    that parses extra files and writes notes) does not run at all when it says False.
    """
    rs = _usable(routes, files)
    if not rs:
        return []
    src = _Source(files, cache)
    by_rel = {f.rel: f for f in files}
    inline = [r for r in rs if _kind(by_rel[r["file"]])]
    services = find_services(files, cache)
    return (rule_list_route_no_pagination(inline, src) + rule_unvalidated_body(inline, src)
            + rule_mixed_error_shapes(inline, files, src, services)
            + rule_no_version_prefix(rs, files, src, services)
            + (rule_openapi_drift(rs, files, services, notes, cache)
               if enabled is None or enabled("openapi-drift") else []))


_LANGS = ["Python", "JavaScript", "TypeScript", "Go"]
_ROUTE_LANGS = _LANGS + ["Java", "Kotlin", "C#", "Ruby", "PHP"]
for _m in [
    RuleMeta("list-route-no-pagination", "1", "API Design", "Medium", "medium",
             "GET route on a plural collection path whose inline handler returns a full query "
             "result with no limit/offset/cursor/page parameter.",
             "Only inline handlers are read (handlers passed by name and queries in service "
             "layers are missed); only findAll()/find({})/findMany()/objects.all()/query(X).all()/"
             "gorm Find/SELECT * literals count as full queries. Small bounded tables are still "
             "flagged; pagination done by middleware is not seen.",
             "Add limit/offset or cursor pagination with a maximum page size.", _LANGS),
    RuleMeta("mixed-error-shapes", "1", "API Design", "Medium", "medium",
             "One service returns 4xx/5xx error bodies with incompatible shapes ({error} vs "
             "{message} vs plain string); one signal per service.",
             "Only responses with an explicit 4xx/5xx status in the same call are read; errors "
             "from a central error handler are not seen. Objects sharing any of error/errors/"
             "message/detail/msg count as compatible.",
             "Pick one error envelope (e.g. RFC 9457 problem+json) and use it everywhere.", _LANGS),
    RuleMeta("no-version-prefix", "3", "API Design", "Low", "low",
             "Service with 3+ non-health routes, none under /vN or v{...}, and no versioning in "
             "its code; one signal per service.",
             "Internal services and server-rendered apps do not need versions; gateway or "
             "host-based versioning is not seen. Any /vN string literal, version-header or "
             "media-type (vnd.x.v1) handling, Nest enableVersioning, ASP.NET [ApiVersion], Rails "
             "namespace/scope/module V1, or a V1 namespace / v1 package in the service's "
             "JS/TS/Python/Go/Java/Kotlin/C#/Ruby/PHP files counts as versioned; versioning "
             "configured only outside those files (custom Spring RequestCondition, YAML, a "
             "gateway) is not seen and is flagged. Always low confidence. Test files do not count;"
             " RSpec files (*_spec.rb, *.spec.rb) count as test files (1.1.0).",
             "Version the public API (/v1 prefix or a version header) before clients depend on it.",
             _ROUTE_LANGS),
    RuleMeta("unvalidated-body", "1", "API Design", "High", "medium",
             "POST/PUT/PATCH route that reads the raw request body with no schema validation in "
             "the handler.",
             "Only inline handlers are read. Validation middleware mounted elsewhere or done in a "
             "called function is not seen; a route in a file importing a validation library is "
             "still reported. Always low confidence (the rule ceiling), set by measured precision. Recognised: zod, joi, yup, celebrate, express-validator, "
             "class-validator, ajv, pydantic, marshmallow, validate(...) calls, explicit 400/422 "
             "checks, Go binding/validate struct tags. FastAPI/Nest typed bodies are never flagged.",
             "Validate the body against a schema before using it.", _LANGS),
    RuleMeta("openapi-drift", "2", "API Design", "Medium", "medium",
             "openapi/swagger spec disagrees with the code: routes missing from the spec, spec "
             "paths with no route.",
             "A route is compared with the deepest spec in an ancestor folder, else the nearest "
             "spec in its own service (docs/, api/, spec/ next to the code), else the nearest "
             "spec outside every service; specs inside another service are never used. Routes "
             "the scanner cannot see (unsupported frameworks, dynamic paths) appear as spec paths "
             "with no route. A prefix mounted in another file, or left out of the spec, is "
             "tolerated by matching trailing whole segments, so two routes that differ only by "
             "prefix can hide each other. ASP.NET Core routes are compared case-insensitively. "
             "Specs sharing no route with the code are ignored; health routes missing from the "
             "spec are not reported. YAML specs are read by indentation (servers urls and "
             "basePath are the only bases); $ref'd path files are not followed; a malformed JSON "
             "spec is skipped. Off by default in 1.1.0: measured 0% (0 of 14) on the corpus, all "
             "from Express routers mounted with app.use('/prefix', router), whose mount prefix "
             "is not resolved; enable it with \"rules\": {\"openapi-drift\": {\"enabled\": true}}.",
             "Generate the spec from code or check it in CI against the route table.",
             _ROUTE_LANGS, default_enabled=False),
]:
    register(_m)
