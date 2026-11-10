from __future__ import annotations

import os
import re
import time
from typing import Callable, Dict, List, Optional

from . import frameworks as _frameworks
from . import patterns as _patterns
from . import registry
from .auth.spring import _ant as _spring_ant
from .api_rules import run_api_rules
from .deps_rules import run_deps_rules
from .filecache import DEFAULT_MAX_BYTES, FileCache
from .fingerprint import enclosing_names, line_fingerprint, normalise, signal_fingerprint
from .hotspots import compute_hotspots
from .ignore import is_comment_only
from .imports import _read, build_graph, graph_signals
from .infra_rules import run_infra_rules
from .meta import SCHEMA_VERSION, audit_meta
from .model import FileInfo, Signal
from .patterns import mask_secrets, run_rules
from .routes import find_routes, route_signals
from .stack import detect_stack
from .text import strip_comments, strip_config_comments
from .walker import MAX_FILE_BYTES, candidate_folders, is_example_file, normalize_scope, walk_result

RULE_CAP = 200
MAX_SIGNAL_ROWS = 50
MAX_ROUTE_ROWS = 30
MAX_SKIPPED = 200  # skipped_files[] in JSON output; summary.skipped_total has the full count
MAX_ROUTES = 500  # routes[] in JSON output; summary.routes_total has the full count


def _in_scope(rel: str, scopes: List[str]) -> bool:
    return any(rel == s or rel.startswith(s + "/") for s in scopes)


def _real_case_scope(root: str, scope: str) -> str:
    """`scope` spelled as the folders are named on disk. On a case-insensitive file system
    (Windows, macOS) `SERVICES` passes an existence check for `services`, but file paths are
    compared case-sensitively, so the scope would match nothing. Each component that is not an
    exact entry of its parent folder takes the single entry that differs only in case. A scope
    that does not exist as given (a case-sensitive file system) is returned unchanged."""
    if not os.path.isdir(os.path.join(root, *scope.split("/"))):
        return scope
    cur, parts = root, []
    for part in scope.split("/"):
        try:
            names = os.listdir(cur)
        except OSError:
            return scope
        if part not in names:
            folded = [n for n in names if n.lower() == part.lower()
                      and os.path.isdir(os.path.join(cur, n))]
            if len(folded) != 1:
                return scope
            part = folded[0]
        parts.append(part)
        cur = os.path.join(cur, part)
    return "/".join(parts)


EMPTY_GRAPH = {"modules": 0, "edges": 0, "cycles": [], "cycles_meta": [], "god_modules": []}


def manifest_dirs(root: str, scopes: List[str]) -> List[str]:
    """Extra folders whose manifests feed stack detection, so a scoped scan still sees the
    manifests above its scope.

    Every ancestor of each scope root up to the scan root; and, when a scope is given and the scan
    root sits below a repository root (a `.git` in some ancestor), the ancestors of the scan root up
    to and including that repository root. Without a `.git` above, nothing outside the root is read.
    """
    dirs: List[str] = []
    for s in scopes:
        cur = os.path.dirname(os.path.join(root, *s.split("/")))
        while len(cur) >= len(root):
            dirs.append(cur)
            if os.path.normcase(cur) == os.path.normcase(root):
                break
            cur = os.path.dirname(cur)
    if scopes and not os.path.exists(os.path.join(root, ".git")):
        above: List[str] = []
        cur = os.path.dirname(root)
        while True:
            above.append(cur)
            if os.path.exists(os.path.join(cur, ".git")):
                dirs += above
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break  # filesystem root without a repository: read nothing above the scan root
            cur = parent
    seen = set()
    return [d for d in dirs if not (os.path.normcase(d) in seen or seen.add(os.path.normcase(d)))]
FOLDERS_ONLY_NOTE = "folders-only: analyzers skipped"


