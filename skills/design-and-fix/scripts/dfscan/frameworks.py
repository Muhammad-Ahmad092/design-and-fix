from __future__ import annotations

import os
import re
from bisect import bisect_right
from typing import Callable, Dict, List, Optional, Set, Tuple

from .model import FileInfo
from .text import strip_comments
from .walker import walk

# Route detection for Spring, ASP.NET Core, Rails and Laravel (spec 9.2). Every parser reads
# comment-stripped text, so routes shown in comments or Javadoc are not listed. Route dicts have
# the same shape as routes.find_routes. Auth markers per framework:
#   Spring   @PreAuthorize / @Secured / @RolesAllowed on the method or the controller class
#   ASP.NET  [Authorize] on the action or controller ([AllowAnonymous] on the action wins),
#            .RequireAuthorization() on a minimal-API route or its MapGroup
#   Rails    before_action :authenticate... in the controller or a parent controller
#            (skip_before_action honoured, only:/except: read), or an `authenticate` block
#            in config/routes.rb
#   Laravel  ->middleware('auth...') on the route or an enclosing Route group

FRAMEWORK_EXTS = (".java", ".kt", ".cs", ".rb", ".php")

_PAIRS = {"(": ")", "[": "]", "{": "}"}
_MAX_SCAN = 200_000
# _match_close results for the most recent large text. Each scan records every bracket it closes
# (and, when it reaches the end of the text, every bracket left open), so repeated calls on one
# file, balanced or not, cost about one pass in total.
_CLOSE_CACHE_MIN = 2048
_close_text: Optional[str] = None
_close_cache: Dict[int, int] = {}


def _scan_root(fi: FileInfo) -> str:
    """Absolute scan root of a walked file (fi.abs minus its root-relative path)."""
    root = fi.abs
    for _ in fi.rel.split("/"):
        root = os.path.dirname(root)
    return root


def _policy_files(fi: FileInfo, files: Optional[List[FileInfo]], folder: str) -> List[FileInfo]:
    """Walked files under a root-relative folder. Other files are only ever read from this list
    (the scan's own walk when given, else a walk scoped to the folder), so .gitignore and the
    no-symlinks-out-of-root policy apply to them as to the scanned file (spec 10.4)."""
    pre = folder.strip("/") + "/" if folder.strip("/") else ""
    if files is not None:
        return [f for f in files if f.rel.startswith(pre)]
    if not pre:
        return []
    try:
        return walk(_scan_root(fi), scope=[pre])
    except OSError:
        return []


_READ_CACHE: Dict[Tuple[str, int, int], str] = {}


def _read_code(fi: FileInfo, lang: str) -> str:
    """Comment-stripped head of another walked file (cached by path, size and mtime)."""
    try:
        st = os.stat(fi.abs)
        key = (fi.abs, st.st_size, int(st.st_mtime_ns))
    except OSError:
        return ""
    hit = _READ_CACHE.get(key)
    if hit is None:
        try:
            with open(fi.abs, "r", encoding="utf-8", errors="ignore") as fh:
                hit = strip_comments(fh.read(_MAX_SCAN), lang)
        except OSError:
            hit = ""
        if len(_READ_CACHE) > 4096:
            _READ_CACHE.clear()
        _READ_CACHE[key] = hit
    return hit


def _route(method: str, path: str, fi: FileInfo, line: int, framework: str, auth: bool) -> dict:
    return {"method": method, "path": path, "file": fi.rel, "line": line,
            "framework": framework, "auth_detected": auth}


def _line_index(text: str) -> Callable[[int], int]:
    """pos -> 1-based line number, by bisect over precomputed line starts."""
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    return lambda pos: bisect_right(starts, pos)


def _join(*parts: str) -> str:
    segs = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(segs)


def _skip_string(text: str, i: int, n: int) -> int:
    """Index just past the string literal whose opening quote is text[i]."""
    c = text[i]
    if c == "\"" and text.startswith("\"\"\"", i):  # Java text block / C# raw string literal
        end = text.find("\"\"\"", i + 3, n)
        return n if end < 0 else end + 3
    if c == "\"" and i > 0 and (text[i - 1] == "@" or (text[i - 1] == "$" and i > 1
                                                       and text[i - 2] == "@")):
        j = i + 1  # C# verbatim string: no backslash escapes, "" is a quote
        while j < n:
            if text[j] == "\"":
                if j + 1 < n and text[j + 1] == "\"":
                    j += 2
                    continue
                return j + 1
            j += 1
        return n
    j = i + 1
    while j < n and text[j] != c:
        j += 2 if text[j] == "\\" else 1
    return j + 1


def _match_close(text: str, open_idx: int) -> int:
    """Index of the bracket closing text[open_idx] (strings skipped), or the last index scanned."""
    global _close_text, _close_cache
    cached = len(text) >= _CLOSE_CACHE_MIN
    if cached:
        if text is not _close_text:
            _close_text, _close_cache = text, {}
        hit = _close_cache.get(open_idx)
        if hit is not None:
            return hit
    stack: List[Tuple[str, int]] = []
    n = min(len(text), open_idx + _MAX_SCAN)
    i = open_idx
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i, n)
            continue
        if c in _PAIRS:
            stack.append((_PAIRS[c], i))
        elif c in ")]}" and stack and stack[-1][0] == c:
            start = stack.pop()[1]
            if cached:
                _close_cache[start] = i
            if not stack:
                return i
        i += 1
    res = max(open_idx, n - 1)
    if cached:
        _close_cache[open_idx] = res
        if n == len(text):  # scanned to the end: every bracket still open is unclosed
            for _, start in stack:
                _close_cache[start] = max(start, n - 1)
    return res


def _chain_end(text: str, pos: int, sep: str) -> int:
    """End of a fluent chain (`.A().B()` or `->a()->b()`) starting at pos."""
    link = re.compile(r"\s*" + re.escape(sep) + r"\s*\w+\s*")
    while True:
        m = link.match(text, pos)
        if not m:
            return pos
        pos = m.end()
        if pos < len(text) and text[pos] == "(":
            pos = _match_close(text, pos) + 1


def _blank_strings(text: str) -> str:
    """Single-line string literal contents replaced by spaces (offsets and length kept)."""
    return re.sub(r"(['\"])(?:\\.|(?!\1)[^\\\n])*\1",
                  lambda m: m.group(1) + " " * (len(m.group(0)) - 2) + m.group(1), text)


# ---------------------------------------------------------------- annotations / attributes
class _Group:
    """A run of annotations (Spring) or attribute lists (ASP.NET) and the declaration after it."""

    def __init__(self, start: int, end: int, items: List[Tuple[int, str, str]]) -> None:
        self.start = start
        self.end = end
        self.items = items  # (position, name, args)
        self.is_class = False
        self.class_name = ""
        self.member = ""


_DECL_MODIFIERS = {
    "public", "private", "protected", "internal", "static", "final", "abstract", "open", "data",
    "sealed", "inner", "suspend", "override", "synchronized", "default", "async", "virtual",
    "partial", "readonly", "unsafe", "new", "extern", "operator", "inline", "annotation",
}
_CLASS_WORDS = {"class", "interface", "object", "record", "enum"}
_WORD_RE = re.compile(r"\s*([A-Za-z_]\w*)")
_MEMBER_RE = re.compile(r"([A-Za-z_]\w*)\s*(?:<[^>()]*>)?\s*\(")


