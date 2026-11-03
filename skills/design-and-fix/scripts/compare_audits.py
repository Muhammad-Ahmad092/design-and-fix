#!/usr/bin/env python3
"""
compare_audits.py - deterministic "Change Since Last Audit" and score trend for design-and-fix audits.

Usage:
    python3 compare_audits.py <old-dir> <new-dir> --repo ROOT [--old-repo ROOT] [--history DIR]
                              [--format markdown|json] [--date YYYY-MM-DD]

    <old-dir> / <new-dir>  folders holding 01-architecture-issues.md, 02-api-optimization.md
                           and/or quick-audit.md (for example docs/architecture/history/<date>/
                           and docs/architecture/).
    --repo      root of the repository the NEW audit cites (Evidence paths are relative to it).
    --old-repo  root of a snapshot the OLD audit cited. Default: --repo, so old Evidence is read
                from the current tree and may no longer resolve; unresolved issues then fall back
                to fuzzy matching and are marked uncertain.
    --history   folder of archived runs (<YYYY-MM-DD>[-N]/). Default: <new-dir>/history when it exists.
    --date      label for the current run in the trend table (default: the date of the new
                audit's `> Audit:` timestamp, else today).

Method (spec section 8). The agent never hashes. Each issue gets a line-number-independent
fingerprint from its ID prefix (SYS/API/SCL/REL/SEC/OBS/MNT, standing in for the rule) plus the
first `path:line` of its Evidence cell, read from the repo through `dfscan.fingerprint`
(enclosing function + normalised line). Matching, in order:
  1. same fingerprint  -> Unchanged, or Changed when severity/status differ
  2. same fingerprint with the path ignored but a different path -> Possibly moved
  3. fallback (1.0.1 rule "same file + same problem"): same ID prefix, a shared Evidence file
     and a similar title, only for issues whose fingerprint could not be computed (or when the
     old side is read from the current tree) -> matched, always marked uncertain
  4. same full ID on both sides (the agent kept the ID, as archive-and-diff.md asks) -> matched,
     uncertain; Possibly moved when the old Evidence file is gone and the new one differs.
     So one ID is never listed as both Fixed and New.
Old issues left over are Resolved (uncertain when their evidence file is gone or unresolvable),
new issues left over are New. Matched entries keep the OLD id; `id_map` (old_id -> new_id) lists
the ones where the new audit used a different number so the agent can keep the old ID.
A certain Possibly moved (step 2) needs --old-repo; without a snapshot, moves are found only
through step 4 and are uncertain.

Evidence is parsed by lint_output.parse_evidence (the linter's parser): `path:line[-line]` with
any path characters, or a bare folder/file path for absence evidence, fingerprinted as
rule|path (line 0). Rows with Status `not-reproducible` are ignored (they count as fixed).
Status differences are reported only when both audits have a Status value (1.0.1 archives have
no Status column). Trend dates come from each run's `> Audit:` timestamp (folder name for 1.0.1
archives, whose folder is the archive date).

Issue tables are parsed by header name (ID must be the first column; Issue, Severity, Evidence,
Status are located by name, so extra columns such as Source or Verify are ignored). Severity comes
from the Severity column, else from the section heading ("## High Issues"). Evidence cells may hold
several `path:line` entries separated by `, ` or `<br>`.

Audit metadata: leading `> Key: value` lines of the audit files (Scope, Context, Revision, ...)
are returned by read_metadata() (Revision is parsed from the `> Audit: ... rev <sha|unknown> ...`
line when no Revision line exists); the Markdown block shows `Revision` when both runs have one.

Markdown output (the format templates/*/ "Change Since Last Audit" copies):

    ## Change Since Last Audit

    Previous: <Band> (<X.X> / 10) -> Current: <Band> (<X.X> / 10)
    Revision: <old> -> <new>                    (only when both audits record one)

    - Fixed: <IDs>                              (uncertain ones end with " (uncertain)")
    - New: <IDs>
    - Still open: <IDs>
    - Changed: <ID> (severity High -> Critical; uncertain), ...
    - Possibly moved: <ID> (old/path.py -> new/path.py), ...
    - Keep old IDs: <new-ID> -> <old-ID>, ...   (only when the new audit renumbered)

    ### Score Trend

    | Date | Overall | Band | Fixed | New |
    |---|---|---|---|---|
    | <YYYY-MM-DD> | <X.X> | <Band> | <IDs or —> | <IDs or —> |

(The arrow in the real output is the Unicode right arrow.) Empty bullets are omitted. The trend lists
the last 10 runs, oldest first, current run last; Fixed/New diff each run's issue IDs against the
run before it. JSON output carries the same data (new, resolved, unchanged, changed, moved, id_map,
previous, current, trend). Exit codes: 0 ok, 2 usage error (missing folder, or no audit file in
the old folder).
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dfscan.fingerprint import signal_fingerprint  # noqa: E402
from lint_output import parse_evidence  # noqa: E402

AUDIT_FILES = ("01-architecture-issues.md", "02-api-optimization.md", "quick-audit.md")
SCORE_FILES = ("01-architecture-issues.md", "quick-audit.md")
ARROW = "→"
FUZZY_TITLE_MIN = 0.6

_ID_RE = re.compile(r"^(SYS|API|SCL|REL|SEC|OBS|MNT)-\d+$")
_EVIDENCE_RE = re.compile(r"([A-Za-z0-9_@./\\\-]+\.[A-Za-z0-9]+):(\d+)")
_SEVERITY_RE = re.compile(r"\b(Critical|High|Medium|Low)\b", re.I)
# Rubric bands first (multi-word "Stable with risks"), then any one-word band of older runs.
_SCORE_RE = re.compile(r"(Healthy|Stable with risks|Fragile|Unsafe|[A-Za-z]+)"
                       r"\s*\(\s*(\d+(?:\.\d+)?)\s*/\s*10\s*\)")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?=T|\b)")
_RUN_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?$")
RETIRED = "not-reproducible"
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_REV_RE = re.compile(r"\brev\s+(\S+)")
_META_RE = re.compile(r"^>\s*([A-Za-z][A-Za-z0-9 _/-]*?):\s*(.*?)\s*$")


@dataclass
class Issue:
    id: str
    severity: str
    status: str
    evidence: List[Tuple[str, int]]
    title: str
    file: str


@dataclass
class Comparison:
    new: List[dict] = field(default_factory=list)
    resolved: List[dict] = field(default_factory=list)
    unchanged: List[dict] = field(default_factory=list)
    changed: List[dict] = field(default_factory=list)
    moved: List[dict] = field(default_factory=list)
    id_map: Dict[str, str] = field(default_factory=dict)
    previous: dict = field(default_factory=dict)
    current: dict = field(default_factory=dict)
    old_meta: Dict[str, str] = field(default_factory=dict)
    new_meta: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"new": self.new, "resolved": self.resolved, "unchanged": self.unchanged,
                "changed": self.changed, "moved": self.moved, "id_map": self.id_map,
                "previous": self.previous, "current": self.current}


# --- parsing ---------------------------------------------------------------

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().replace("\r\n", "\n")


def _split_row(line: str) -> List[str]:
    """Split a Markdown table row on `|`, ignoring pipes inside backticks and `\\|` escapes."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: List[str] = []
    cur: List[str] = []
    in_code = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _parse_evidence(cell: str) -> List[Tuple[str, int]]:
    """(path, first line) per Evidence entry, line 0 for a bare folder/file path. Uses the
    linter's parser; prose of 1.0.1 audits falls back to an embedded `file.ext:line`."""
    found = [(path, first) for path, first, _last, _tok in parse_evidence(cell)]
    if not found:
        found = [(p, int(n)) for p, n in _EVIDENCE_RE.findall(cell.replace("`", ""))]
    out: List[Tuple[str, int]] = []
    for path, line in found:
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if line == 0:
            path = path.rstrip("/")
        entry = (path, line)
        if path and entry not in out:
            out.append(entry)
    return out


