"""Laravel adapter: controller middleware ($this->middleware('auth')->only/except(...) in the
constructor, Laravel 11 HasMiddleware `new Middleware('auth', except: [...])`, parents followed)
and the middleware of the group that loads the route file (RouteServiceProvider,
bootstrap/app.php, a route file that requires another), with Kernel middleware groups."""
from __future__ import annotations

import posixpath
import re
from typing import Dict, List, Optional, Set, Tuple

from ..frameworks import (_PHP_AUTH_RE, _blank_strings, _match_close, _php_code, _php_groups,
                          _php_uses)
from .core import (_LOCAL, PROTECTED, PUBLIC, UNRESOLVED, AuthVerdict, Resolver,
                   public_action)

_CLASS_RE = re.compile(r"\bclass\s+(\w+)(?:\s+extends\s+([\w\\]+))?")
_NS_RE = re.compile(r"(?m)^[ \t]*namespace\s+([\w\\]+)\s*;")  # [ \t]: never crosses lines
_THIS_MW_RE = re.compile(r"\$this\s*->\s*middleware\s*\(")
_STATIC_MW_RE = re.compile(r"\bfunction\s+middleware\s*\(\s*\)\s*(?::\s*array\s*)?\{")
_NEW_MW_RE = re.compile(r"\bnew\s+(?:\\?[\w\\]*\\)?Middleware\s*\(")
_LIST_RE = re.compile(r"['\"](\w+)['\"]")
# (only actions or None, except actions, evidence)
_Mw = Tuple[Optional[Set[str]], Set[str], str]


def _names(text: str) -> Set[str]:
    return {n.lower() for n in _LIST_RE.findall(text)}


def _controller_middleware(rel: str, code: str) -> List[_Mw]:
    """Auth middleware declared by one controller file."""
    blank = _blank_strings(code)
    out: List[_Mw] = []
    for m in _THIS_MW_RE.finditer(blank):
        close = _match_close(code, m.end() - 1)
        if not _PHP_AUTH_RE.search(code[m.end():close]):
            continue
        only: Optional[Set[str]] = None
        exc: Set[str] = set()
        pos = close + 1
        while True:  # ->only([...]) / ->except('show') links after the call
            lm = re.compile(r"\s*->\s*(only|except)\s*\(").match(code, pos)
            if not lm:
                break
            lclose = _match_close(code, lm.end() - 1)
            names = _names(code[lm.end():lclose])
            if lm.group(1) == "only":
                only = names
            else:
                exc |= names
            pos = lclose + 1
        line = code.count("\n", 0, m.start()) + 1
        out.append((only, exc, "$this->middleware(%s) in %s:%d"
                    % (code[m.end():close].strip(), rel, line)))
    sm = _STATIC_MW_RE.search(blank)
    if sm:
        body_end = _match_close(code, sm.end() - 1)
        body = code[sm.end():body_end]
        line = code.count("\n", 0, sm.start()) + 1
        rest = body
        for nm in reversed(list(_NEW_MW_RE.finditer(_blank_strings(body)))):
            end = _match_close(body, nm.end() - 1)
            args = body[nm.end():end]
            rest = rest[:nm.start()] + " " * (end + 1 - nm.start()) + rest[end + 1:]
            if not _PHP_AUTH_RE.search(args.split(",", 1)[0]):
                continue
            om = re.search(r"\bonly\s*:\s*(\[[^\]]*\]|['\"]\w+['\"])", args)
            em = re.search(r"\bexcept\s*:\s*(\[[^\]]*\]|['\"]\w+['\"])", args)
            out.append((_names(om.group(1)) if om else None, _names(em.group(1)) if em else set(),
                        "HasMiddleware middleware() in %s:%d" % (rel, line)))
        if _PHP_AUTH_RE.search(rest):  # plain entries in the returned array: 'auth', ...
            out.append((None, set(), "HasMiddleware middleware() in %s:%d" % (rel, line)))
    return out