def _classify(group: _Group, code: str) -> None:
    pos = group.end
    while True:
        m = _WORD_RE.match(code, pos)
        if not m:
            return
        word = m.group(1)
        pos = m.end()
        if word in _DECL_MODIFIERS:
            continue
        if word in _CLASS_WORDS:
            nm = _WORD_RE.match(code, pos)
            group.is_class = True
            group.class_name = nm.group(1) if nm else ""
            return
        eol = code.find("\n", m.start())
        rest = code[m.start():eol if eol >= 0 else len(code)]
        mm = _MEMBER_RE.search(rest)
        group.member = mm.group(1) if mm else ""
        return


_JAVA_ANN_RE = re.compile(r"(?<![\w@.\"'])@([A-Za-z_][\w.]*)")


def _java_groups(code: str) -> List[_Group]:
    groups: List[_Group] = []
    pos = 0
    # Annotation names are found with string contents blanked ("see @GetMapping" in a string is
    # not an annotation); arguments are read from the real text at the same offsets.
    for m in _JAVA_ANN_RE.finditer(_blank_strings(code)):
        if m.start() < pos:
            continue
        end = m.end()
        k = end
        while k < len(code) and code[k] in " \t":
            k += 1
        args = ""
        if k < len(code) and code[k] == "(":
            close = _match_close(code, k)
            args = code[k + 1:close]
            end = close + 1
        item = (m.start(), m.group(1).rsplit(".", 1)[-1], args)
        if groups and not code[groups[-1].end:m.start()].strip():
            groups[-1].items.append(item)
            groups[-1].end = end
        else:
            groups.append(_Group(m.start(), end, [item]))
        pos = end
    for g in groups:
        _classify(g, code)
    return groups


_CS_ATTR_START_RE = re.compile(r"(?m)^[ \t]*\[")


def _cs_groups(code: str) -> List[_Group]:
    groups: List[_Group] = []
    pos = 0
    for m in _CS_ATTR_START_RE.finditer(code):
        start = m.end() - 1
        if start < pos:
            continue
        items: List[Tuple[int, str, str]] = []
        end = start
        while end < len(code) and code[end] == "[":
            close = _match_close(code, end)
            items.append((end, "", code[end + 1:close]))
            end = close + 1
            nxt = re.compile(r"\s*").match(code, end)
            if nxt and nxt.end() < len(code) and code[nxt.end()] == "[":
                end = nxt.end()
        g = _Group(start, end, items)
        _classify(g, code)
        groups.append(g)
        pos = end
    return groups


# (body open brace, body close brace, class group or None, class name, base class name or "")
_ClassSpan = Tuple[int, int, Optional[_Group], str, str]


def _enclosing_span(classes: List[_ClassSpan], pos: int) -> Optional[_ClassSpan]:
    """Innermost class whose body contains pos. A nested record/enum/class declared before the
    handlers does not hide its parent."""
    best: Optional[_ClassSpan] = None
    for span in classes:
        if span[0] < pos <= span[1] and (best is None or span[0] > best[0]):
            best = span
    return best


def _enclosing_class(classes: List[_ClassSpan], pos: int) -> Optional[_Group]:
    """Class-level group of the innermost class containing pos (None: no class or no group)."""
    best = _enclosing_span(classes, pos)
    return best[2] if best else None


_CLASS_DECL_RE = re.compile(
    r"(?<![.\w])(?:class|interface|object|record|struct|enum(?!\s+class\b))\s+([A-Za-z_]\w*)")
# A header line that starts a new declaration (or an annotation of the next one): the class
# before it has no body (Kotlin).
_HEADER_STOP_RE = re.compile(
    r"[ \t]*(?:(?:public|private|protected|internal|class|interface|object|record|struct|enum"
    r"|fun|val|var|data|sealed|open|abstract|final|static)\b|@)")
_ANN_NAME_RE = re.compile(r"@[A-Za-z_][\w.]*")
_CS_BASE_RE = re.compile(r"^\s*(?:<[^{:]*>)?\s*:\s*(?:global::)?([\w.]+)")


def _class_body(code: str, pos: int) -> Optional[Tuple[int, int]]:
    """(open, close) of the body brace of the class whose header ends at pos, or None.
    Annotations inside the header (`class X @Autowired constructor(...)`,
    `extends Base<@NonNull T>`) are skipped; one at the start of a line belongs to the next
    declaration, so the class has no body."""
    i, limit = pos, min(len(code), pos + 4000)
    while i < limit:
        c = code[i]
        if c == "(":
            i = _match_close(code, i) + 1
            continue
        if c == "{":
            return i, _match_close(code, i)
        if c == "@":
            m = _ANN_NAME_RE.match(code, i)
            if not m:
                return None
            i = m.end()
            k = i
            while k < limit and code[k] in " \t":
                k += 1
            if k < limit and code[k] == "(":
                i = _match_close(code, k) + 1
            continue
        if c in ";}=[":
            return None
        if c == "\n" and _HEADER_STOP_RE.match(code, i + 1):
            return None
        i += 1
    return None


def _cs_base(header: str) -> str:
    """Base class named in a C# class header (`(ctor args) : Base<T>, IFoo`), or ""."""
    prev = None
    while prev != header:  # drop primary-constructor / generic-argument parentheses
        prev, header = header, re.sub(r"\([^()]*\)", "", header)
    m = _CS_BASE_RE.match(header)
    return m.group(1).rsplit(".", 1)[-1] if m else ""


def _class_index(code: str, groups: List[_Group]) -> List[_ClassSpan]:
    """Body span, class-level group (or None), name and base of every class with a body."""
    decls = [(m.start(), m.end(), m.group(1))
             for m in _CLASS_DECL_RE.finditer(_blank_strings(code))]
    owner: Dict[int, _Group] = {}
    for g in groups:
        if g.is_class:
            after = [d for d, _, _ in decls if d >= g.end]
            if after:
                owner[after[0]] = g
    out: List[_ClassSpan] = []
    for start, end, name in decls:
        body = _class_body(code, end)
        if body:
            out.append((body[0], body[1], owner.get(start), name, _cs_base(code[end:body[0]])))
    return out


# ---------------------------------------------------------------- Spring
_SPRING_MAPPING = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
                   "PatchMapping": "PATCH", "DeleteMapping": "DELETE", "RequestMapping": ""}
_SPRING_AUTH = {"PreAuthorize", "Secured", "RolesAllowed"}
_SPRING_CLIENT = {"FeignClient", "HttpExchange"}  # client interfaces, not server routes
# An auth annotation that admits anonymous callers protects nothing.
_SPRING_ANON_RE = re.compile(r"\bpermitAll\s*\(|\bisAnonymous\s*\(|IS_AUTHENTICATED_ANONYMOUSLY")
# `method = RequestMethod.POST`, `method = POST` (static import), `method = {GET, POST}`.
_REQ_METHOD_KEY_RE = re.compile(r"\bmethod\s*=\s*")
_REQ_METHOD_RE = re.compile(r"(?<!\w)(?:RequestMethod\s*\.\s*)?(GET|POST|PUT|PATCH|DELETE)\b")
_PATH_KEY_RE = re.compile(r"\b(?:value|path)\s*=\s*")
_NAMED_ARG_RE = re.compile(r"[A-Za-z_]\w*\s*=(?!=)")
# Same-file String constants used in mapping paths (`BASE + "/x"`).
_SPRING_CONST_RE = re.compile(
    r"\b(?:static\s+final|final\s+static)\s+String\s+([A-Za-z_]\w*)\s*=\s*\"([^\"\n]*)\"\s*;"
    r"|\bconst\s+val\s+([A-Za-z_]\w*)\s*(?::\s*String\s*)?=\s*\"([^\"\n]*)\"")
