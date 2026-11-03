from __future__ import annotations

import datetime
import os
import subprocess
from typing import List

from . import registry

PLUGIN_VERSION = "1.0.2"
SCHEMA_VERSION = 3


def _has_git(root: str) -> bool:
    cur = os.path.abspath(root)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _revision(root: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    rev = proc.stdout.strip()
    return rev if proc.returncode == 0 and rev else "unknown"


def audit_meta(root: str, scope: List[str], config_hash: str, use_git: bool) -> dict:
    return {
        "plugin_version": PLUGIN_VERSION,
        "schema_version": SCHEMA_VERSION,
        "rule_versions": {rid: m.version for rid, m in sorted(registry.RULES.items())},
        "config_hash": config_hash,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo_revision": _revision(root) if use_git and _has_git(root) else "unknown",
        "scope": list(scope),
    }