def _clear_module_memos() -> None:
    """Start a scan without module-level memos of an earlier scan: snippet masking, line
    normalising, the last docstring-blanked text, every frameworks.py memo (file heads, C# class
    tables, route details, bracket matching) and the Spring path-pattern memo."""
    mask_secrets.cache_clear()
    normalise.cache_clear()
    _patterns._DOCSTRINGS_LAST[:] = [None, None]
    _frameworks.clear_scan_memos()
    _spring_ant.cache_clear()


def scan(root: str, scope: Optional[List[str]] = None, list_depth: int = 2,
         top: int = 15, use_git: bool = True, since: str = "12.months",
         folders_only: bool = False, *, config_hash: str = "", respect_gitignore: bool = True,
         max_files: int = 50000, timeout_seconds: Optional[float] = None,
         progress: Optional[Callable[[str], None]] = None, verbose: bool = False,
         max_file_bytes: int = MAX_FILE_BYTES, exclude: Optional[List[str]] = None,
         include: Optional[List[str]] = None,
         rule_config: Optional[Dict[str, Dict[str, object]]] = None,
         file_cache_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """`rule_config` maps rule id -> {"enabled": bool, "severity": str} (from the config file).
    `file_cache_bytes` bounds the per-scan file cache (each file is read and comment-stripped
    once); 0 turns the cache off (every rule family reads files directly). The output does not
    depend on it.
    Disabled rules (config `"enabled": false`, or `RuleMeta.default_enabled` False with no config
    `"enabled": true`) are not skipped inside the rule runners (except openapi-drift, which does
    not run, so a disabled spec check writes no notes); their signals are dropped before the
    per-rule cap and are not counted as suppressed."""
    root = os.path.abspath(root)
    started = time.perf_counter()
    deadline = started + timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
    say = progress or (lambda msg: None)
    timing: Dict[str, float] = {}
    timed_out = False
    walk_partial = False
    notes: List[str] = []

    def expired() -> bool:
        nonlocal timed_out
        if deadline is not None and time.perf_counter() > deadline:
            timed_out = True
        return timed_out

    cache = FileCache(file_cache_bytes) if file_cache_bytes > 0 else None
    _clear_module_memos()
    norm_scope = [_real_case_scope(root, s)
                  for s in (normalize_scope(x) for x in (scope or [])) if s]
    # Stack detection and candidate folders always cover the whole root;
    # imports, routes, signals and hotspots are restricted to the scope.
    walked = walk_result(root, respect_gitignore=respect_gitignore, max_files=max_files,
                         deadline=deadline, progress=progress, max_file_bytes=max_file_bytes,
                         exclude=exclude, include=include)
    all_files = walked.files
    if walked.partial and not expired():  # (a walk stopped by the deadline is a timeout)
        walk_partial = True
        notes.append("max_files %d reached: partial results" % max_files)
    # Disclose settings that hide findings, so an audit can say what was not looked at.
    rule_cfg = rule_config or {}
    disabled = sorted(r for r, c in rule_cfg.items() if c.get("enabled") is False)
    if disabled:
        notes.append("rules disabled by config or profile: %s (their signals are dropped and not counted in "
                     "summary.suppressed)" % ", ".join(disabled))
    default_off = registry.default_off_rules(rule_cfg)
    if default_off:
        notes.append("rules off by default (enable in config): %s" % ", ".join(default_off))
    if exclude:
        notes.append("config exclude patterns: %s" % ", ".join(exclude))
    if include:
        notes.append("config include patterns: %s (only matching files are scanned)"
                     % ", ".join(include))
    if walked.invalid_gitignore:
        notes.append("%d invalid .gitignore pattern(s) match nothing: %s" % (
            len(walked.invalid_gitignore), ", ".join(walked.invalid_gitignore[:10])))
    timing["walk"] = time.perf_counter() - started
    say("walked %d files" % len(all_files))
    stack, frameworks = detect_stack(all_files, manifest_dirs(root, norm_scope))
    for f in all_files:
        # A scope that is itself inside an examples folder is scanned as ordinary code.
        if f.is_example and any(_in_scope(f.rel, [s]) and is_example_file(s + "/x")
                                for s in norm_scope):
            f.is_example = False
    files = [f for f in all_files if _in_scope(f.rel, norm_scope)] if norm_scope else all_files
    code = [f for f in files if f.lang is not None]
    graph: dict = dict(EMPTY_GRAPH)
    routes: List[dict] = []
    raw: List[Signal] = []
    hotspots: dict = {"available": False, "since": since, "files": [], "note": None}
    if folders_only:
        notes.append(FOLDERS_ONLY_NOTE)
    elif not expired():
        t0 = time.perf_counter()
        say("analyzing imports")
        graph = build_graph(files, norm_scope, cache)
        timing["imports"] = time.perf_counter() - t0
        if not expired():
            t0 = time.perf_counter()
            say("finding routes")
            routes = find_routes(files, cache)
            timing["routes"] = time.perf_counter() - t0
        if not expired():
            t0 = time.perf_counter()
            say("running rules")
            folders = [f for f in candidate_folders(files, 2, 10 ** 9)
                       if not norm_scope or _in_scope(f["path"], norm_scope)]
            rule_times: Dict[str, float] = {}
            runners = (
                ("graph_rules", lambda: graph_signals(graph)),
                ("route_rules", lambda: route_signals(routes, files, cache)),
                ("pattern_rules", lambda: run_rules(files, folders, norm_scope, all_files,
                                                    deadline, rule_times, cache, notes)),
                ("api_rules", lambda: run_api_rules(
                    files, routes, notes, cache, lambda r: registry.is_enabled(r, rule_cfg))),
                ("infra_rules", lambda: run_infra_rules(files, cache)),
                ("deps_rules", lambda: run_deps_rules(files, notes, cache)),
            )
            for name, runner in runners:
                t1 = time.perf_counter()
                try:
                    raw += runner()
                except Exception as exc:  # one broken rule family must not abort the scan
                    notes.append("%s failed (%s): its signals are missing" % (
                        name, type(exc).__name__))
                if name != "pattern_rules":  # run_rules records per-rule timings itself
                    rule_times[name] = time.perf_counter() - t1
            timing.update(rule_times)
            expired()
        if not expired():
            t0 = time.perf_counter()
            say("computing hotspots")
            hotspots = compute_hotspots(root, files, since, use_git, top)
            timing["hotspots"] = time.perf_counter() - t0
            if hotspots["note"]:
                notes.append(hotspots["note"])
    # Drop malformed signals, fingerprint every valid one (so suppressing or disabling one of two
    # identical lines does not shift the other's id), then drop disabled and suppressed signals
    # and cap at RULE_CAP per rule (graph, route and pattern alike). Files are read and split once.
    known = {f.rel for f in all_files}
    by_rel = {f.rel: f for f in all_files}
    invalid = 0
    suppressed = 0
    per_rule: Dict[str, int] = {}
    signals: List[dict] = []
    stamp_cache: Dict[str, dict] = {}
    seen: Dict[str, int] = {}
    t0 = time.perf_counter()
    check_deadline = not timed_out  # a scan already out of time still stamps what it found
    for i, s in enumerate(raw):
        if check_deadline and i % 256 == 255 and expired():
            break
        if registry.validate_signal(s, known) is not None:
            invalid += 1
            continue
        _stamp(s, by_rel, stamp_cache, seen, cache)
        if not registry.is_enabled(s.rule, rule_cfg):
            continue
        rc = rule_cfg.get(s.rule, {})
        if "severity" in rc:
            s.severity_hint = str(rc["severity"])
        if s.line > 0 and s.file in by_rel and _is_suppressed(s, by_rel[s.file], stamp_cache,
                                                              cache):
            suppressed += 1
            continue
        per_rule[s.rule] = per_rule.get(s.rule, 0) + 1
        if per_rule[s.rule] <= RULE_CAP:
            s.snippet = mask_secrets(s.snippet)
            s.uncertain_reason = mask_secrets(s.uncertain_reason)
            signals.append(s.to_dict())
    timing["stamp"] = time.perf_counter() - t0
    if invalid:
        notes.append("%d invalid signal(s) dropped" % invalid)
    partial = timed_out or walk_partial
    if timed_out:
        notes.append("timeout after %.3fs: partial results" % timeout_seconds)
    if verbose:
        notes += ["timing %s: %.3fs" % (k, v) for k, v in sorted(timing.items())]
    truncated = {r: n for r, n in per_rule.items() if n > RULE_CAP}
    skipped_all = [x for x in walked.skipped if not norm_scope or _in_scope(x["path"], norm_scope)]
    skipped_by_reason: Dict[str, int] = {}
    for x in skipped_all:
        skipped_by_reason[x["reason"]] = skipped_by_reason.get(x["reason"], 0) + 1
    # An excluded folder that contains a scope counts too: it emptied (part of) that scope.
    excluded = [p for p in walked.excluded if not norm_scope or _in_scope(p, norm_scope)
                or any(_in_scope(s, [p]) for s in norm_scope)]
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
        # Route paths and notes can quote source text (a token in a URL path): masked here, so
        # every renderer (JSON, Markdown, SARIF, --output-dir) gets masked data.
        "routes": [dict(r, path=mask_secrets(r["path"])) for r in routes[:MAX_ROUTES]],
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
            "invalid_signals": invalid,
            "suppressed": suppressed,
            "partial": partial,
            "skipped_total": len(skipped_all),
            "default_off_rules": default_off,
            "scope_report": {"scanned": len(files),
                             "skipped_by_reason": dict(sorted(skipped_by_reason.items())),
                             "excluded_by_config": len(excluded)},
        },
        "notes": [mask_secrets(n) for n in notes],
        "skipped_files": skipped_all[:MAX_SKIPPED],
        "meta": audit_meta(root, norm_scope, config_hash, use_git),
    }