_EXPR_TOKEN_RE = re.compile(r"\"((?:\\.|[^\"\\\n])*)\"|([A-Za-z_][\w.]*)")


def _split_top(s: str) -> List[str]:
    """s split at commas outside brackets and string literals."""
    parts: List[str] = []
    depth, start, i, n = 0, 0, 0, len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            i = _skip_string(s, i, n)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[start:i])
            start = i + 1
        i += 1
    parts.append(s[start:])
    return parts


def _spring_expr(expr: str, consts: Dict[str, str]) -> str:
    """Value of a path expression made of string literals and constants joined by `+`. A
    constant that is not defined in this file is kept as a `{NAME}` placeholder."""
    out: List[str] = []
    for tok in _EXPR_TOKEN_RE.finditer(expr):
        if tok.group(1) is not None:
            out.append(tok.group(1))
        else:
            name = tok.group(2).rsplit(".", 1)[-1]
            out.append(consts.get(name, "{%s}" % name))
    return "".join(out)


def _spring_paths(args: str, consts: Optional[Dict[str, str]] = None) -> List[str]:
    consts = consts or {}
    m = _PATH_KEY_RE.search(_blank_strings(args))
    if m:
        rest = args[m.end():].lstrip()
    else:
        rest = args.strip()
        if not rest or _NAMED_ARG_RE.match(rest):  # only other named arguments: no path
            return [""]
    if rest[:1] in ("[", "{"):
        elems = _split_top(rest[1:_match_close(rest, 0)])
    else:
        elems = _split_top(rest)[:1]
    paths = [_spring_expr(e, consts) for e in elems if e.strip()]
    return paths or [""]


def _spring_methods(args: str) -> List[str]:
    m = _REQ_METHOD_KEY_RE.search(_blank_strings(args))
    if not m:
        return []
    rest = args[m.end():]
    part = rest[:_match_close(rest, 0) + 1] if rest[:1] in ("{", "[") else _split_top(rest)[0]
    return _REQ_METHOD_RE.findall(part)


def _spring_auth(items: List[Tuple[int, str, str]]) -> Optional[bool]:
    """True: an auth annotation; False: permitAll()/anonymous; None: neither."""
    found: Optional[bool] = None
    for _, name, args in items:
        if name == "PermitAll" or (name in _SPRING_AUTH and _SPRING_ANON_RE.search(args)):
            return False
        if name in _SPRING_AUTH:
            found = True
    return found


def _spring_routes(fi: FileInfo, code: str) -> List[dict]:
    groups = _java_groups(code)
    classes = _class_index(code, groups)
    line_of = _line_index(code)
    consts: Dict[str, str] = {}
    for cm in _SPRING_CONST_RE.finditer(code):
        if cm.group(1):
            consts[cm.group(1)] = cm.group(2)
        else:
            consts[cm.group(3)] = cm.group(4)
    out: List[dict] = []
    for g in groups:
        if g.is_class:
            continue
        cls = _enclosing_class(classes, g.start)
        cls_items = cls.items if cls else []
        if {n for _, n, _ in cls_items} & _SPRING_CLIENT:
            continue
        prefixes = [p for _, name, args in cls_items if name == "RequestMapping"
                    for p in _spring_paths(args, consts)] or [""]
        own = _spring_auth(g.items)
        auth = own if own is not None else bool(_spring_auth(cls_items))
        for pos, name, args in g.items:
            if name not in _SPRING_MAPPING:
                continue
            methods = [_SPRING_MAPPING[name]] if _SPRING_MAPPING[name] else (
                _spring_methods(args) or ["ANY"])
            for prefix in prefixes:
                for path in _spring_paths(args, consts):
                    for meth in methods:
                        out.append(_route(meth, _join(prefix, path), fi, line_of(pos),
                                          "spring", auth))
    return out


# ---------------------------------------------------------------- ASP.NET Core
_CS_VERB_RE = re.compile(
    r"\bHttp(Get|Post|Put|Patch|Delete)(?:Attribute)?\b(?:\s*\(\s*(?:template\s*:\s*)?@?\"([^\"]*)\")?")
_CS_ACCEPT_RE = re.compile(r"\bAcceptVerbs(?:Attribute)?\s*\(([^)]*)\)")
_CS_ROUTE_RE = re.compile(r"\bRoute(?:Attribute)?\s*\(\s*(?:template\s*:\s*)?@?\"([^\"]*)\"")
_CS_AUTH_RE = re.compile(r"\bAuthorize(?:Attribute)?\b")
_CS_ANON_RE = re.compile(r"\bAllowAnonymous(?:Attribute)?\b")
_CS_MAP_RE = re.compile(r"\b(\w+)\s*\.\s*Map(Get|Post|Put|Patch|Delete)\s*\(\s*@?\"([^\"]*)\"")
_CS_MAP_METHODS_RE = re.compile(r"\b(\w+)\s*\.\s*MapMethods\s*\(\s*@?\"([^\"]*)\"")
_CS_HEALTH_RE = re.compile(r"\b\w+\s*\.\s*MapHealthChecks\s*\(\s*@?\"([^\"]*)\"")
_CS_GROUP_RE = re.compile(r"\b(\w+)\s*=\s*(\w+)\s*\.\s*MapGroup\s*\(\s*@?\"([^\"]*)\"")
# `app.MapGroup("/pub").RequireAuthorization().MapPost(...)`: a group used inline, not assigned.
_CS_INLINE_GROUP_RE = re.compile(r"\b(\w+)\s*\.\s*MapGroup\s*\(\s*@?\"([^\"]*)\"\s*\)")
_CS_LINK_RE = re.compile(r"\s*\.\s*(\w+)\s*\(")
_CS_MAP_NAME_RE = re.compile(r"Map(Get|Post|Put|Patch|Delete)$")
# Framework base classes: the end of a controller's inheritance chain.
_CS_FRAMEWORK_BASES = {"", "ControllerBase", "Controller", "ApiController", "ODataController",
                       "PageModel", "Object", "object"}
_CsClass = Tuple[str, bool, bool, List[str]]  # (base, [Authorize], [AllowAnonymous], [Route]s)


def _cs_flags(attr_text: str) -> Tuple[bool, bool]:
    blank = _blank_strings(attr_text)
    return bool(_CS_AUTH_RE.search(blank)), bool(_CS_ANON_RE.search(blank))


def _cs_template(path: str, cls: str, action: str) -> str:
    name = cls[:-len("Controller")] if cls.endswith("Controller") and cls != "Controller" else cls
    return path.replace("[controller]", name.lower()).replace("[action]", action.lower())


def _cs_class_table(classes: List[_ClassSpan]) -> Dict[str, _CsClass]:
    table: Dict[str, _CsClass] = {}
    for _, _, g, name, base in classes:
        text = " ".join(args for _, _, args in g.items) if g else ""
        auth, anon = _cs_flags(text)
        table.setdefault(name, (base, auth, anon, _CS_ROUTE_RE.findall(text)))
    return table