def _clean(cell: str) -> str:
    return cell.replace("`", "").replace("**", "").strip()


def _parse_file(text: str, fname: str) -> List[Issue]:
    issues: List[Issue] = []
    heading_sev = ""
    cols: Optional[Dict[str, int]] = None
    lines = text.split("\n")
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        m = _HEADING_RE.match(stripped)
        if m and not stripped.startswith("|"):
            sev = _SEVERITY_RE.search(m.group(1))
            heading_sev = sev.group(1).capitalize() if sev else ""
            cols = None
            continue
        if not stripped.startswith("|"):
            cols = None
            continue
        cells = _split_row(stripped)
        if cols is None:
            nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
            if cells and cells[0].strip().lower() == "id" and re.match(r"^\|?\s*:?-{3,}", nxt):
                cols = {_clean(c).lower(): n for n, c in enumerate(cells)}
            continue
        if all(re.match(r"^:?-{2,}:?$", c.strip()) for c in cells if c.strip()):
            continue
        iid = _clean(cells[0]) if cells else ""
        if not _ID_RE.match(iid):
            continue

        def col(name: str) -> str:
            n = cols.get(name) if cols else None
            return cells[n] if n is not None and n < len(cells) else ""

        sev_cell = _clean(col("severity"))
        sev_m = _SEVERITY_RE.search(sev_cell)
        severity = sev_m.group(1).capitalize() if sev_m else heading_sev
        if _clean(col("status")).lower() == RETIRED:
            continue   # kept one run for the record; counts as fixed
        issues.append(Issue(iid, severity, _clean(col("status")),
                            _parse_evidence(col("evidence")), _clean(col("issue")), fname))
    return issues


