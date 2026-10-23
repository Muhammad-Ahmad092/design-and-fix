from __future__ import annotations

import os
from typing import Dict, List, Optional

from .hotspots import compute_hotspots
from .imports import build_graph, graph_signals
from .model import Signal
from .patterns import run_rules
from .routes import find_routes, route_signals
from .stack import detect_stack
from .walker import candidate_folders, normalize_scope, walk

SCHEMA_VERSION = 2
RULE_CAP = 200
MAX_SIGNAL_ROWS = 50
MAX_ROUTE_ROWS = 30
MAX_ROUTES = 500  # routes[] in JSON output; summary.routes_total has the full count


def _in_scope(rel: str, scopes: List[str]) -> bool:
    return any(rel == s or rel.startswith(s + "/") for s in scopes)


EMPTY_GRAPH = {"modules": 0, "edges": 0, "cycles": [], "god_modules": []}
FOLDERS_ONLY_NOTE = "folders-only: analyzers skipped"


def scan(root: str, scope: Optional[List[str]] = None, list_depth: int = 2,
         top: int = 15, use_git: bool = True, since: str = "12.months",
         folders_only: bool = False) -> dict:
    root = os.path.abspath(root)
    norm_scope = [s for s in (normalize_scope(x) for x in (scope or [])) if s]
    # Stack detection and candidate folders always cover the whole root;
    # imports, routes, signals and hotspots are restricted to the scope.
    all_files = walk(root)
    stack, frameworks = detect_stack(all_files)
    files = [f for f in all_files if _in_scope(f.rel, norm_scope)] if norm_scope else all_files
    code = [f for f in files if f.lang is not None]
    notes: List[str] = []
    graph: dict = dict(EMPTY_GRAPH)
    routes: List[dict] = []
    raw: List[Signal] = []
    hotspots: dict = {"available": False, "since": since, "files": [], "note": None}
    if folders_only:
        notes.append(FOLDERS_ONLY_NOTE)
    else:
        graph = build_graph(files, norm_scope)
        routes = find_routes(files)
        folders = [f for f in candidate_folders(files, 2, 10 ** 9)
                   if not norm_scope or _in_scope(f["path"], norm_scope)]
        raw = (graph_signals(graph) + route_signals(routes, files)
               + run_rules(files, folders, norm_scope, all_files))
        hotspots = compute_hotspots(root, files, since, use_git, top)
        if hotspots["note"]:
            notes.append(hotspots["note"])
    # At most RULE_CAP signals per rule (graph, route and pattern rules alike).
    per_rule: Dict[str, int] = {}
    signals: List[dict] = []
    for s in raw:
        per_rule[s.rule] = per_rule.get(s.rule, 0) + 1
        if per_rule[s.rule] <= RULE_CAP:
            signals.append(s.to_dict())
    truncated = {r: n for r, n in per_rule.items() if n > RULE_CAP}
    by_category: Dict[str, int] = {}
    for s in signals:
        by_category[s["category"]] = by_category.get(s["category"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "root": root,
        "scope": norm_scope,
        "detected_stack": stack,
        "frameworks": frameworks,
        "candidate_folders": candidate_folders(all_files, list_depth, top),
        "import_graph": graph,
        "routes": routes[:MAX_ROUTES],
        "hotspots": hotspots,
        "signals": signals,
        "summary": {
            "files_scanned": len(files),
            "code_files": len(code),
            "approx_loc": sum(f.loc for f in code),
            "test_files": sum(1 for f in code if f.is_test),
            "signals_by_category": by_category,
            "truncated": truncated,
            "routes_total": len(routes),
        },
        "notes": notes,
    }


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(result: dict) -> str:
    summ = result["summary"]
    out: List[str] = ["# Scan summary", ""]
    out.append("Root: `%s`" % result["root"])
    if result.get("scope"):
        out.append("Scope: " + ", ".join("`%s`" % s for s in result["scope"]))
    out.append("Files scanned: %d | Code files: %d | Approx LOC: %d | Test files: %d" % (
        summ.get("files_scanned", 0), summ["code_files"], summ["approx_loc"], summ["test_files"]))
    out += ["", "## Stack", ""]
    out.append("Languages: " + (", ".join(result["detected_stack"]) or "none"))
    out.append("Frameworks: " + (", ".join(result["frameworks"]) or "none"))
    out += ["", "## Candidate folders", "", "| Path | Files | Approx LOC |", "|---|---|---|"]
    for f in result["candidate_folders"]:
        out.append("| %s | %d | %d |" % (_cell(f["path"]), f["file_count"], f["approx_loc"]))
    out += ["", "## Signals by category", "", "| Category | Count |", "|---|---|"]
    for cat, n in sorted(summ["signals_by_category"].items()):
        out.append("| %s | %d |" % (_cell(cat), n))
    signals = result["signals"]
    out += ["", "## Signals", "", "| Rule | Severity | File:line |", "|---|---|---|"]
    for s in signals[:MAX_SIGNAL_ROWS]:
        loc = s["file"] + (":%d" % s["line"] if s["line"] else "")
        out.append("| %s | %s | %s |" % (_cell(s["rule"]), _cell(s["severity_hint"]), _cell(loc)))
    if len(signals) > MAX_SIGNAL_ROWS:
        out += ["", "_%d more signals not shown._" % (len(signals) - MAX_SIGNAL_ROWS)]
    out += ["", "## Import cycles", ""]
    cycles = result["import_graph"].get("cycles", [])
    if cycles:
        out += ["- " + " -> ".join(c) for c in cycles[:20]]
    else:
        out.append("None detected.")
    routes = result["routes"]
    out += ["", "## Routes", "", "| Method | Path | File:line | Auth |", "|---|---|---|---|"]
    for r in routes[:MAX_ROUTE_ROWS]:
        out.append("| %s | %s | %s:%s | %s |" % (
            _cell(r["method"]), _cell(r["path"]), _cell(r["file"]), r["line"],
            "yes" if r.get("auth_detected") else "no"))
    routes_total = summ.get("routes_total", len(routes))
    if routes_total > MAX_ROUTE_ROWS:
        shown = min(len(routes), MAX_ROUTE_ROWS)
        out += ["", "_%d more routes not shown._" % (routes_total - shown)]
    hs = result["hotspots"]
    out += ["", "## Hotspots", ""]
    if hs["available"] and hs["files"]:
        out += ["Since %s." % hs["since"], "",
                "| File | Commits | LOC | Score |", "|---|---|---|---|"]
        for h in hs["files"]:
            out.append("| %s | %d | %d | %d |" % (
                _cell(h["file"]), h["commits"], h["loc"], h["score"]))
    elif hs["available"]:
        out.append("No changed code files in the window.")
    else:
        out.append("Unavailable.")
    out += ["", "## Notes", ""]
    notes = list(result["notes"])
    for rule, n in sorted(summ.get("truncated", {}).items()):
        notes.append("rule %s capped at %d of %d signals" % (rule, RULE_CAP, n))
    out += ["- " + n for n in notes] if notes else ["None."]
    return "\n".join(out) + "\n"
