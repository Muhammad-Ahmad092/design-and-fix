#!/usr/bin/env python3
"""
verify_precision.py - precision of scanner-sourced issues AFTER the skill's verification step.

The scanner precision study (run_precision.py) measures raw signals. The skill then reads the code
and keeps, downgrades or drops each signal, so what a user sees is better than the raw rate. This
tool measures that: it pulls every issue whose Source is `scanner:<rule>` out of an audit, lets a
human label each one, and reports precision per rule.

Usage:
    python evals/verify_precision.py extract AUDIT_DIR [--out sheet.json]
    python evals/verify_precision.py report LABELS [--out report.md]

extract  prints (or writes) a labelling sheet: one row per issue per scanner rule, with
         id, rule, evidence, status, file, label ("") and note. Label each row tp | fp | unsure
         by reading the cited code (rules in evals/LABELLING.md). Rows whose status is
         `not-reproducible` are kept in the sheet (the skill rejected them) but excluded from
         precision: they never reached the reader.
report   LABELS is one sheet or a folder of sheets. Prints precision = tp / (tp + fp) per rule
         with decided counts and a Wilson 95% interval; `unsure` and unlabelled rows are counted
         but not decided. Pass --out to write the Markdown to a file.

Offline, standard library only. Audit tables are parsed with the project's own parsers
(compare_audits / lint_output), imported from skills/design-and-fix/scripts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "skills", "design-and-fix", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import compare_audits as ca  # noqa: E402
from lint_output import SOURCE_RE  # noqa: E402

LABELS = ("tp", "fp", "unsure")
REJECTED_STATUS = "not-reproducible"
_ID_RE = re.compile(r"^(?:SYS|API|SCL|REL|SEC|OBS|MNT)-\d+$")
_RULE_RE = re.compile(r"[\w.-]+")


def read_audit_tables(audit_dir: str) -> List[dict]:
    """One dict per issue row (lower-cased column header -> raw cell, plus `id` and `_file`)
    from the audit files in `audit_dir`. First occurrence of an ID wins."""
    out = []  # type: List[dict]
    seen = set()
    for fname in ca.AUDIT_FILES:
        path = os.path.join(audit_dir, fname)
        if not os.path.isfile(path):
            continue
        lines = ca.read_text(path).split("\n")
        cols = None  # type: Optional[Dict[str, int]]
        for idx, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped.startswith("|"):
                cols = None
                continue
            cells = ca.split_row(stripped)
            if cols is None:
                nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
                if cells and cells[0].strip().lower() == "id" and re.match(r"^\|?\s*:?-{3,}", nxt):
                    cols = {ca.clean_cell(c).lower(): n for n, c in enumerate(cells)}
                continue
            iid = ca.clean_cell(cells[0]) if cells else ""
            if not _ID_RE.match(iid) or iid in seen:
                continue
            seen.add(iid)
            row = {"_file": fname}  # type: dict
            for name, n in cols.items():
                row[name] = cells[n] if n < len(cells) else ""
            row["id"] = iid
            out.append(row)
    return out


def scanner_rules(source: str) -> List[str]:
    """Rule ids named by a Source cell (`scanner:a, b`); [] for `agent` or an invalid cell.
    `scanner+agent:x` counts: the scanner signal was the starting point."""
    src = ca.clean_cell(source)
    if not SOURCE_RE.match(src) or ":" not in src:
        return []
    head, _, tail = src.partition(":")
    if head not in ("scanner", "scanner+agent"):
        return []
    return [r for r in re.split(r",\s*", tail) if _RULE_RE.fullmatch(r)]


def extract(audit_dir: str) -> List[dict]:
    """Sheet rows for every issue sourced from the scanner: one row per (issue, rule)."""
    rows = []  # type: List[dict]
    for issue in read_audit_tables(audit_dir):
        for rule in scanner_rules(issue.get("source", "")):
            rows.append({
                "id": issue["id"],
                "rule": rule,
                "evidence": ca.clean_cell(issue.get("evidence", "")),
                "status": ca.clean_cell(issue.get("status", "")).lower(),
                "file": issue["_file"],
                "label": "",
                "note": "",
            })
    return rows


def wilson(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def load_sheets(path: str) -> List[dict]:
    """Rows from one sheet file or every *.json in a folder (file names starting `_` skipped)."""
    files = [path]
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in sorted(os.listdir(path))
                 if f.endswith(".json") and not f.startswith("_")]
    rows = []  # type: List[dict]
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError("%s: a sheet is a JSON list of rows" % f)
        for e in data:
            label = e.get("label", "")
            if label not in LABELS + ("",):
                raise ValueError("bad label %r in %s (use tp, fp or unsure)" % (label, f))
            rows.append(e)
    return rows


def summarize(rows: List[dict]) -> Dict[str, Dict[str, int]]:
    """Per rule: tp, fp, unsure, unlabelled, rejected (skill dropped it, not in precision)."""
    out = {}  # type: Dict[str, Dict[str, int]]
    for e in rows:
        r = out.setdefault(e["rule"], {"tp": 0, "fp": 0, "unsure": 0, "unlabelled": 0,
                                       "rejected": 0})
        if e.get("status") == REJECTED_STATUS:
            r["rejected"] += 1
        else:
            r[e.get("label") or "unlabelled"] += 1
    return out


def _pct(tp: int, n: int) -> str:
    if n == 0:
        return "n/a (0 decided)"
    lo, hi = wilson(tp, n)
    return "%.0f%% (95%% CI %.0f-%.0f%%, n=%d)" % (100 * tp / n, 100 * lo, 100 * hi, n)


def render(rows: List[dict]) -> str:
    stats = summarize(rows)
    lines = ["# Precision after verification", "",
             "| Rule | Decided (tp+fp) | tp | fp | unsure | Unlabelled | Rejected by skill | Precision |",
             "|---|---|---|---|---|---|---|---|"]
    tp_all = fp_all = 0
    for rule in sorted(stats):
        s = stats[rule]
        n = s["tp"] + s["fp"]
        tp_all += s["tp"]
        fp_all += s["fp"]
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %s |" % (
            rule, n, s["tp"], s["fp"], s["unsure"], s["unlabelled"], s["rejected"], _pct(s["tp"], n)))
    lines.append("| **all** | %d | %d | %d | | | | %s |" % (
        tp_all + fp_all, tp_all, fp_all, _pct(tp_all, tp_all + fp_all)))
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Precision of scanner-sourced issues after verification")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("audit_dir")
    e.add_argument("--out")
    r = sub.add_parser("report")
    r.add_argument("labels")
    r.add_argument("--out")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "extract":
            if not os.path.isdir(args.audit_dir):
                raise ValueError("not a folder: %s" % args.audit_dir)
            text = json.dumps(extract(args.audit_dir), indent=2, ensure_ascii=False) + "\n"
        else:
            text = render(load_sheets(args.labels))
    except (OSError, ValueError) as exc:
        print("verify_precision: %s" % exc, file=sys.stderr)
        return 2
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