def parse_issues(audit_dir: str) -> List[Issue]:
    """Issues from 01/02/quick-audit tables in `audit_dir` (first occurrence of an ID wins)."""
    out: List[Issue] = []
    seen = set()
    for fname in AUDIT_FILES:
        path = os.path.join(audit_dir, fname)
        if not os.path.isfile(path):
            continue
        for issue in _parse_file(_read(path), fname):
            if issue.id not in seen:
                seen.add(issue.id)
                out.append(issue)
    return out


def read_score(audit_dir: str) -> Tuple[Optional[float], str]:
    """(overall score, band) from the headline of 01-architecture-issues.md or quick-audit.md."""
    for fname in SCORE_FILES:
        path = os.path.join(audit_dir, fname)
        if not os.path.isfile(path):
            continue
        lines = _read(path).split("\n")
        for n, line in enumerate(lines):
            h = _HEADING_RE.match(line)
            if not h:
                continue
            title = h.group(1)
            window = None
            if title.lower().startswith("architecture health score"):
                window = title
            elif title.lower().startswith("health band"):
                window = ""
                for nxt in lines[n + 1:]:
                    if nxt.startswith("#"):
                        break
                    window += nxt + "\n"
            if window:
                sm = _SCORE_RE.search(window)
                if sm:
                    return float(sm.group(2)), sm.group(1)
    return None, ""


def read_metadata(audit_dir: str) -> Dict[str, str]:
    """Leading `> Key: value` lines of the first audit file found (Scope, Context, Revision, ...)."""
    for fname in AUDIT_FILES:
        path = os.path.join(audit_dir, fname)
        if not os.path.isfile(path):
            continue
        meta: Dict[str, str] = {}
        for line in _read(path).split("\n")[:40]:
            m = _META_RE.match(line)
            if m:
                meta[m.group(1)] = m.group(2)
        rev = _REV_RE.search(meta.get("Audit", ""))
        if rev and "Revision" not in meta:
            meta["Revision"] = rev.group(1)
        return meta
    return {}


# --- fingerprints ------------------------------------------------------------

def _resolve(repo_root: str, rel: str, allow_dir: bool = False) -> Optional[str]:
    if not rel or os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel):
        return None
    root = os.path.realpath(repo_root)
    full = os.path.realpath(os.path.join(root, *rel.split("/")))
    if full != root and not full.startswith(root + os.sep):
        return None
    if os.path.isfile(full) or (allow_dir and os.path.isdir(full)):
        return full
    return None


def _evidence_exists(repo_root: str, rel: str, line: int) -> bool:
    return _resolve(repo_root, rel, allow_dir=line == 0) is not None


def _fingerprints(issue: Issue, repo_root: str) -> Tuple[Optional[str], Optional[str]]:
    """(full fingerprint, path-free fingerprint) of the first Evidence entry, or (None, None).

    A bare path (line 0, absence evidence) gets rule|path like the scanner's folder-level
    signals, and no path-free fingerprint."""
    if not issue.evidence:
        return None, None
    rel, line = issue.evidence[0]
    rule = issue.id.split("-")[0]
    if line == 0:
        if _resolve(repo_root, rel, allow_dir=True) is None:
            return None, None
        return signal_fingerprint(rule, rel, "", 0, None), None
    full = _resolve(repo_root, rel)
    if full is None or line < 0:
        return None, None
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None, None
    if line > len(text.split("\n")):
        return None, None
    return (signal_fingerprint(rule, rel, text, line, None),
            signal_fingerprint(rule, "", text, line, None))


