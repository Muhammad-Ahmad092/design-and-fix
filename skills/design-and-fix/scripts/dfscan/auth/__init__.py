"""Auth resolution for unauth-write-route (see core.py). `from dfscan import auth;
auth.resolve(route, files, read)` is the entry point; ADAPTERS maps a route's framework name
to its adapter, one module per framework family."""
from __future__ import annotations

from . import aspnet, django, express, go, laravel, nest, python_web, rails, spring
from .core import (ADAPTERS, AUTH_MARKERS, HEALTH_RE, PROTECTED, PUBLIC, PUBLIC_AUTH_RE, STATUS_RE,
                   UNPROTECTED, UNRESOLVED, AuthVerdict, Resolver, auth_use, blank_strings,
                   call_args, code_text, has_auth, is_health_path, is_public_path, kind_of,
                   is_public_action, resolve, split_args, split_lines)
from .django_views import DjangoView, DjangoViews, django_route_parts, django_view_ref

ADAPTERS.update({
    "express": express.adapter,
    "nestjs": nest.adapter,
    "flask": python_web.flask_adapter,
    "fastapi": python_web.fastapi_adapter,
    "django": django.adapter,
    "gin": go.adapter,
    "echo": go.adapter,
    "chi": go.adapter,
    "spring": spring.adapter,
    "aspnetcore": aspnet.adapter,
    "rails": rails.adapter,
    "laravel": laravel.adapter,
})

__all__ = ["ADAPTERS", "AUTH_MARKERS", "HEALTH_RE", "PROTECTED", "PUBLIC", "PUBLIC_AUTH_RE",
           "STATUS_RE", "UNPROTECTED", "UNRESOLVED", "AuthVerdict", "DjangoView", "DjangoViews",
           "Resolver", "auth_use", "blank_strings", "call_args", "code_text",
           "django_route_parts", "django_view_ref", "has_auth", "is_health_path",
           "is_public_action", "is_public_path", "kind_of", "resolve", "split_args", "split_lines"]
