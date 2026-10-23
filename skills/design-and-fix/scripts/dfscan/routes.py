from __future__ import annotations

import re
from typing import Dict, List

from .imports import _read
from .model import FileInfo, Signal

AUTH_MARKERS = re.compile(
    r"auth|requireauth|authenticate|jwt|protect|passport|login_required|"
    r"permission_required|get_current_user|current_user|isauthenticated|"
    r"guard|verifytoken|bearer",
    re.I,
)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HEALTH_RE = re.compile(r"(^|/)(health|healthz|ready|readyz|live|livez|ping)/?$")

_Q = r"""['"`]"""
_JS_RE = re.compile(
    r"\b(?:app|router|server|r)\.(get|post|put|patch|delete)\(\s*(" + _Q + r")([^'\"`]*)\2(.*)")
_NEST_CTRL_RE = re.compile(r"@Controller\(\s*(?:" + _Q + r"([^'\"`]*)" + _Q + r")?")
_NEST_RE = re.compile(r"@(Get|Post|Put|Patch|Delete)\(\s*(?:" + _Q + r"([^'\"`]*)" + _Q + r")?(.*)")
_PY_VERB_RE = re.compile(r"^\s*@(\w+)\.(get|post|put|patch|delete)\(\s*(['\"])([^'\"]*)\3(.*)")
_PY_ROUTE_RE = re.compile(r"^\s*@(\w+)\.route\(\s*(['\"])([^'\"]*)\2(.*)")
_DJANGO_RE = re.compile(r"(?<![\w.])(?:re_)?path\(\s*r?(['\"])([^'\"]*)\1(.*)")
_GO_RECV = r"(?:r|g|e|app|api|mux|router|group|v\d+|\w*[Gg]roup|\w*[Rr]outer)"
_GO_UP_RE = re.compile(r"\b" + _GO_RECV + r"\.(GET|POST|PUT|PATCH|DELETE)\(\s*\"(/[^\"]*)\"(.*)")
_GO_CHI_RE = re.compile(r"\b" + _GO_RECV + r"\.(Get|Post|Put|Patch|Delete)\(\s*\"(/[^\"]*)\"(.*)")
_GO_HANDLE_RE = re.compile(r"\bhttp\.HandleFunc\(\s*\"([^\"]*)\"(.*)")
_USE_RE = re.compile(r"\b(?:router|app|r)\.[Uu]se\((.*)")
_METHODS_RE = re.compile(r"methods\s*=\s*[\[(]([^\])]*)[\])]", re.I)
_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s")


def _is_comment(stripped: str) -> bool:
    return stripped.startswith(("//", "#", "/*", "*"))


def _has_auth(text: str) -> bool:
    return bool(AUTH_MARKERS.search(text))


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
        m = _USE_RE.search(ln)
        if m and _has_auth(m.group(1)):
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


def _go_routes(f: FileInfo, lines: List[str], text: str) -> List[dict]:
    out: List[dict] = []
    upper_fw = "echo" if "labstack/echo" in text else "gin"
    use_auth = False
    for i, ln in enumerate(lines):
        if _is_comment(ln.strip()):
            continue
        m = _USE_RE.search(ln)
        if m and _has_auth(m.group(1)):
            use_auth = True
        m = _GO_UP_RE.search(ln)
        if m:
            out.append(_route(m.group(1), m.group(2), f, i + 1, upper_fw,
                              use_auth or _has_auth(m.group(3))))
            continue
        m = _GO_CHI_RE.search(ln)
        if m:
            out.append(_route(m.group(1).upper(), m.group(2), f, i + 1, "chi",
                              use_auth or _has_auth(m.group(3))))
            continue
        m = _GO_HANDLE_RE.search(ln)
        if m:
            out.append(_route("ANY", m.group(1), f, i + 1, "net/http",
                              use_auth or _has_auth(m.group(2))))
    return out


def find_routes(files: List[FileInfo]) -> List[dict]:
    routes: List[dict] = []
    for f in files:
        if f.is_test or f.lang is None:
            continue
        if f.ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            kind = "js"
        elif f.ext == ".py":
            kind = "py"
        elif f.ext == ".go":
            kind = "go"
        else:
            continue
        text = _read(f)
        lines = text.splitlines()
        if kind == "js":
            routes.extend(_js_routes(f, lines))
        elif kind == "py":
            routes.extend(_py_routes(f, lines, text))
        else:
            routes.extend(_go_routes(f, lines, text))
    return routes


def _service_folder(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) < 3:
        return parts[0] if len(parts) > 1 else "."
    return "/".join(parts[:2])


def route_signals(routes: List[dict], files: List[FileInfo]) -> List[Signal]:
    signals: List[Signal] = []
    for r in routes:
        if r["method"] in WRITE_METHODS and not r["auth_detected"]:
            signals.append(Signal("unauth-write-route", "Security", "Critical", r["file"],
                                  r["line"], "%s %s" % (r["method"], r["path"]), "medium"))
    by_folder: Dict[str, List[dict]] = {}
    for r in routes:
        by_folder.setdefault(_service_folder(r["file"]), []).append(r)
    for folder in sorted(by_folder):
        rs = by_folder[folder]
        if any(HEALTH_RE.search(r["path"].rstrip("/") or "/") for r in rs):
            continue
        first = min(rs, key=lambda r: (r["file"], r["line"]))
        signals.append(Signal("no-health-endpoint", "Observability", "Medium", first["file"],
                              first["line"], "no health endpoint in " + folder, "medium"))
    return signals