def issue_fingerprint(issue: Issue, repo_root: str) -> Optional[str]:
    """Line-independent fingerprint of `issue` (ID prefix as rule + first Evidence line), or None
    when the file or line cannot be read."""
    return _fingerprints(issue, repo_root)[0]


# --- comparison --------------------------------------------------------------

def _norm_status(value: str) -> str:
    return value.strip().lower() or "open"


def _differences(old: Issue, new: Issue) -> List[str]:
    parts = []
    if old.severity.lower() != new.severity.lower():
        parts.append("severity %s %s %s" % (old.severity or "?", ARROW, new.severity or "?"))
    if old.status.strip() and new.status.strip() and \
            _norm_status(old.status) != _norm_status(new.status):
        parts.append("status %s %s %s" % (old.status or "Open", ARROW, new.status or "Open"))
    return parts


def _first_path(issue: Issue) -> str:
    return issue.evidence[0][0] if issue.evidence else "?"


def _entry(iid: str, old_id: Optional[str], uncertain: bool, reason: str, new_id: Optional[str] = None) -> dict:
    return {"id": iid, "old_id": old_id, "new_id": new_id, "uncertain": uncertain, "reason": reason}


def compare(old_dir: str, new_dir: str, repo_root: str, old_repo_root: Optional[str] = None) -> Comparison:
    """Classify every old and new issue; see the module docstring for the matching order."""
    separate_old = old_repo_root is not None
    old_repo = old_repo_root if separate_old else repo_root
    old_issues = parse_issues(old_dir)
    new_issues = parse_issues(new_dir)
    old_fp = [_fingerprints(i, old_repo) for i in old_issues]
    new_fp = [_fingerprints(i, repo_root) for i in new_issues]

    result = Comparison()
    result.old_meta, result.new_meta = read_metadata(old_dir), read_metadata(new_dir)
    (po, pb), (co, cb) = read_score(old_dir), read_score(new_dir)
    result.previous = {"overall": po, "band": pb}
    result.current = {"overall": co, "band": cb}

    matched_old: Dict[int, int] = {}
    used_new = set()
    kinds: Dict[int, Tuple[str, bool, str]] = {}   # old index -> (class, uncertain, reason)

    def link(o: int, n: int, kind: str, uncertain: bool, reason: str = "") -> None:
        matched_old[o] = n
        used_new.add(n)
        diff = _differences(old_issues[o], new_issues[n])
        if kind == "unchanged" and diff:
            kind = "changed"
        if diff:
            reason = "; ".join(([reason] if kind == "moved" and reason else []) + diff)
        kinds[o] = (kind, uncertain, reason)

    # 1. exact fingerprint
    for o, oi in enumerate(old_issues):
        fp = old_fp[o][0]
        if fp is None:
            continue
        for n, ni in enumerate(new_issues):
            if n not in used_new and new_fp[n][0] == fp:
                link(o, n, "unchanged", False)
                break

    # 2. same snippet, different path -> possibly moved
    for o, oi in enumerate(old_issues):
        pf = old_fp[o][1]
        if o in matched_old or pf is None:
            continue
        for n, ni in enumerate(new_issues):
            if n in used_new or new_fp[n][1] != pf or _first_path(ni) == _first_path(oi):
                continue
            link(o, n, "moved", False, "%s %s %s" % (_first_path(oi), ARROW, _first_path(ni)))
            break

    # 3. fuzzy fallback: same prefix + shared evidence file + similar title
    pairs = []
    for o, oi in enumerate(old_issues):
        if o in matched_old:
            continue
        for n, ni in enumerate(new_issues):
            if n in used_new or oi.id.split("-")[0] != ni.id.split("-")[0]:
                continue
            if separate_old and old_fp[o][0] is not None and new_fp[n][0] is not None:
                continue   # both fingerprints trustworthy and different: not the same problem
            if not ({p for p, _ in oi.evidence} & {p for p, _ in ni.evidence}):
                continue
            ratio = difflib.SequenceMatcher(None, oi.title.lower(), ni.title.lower()).ratio()
            if ratio >= FUZZY_TITLE_MIN:
                pairs.append((-ratio, o, n))
    for _, o, n in sorted(pairs):
        if o in matched_old or n in used_new:
            continue
        link(o, n, "unchanged", True, "matched by same file and similar title")

    # 4. same full ID left on both sides: the agent kept the ID, so it claims the same issue
    new_by_id = {ni.id: n for n, ni in enumerate(new_issues) if n not in used_new}
    for o, oi in enumerate(old_issues):
        n = new_by_id.get(oi.id)
        if o in matched_old or n is None or n in used_new:
            continue
        ni = new_issues[n]
        old_path, new_path = _first_path(oi), _first_path(ni)
        gone = bool(oi.evidence) and not _evidence_exists(repo_root, *oi.evidence[0])
        if gone and ni.evidence and old_path != new_path:
            link(o, n, "moved", True, "%s %s %s" % (old_path, ARROW, new_path))
            continue
        shared = {p for p, _ in oi.evidence} & {p for p, _ in ni.evidence}
        same_title = oi.title.strip().lower() == ni.title.strip().lower()
        if shared or same_title:
            why = "matched by ID" + (" and file" if shared else "") + (" and title" if same_title else "")
        else:
            why = "same ID but different file and title; confirm by reading"
        link(o, n, "unchanged", True, why)

    for o, oi in enumerate(old_issues):
        if o in matched_old:
            kind, uncertain, reason = kinds[o]
            n = matched_old[o]
            new_id = new_issues[n].id
            getattr(result, kind).append(_entry(oi.id, oi.id, uncertain, reason, new_id))
            if new_id != oi.id:
                result.id_map[oi.id] = new_id
            continue
        missing = [p for p, ln in oi.evidence if not _evidence_exists(repo_root, p, ln)]
        if not oi.evidence:
            uncertain, reason = True, "no evidence cited, cannot verify"
        elif missing:
            uncertain, reason = True, "evidence file %s no longer exists" % missing[0]
        elif old_fp[o][0] is None:
            uncertain, reason = True, "evidence line %s:%d cannot be resolved" % oi.evidence[0]
        else:
            uncertain, reason = False, "not reported in the new audit"
        result.resolved.append(_entry(oi.id, oi.id, uncertain, reason))

    for n, ni in enumerate(new_issues):
        if n in used_new:
            continue
        if new_fp[n][0] is None:
            uncertain, reason = True, "evidence cannot be resolved in the repo"
        else:
            uncertain, reason = False, "no matching issue in the old audit"
        result.new.append(_entry(ni.id, None, uncertain, reason, ni.id))
    return result


