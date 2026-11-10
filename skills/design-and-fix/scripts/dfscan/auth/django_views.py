"""Django/DRF view index shared by the route inventory (routes.py) and the Django
auth adapter: resolves a urls.py view reference to its HTTP methods and view-level auth."""
from __future__ import annotations

import posixpath
import re
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from ..model import FileInfo
from .core import (Read, _decorators_above, _indent, _py_block, blank_strings, call_args,
                   code_text, split_args, split_lines)

_DRF_METHODS: Dict[str, Tuple[str, ...]] = {
    "CreateAPIView": ("POST",), "ListCreateAPIView": ("GET", "POST"),
    "UpdateAPIView": ("PUT", "PATCH"), "RetrieveUpdateAPIView": ("GET", "PUT", "PATCH"),
    "DestroyAPIView": ("DELETE",), "RetrieveDestroyAPIView": ("GET", "DELETE"),
    "RetrieveUpdateDestroyAPIView": ("GET", "PUT", "PATCH", "DELETE"),
    "ListAPIView": ("GET",), "RetrieveAPIView": ("GET",),
    "ModelViewSet": ("GET", "POST", "PUT", "PATCH", "DELETE"), "ReadOnlyModelViewSet": ("GET",),
    "CreateModelMixin": ("POST",), "UpdateModelMixin": ("PUT", "PATCH"),
    "DestroyModelMixin": ("DELETE",), "ListModelMixin": ("GET",), "RetrieveModelMixin": ("GET",),
}
_DJANGO_GENERIC_METHODS: Dict[str, Tuple[str, ...]] = {
    "CreateView": ("GET", "POST"), "UpdateView": ("GET", "POST"), "DeleteView": ("GET", "POST"),
    "FormView": ("GET", "POST"), "TemplateView": ("GET",), "ListView": ("GET",),
    "DetailView": ("GET",), "RedirectView": ("GET",),
}
_DRF_PLAIN = {"APIView", "GenericAPIView", "ViewSet", "GenericViewSet", "ViewSetMixin"}
_KNOWN_BASES = {"object", "View", "ABC", "Generic", "ContextMixin", "TemplateResponseMixin",
                "SingleObjectMixin", "MultipleObjectMixin", "FormMixin", "ModelFormMixin",
                "ProcessFormView", "BaseFormView", "BaseCreateView", "BaseUpdateView",
                "BaseDeleteView", "BaseDetailView", "BaseListView"} | _DRF_PLAIN
_HANDLERS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE",
             "list": "GET", "retrieve": "GET", "create": "POST", "update": "PUT",
             "partial_update": "PATCH", "destroy": "DELETE"}
_METHOD_ORDER = ("GET", "POST", "PUT", "PATCH", "DELETE")
# `auth` not followed by `or` (Author, authors_only) unless it is authorize/authorise:
# identifiers are CamelCase (IsAuthenticated, AuthRequiredMixin), so a word boundary alone
# would miss them.
_DJANGO_AUTH_RE = re.compile(
    r"auth(?!or(?!i[sz]))|login_required|permission_required|user_passes_test"
    r"|staff_member_required|superuser_required|PermissionRequired|UserPassesTest|AccessMixin|IsAdmin|LoginRequired", re.I)


def _auth_text(text: str) -> bool:
    """Auth marker in decorator or base-class text, string contents blanked
    (@extend_schema(tags=["authors"]) is not auth)."""
    return bool(_DJANGO_AUTH_RE.search(blank_strings(text)))