_CS_TABLE_CACHE: Dict[Tuple[str, int, int], Dict[str, _CsClass]] = {}
_cs_scan_files: Optional[List[FileInfo]] = None
_cs_scan_table: Dict[str, _CsClass] = {}


def _cs_file_table(f: FileInfo) -> Dict[str, _CsClass]:
    try:
        st = os.stat(f.abs)
        key = (f.abs, st.st_size, int(st.st_mtime_ns))
    except OSError:
        return {}
    hit = _CS_TABLE_CACHE.get(key)
    if hit is None:
        code = _read_code(f, "C#")
        hit = _cs_class_table(_class_index(code, _cs_groups(code))) if "class" in code else {}
        if len(_CS_TABLE_CACHE) > 20000:
            _CS_TABLE_CACHE.clear()
        _CS_TABLE_CACHE[key] = hit
    return hit


def _cs_other_classes(fi: FileInfo, files: Optional[List[FileInfo]]) -> Dict[str, _CsClass]:
    """Classes declared in the other walked .cs files: the whole scan when `files` is given,
    else the scanned file's folder (and below)."""
    global _cs_scan_files, _cs_scan_table
    if files is not None and files is _cs_scan_files:
        return _cs_scan_table
    if files is not None:
        cands = [f for f in files if f.ext == ".cs" and not f.is_test]
    else:
        cands = [f for f in _policy_files(fi, None, fi.rel.rpartition("/")[0]) if f.ext == ".cs"]
    table: Dict[str, _CsClass] = {}
    for f in cands:
        if f.rel != fi.rel:
            for name, entry in _cs_file_table(f).items():
                table.setdefault(name, entry)
    if files is not None:
        _cs_scan_files, _cs_scan_table = files, table
    return table


def _cs_inherited(base: str, local: Dict[str, _CsClass],
                  other: Callable[[], Dict[str, _CsClass]]) -> _CsClass:
    """[Authorize] / [AllowAnonymous] / first [Route] list inherited through up to 4 base
    controllers ([Authorize] and [Route] are inherited attributes in ASP.NET Core)."""
    auth = anon = False
    routes: List[str] = []
    seen: Set[str] = set()
    for _ in range(4):
        if base in _CS_FRAMEWORK_BASES or base in seen:
            break
        seen.add(base)
        entry = local.get(base)
        if entry is None:
            entry = other().get(base)
        if entry is None:
            break
        base, a, n, r = entry
        auth, anon, routes = auth or a, anon or n, routes or r
    return "", auth, anon, routes


def _aspnet_controller_routes(fi: FileInfo, code: str,
                              files: Optional[List[FileInfo]] = None) -> List[dict]:
    groups = _cs_groups(code)
    classes = _class_index(code, groups)
    local = _cs_class_table(classes)
    line_of = _line_index(code)
    memo: List[Dict[str, _CsClass]] = []

    def other() -> Dict[str, _CsClass]:
        if not memo:
            memo.append(_cs_other_classes(fi, files))
        return memo[0]

    out: List[dict] = []
    for g in groups:
        if g.is_class:
            continue
        text = " ".join(args for _, _, args in g.items)
        verbs = [(m.group(1).upper(), m.group(2)) for m in _CS_VERB_RE.finditer(text)]
        for am in _CS_ACCEPT_RE.finditer(text):
            verbs += [(v.upper(), None) for v in re.findall(r"\"(\w+)\"", am.group(1))]
        routes = _CS_ROUTE_RE.findall(text)
        if not verbs and not routes:
            continue
        span = _enclosing_span(classes, g.start)
        cls = span[2] if span else None
        cls_text = " ".join(args for _, _, args in cls.items) if cls else ""
        cls_name = span[3] if span else ""
        prefixes = _CS_ROUTE_RE.findall(cls_text)
        cls_auth, cls_anon = _cs_flags(cls_text)
        if span and span[4] not in _CS_FRAMEWORK_BASES:
            _, b_auth, b_anon, b_routes = _cs_inherited(span[4], local, other)
            cls_auth, cls_anon = cls_auth or b_auth, cls_anon or b_anon
            prefixes = prefixes or b_routes
        prefixes = prefixes or [""]
        auth, anon = _cs_flags(text)
        detected = False if anon else (auth or (cls_auth and not cls_anon))
        if not verbs:
            verbs = [("ANY", None)]
        line = line_of(g.start)
        for meth, sub in verbs:
            subs = [sub] if sub is not None else (routes or [""])
            for s in subs:
                for prefix in prefixes:
                    if s.startswith(("/", "~/")):
                        path = _join(s.lstrip("~"))
                    else:
                        path = _join(prefix, s)
                    path = _cs_template(path, cls_name, g.member)
                    out.append(_route(meth, path, fi, line, "aspnetcore", detected))
    return out


def _aspnet_minimal_routes(fi: FileInfo, code: str) -> List[dict]:
    groups: Dict[str, Tuple[str, bool]] = {}
    line_of = _line_index(code)
    # Variables given `.RequireAuthorization(` anywhere in the file (one pass, not one per group).
    authed_vars = set(re.findall(r"\b(\w+)\s*\.\s*RequireAuthorization\s*\(", code))
    for m in _CS_GROUP_RE.finditer(code):
        open_idx = code.index("(", m.start(2) + len(m.group(2)))
        chain = code[m.start():_chain_end(code, _match_close(code, open_idx) + 1, ".")]
        parent = groups.get(m.group(2), ("", False))
        auth = parent[1] or "RequireAuthorization" in chain or m.group(1) in authed_vars
        groups[m.group(1)] = (_join(parent[0], m.group(3)), auth)
    out: List[dict] = []

    def emit(methods: List[str], path: str, open_idx: int, start: int, prefix: str,
             group_auth: bool) -> None:
        close = _match_close(code, open_idx)
        chain = code[close + 1:_chain_end(code, close + 1, ".")]
        args = _blank_strings(code[open_idx:close + 1])
        if "AllowAnonymous" in chain or _CS_ANON_RE.search(args):
            auth = False
        else:
            auth = group_auth or "RequireAuthorization" in chain or bool(_CS_AUTH_RE.search(args))
        for meth in methods:
            out.append(_route(meth, _join(prefix, path), fi, line_of(start), "aspnetcore", auth))

    for m in _CS_MAP_RE.finditer(code):
        prefix, group_auth = groups.get(m.group(1), ("", False))
        emit([m.group(2).upper()], m.group(3), code.index("(", m.start(2)), m.start(), prefix,
             group_auth)
    for m in _CS_MAP_METHODS_RE.finditer(code):
        open_idx = code.index("(", m.start(0) + len(m.group(1)))
        parts = _split_top(code[open_idx + 1:_match_close(code, open_idx)])
        verbs = parts[1] if len(parts) > 1 else ""
        methods = [(a or b).upper() for a, b in
                   re.findall(r"\"(\w+)\"|HttpMethods\s*\.\s*(\w+)", verbs)]
        prefix, group_auth = groups.get(m.group(1), ("", False))
        if methods:
            emit(methods, m.group(2), open_idx, m.start(), prefix, group_auth)
    for m in _CS_INLINE_GROUP_RE.finditer(code):
        parent = groups.get(m.group(1), ("", False))
        prefix, group_auth = _join(parent[0], m.group(2)), parent[1]
        pos = m.end()
        while True:
            lm = _CS_LINK_RE.match(code, pos)
            if not lm:
                break
            open_idx = lm.end() - 1
            vm = _CS_MAP_NAME_RE.match(lm.group(1))
            if vm:
                pm = re.match(r"\(\s*@?\"([^\"]*)\"", code[open_idx:open_idx + 2000])
                if pm:
                    emit([vm.group(1).upper()], pm.group(1), open_idx, lm.start(1), prefix,
                         group_auth)
                break
            if lm.group(1) == "RequireAuthorization":
                group_auth = True
            pos = _match_close(code, open_idx) + 1
    for m in _CS_HEALTH_RE.finditer(code):
        out.append(_route("GET", _join(m.group(1)), fi, line_of(m.start()), "aspnetcore",
                          False))
    return out


