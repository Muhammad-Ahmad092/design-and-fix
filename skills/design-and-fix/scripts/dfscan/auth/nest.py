"""NestJS adapter: global guards (useGlobalGuards, APP_GUARD) and @Public()-style opt-outs."""
from __future__ import annotations

import posixpath
import re
from typing import List, Optional, Tuple

from .core import _LOCAL, PROTECTED, PUBLIC, UNRESOLVED, AuthVerdict, Resolver, call_args

_NEST_PUBLIC_RE = re.compile(r"@(?:Public|IsPublic|AllowAnonymous|Anonymous|SkipAuth|NoAuth)\(")
_NEST_GUARD_AUTH_RE = re.compile(r"auth|jwt|session|passport|role|permission|acl|access|clerk"
                                 r"|firebase|cognito|keycloak", re.I)


_NestScan = Tuple[List[Tuple[str, str]], List[str], List[str]]


def _nest_scan(ctx: Resolver) -> _NestScan:
    """([(file, evidence)] of global auth guards, bootstrap/module files, NestFactory files),
    over every scanned JS/TS file once, in file order."""
    def build() -> _NestScan:
        guards: List[Tuple[str, str]] = []
        boots: List[str] = []
        factories: List[str] = []
        for f in ctx.of_kind("js"):
            raw = ctx.raw(f.rel)
            if "NestFactory" not in raw and "@Module" not in raw and "APP_GUARD" not in raw:
                continue
            boots.append(f.rel)
            if "NestFactory" in raw:
                factories.append(f.rel)
            lines = ctx.lines(f.rel)
            for i, ln in enumerate(lines):
                m = re.search(r"\buseGlobalGuards\(", ln)
                if m and _NEST_GUARD_AUTH_RE.search(call_args(lines, i, m.end() - 1)[0]):
                    guards.append((f.rel, "app.useGlobalGuards at %s:%d" % (f.rel, i + 1)))
                    break
                if re.search(r"\bprovide\s*:\s*APP_GUARD\b", ln):
                    near = " ".join(lines[max(0, i - 3):i + 4])
                    g = re.search(r"\buse(?:Class|Existing)\s*:\s*(\w+)", near)
                    if g and _NEST_GUARD_AUTH_RE.search(g.group(1)):
                        guards.append((f.rel, "APP_GUARD %s at %s:%d" % (g.group(1), f.rel, i + 1)))
                        break
        return guards, boots, factories
    return ctx.memo("nest_scan", build)  # type: ignore[return-value]


def _nest_unit(ctx: Resolver, rel: str) -> Optional[str]:
    """The NestJS app a file belongs to: the deeper of its app/service unit (a folder with a
    NestFactory.create entry point is a service root) and its nearest package.json folder."""
    def build() -> set:
        return {posixpath.dirname(f.rel) for f in ctx.files
                if posixpath.basename(f.rel) == "package.json"}
    pkgs = ctx.memo("nest_pkg_dirs", build)
    d = posixpath.dirname(rel)
    while d not in pkgs and d:  # type: ignore[operator]
        d = posixpath.dirname(d)
    pkg = d if d in pkgs else None  # type: ignore[operator]
    unit = ctx.app_unit(rel)
    cands = [u for u in (unit, pkg) if u is not None]
    return max(cands, key=lambda u: (0 if u == "" else u.count("/") + 1)) if cands else None


def _nest_global(ctx: Resolver, rel: str) -> Tuple[str, bool, str]:
    """(evidence of a global auth guard of the route's own app or "", whether a NestJS
    bootstrap/module of that app was scanned, evidence of a guard whose app cannot be decided
    or ""). Guards and bootstrap files of another app (a unit with its own NestFactory entry
    point) never apply; with no unit at all every file counts, as before."""
    def build() -> Tuple[str, bool, str]:
        guards, boots, factories = _nest_scan(ctx)
        unit = _nest_unit(ctx, rel)
        if unit is None:
            return (guards[0][1] if guards else ""), bool(boots), ""
        apps = {_nest_unit(ctx, f) for f in factories}
        same = [e for f, e in guards if _nest_unit(ctx, f) == unit]
        if same:
            return same[0], True, ""
        boot = any(_nest_unit(ctx, f) == unit for f in boots)
        # A guard in a shared library or a parent folder that is not another app's unit may
        # still be imported by this app's module.
        other = [e for f, e in guards if _nest_unit(ctx, f) not in apps]
        return "", boot, other[0] if other else ""
    return ctx.memo("nest_global:" + str(_nest_unit(ctx, rel)),  # type: ignore[return-value]
                    build)


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    guard, boot, maybe = _nest_global(ctx, route["file"])
    if not guard and maybe:
        return AuthVerdict(UNRESOLVED, "global guard %s is outside this NestJS app's folder; "
                                       "whether the app imports it is not resolved" % maybe)
    if guard:
        lines = ctx.lines(route["file"])
        idx = route["line"] - 1
        j = idx
        while j >= 0 and (j == idx or lines[j].strip().startswith("@")):
            if _NEST_PUBLIC_RE.search(lines[j]):
                return AuthVerdict(PUBLIC, "public decorator under global guard (%s)" % guard)
            j -= 1
        c = idx
        while c >= 0 and not re.match(r"^\s*(?:export\s+)?(?:default\s+)?class\s", lines[c]):
            c -= 1
        c -= 1
        while c >= 0 and lines[c].strip().startswith("@"):
            if _NEST_PUBLIC_RE.search(lines[c]):
                return AuthVerdict(PUBLIC, "public controller under global guard (%s)" % guard)
            c -= 1
        return AuthVerdict(PROTECTED, guard)
    if not boot:
        return AuthVerdict(UNRESOLVED, "NestJS global guards (APP_GUARD, app.useGlobalGuards) are "
                                       "registered in a module or main.ts not in the scanned files")
    return _LOCAL