_CLASS_HEAD_RE = re.compile(r"^(\s*)class\s+(\w+)\s*(\(|:)")
_DEF_HEAD_RE = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\(")
_PERM_ATTR_RE = re.compile(r"^\s*permission_classes\s*(?::[^=\n]*)?=\s*")
_DJANGO_PATH_RE = re.compile(r"(?<![\w.])(?:re_)?path\(")
_DRF_REGISTER_RE = re.compile(r"\b\w+\.register\(")


class DjangoView(NamedTuple):
    rel: str
    methods: Tuple[str, ...]   # HTTP methods the view handles (empty: unknown)
    method_auth: Set[str]      # methods whose handler has its own auth decorator
    auth: str                  # class/function-level auth evidence ("" if none)
    public: str                # AllowAny / empty permission_classes evidence ("" if none)
    unresolved: str            # a base class outside the scanned files ("" if none)
    drf: bool
    exempt: bool = False       # @login_not_required (opts out of LoginRequiredMiddleware)


def _perm_kind(value: str) -> str:
    """'public' for AllowAny-only or empty permission classes, else 'auth' (any permission or
    ownership policy class counts as a check)."""
    names = [n.split(".")[-1] for n in re.findall(r"\b[A-Za-z_][\w.]*", value)]
    names = [n for n in names if n not in ("permission_classes", "Sequence", "Type", "List",
                                           "Tuple", "tuple", "list")]
    return "public" if all(n == "AllowAny" for n in names) else "auth"


def django_route_parts(lines: List[str], idx: int) -> Optional[Tuple[str, str, str]]:
    """(kind, path, rest) for a Django `path(...)`/`re_path(...)` or DRF `router.register(...)`
    call starting on lines[idx] (multi-line calls joined), or None. kind is "path"/"register";
    rest is the call text after the path string."""
    ln = lines[idx]
    for kind, rx in (("path", _DJANGO_PATH_RE), ("register", _DRF_REGISTER_RE)):
        m = rx.search(ln)
        if not m:
            continue
        inner, _ = call_args(lines, idx, m.end() - 1, max_lines=8)
        pm = re.match(r"\s*r?(['\"])([^'\"]*)\1(.*)", inner, re.S)
        if pm:
            return kind, pm.group(2), pm.group(3)
    return None


def django_view_ref(rest: str) -> Optional[Tuple[str, str, Tuple[str, ...]]]:
    """("class", name, as_view mapping methods) or ("func", name, ()) from the text after a
    path string; None for include(...) and unrecognised views."""
    if re.search(r"\binclude\(", rest):
        return None
    # Only a view passed directly: a wrapper (admin_view, custom decorators) may add auth.
    m = re.match(r"\s*,\s*([\w.]+)\.as_view\(\s*(\{[^}]*\})?", rest)
    if m:
        mapping = tuple(x.upper() for x in re.findall(
            r"['\"](get|post|put|patch|delete)['\"]\s*:", m.group(2) or "", re.I))
        return "class", m.group(1).split(".")[-1], mapping
    args = split_args(rest.lstrip(", "))
    if args:
        m = re.match(r"^([\w.]+)$", args[0])
        if m:
            return "func", m.group(1).split(".")[-1], ()
    return None


class DjangoViews:
    """Index of the scanned Python classes and top-level functions, resolving a urls.py view
    reference to the HTTP methods it handles and its view-level auth."""

    def __init__(self, files: List[FileInfo], read: Read,
                 lines_of: Optional[Callable[[FileInfo], List[str]]] = None) -> None:
        self._classes: Dict[str, List[Tuple[str, int]]] = {}
        self._funcs: Dict[str, List[Tuple[str, int]]] = {}
        self._lines: Dict[str, List[str]] = {}
        for f in files:
            if f.ext != ".py" or f.is_test:
                continue
            raw = read(f)
            if "class " not in raw and "def " not in raw:
                continue
            lines = lines_of(f) if lines_of else split_lines(code_text(raw, "py"))
            self._lines[f.rel] = lines
            for i, ln in enumerate(lines):
                m = _CLASS_HEAD_RE.match(ln)
                if m:
                    self._classes.setdefault(m.group(2), []).append((f.rel, i))
                    continue
                m = _DEF_HEAD_RE.match(ln)
                if m and not m.group(1):
                    self._funcs.setdefault(m.group(2), []).append((f.rel, i))

    @staticmethod
    def _nearest(cands: List[Tuple[str, int]], near: str) -> Tuple[str, int]:
        d = posixpath.dirname(near)

        def dist(c: Tuple[str, int]) -> Tuple[int, str]:
            cd = posixpath.dirname(c[0])
            common = posixpath.commonprefix([d + "/", cd + "/"]).count("/")
            return (-common, c[0])
        return min(cands, key=dist)

    def lookup(self, ref: Tuple[str, str, Tuple[str, ...]], near: str) -> Optional[DjangoView]:
        kind, name, mapping = ref
        if kind == "class":
            cands = self._classes.get(name)
            if not cands:
                return None
            rel, idx = self._nearest(cands, near)
            v = self._class(rel, idx, 0)
            if mapping:
                v = v._replace(methods=tuple(m for m in _METHOD_ORDER if m in mapping))
            return v
        cands = self._funcs.get(name)
        if not cands:
            return None
        rel, idx = self._nearest(cands, near)
        return self._func(rel, idx)

    def _func(self, rel: str, idx: int) -> DjangoView:
        deco = _decorators_above(self._lines[rel], idx)
        methods: Set[str] = set()
        m = re.search(r"@(?:api_view|require_http_methods)\(\s*[\[(]([^\])]*)", deco)
        if m:
            methods |= {x.upper() for x in re.findall(r"[A-Za-z]+", m.group(1))}
        elif re.search(r"@api_view\b", deco):
            methods.add("GET")
        for name, meth in (("require_POST", "POST"), ("require_GET", "GET"),
                           ("require_safe", "GET")):
            if "@" + name in deco:
                methods.add(meth)
        perm = re.search(r"@permission_classes\(\s*([\[(][^\])]*[\])])", deco)
        auth = public = ""
        if perm and _perm_kind(perm.group(1)) == "public":
            public = "AllowAny on view %s" % rel
        elif perm:
            auth = "permission_classes on view %s" % rel
        rest = re.sub(r"@(?:api_view|require_http_methods|permission_classes)\([^)]*\)", "", deco)
        if not auth and _auth_text(rest):
            auth = "auth decorator on view %s" % rel
        return DjangoView(rel, tuple(x for x in _METHOD_ORDER if x in methods), set(), auth,
                          public, "", "api_view" in deco, "login_not_required" in deco)

    def _class(self, rel: str, idx: int, depth: int) -> DjangoView:
        lines = self._lines[rel]
        head = lines[idx]
        bases_text = ""
        if _CLASS_HEAD_RE.match(head).group(3) == "(":
            bases_text, _ = call_args(lines, idx, head.index("("), max_lines=6)
        bases = [b.strip() for b in split_args(bases_text) if "=" not in b]
        deco = _decorators_above(lines, idx)
        body = _py_block(lines, idx)
        body_indent = min((_indent(t) for _, t in body if t.strip()), default=0)
        methods: Set[str] = set()
        method_auth: Set[str] = set()
        perm = ""
        for j, t in body:
            if _indent(t) != body_indent:
                continue
            m = _DEF_HEAD_RE.match(t)
            if m and m.group(2) == "dispatch":
                dispatch_deco = _decorators_above(lines, j)
                if "login_not_required" in dispatch_deco:
                    deco += " @login_not_required"
                elif _auth_text(dispatch_deco):
                    deco += " login_required"  # a decorated dispatch() guards every method
            if m and m.group(2) in _HANDLERS:
                meth = _HANDLERS[m.group(2)]
                methods.add(meth)
                if _auth_text(_decorators_above(lines, j)):
                    method_auth.add(meth)
            if not perm and _PERM_ATTR_RE.match(t):
                col = t.index("=") + 1
                value = t[col:]
                stripped = value.strip()
                if stripped[:1] in "([":
                    value, _ = call_args(lines, j, t.index(stripped[0], col), max_lines=8)
                    value = value or "AllowAny"  # an empty list allows everyone
                perm = _perm_kind(value)
        exempt = "login_not_required" in deco
        auth = ("auth decorator on class %s" % rel
                if _auth_text(deco.replace("login_not_required", "")) else "")
        unresolved = ""
        drf = False
        inherited_perm = ""
        for b in bases:
            last = b.split(".")[-1].split("[")[0]
            if last in _DRF_METHODS:
                methods |= set(_DRF_METHODS[last])
                drf = True
            elif last in _DJANGO_GENERIC_METHODS:
                methods |= set(_DJANGO_GENERIC_METHODS[last])
            elif last in _KNOWN_BASES:
                drf = drf or last in _DRF_PLAIN
            elif _auth_text(last):
                auth = auth or "base class %s of %s" % (last, rel)
            else:
                cands = self._classes.get(last)
                if not cands or depth >= 4:
                    unresolved = unresolved or (
                        "view base class %s (%s) is not in the scanned files" % (last, rel))
                    continue
                brel, bidx = self._nearest(cands, rel)
                if (brel, bidx) == (rel, idx):
                    continue
                sub = self._class(brel, bidx, depth + 1)
                methods |= set(sub.methods)
                method_auth |= {m for m in sub.method_auth if m not in methods or m in sub.methods}
                drf = drf or sub.drf
                auth = auth or sub.auth
                unresolved = unresolved or sub.unresolved
                if not inherited_perm:
                    inherited_perm = "public" if sub.public else ""
        public = ""
        if perm == "auth":
            auth = auth or "permission_classes on %s" % rel
        elif perm == "public":
            public = "AllowAny on view in %s" % rel
        elif inherited_perm == "public":
            public = "AllowAny inherited by view in %s" % rel
        return DjangoView(rel, tuple(m for m in _METHOD_ORDER if m in methods), method_auth,
                          auth, public, unresolved, drf, exempt)