# ---------------------------------------------------------------- Rails
_RB_VERB_RE = re.compile(
    r"^(get|post|put|patch|delete|match)\b\s*\(?\s*(?:(['\"])([^'\"]*)\2|:(\w+))(.*)$")
_RB_RESOURCES_RE = re.compile(
    r"^(resources|resource)\b\s*\(?\s*((?::\w+\b(?!\s*=>)\s*,\s*)*:\w+\b(?!\s*=>))(.*)$")
# One-line brace block: `collection { get :search; post :bulk }`.
_RB_BRACE_BLOCK_RE = re.compile(r"^(member|collection)\s*\{(.*)\}$")
_RB_ROOT_RE = re.compile(r"^root\b(.*)$")
_RB_TARGET_RE = re.compile(r"(?:\bto:\s*|=>\s*)(['\"])([\w/]+)#(\w+)\1")
_RB_BLOCK_RE = re.compile(r"\bdo\s*(?:\|[^|]*\|)?\s*$")
_RB_END_RE = re.compile(r"^end\b")
# Ruby statements closed by `end` without a `do` (kept on the block stack so `end` pairs up).
_RB_KEYWORD_BLOCK_RE = re.compile(r"(?:if|unless|case|begin|def|class|module|while|until)\b")
_RB_SYMS_RE = re.compile(r":(\w+)")
_RB_CONCERN_RE = re.compile(r"concern\s*\(?\s*:(\w+)")
_RB_CONCERNS_STMT_RE = re.compile(r"concerns\s*\(?\s*((?::\w+\s*,?\s*)+)\)?$")
_RESOURCES_ACTIONS = [("index", "GET", False), ("show", "GET", True), ("create", "POST", False),
                      ("update", "PATCH", True), ("destroy", "DELETE", True)]
_RESOURCE_ACTIONS = [("show", "GET"), ("create", "POST"), ("update", "PATCH"),
                     ("destroy", "DELETE")]


def _rb_opt(opts: str, key: str, value: str) -> Optional[re.Match]:
    """`key: <value>` or `:key => <value>` in a Ruby option list."""
    return re.search(r"(?:\b%s:|:%s\s*=>)\s*(%s)" % (key, key, value), opts)


def _rb_option_list(opts: str, key: str) -> Optional[List[str]]:
    """Names in `only:`/`except:` given as [:a, :b], %i[a b], %w(a b), :a or 'a'."""
    m = _rb_opt(opts, key, r"\[[^\]]*\]|%[iwIW][\[(][^\])]*[\])]|:\w+|['\"]\w+['\"]")
    if not m:
        return None
    return re.findall(r"\w+", re.sub(r"^%[iwIW]", "", m.group(1)))


def _rb_str_opt(opts: str, key: str) -> Optional[str]:
    """Value of `key: 'x'` / `key: :x` / `:key => "x"` (None when absent)."""
    m = _rb_opt(opts, key, r"['\"][^'\"]*['\"]|:\w+")
    return m.group(1).strip("'\":") if m else None


def _rb_wanted(action: str, opts: str) -> bool:
    only = _rb_option_list(opts, "only")
    if only is not None:
        return action in only
    exc = _rb_option_list(opts, "except")
    return not (exc is not None and action in exc)


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _pluralize(name: str) -> str:
    """Controller name of a singular `resource` (setting -> settings, address -> addresses)."""
    if name.endswith(("ss", "x", "z", "ch", "sh")):
        return name + "es"
    if name.endswith("s"):
        return name
    if name.endswith("y") and name[-2:-1] not in ("a", "e", "i", "o", "u", ""):
        return name[:-1] + "ies"
    return name + "s"


_RB_AUTH_FILTER = (r"(?::\w+[!?]?\s*,\s*)*:(?:authenticate\w*|require_(?:login|user|auth\w*)"
                   r"|doorkeeper_authorize)!?")


class _RailsAuth:
    """Resolve `before_action :authenticate...` for controller actions (parent chain and
    included app/controllers/concerns followed). Controller files are read only from the walked
    file list, so ignored files and symlinks out of the root are not consulted."""

    # [ \t]* rather than \s*: \s* crosses newlines, which is quadratic on long blank runs.
    _BEFORE_RE = re.compile(
        r"^[ \t]*(?:prepend_)?before_(?:action|filter)\s+" + _RB_AUTH_FILTER + r"(.*)$"
        r"|^[ \t]*(http_basic_authenticate_with)\b(.*)$", re.M)
    _SKIP_RE = re.compile(
        r"^[ \t]*(?:skip_before_(?:action|filter)\s+" + _RB_AUTH_FILTER +
        r"|allow_unauthenticated_access\b)(.*)$", re.M)
    _CLASS_RE = re.compile(r"^[ \t]*class\s+([\w:]+)\s*<\s*([\w:]+)", re.M)
    _INCLUDE_RE = re.compile(r"^[ \t]*include\s+([\w:]+)", re.M)
    _MODULE_RE = re.compile(r"module\s+([\w:]+)")

    def __init__(self, controllers: Dict[str, FileInfo]) -> None:
        self.controllers = controllers  # path under app/controllers -> walked file
        self.cache: Dict[str, str] = {}

    def _text(self, rel: str) -> str:
        if rel not in self.cache:
            fi = self.controllers.get(rel)
            self.cache[rel] = _read_code(fi, "Ruby") if fi is not None else ""
        return self.cache[rel]

    @classmethod
    def _modules(cls, head: str) -> List[str]:
        """Snake-cased names of the `module` blocks still open at the end of head."""
        stack: List[List[str]] = []
        for raw in head.split("\n"):
            s = raw.strip()
            mm = cls._MODULE_RE.match(s)
            if mm:
                stack.append([_snake(p) for p in mm.group(1).split("::") if p])
                if re.search(r";\s*end\s*$", s):  # `module Foo; end` on one line
                    stack.pop()
            elif _RB_END_RE.match(s):
                if stack:
                    stack.pop()
            elif _RB_BLOCK_RE.search(s) or _RB_KEYWORD_BLOCK_RE.match(s):
                stack.append([])
        return [p for parts in stack for p in parts]

    def _events(self, text: str, at: Optional[int] = None) -> List[Tuple[int, bool, str]]:
        """(position, adds auth, options) of the auth filters in text (all at `at` if given)."""
        events = [(m.start(), True, m.group(1) if m.group(1) is not None else m.group(3) or "")
                  for m in self._BEFORE_RE.finditer(text)]
        events += [(m.start(), False, m.group(1) or "") for m in self._SKIP_RE.finditer(text)]
        return [(at, a, o) for _, a, o in events] if at is not None else events

    def action(self, controller: str, action: str, depth: int = 0) -> bool:
        text = self._text(controller + "_controller.rb")
        if not text or depth > 4:
            return False
        auth = False
        cm = self._CLASS_RE.search(text)
        base = cm.group(2) if cm else ""
        if cm and base.lstrip(":") not in ("ActionController::Base", "ActionController::API"):
            parent = "/".join(_snake(p) for p in base.lstrip(":").split("::"))
            if parent.endswith("_controller"):
                # Ruby constant lookup: inside `module Admin`, BaseController is tried as
                # Admin::BaseController first, then top-level (a leading :: is top-level only).
                mods = [] if base.startswith("::") else self._modules(text[:cm.start()])
                for k in range(len(mods), -1, -1):
                    cand = "/".join(mods[:k] + [parent[:-len("_controller")]])
                    if self._text(cand + "_controller.rb"):
                        auth = self.action(cand, action, depth + 1)
                        break
        events = self._events(text)
        for im in self._INCLUDE_RE.finditer(text):  # `include Authentication` (Rails 8 concern)
            rel = "/".join(_snake(p) for p in im.group(1).lstrip(":").split("::"))
            concern = self._text("concerns/%s.rb" % rel)
            if concern:
                events += self._events(concern, im.start())
        for _, add, opts in sorted(events, key=lambda e: e[0]):
            if _rb_wanted(action, opts):
                auth = add
        return auth


