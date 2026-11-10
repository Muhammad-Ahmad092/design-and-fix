"""Django/DRF adapter: view permissions, settings MIDDLEWARE and DEFAULT_PERMISSION_CLASSES."""
from __future__ import annotations

import re

from .core import (_LOCAL, PROTECTED, PUBLIC, UNPROTECTED, UNRESOLVED, AuthVerdict, Resolver,
                   call_args, split_lines)
from .django_views import _perm_kind, django_route_parts, django_view_ref

def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    rel = route["file"]
    lines = ctx.lines(rel)
    idx = route["line"] - 1
    parts = django_route_parts(lines, idx) if 0 <= idx < len(lines) else None
    ref = None
    if parts is not None:
        rest = parts[2]
        if parts[0] == "register":
            m = re.match(r"\s*,\s*([\w.]+)", rest)
            ref = ("class", m.group(1).split(".")[-1], ()) if m else None
        else:
            ref = django_view_ref(rest)
    view = ctx.django_views().lookup(ref, rel) if ref else None
    if view is not None:
        if view.public:
            return AuthVerdict(PUBLIC, view.public)
        if view.unresolved:
            return AuthVerdict(UNRESOLVED, view.unresolved)
    all_settings = ctx.django_settings()
    # Settings of the route's own app or service unit only: another project's MIDDLEWARE or
    # DRF defaults never apply here.
    settings = [s for s in all_settings if ctx.same_app(rel, s)]
    if not settings:
        if all_settings:
            return AuthVerdict(UNRESOLVED, "the Django settings in %s belong to another app or "
                                           "service; this route's own settings (MIDDLEWARE, "
                                           "DEFAULT_PERMISSION_CLASSES) are not in the scanned "
                                           "files" % all_settings[0])
        return AuthVerdict(UNRESOLVED, "Django settings (MIDDLEWARE, REST_FRAMEWORK "
                                       "DEFAULT_PERMISSION_CLASSES) are not in the scanned files")
    # DRF opts every APIView out of LoginRequiredMiddleware (DRF 3.16, Django 5.1+): DRF views
    # are decided by DEFAULT_PERMISSION_CLASSES below.
    drf_view = view is not None and view.drf
    for s in settings:
        if not drf_view and "LoginRequiredMiddleware" in "\n".join(ctx.lines(s)):
            if view is not None and view.exempt:
                break  # @login_not_required opts the view out of the middleware
            return AuthVerdict(PROTECTED, "LoginRequiredMiddleware in %s" % s)
    if view is None or not view.drf:
        return _LOCAL
    for s in settings:
        text = "\n".join(ctx.lines(s))
        m = re.search(r"DEFAULT_PERMISSION_CLASSES['\"]?\s*\]?\s*[:=]\s*", text)
        if not m:
            continue
        tail = split_lines(text[m.end():])
        value = tail[0]
        if value.strip()[:1] in "([":
            value = call_args(tail, 0, tail[0].index(value.strip()[0]), max_lines=10)[0]
        if _perm_kind(value or "AllowAny") == "auth":
            return AuthVerdict(PROTECTED, "DRF DEFAULT_PERMISSION_CLASSES in %s" % s)
        return AuthVerdict(UNPROTECTED, "DRF DEFAULT_PERMISSION_CLASSES in %s allows any" % s)
    return AuthVerdict(UNPROTECTED, "DRF default permission is AllowAny (no "
                                    "DEFAULT_PERMISSION_CLASSES in %s)" % ", ".join(settings))