class _Php:
    """Per-scan index of PHP classes: short name -> [(file, fully qualified name, parent)]."""

    def __init__(self, ctx: Resolver) -> None:
        self.ctx = ctx
        self.classes: Dict[str, List[Tuple[str, str, str]]] = {}
        self.code: Dict[str, str] = {}
        self.mw: Dict[str, List[_Mw]] = {}
        for f in ctx.files:
            if f.ext != ".php" or f.is_example:
                continue
            raw = ctx.raw(f.rel)
            if "class" not in raw:
                continue
            code = self.text(f.rel)
            ns = _NS_RE.search(code)
            for cm in _CLASS_RE.finditer(_blank_strings(code)):
                fq = (ns.group(1) + "\\" if ns else "") + cm.group(1)
                self.classes.setdefault(cm.group(1), []).append((f.rel, fq, cm.group(2) or ""))

    def text(self, rel: str) -> str:
        if rel not in self.code:
            self.code[rel] = _php_code(self.ctx.raw(rel))
        return self.code[rel]

    def find(self, name: str, hint: str, near: str = "") -> Optional[Tuple[str, str, str]]:
        """Class `name` (short or qualified), preferring a candidate in the same app as the file
        `near`, then the one whose namespace shares the longest suffix with `hint` (a fully or
        partly qualified name)."""
        short = name.rsplit("\\", 1)[-1]
        cands = self.classes.get(short, [])
        if not cands:
            return None
        want = [p.lower() for p in hint.strip("\\").split("\\") if p]

        def score(c: Tuple[str, str, str]) -> Tuple[int, int, int]:
            have = [p.lower() for p in c[1].split("\\")]
            k = 0
            while k < min(len(have), len(want)) and have[-1 - k] == want[-1 - k]:
                k += 1
            return (0 if not near or self.ctx.same_app(near, c[0]) else 1), -k, len(c[0])
        return sorted(cands, key=score)[0]

    def middleware(self, rel: str) -> List[_Mw]:
        if rel not in self.mw:
            self.mw[rel] = _controller_middleware(rel, self.text(rel))
        return self.mw[rel]


def _php(ctx: Resolver) -> _Php:
    return ctx.memo("laravel_php", lambda: _Php(ctx))  # type: ignore[return-value]


def _controller_auth(ctx: Resolver, cls: Tuple[str, str, str], action: str) -> str:
    """Evidence of auth middleware covering `action` in the controller or its parents."""
    php = _php(ctx)
    act = action.lower()
    seen: Set[str] = set()
    for _ in range(5):
        rel, fq, parent = cls
        if fq in seen:
            break
        seen.add(fq)
        for only, exc, evidence in php.middleware(rel):
            if (only is None or act in only) and act not in exc:
                return evidence
        if not parent:
            break
        ns = fq.rsplit("\\", 1)[0] if "\\" in fq else ""
        nxt = php.find(parent, parent if "\\" in parent else ns + "\\" + parent, rel)
        if nxt is None:
            break
        cls = nxt
    return ""


_KERNEL_GROUP_RE = r"['\"]%s['\"]\s*=>\s*\["


def _group_has_auth(ctx: Resolver, names: List[str], rel: str) -> str:
    """Evidence when a middleware group named here (Kernel $middlewareGroups of the route file's
    own app) includes auth."""
    for f in ctx.files:
        if f.ext != ".php" or "middlewareGroups" not in ctx.raw(f.rel) \
                or not ctx.same_app(rel, f.rel):
            continue
        code = _php(ctx).text(f.rel)
        for name in names:
            m = re.search(_KERNEL_GROUP_RE % re.escape(name), code)
            if m and _PHP_AUTH_RE.search(code[m.end():_match_close(code, m.end() - 1)]):
                return "middleware group '%s' in %s" % (name, f.rel)
    return ""


def _path_matches(literal: str, rel: str) -> bool:
    """A path string in a loader ('routes/api.php', '/../routes/api.php', '/auth.php') names the
    route file rel: its tail after leading ./, ../ and / matches the end of rel."""
    tail = re.sub(r"^(?:\.{1,2}/|/)+", "", literal.replace("\\", "/"))
    return bool(tail) and (rel == tail or rel.endswith("/" + tail))