def _rails_controllers(fi: FileInfo, files: Optional[List[FileInfo]]) -> Dict[str, FileInfo]:
    """Walked files under <app root>/app/controllers, keyed by their path below it."""
    parts = fi.rel.split("/")
    idx = len(parts) - 1 - parts[::-1].index("config")
    folder = "/".join(parts[:idx] + ["app", "controllers"])
    pre = folder + "/"
    return {f.rel[len(pre):]: f for f in _policy_files(fi, files, folder)
            if f.rel.startswith(pre)}


def _rb_opens(s: str) -> bool:
    return bool(_RB_BLOCK_RE.search(s) or _RB_KEYWORD_BLOCK_RE.match(s))


def _rb_expand_concerns(stmts: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """`concern :x do ... end` blocks removed (they define no route by themselves) and replayed
    inside each `resources ..., concerns: [...]` and `concerns :x` (at that statement's line)."""
    concerns: Dict[str, List[Tuple[int, str]]] = {}
    flat: List[Tuple[int, str]] = []
    i = 0
    while i < len(stmts):
        line, s = stmts[i]
        cm = _RB_CONCERN_RE.match(s)
        if cm and _RB_BLOCK_RE.search(s):
            depth, body = 1, []
            i += 1
            while i < len(stmts) and depth:
                s2 = stmts[i][1]
                if _RB_END_RE.match(s2):
                    depth -= 1
                elif s2 and _rb_opens(s2):
                    depth += 1
                if depth:
                    body.append(stmts[i])
                i += 1
            concerns[cm.group(1)] = body
            continue
        flat.append(stmts[i])
        i += 1
    if not concerns:
        return flat
    out: List[Tuple[int, str]] = []
    for line, s in flat:
        sm = _RB_CONCERNS_STMT_RE.match(s)
        if sm:
            for name in _RB_SYMS_RE.findall(sm.group(1)):
                out += [(line, x) for _, x in concerns.get(name, [])]
            continue
        cc = _rb_opt(s, "concerns", r"\[[^\]]*\]|:\w+") if _RB_RESOURCES_RE.match(s) else None
        body = [(line, x) for n in (_RB_SYMS_RE.findall(cc.group(1)) if cc else [])
                for _, x in concerns.get(n, [])]
        if not body:
            out.append((line, s))
        elif _RB_BLOCK_RE.search(s):
            out += [(line, s)] + body
        else:
            out += [(line, s + " do")] + body + [(line, "end")]
    return out


def _rails_routes(fi: FileInfo, code: str, files: Optional[List[FileInfo]] = None) -> List[dict]:
    resolver = _RailsAuth(_rails_controllers(fi, files))
    out: List[dict] = []
    # frame: (path prefix, controller module, block auth, resource name, resource kind)
    stack: List[Tuple[str, str, bool, str, str]] = []

    def cur() -> Tuple[str, str, bool, str, str]:
        return stack[-1] if stack else ("", "", False, "", "")

    def add(meth: str, path: str, line: int, controller: str, action: str) -> None:
        auth = cur()[2] or (bool(controller) and resolver.action(controller, action))
        out.append(_route(meth, path, fi, line, "rails", auth))

    stmts: List[Tuple[int, str]] = []
    for idx, raw in enumerate(code.split("\n")):
        s = raw.strip()
        bm = _RB_BRACE_BLOCK_RE.match(s)
        if bm:  # expanded to `member do` / statements / `end`, all on the same line number
            stmts.append((idx + 1, bm.group(1) + " do"))
            stmts += [(idx + 1, x.strip()) for x in bm.group(2).split(";")]
            stmts.append((idx + 1, "end"))
        else:
            stmts.append((idx + 1, s))
    for line, s in _rb_expand_concerns(stmts):
        if not s:
            continue
        prefix, module, block_auth, res, res_kind = cur()
        if _RB_END_RE.match(s):
            if stack:
                stack.pop()
            continue
        opens = _rb_opens(s)
        head = _RB_BLOCK_RE.sub("", s).strip() if opens else s
        frame = (prefix, module, block_auth, "", "")
        m = _RB_RESOURCES_RE.match(head)
        if m:
            names = _RB_SYMS_RE.findall(m.group(2))
            opts = m.group(3)
            ctrl_opt = _rb_opt(opts, "controller", r"['\"]?[\w/]+")
            mod_opt = _rb_str_opt(opts, "module")
            path_opt = _rb_str_opt(opts, "path")
            ctrl_mod = module + mod_opt + "/" if mod_opt else module
            for name in names:
                if ctrl_opt:
                    ctrl = ctrl_mod + ctrl_opt.group(1).strip("'\"")
                else:  # a singular `resource :setting` is served by SettingsController
                    ctrl = ctrl_mod + (name if m.group(1) == "resources" else _pluralize(name))
                base = _join(prefix, name if path_opt is None else path_opt)
                if m.group(1) == "resources":
                    for action, meth, member in _RESOURCES_ACTIONS:
                        if _rb_wanted(action, opts):
                            add(meth, _join(base, ":id") if member else base, line, ctrl, action)
                    single = name[:-1] if name.endswith("s") else name
                    frame = (_join(base, ":%s_id" % single), module, block_auth, name, ctrl)
                else:
                    for action, meth in _RESOURCE_ACTIONS:
                        if _rb_wanted(action, opts):
                            add(meth, base, line, ctrl, action)
                    frame = (base, module, block_auth, "", ctrl)
        elif head.startswith(("member", "collection")) and res_kind:
            if res:  # plural resources: the frame prefix ends in /:<name>_id
                base = prefix.rsplit("/", 1)[0]
                base = base + "/:id" if head.startswith("member") else base
            else:  # singular resource: no id segment
                base = prefix
            frame = (base, module, block_auth, res, res_kind)
        elif head.startswith("namespace"):
            nm = _RB_SYMS_RE.search(head) or re.search(r"['\"]([\w/]+)['\"]", head)
            name = nm.group(1) if nm else ""
            path_opt = _rb_str_opt(head, "path")
            mod_opt = _rb_str_opt(head, "module")
            mod = mod_opt if mod_opt is not None else name
            frame = (_join(prefix, name if path_opt is None else path_opt),
                     module + mod + "/" if mod else module, block_auth, "", "")
        elif head.startswith("scope"):
            pm = re.search(r"(?:\bpath:|:path\s*=>)\s*['\"]([^'\"]*)['\"]", head) or \
                re.match(r"scope\s*\(?\s*['\"]([^'\"]*)['\"]", head)
            mod = _rb_str_opt(head, "module")
            frame = (_join(prefix, pm.group(1)) if pm else prefix,
                     module + mod + "/" if mod else module, block_auth, "", "")
        elif re.match(r"authenticated?\b", head):
            frame = (prefix, module, True, "", "")
        else:
            vm = _RB_VERB_RE.match(head)
            rm = _RB_ROOT_RE.match(head) if not vm else None
            if vm:
                seg = vm.group(3) if vm.group(3) is not None else vm.group(4)
                rest = vm.group(5)
                target = _RB_TARGET_RE.search(rest)
                if target:
                    ctrl, action = module + target.group(2), target.group(3)
                elif res_kind and vm.group(4):
                    ctrl, action = res_kind, vm.group(4)
                else:
                    ctrl, action = "", ""
                if vm.group(1) == "match":
                    via = _rb_opt(rest, "via", r"\[[^\]]*\]|:\w+|['\"]\w+['\"]")
                    methods = [v.upper() for v in re.findall(r"\w+", via.group(1))] if via else []
                    methods = ["ANY" if v == "ALL" else v for v in methods] or ["ANY"]
                else:
                    methods = [vm.group(1).upper()]
                base = prefix
                on = _rb_str_opt(rest, "on")
                if on in ("member", "collection") and res_kind and res:
                    base = prefix.rsplit("/", 1)[0]  # drop /:<name>_id
                    base = _join(base, ":id") if on == "member" else base
                for meth in methods:
                    add(meth, _join(base, seg), line, ctrl, action)
            elif rm:
                target = re.search(r"(['\"])([\w/]+)#(\w+)\1", rm.group(1))
                ctrl, action = (module + target.group(2), target.group(3)) if target else ("", "")
                add("GET", _join(prefix), line, ctrl, action)
        if opens:
            stack.append(frame)
    return out


# ---------------------------------------------------------------- Laravel
_PHP_ROUTE_RE = re.compile(r"\bRoute::(\w+)\s*\(")
_PHP_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE",
              "options": "OPTIONS", "any": "ANY"}
