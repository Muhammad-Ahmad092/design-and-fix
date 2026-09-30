#!/usr/bin/env python3
"""
scan_repo.py — optional helper for Mode 2 (Existing Project) audits.

Scans a repo root and reports:
  - detected languages / stack (by manifest and extension signals)
  - candidate service/module folders to audit
  - rough size per folder (file count, approx LOC)

Does NOT read file contents for analysis — this is a scoping aid only.
The skill still reads actual source files itself when auditing.

Usage:
    python3 scan_repo.py <path> [--max-depth N] [--top N]

Output: JSON to stdout.
"""

import argparse
import json
import os
import sys

IGNORE_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".next", ".cache", "coverage",
    ".idea", ".vscode", "out",
}

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
    "docker-compose.yml": "Docker Compose present",
    "docker-compose.yaml": "Docker Compose present",
    "Dockerfile": "Containerized",
}

EXT_SIGNALS = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".ex": "Elixir", ".cs": "C#",
    ".sql": "SQL", ".proto": "Protocol Buffers (gRPC likely)",
    ".graphql": "GraphQL",
}


def should_ignore(dirname: str) -> bool:
    return dirname in IGNORE_DIRS or dirname.startswith(".")


def scan(root: str, max_depth: int):
    stack_signals = set()
    ext_counts = {}
    folders = []

    root = os.path.abspath(root)

    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1

        dirnames[:] = [d for d in dirnames if not should_ignore(d)]

        if depth > max_depth:
            dirnames[:] = []
            continue

        for fname in filenames:
            if fname in MANIFEST_SIGNALS:
                stack_signals.add(MANIFEST_SIGNALS[fname])
            ext = os.path.splitext(fname)[1]
            if ext in EXT_SIGNALS:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

        # Treat depth==1 and depth==2 folders as audit candidates
        # (typical: services/, apps/, packages/, src/<module>)
        if 1 <= depth <= 2 and filenames:
            code_files = [
                f for f in filenames
                if os.path.splitext(f)[1] in EXT_SIGNALS
            ]
            if code_files:
                approx_loc = 0
                for f in code_files:
                    fpath = os.path.join(dirpath, f)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                            approx_loc += sum(1 for _ in fh)
                    except OSError:
                        pass
                folders.append({
                    "path": os.path.relpath(dirpath, root),
                    "file_count": len(code_files),
                    "approx_loc": approx_loc,
                })

    for ext, count in ext_counts.items():
        if count > 0:
            stack_signals.add(EXT_SIGNALS[ext])

    folders.sort(key=lambda f: f["approx_loc"], reverse=True)

    return {
        "root": root,
        "detected_stack": sorted(stack_signals),
        "candidate_folders": folders,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Repo root to scan")
    parser.add_argument("--max-depth", type=int, default=3,
                         help="Max directory depth to walk (default: 3)")
    parser.add_argument("--top", type=int, default=15,
                         help="Max candidate folders to return (default: 15)")
    args = parser.parse_args()

    if not os.path.isdir(args.path):
        print(json.dumps({"error": f"not a directory: {args.path}"}))
        sys.exit(1)

    result = scan(args.path, args.max_depth)
    result["candidate_folders"] = result["candidate_folders"][:args.top]

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