_RULE_ID = r"[a-z0-9][a-z0-9-]*"
# Grammar (inside a comment): `dfscan:ignore[:] [rule[, rule | rule]...] [-- reason]`, optionally
# closed by `*/`. Anything else after the marker (for example `dfscan:ignore Reason text`) makes the
# comment NOT a suppression, so a typo can never silence every rule.
_IGNORE = re.compile(
    r"dfscan:ignore(?![A-Za-z0-9_-]):?"
    r"(?P<rules>(?:[ \t]*" + _RULE_ID + r"(?:(?:[ \t]*,[ \t]*|[ \t]+)" + _RULE_ID + r")*)?)"
    r"[ \t]*,?[ \t]*(?:--|\*/|$)")
_LOOSE_COMMENT = re.compile(r"(?:^|(?<=[ \t]))(?://|#|/\*)")


def _ignores(comment: str, rule: str) -> bool:
    """`comment` is the comment part of one source line (see _comment_text)."""
    for m in _IGNORE.finditer(comment.rstrip("\r")):
        names = [n for n in re.split(r"[ \t,]+", m.group("rules").strip()) if n]
        if not names or rule in names:  # bare `dfscan:ignore` covers every rule
            return True
    return False


def _file_text(fi: FileInfo, cache: Dict[str, dict],
               files: Optional[FileCache] = None) -> dict:
    """Per-file cache: the original source split on LF, plus lazily built helpers. Only files
    with signals are held here; `files` is the scan's file cache."""
    ft = cache.get(fi.rel)
    if ft is None:
        text = files.text(fi) if files is not None else _read(fi)
        ft = {"lines": text.split("\n"), "names": None, "stripped": None}
        cache[fi.rel] = ft
    return ft