def _loader(ctx: Resolver, rel: str, depth: int = 0) -> Tuple[str, str]:
    """("protected" | "plain" | "missing", evidence) for the group that loads route file rel."""
    cache: Dict[str, Tuple[str, str]] = ctx.memo("laravel_loaders", dict)  # type: ignore
    if rel in cache:
        return cache[rel]
    res: Tuple[str, str] = ("missing", "")
    base = posixpath.basename(rel)
    ref = re.compile(r"['\"]([^'\"\n]*?\b%s)['\"]" % re.escape(base))
    php = _php(ctx)
    for f in ctx.files:
        # Only a loader in the route file's own app (composer.json / artisan folder) or
        # service counts: another app's RouteServiceProvider never applies.
        if f.ext != ".php" or f.rel == rel or base not in ctx.raw(f.rel) \
                or not ctx.same_app(rel, f.rel):
            continue
        code = php.text(f.rel)
        for m in ref.finditer(code):
            if not _path_matches(m.group(1), rel):
                continue
            if res[0] == "missing":
                res = ("plain", "")
            start = max(code.rfind(";", 0, m.start()), code.rfind("{", 0, m.start())) + 1
            stmt = code[start:m.start()]
            names: List[str] = []
            for mm in re.finditer(r"(?:->|::)\s*middleware\s*\(|['\"]middleware['\"]\s*=>\s*", stmt):
                if stmt[mm.end() - 1] == "(":
                    names += _LIST_RE.findall(stmt[mm.end():_match_close(stmt, mm.end() - 1)])
                else:
                    seg = re.match(r"\s*(\[[^\]]*\]|['\"][^'\"]*['\"])", stmt[mm.end():])
                    names += _LIST_RE.findall(seg.group(1)) if seg else []
            line = code.count("\n", 0, m.start()) + 1
            if any(_PHP_AUTH_RE.match("'%s'" % n) for n in names):
                res = (PROTECTED, "auth middleware on the group loading %s (%s:%d)"
                       % (rel, f.rel, line))
                break
            group = _group_has_auth(ctx, names, rel) if names else ""
            if group:
                res = (PROTECTED, "%s loads %s with %s" % (f.rel, rel, group))
                break
            # A route file that requires this one: its enclosing groups and its own loader.
            if "Route::" in code and depth < 3:
                if any(s[3] for s in _php_groups(code) if s[0] < m.start() < s[1]):
                    res = (PROTECTED, "required inside an auth group in %s:%d" % (f.rel, line))
                    break
                up = _loader(ctx, f.rel, depth + 1)
                if up[0] == PROTECTED:
                    res = up
                    break
        if res[0] == PROTECTED:
            break
    cache[rel] = res
    return res


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    extra = ctx.route_extra(route)
    ctrl, action = extra.get("controller", ""), extra.get("action", "")
    if public_action(route, extra):
        return AuthVerdict(PUBLIC, "authentication action (public by design)")
    if ctx.signup_route(route):
        return AuthVerdict(PUBLIC, "sign-up route (public by design)")
    cls = None
    if ctrl:
        php = _php(ctx)
        uses = ctx.memo("laravel_uses:" + route["file"], lambda: _php_uses(php.text(route["file"])))
        hint = ctrl if "\\" in ctrl else uses.get(ctrl) or (  # type: ignore[union-attr]
            extra.get("namespace", "") + "\\" + ctrl)
        cls = php.find(ctrl, hint, route["file"])
        if cls is not None and not extra.get("without_auth"):
            evidence = _controller_auth(ctx, cls, action)
            if evidence:
                return AuthVerdict(PROTECTED, evidence)
    state, evidence = _loader(ctx, route["file"])
    if state == PROTECTED:
        return AuthVerdict(PROTECTED, evidence)
    if state == "missing":
        return AuthVerdict(UNRESOLVED, "%s is loaded by a RouteServiceProvider or bootstrap/app.php "
                                       "that is not in the scanned files (its group middleware is "
                                       "unknown)" % route["file"])
    if ctrl and cls is None:
        return AuthVerdict(UNRESOLVED, "controller %s (its middleware) is not in the scanned files"
                           % ctrl)
    return _LOCAL

