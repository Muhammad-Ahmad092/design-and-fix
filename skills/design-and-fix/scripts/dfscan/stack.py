from __future__ import annotations

import fnmatch
import os
from typing import List, Tuple

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
}


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(200_000).lower()
    except OSError:
        return ""


def detect_stack(files: List[FileInfo]) -> Tuple[List[str], List[str]]:
    stack = set()
    frameworks = set()
    for f in files:
        name = os.path.basename(f.rel)
        for pattern, label in MANIFEST_SIGNALS.items():
            if fnmatch.fnmatch(name, pattern):
                stack.add(label)
        if f.lang is not None:
            stack.add(_LANG_LABELS.get(f.lang, f.lang))
        needles = FRAMEWORK_SIGNALS.get(name)
        if needles:
            text = _read(f.abs)
            for needle, fw in needles:
                if needle.lower() in text:
                    frameworks.add(fw)
    if "Node.js / JavaScript" in stack:
        stack.discard("JavaScript")  # package.json and .js files are one ecosystem
    return sorted(stack), sorted(frameworks)