_PHP_ATTRS = {"middleware", "prefix", "name", "controller", "domain", "where", "withoutMiddleware"}
_PHP_CHAINED_VERB_RE = re.compile(
    r"->\s*(get|post|put|patch|delete|options|any|match|resources|resource|apiResources"
    r"|apiResource)\s*\(")
# 'auth' / 'auth:sanctum' middleware names, or the Authenticate middleware class itself.
_PHP_AUTH_RE = re.compile(
    r"['\"]auth(?:[:.][\w.,:-]*)?['\"]|\bAuthenticate(?:WithBasicAuth)?::class\b")
_PHP_STR_RE = re.compile(r"\s*['\"]([^'\"]*)['\"]")
# A group closure: `function () {` (block body) or `fn () =>` (arrow body, ends with the call).
_PHP_CLOSURE_RE = re.compile(
    r"\b(?:function\s*\([^)]*\)\s*(?:use\s*\([^)]*\)\s*)?(?::\s*\??[\w\\|]+\s*)?\{"
    r"|fn\s*\([^)]*\)\s*(?::\s*\??[\w\\|]+\s*)?=>)")
_PHP_RESOURCE_NAMES = ("resource", "apiResource", "resources", "apiResources")
# Resource options passed as the third argument: ['only' => [...]] / ['except' => 'show'].
_PHP_RES_OPT_RE = r"['\"]%s['\"]\s*=>\s*(\[[^\]]*\]|['\"][^'\"]*['\"])"
_PHP_HEALTH_RE = re.compile(r"\bhealth\s*:\s*['\"]([^'\"]+)['\"]")
_PHP_RESOURCE = [("index", "GET", ""), ("store", "POST", ""), ("show", "GET", "/{id}"),
                 ("update", "PUT", "/{id}"), ("destroy", "DELETE", "/{id}")]
_PHP_RESOURCE_WEB = [("create", "GET", "/create"), ("edit", "GET", "/{id}/edit")]


def _php_code(text: str) -> str:
    """Comment-stripped PHP, with whole-line `#` comments blanked too (`#[...]` attributes kept)."""
    code = strip_comments(text, "PHP")
    return re.sub(r"(?m)^([ \t]*)#(?!\[)[^\n]*", lambda m: m.group(1), code)


def _php_middleware_auth(chain: str) -> bool:
    for m in re.finditer(r"->\s*middleware\s*\(", chain):
        close = _match_close(chain, chain.index("(", m.start()))
        if _PHP_AUTH_RE.search(chain[m.end():close]):
            return True
    return False


def _php_groups(code: str) -> List[Tuple[int, int, str, bool]]:
    """(closure start, closure end, prefix, auth) of every Route group closure."""
    spans: List[Tuple[int, int, str, bool]] = []
    for m in _PHP_ROUTE_RE.finditer(code):
        open_idx = m.end() - 1
        close = _match_close(code, open_idx)
        end = _chain_end(code, close + 1, "->")
        chain = code[m.start():end]
        if m.group(1) == "group":
            attrs_end, call_close = open_idx, close
        else:
            g = re.search(r"->\s*group\s*\(", chain)
            if not g:
                continue
            attrs_end = m.start() + g.start()
            call_close = _match_close(code, m.start() + g.end() - 1)
        closure = _PHP_CLOSURE_RE.search(code, attrs_end, end)
        if not closure:
            continue
        if code[closure.end() - 1] == "{":
            brace = closure.end() - 1
            body_end = _match_close(code, brace)
        else:  # arrow function: its body runs to the end of the group(...) call
            brace, body_end = closure.end() - 1, call_close
        attrs = code[m.start():closure.start()]
        prefix = ""
        pm = re.search(r"(?:->|::)\s*prefix\s*\(\s*['\"]([^'\"]*)['\"]", attrs) or \
            re.search(r"['\"]prefix['\"]\s*=>\s*['\"]([^'\"]*)['\"]", attrs)
        if pm:
            prefix = pm.group(1)
        auth = False
        if m.group(1) == "middleware" and _PHP_AUTH_RE.search(code[open_idx:close + 1]):
            auth = True
        if _php_middleware_auth(attrs):
            auth = True
        mw = re.search(r"['\"]middleware['\"]\s*=>\s*(\[[^\]]*\]|['\"][^'\"]*['\"])", attrs)
        if mw and _PHP_AUTH_RE.search(mw.group(1)):
            auth = True
        spans.append((brace, body_end, prefix, auth))
    return spans


