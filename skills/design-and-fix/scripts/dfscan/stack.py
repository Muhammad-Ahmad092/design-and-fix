from __future__ import annotations

import fnmatch
import os
from typing import Iterable, List, Set, Tuple

from .model import FileInfo

MANIFEST_SIGNALS = {
    "package.json": "Node.js / JavaScript",
    "pnpm-lock.yaml": "Node.js (pnpm)",
    "yarn.lock": "Node.js (yarn)",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "Pipfile": "Python (pipenv)",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java/Kotlin (Gradle)",
    "build.gradle.kts": "Kotlin (Gradle)",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "mix.exs": "Elixir",
    "*.csproj": "C# / .NET",
    "*.fsproj": "C# / .NET",
    "*.sln": "C# / .NET",
    "docker-compose.yml": "Docker Compose present",
    "docker-compose.yaml": "Docker Compose present",
    "Dockerfile": "Containerized",
}

# Extension-derived labels that name the same ecosystem as a manifest label share its label.
_LANG_LABELS = {"C#": "C# / .NET"}

# manifest filename -> [(needle, framework name)]
FRAMEWORK_SIGNALS = {
    "package.json": [
        ('"express"', "express"), ('"fastify"', "fastify"),
        ('"@nestjs/core"', "@nestjs/core"), ('"koa"', "koa"), ('"next"', "next"),
    ],
    "requirements.txt": [("flask", "flask"), ("fastapi", "fastapi"), ("django", "django")],
    "pyproject.toml": [("flask", "flask"), ("fastapi", "fastapi"), ("django", "django")],
    "go.mod": [("gin-gonic/gin", "gin"), ("go-chi/chi", "chi"), ("labstack/echo", "echo")],
    "pom.xml": [("spring-boot", "spring-boot")],
    "build.gradle": [("org.springframework.boot", "spring-boot"), ("spring-boot", "spring-boot")],
    "build.gradle.kts": [("org.springframework.boot", "spring-boot"), ("spring-boot", "spring-boot")],
    "*.csproj": [("microsoft.aspnetcore", "aspnetcore"), ('sdk="microsoft.net.sdk.web"', "aspnetcore")],
    "Gemfile": [("gem 'rails'", "rails"), ('gem "rails"', "rails")],
    "composer.json": [('"laravel/framework"', "laravel")],
}


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(200_000).lower()
    except OSError:
        return ""


def _manifests_in(dirs: Iterable[str], known: Set[str]) -> List[Tuple[str, str]]:
    """(basename, absolute path) of manifest files directly inside each dir, skipping known paths."""
    out: List[Tuple[str, str]] = []
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            path = os.path.join(d, name)
            key = os.path.normcase(os.path.abspath(path))
            if key in known or not os.path.isfile(path):
                continue
            if any(fnmatch.fnmatch(name, p) for p in MANIFEST_SIGNALS):
                known.add(key)
                out.append((name, path))
    return out


def detect_stack(files: List[FileInfo],
                 extra_manifest_dirs: Iterable[str] = ()) -> Tuple[List[str], List[str]]:
    """Languages/ecosystems and frameworks from the listed files plus the manifests found
    directly inside `extra_manifest_dirs` (absolute paths; read-only, manifests only)."""
    stack = set()
    frameworks = set()
    entries = [(os.path.basename(f.rel), f.abs, f.lang) for f in files]
    known = {os.path.normcase(os.path.abspath(f.abs)) for f in files}
    entries += [(name, path, None) for name, path in _manifests_in(extra_manifest_dirs, known)]
    for name, path, lang in entries:
        for pattern, label in MANIFEST_SIGNALS.items():
            if fnmatch.fnmatch(name, pattern):
                stack.add(label)
        if lang is not None:
            stack.add(_LANG_LABELS.get(lang, lang))
        needles = [n for pattern, ns in FRAMEWORK_SIGNALS.items()
                   if fnmatch.fnmatch(name, pattern) for n in ns]
        if needles:
            text = _read(path)
            for needle, fw in needles:
                if needle.lower() in text:
                    frameworks.add(fw)
    if "Node.js / JavaScript" in stack:
        stack.discard("JavaScript")  # package.json and .js files are one ecosystem
    return sorted(stack), sorted(frameworks)