def _comment_text(fi: FileInfo, ft: dict, idx: int) -> str:
    """The comment part of line `idx` (0-based) of the original source, or "".

    Code files: where the comment-stripped text first differs from the source (strings are not
    comments, so `"http://x/dfscan:ignore"` is not a marker). Other files (config, manifests,
    Dockerfiles): from a `//`, `#` or `/*` at line start or after whitespace."""
    raw = ft["lines"][idx]
    if fi.lang is None and fi.ext != ".json":
        m = _LOOSE_COMMENT.search(raw)
        return raw[m.start():] if m else ""
    if ft["stripped"] is None:
        text = "\n".join(ft["lines"])
        if fi.lang is None:
            stripped = strip_comments(text, "JavaScript")  # JSON / JSONC
        else:
            stripped = strip_comments(text, fi.lang)
            if fi.lang == "PHP":
                stripped = strip_config_comments(stripped)  # PHP also has # comments
        ft["stripped"] = stripped.split("\n")
    code = ft["stripped"][idx] if idx < len(ft["stripped"]) else raw
    for i, (a, b) in enumerate(zip(raw, code)):
        if a != b:
            return raw[i:]
    return ""


def _is_suppressed(sig: Signal, fi: FileInfo, cache: Dict[str, dict],
                   files: Optional[FileCache] = None) -> bool:
    """`dfscan:ignore [rule[, rule]]` in a comment on the signal line, or in a comment-only line above it.

    Looks in the ORIGINAL source (the rules scan comment-stripped text). Line-0 (folder) signals
    cannot be suppressed.
    """
    ft = _file_text(fi, cache, files)
    lines = ft["lines"]

    def marked(idx: int) -> bool:
        return ("dfscan:ignore" in lines[idx]
                and _ignores(_comment_text(fi, ft, idx), sig.rule))

    if 1 <= sig.line <= len(lines) and marked(sig.line - 1):
        return True
    above = sig.line - 1  # the line above counts only when it is a comment by itself
    return 1 <= above <= len(lines) and is_comment_only(lines[above - 1]) and marked(above - 1)