# --- trend -------------------------------------------------------------------

def has_audit(audit_dir: str) -> bool:
    return any(os.path.isfile(os.path.join(audit_dir, f)) for f in AUDIT_FILES)


def audit_date(audit_dir: str) -> Optional[str]:
    """YYYY-MM-DD of the run's `> Audit:` timestamp (the date the audit ran), or None."""
    dates = _DATE_RE.findall(read_metadata(audit_dir).get("Audit", ""))
    return dates[-1] if dates else None


def _run_dir_key(name: str) -> Tuple[str, int, str]:
    """Archive folders in run order: <date>, <date>-2, ..., <date>-10 (not string order)."""
    m = _RUN_DIR_RE.match(name)
    return (m.group(1), int(m.group(2) or 1), "") if m else (name, 0, name)


def trend(history_root: str, current_dir: str, last: int = 10,
          current_date: Optional[str] = None) -> List[dict]:
    """Rows {date, overall, band, fixed, new} for archived runs plus the current one, oldest first.

    Fixed/new are the issue IDs that left/entered relative to the previous run in the list."""
    runs: List[Tuple[str, str]] = []
    if history_root and os.path.isdir(history_root):
        for name in sorted(os.listdir(history_root), key=_run_dir_key):
            path = os.path.join(history_root, name)
            if os.path.isdir(path) and has_audit(path):
                runs.append((audit_date(path) or name, path))
    label = current_date or audit_date(current_dir) or datetime.date.today().isoformat()
    runs.append((label, current_dir))
    rows: List[dict] = []
    prev_ids: Optional[set] = None
    for label, path in runs:
        ids = {i.id for i in parse_issues(path)}
        overall, band = read_score(path)
        rows.append({"date": label, "overall": overall, "band": band,
                     "fixed": sorted(prev_ids - ids) if prev_ids is not None else [],
                     "new": sorted(ids - prev_ids) if prev_ids is not None else []})
        prev_ids = ids
    return rows[-last:] if last > 0 else rows


