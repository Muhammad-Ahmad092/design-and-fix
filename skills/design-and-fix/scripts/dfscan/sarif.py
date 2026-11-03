from __future__ import annotations

import os
from urllib.parse import quote
from pathlib import Path
from typing import Dict, List

from . import registry
from .meta import PLUGIN_VERSION

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
LEVELS = {"Critical": "error", "High": "error", "Medium": "warning", "Low": "note"}


def _rules() -> List[dict]:
    out = []
    for rid in sorted(registry.RULES):
        m = registry.RULES[rid]
        out.append({
            "id": rid,
            "shortDescription": {"text": m.description},
            "fullDescription": {"text": m.limitations or m.description},
            "help": {"text": m.remediation},
            "defaultConfiguration": {"level": LEVELS.get(m.default_severity, "warning")},
            "properties": {"category": m.category},
        })
    return out


def _location(root: str, rel: str, line: int) -> dict:
    art = {"uri": quote(rel.replace("\\", "/"), safe="/"), "uriBaseId": "%SRCROOT%"}
    phys = {"artifactLocation": art}  # type: Dict[str, dict]
    folder = line <= 0 and (rel in ("", ".") or os.path.isdir(os.path.join(root, *rel.split("/"))))
    if not folder:
        phys["region"] = {"startLine": max(line, 1)}
    return {"physicalLocation": phys}


def to_sarif(result: dict) -> dict:
    """Convert a scan result to SARIF 2.1.0. URIs are root-relative POSIX paths against %SRCROOT%;
    folder-level signals (a folder path, line 0) carry no region."""
    root = result.get("root", "")
    results = []
    for s in result.get("signals", []):
        results.append({
            "ruleId": s["rule"],
            "level": LEVELS.get(s["severity_hint"], "warning"),
            "message": {"text": s.get("snippet") or registry.RULES[s["rule"]].description},
            "locations": [_location(root, s["file"], s["line"])],
            "partialFingerprints": {"dfscan/v1": s.get("fingerprint", "")},
        })
    base = {}  # type: Dict[str, dict]
    if root:
        uri = Path(root).as_uri()
        base["%SRCROOT%"] = {"uri": uri if uri.endswith("/") else uri + "/"}
    run = {
        "tool": {"driver": {"name": "design-and-fix", "version": PLUGIN_VERSION,
                            "rules": _rules()}},
        "results": results,
        "originalUriBaseIds": base,
    }
    return {"$schema": SARIF_SCHEMA, "version": "2.1.0", "runs": [run]}