GRAPH_RULES = {"import-cycle", "god-module"}


def _stamp(sig: Signal, by_rel: Dict[str, FileInfo], cache: Dict[str, dict],
           seen: Dict[str, int], files: Optional[FileCache] = None) -> None:
    meta = registry.RULES[sig.rule]
    sig.rule_version = meta.version
    # The registry confidence is the measured ceiling (evals/precision.md): a rule
    # under 50% precision never reports a signal above "low".
    if registry.CONFIDENCES.index(sig.confidence) < registry.CONFIDENCES.index(meta.confidence):
        sig.confidence = meta.confidence
    fi = by_rel.get(sig.file)
    if fi is not None and sig.line > 0 and sig.rule not in GRAPH_RULES:
        ft = _file_text(fi, cache, files)
        if ft["names"] is None:
            ft["names"] = enclosing_names(ft["lines"])
        base = line_fingerprint(sig.rule, sig.file, ft["lines"], ft["names"], sig.line)
        n = seen.get(base, 0)
        seen[base] = n + 1
        sig.fingerprint = base if n == 0 else line_fingerprint(
            sig.rule, sig.file, ft["lines"], ft["names"], sig.line, occurrence=n)
        return
    args = (sig.rule, sig.file, "", 0, None)
    key = None  # type: Optional[str]
    if fi is not None and sig.line > 0:
        # Whole-module findings: identity is the rule, the file and (cycles) the chain.
        key = sig.snippet if sig.rule == "import-cycle" else ""
        args = (sig.rule, sig.file, "", 1, None)
    base = signal_fingerprint(*args, key=key)
    n = seen.get(base, 0)
    seen[base] = n + 1
    sig.fingerprint = base if n == 0 else signal_fingerprint(*args, key=key, occurrence=n)


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