# --- rendering ---------------------------------------------------------------

def _score_text(s: dict) -> str:
    if s.get("overall") is None:
        return "unknown"
    return "%s (%.1f / 10)" % (s.get("band") or "?", s["overall"])


def _ids_text(entries: List[dict], with_reason: bool = False) -> str:
    parts = []
    for e in sorted(entries, key=lambda x: x["id"]):
        if with_reason and e["reason"]:
            parts.append("%s (%s%s)" % (e["id"], e["reason"], "; uncertain" if e["uncertain"] else ""))
        elif e["uncertain"]:
            parts.append("%s (uncertain)" % e["id"])
        else:
            parts.append(e["id"])
    return ", ".join(parts)


def render_markdown(cmp: Comparison, rows: Optional[List[dict]] = None) -> str:
    out = ["## Change Since Last Audit", "",
           "Previous: %s %s Current: %s" % (_score_text(cmp.previous), ARROW, _score_text(cmp.current))]
    old_rev, new_rev = cmp.old_meta.get("Revision"), cmp.new_meta.get("Revision")
    if old_rev and new_rev:
        out.append("Revision: %s %s %s" % (old_rev, ARROW, new_rev))
    out.append("")
    still_open = cmp.unchanged + cmp.changed + cmp.moved
    bullets = [("Fixed", _ids_text(cmp.resolved)), ("New", _ids_text(cmp.new)),
               ("Still open", _ids_text(still_open)),
               ("Changed", _ids_text(cmp.changed, True)),
               ("Possibly moved", _ids_text(cmp.moved, True)),
               ("Keep old IDs", ", ".join("%s %s %s" % (n, ARROW, o)
                                          for o, n in sorted(cmp.id_map.items())))]
    for name, text in bullets:
        if text:
            out.append("- %s: %s" % (name, text))
    if not any(text for _, text in bullets):
        out.append("- No issues in either audit.")
    if rows:
        out += ["", "### Score Trend", "", "| Date | Overall | Band | Fixed | New |", "|---|---|---|---|---|"]
        for r in rows:
            out.append("| %s | %s | %s | %s | %s |" % (
                r["date"], "%.1f" % r["overall"] if r["overall"] is not None else "—",
                r["band"] or "—", ", ".join(r["fixed"]) or "—", ", ".join(r["new"]) or "—"))
    return "\n".join(out) + "\n"


# --- CLI ---------------------------------------------------------------------

def _fail(msg: str) -> int:
    sys.stderr.write("error: %s\n" % msg)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compare two design-and-fix audit runs by fingerprint.")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--repo", required=True, help="repository root the new audit cites")
    ap.add_argument("--old-repo", default=None, help="snapshot root the old audit cited (default: --repo)")
    ap.add_argument("--history", default=None, help="folder of archived runs for the trend table")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ap.add_argument("--date", default=None, help="label for the current run in the trend table")
    args = ap.parse_args(argv)

    for label, path in (("old audit folder", args.old), ("new audit folder", args.new),
                        ("repo root", args.repo), ("old repo root", args.old_repo)):
        if path is not None and not os.path.isdir(path):
            return _fail("%s not found: %s" % (label, path))
    if not has_audit(args.old):
        return _fail("no audit file (%s) in the old audit folder %s; pass the folder you just "
                     "archived into" % (", ".join(AUDIT_FILES), args.old))
    history = args.history
    if history is None and os.path.isdir(os.path.join(args.new, "history")):
        history = os.path.join(args.new, "history")

    cmp = compare(args.old, args.new, args.repo, args.old_repo)
    rows = trend(history, args.new, current_date=args.date) if history else None
    if args.format == "json":
        data = cmp.to_dict()
        data["trend"] = rows or []
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    else:
        text = render_markdown(cmp, rows)
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
