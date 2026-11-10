"""ASP.NET Core adapter: global authorization (FallbackPolicy for every endpoint; a global
AuthorizeFilter or MapControllers().RequireAuthorization() for controllers) with [AllowAnonymous]
/ .AllowAnonymous() opting out, and the startup (Program.cs / Startup.cs) outside the scan."""
from __future__ import annotations

import re
from typing import List, Tuple

from ..text import strip_comments
from .core import _LOCAL, PROTECTED, PUBLIC, UNRESOLVED, AuthVerdict, Resolver, public_action

# FallbackPolicy = <policy> (not null) / SetFallbackPolicy(<policy>): every endpoint without its
# own authorization metadata needs it.
_FALLBACK_RE = re.compile(r"\bFallbackPolicy\s*=\s*(?!null\b)\S"
                          r"|\bSetFallbackPolicy\s*\(\s*(?!null\b)\S")
_FILTER_RE = re.compile(r"\bFilters\s*\.\s*Add\s*(?:<\s*AuthorizeFilter\s*>"
                        r"|\(\s*(?:new\s+AuthorizeFilter\b|typeof\s*\(\s*AuthorizeFilter\s*\)))")
_MAP_CONTROLLERS_RE = re.compile(r"\bMapControllers\s*\(\s*\)\s*\.\s*RequireAuthorization\s*\(")
_STARTUP_RE = re.compile(r"\bWebApplication\s*\.\s*Create(?:Builder|SlimBuilder)?\s*\("
                         r"|\bUseStartup\s*<|\bConfigureServices\s*\("
                         r"|\bAddControllers(?:WithViews)?\s*\(|\bMapControllers\s*\(|\bConfigureWebHost")


_Globals = Tuple[List[Tuple[str, str, str]], List[str]]


def _globals(ctx: Resolver) -> _Globals:
    """([(kind "fallback" | "controllers", evidence, file)], startup files)."""
    def build() -> _Globals:
        found: List[Tuple[str, str, str]] = []
        startup: List[str] = []
        for f in ctx.files:
            if f.ext != ".cs" or f.is_example:
                continue
            raw = ctx.raw(f.rel)
            if not _STARTUP_RE.search(raw) and "FallbackPolicy" not in raw \
                    and "AuthorizeFilter" not in raw:
                continue
            code = strip_comments(raw, "C#")
            if _STARTUP_RE.search(code):
                startup.append(f.rel)
            for key, rx, what in (("fallback", _FALLBACK_RE, "FallbackPolicy"),
                                  ("controllers", _FILTER_RE, "global AuthorizeFilter"),
                                  ("controllers", _MAP_CONTROLLERS_RE,
                                   "MapControllers().RequireAuthorization()")):
                m = rx.search(code)
                if m:
                    found.append((key, "%s in %s:%d" % (
                        what, f.rel, code.count("\n", 0, m.start()) + 1), f.rel))
        return found, startup
    return ctx.memo("aspnet_globals", build)  # type: ignore[return-value]


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    """Global policies and the startup count only within the route's own app (the folder with
    its .csproj) or service unit (Resolver.same_app)."""
    extra = ctx.route_extra(route)
    all_found, all_startup = _globals(ctx)
    rel = route["file"]
    found = [(k, e) for k, e, f in all_found if ctx.same_app(rel, f)]
    kinds = ("fallback", "controllers") if extra.get("kind") == "controller" else ("fallback",)
    policy = next((e for k in kinds for kk, e in found if kk == k), "")
    if extra.get("anon") and (policy or extra.get("class_auth")):
        return AuthVerdict(PUBLIC, "[AllowAnonymous] opts out of %s"
                           % (policy or "the controller's [Authorize]"))
    if policy:
        return AuthVerdict(PROTECTED, policy)
    if public_action(route, extra):
        return AuthVerdict(PUBLIC, "authentication action (public by design)")
    if ctx.signup_route(route):
        return AuthVerdict(PUBLIC, "sign-up route (public by design)")
    if not any(ctx.same_app(rel, s) for s in all_startup):
        return AuthVerdict(UNRESOLVED, "ASP.NET Core startup (Program.cs / Startup.cs, where a "
                                       "FallbackPolicy or global AuthorizeFilter would be set) "
                                       "is not in the scanned files")
    return _LOCAL