def _laravel_routes(fi: FileInfo, code: str) -> List[dict]:
    spans = _php_groups(code)
    line_of = _line_index(code)
    out: List[dict] = []
    for m in _PHP_ROUTE_RE.finditer(code):
        name = m.group(1)
        open_idx = m.end() - 1
        close = _match_close(code, open_idx)
        lead = ""  # `Route::middleware('auth')->prefix('x')->post(...)`: attributes before the verb
        if name in _PHP_ATTRS:
            end = _chain_end(code, close + 1, "->")
            vm = _PHP_CHAINED_VERB_RE.search(code, close + 1, end)
            if not vm or re.search(r"->\s*group\s*\(", code[close + 1:vm.start()]):
                continue
            lead = "->" + code[m.end() - len(name) - 1:vm.start()]
            name = vm.group(1)
            open_idx = vm.end() - 1
            close = _match_close(code, open_idx)
        if name not in _PHP_VERBS and name != "match" and name not in _PHP_RESOURCE_NAMES:
            continue
        args = code[open_idx + 1:close]
        chain = lead + code[close + 1:_chain_end(code, close + 1, "->")]
        inside = sorted(s for s in spans if s[0] < m.start() < s[1])
        prefix = [s[2] for s in inside]
        pm = re.search(r"->\s*prefix\s*\(\s*['\"]([^'\"]*)['\"]", lead)
        if pm:
            prefix.append(pm.group(1))
        auth = any(s[3] for s in inside) or _php_middleware_auth(chain)
        wm = re.search(r"->\s*withoutMiddleware\s*\(([^)]*)\)", chain)
        if wm and _PHP_AUTH_RE.search(wm.group(1)):
            auth = False
        line = line_of(m.start())
        if name in _PHP_VERBS or name == "match":
            if name == "match":
                am = re.match(r"\s*\[([^\]]*)\]\s*,", args)
                if not am:
                    continue
                methods = [x.upper() for x in re.findall(r"['\"](\w+)['\"]", am.group(1))]
                sm = _PHP_STR_RE.match(args, am.end())
            else:
                methods = [_PHP_VERBS[name]]
                sm = _PHP_STR_RE.match(args)
            if not sm:
                continue
            for meth in methods:
                out.append(_route(meth, _join(*(prefix + [sm.group(1)])), fi, line, "laravel", auth))
            continue
        if name in ("resources", "apiResources"):  # Route::apiResources(['photos' => C::class])
            am = re.match(r"\s*\[", args)
            names = re.findall(r"['\"]([\w.-]+)['\"]\s*=>",
                               args[am.end():_match_close(args, am.end() - 1)]) if am else []
        else:
            sm = _PHP_STR_RE.match(args)
            names = [sm.group(1)] if sm else []
        if not names:
            continue
        actions = _PHP_RESOURCE + (_PHP_RESOURCE_WEB if name in ("resource", "resources") else [])
        only = re.search(r"->\s*only\s*\(([^)]*)\)", chain)
        exc = re.search(r"->\s*except\s*\(([^)]*)\)", chain)
        keep: Optional[Set[str]] = set(re.findall(r"\w+", only.group(1))) if only else None
        drop: Set[str] = set(re.findall(r"\w+", exc.group(1))) if exc else set()
        opt_only = re.search(_PHP_RES_OPT_RE % "only", args)
        opt_exc = re.search(_PHP_RES_OPT_RE % "except", args)
        if opt_only:
            found = set(re.findall(r"\w+", opt_only.group(1)))
            keep = found if keep is None else keep & found
        if opt_exc:
            drop |= set(re.findall(r"\w+", opt_exc.group(1)))
        for res_name in names:
            parts = res_name.split(".")
            base = "/".join(p + "/{%s}" % p.rstrip("s") for p in parts[:-1]) + "/" + parts[-1]
            for action, meth, suffix in actions:
                if (keep is not None and action not in keep) or action in drop:
                    continue
                out.append(_route(meth, _join(*(prefix + [base + suffix])), fi, line, "laravel",
                                  auth))
    for m in (_PHP_HEALTH_RE.finditer(code) if "withRouting" in code else ()):
        out.append(_route("GET", _join(m.group(1)), fi, line_of(m.start()), "laravel", False))
    return out


# ---------------------------------------------------------------- dispatch
_RAILS_ROUTES_RE = re.compile(r"(?:^|/)config/routes(?:/[\w-]+)?\.rb$")


def find_framework_routes(fi: FileInfo, text: str,
                          files: Optional[List[FileInfo]] = None) -> List[dict]:
    """Routes of one Java/Kotlin (Spring), C# (ASP.NET Core), Ruby (Rails config/routes.rb) or PHP
    (Laravel) file, in the routes.find_routes dict shape.

    `files` is the scan's walked file list. Rails controllers and ASP.NET base controllers are
    read only from it (gitignored files and symlinks out of the root never count). Without it,
    a walk scoped to app/controllers (Rails) or to the controller's folder (ASP.NET) is used.

    False-positive guards: comments are stripped before matching; Spring @FeignClient /
    @HttpExchange interfaces are HTTP clients and skipped; only config/routes.rb (and
    config/routes/*.rb) is read for Rails. Missed: Spring WebFlux RouterFunction routes, ASP.NET
    conventional routing (MapControllerRoute), auth applied by a global policy or security filter
    chain (lowers confidence in route_signals instead), Laravel `$this->middleware('auth')` in a
    controller constructor, and prefixes applied in another file (RouteServiceProvider, MapGroup
    passed between files).
    """
    ext = fi.ext
    if ext in (".java", ".kt"):
        if "Mapping" not in text:
            return []
        return _spring_routes(fi, strip_comments(text, fi.lang or "Java"))
    if ext == ".cs":
        if not re.search(r"Http(?:Get|Post|Put|Patch|Delete)|\.Map(?:Get|Post|Put|Patch|Delete"
                         r"|Methods|Group|HealthChecks)\b|\bRoute\(|\bAcceptVerbs\b", text):
            return []
        code = strip_comments(text, "C#")
        return _aspnet_controller_routes(fi, code, files) + _aspnet_minimal_routes(fi, code)
    if ext == ".rb":
        if not _RAILS_ROUTES_RE.search(fi.rel):
            return []
        return _rails_routes(fi, strip_comments(text, "Ruby"), files)
    if ext == ".php":
        if "Route::" not in text and "health" not in text:
            return []
        return _laravel_routes(fi, _php_code(text))
    return []


# ---------------------------------------------------------------- global auth policies
_GLOBAL_AUTH_RES = {
    ".java": re.compile(r"\.anyRequest\(\)\s*\.\s*authenticated\(\)|anyRequest\(\)\s*\.\s*hasRole"
                        r"|authorizeHttpRequests|authorizeRequests"),
    ".cs": re.compile(r"FallbackPolicy|MapControllers\(\)\s*\.\s*RequireAuthorization\(|"
                      r"AuthorizeFilter\(|RequireAuthenticatedUser\(\)"),
}
_GLOBAL_AUTH_RES[".kt"] = re.compile(_GLOBAL_AUTH_RES[".java"].pattern + r"|authorize\s*\(\s*anyRequest")


def global_auth_policy(fi: FileInfo, text: str) -> bool:
    """A Spring Security filter chain (authorizeHttpRequests / anyRequest().authenticated()) or an
    ASP.NET Core global policy (FallbackPolicy, RequireAuthenticatedUser, global AuthorizeFilter,
    MapControllers().RequireAuthorization()) in this file. Which routes it covers is not resolved
    (permitAll() matchers are not read)."""
    rx = _GLOBAL_AUTH_RES.get(fi.ext)
    if rx is None:
        return False
    return bool(rx.search(strip_comments(text, fi.lang or "Java")))
