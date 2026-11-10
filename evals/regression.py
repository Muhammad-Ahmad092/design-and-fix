#!/usr/bin/env python3
"""
regression.py - the release regression gate (offline, stdlib only).

Usage:
    python evals/regression.py [--floors evals/precision-floors.json] [--report evals/precision.md]
    python evals/regression.py --write-floors     # regenerate floors from the report
    python evals/regression.py --write-measured   # regenerate dfscan/measured.json
    python evals/regression.py --write-fingerprints

Exit 1 when a rule's version changed since precision.md was measured (stale), when a rule with
n >= 10 decided labels is below its precision floor, when a must_find issue of the fixture
monorepo is no longer found, or when the fingerprints of tests/fixtures/monorepo differ from
evals/fingerprints-monorepo.json. Exit 0 otherwise.

What CI checks (release-gate job, no corpus available): staleness, fixture recall, fingerprints,
and floors against the COMMITTED report. What only runs locally: re-measuring precision on the
corpus (evals/run_precision.py). CI cannot see a precision drop that was never re-measured,
which is why a changed rule version fails the gate until it is re-measured.
measured.json: no entry below n = 5 decided labels, "high" only from n = 10.
Floor = precision at the last release minus 10 percentage points (minimum 0).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "skills", "design-and-fix", "scripts")
MEASURED = os.path.join(SCRIPTS, "dfscan", "measured.json")
MIN_N = 10
MIN_MEASURED_N = 5
MARGIN = 0.10


def parse_report(text: str) -> Dict[str, dict]:
    """Per-rule {"n", "tp", "fp"} from the first table whose header has Rule, tp and fp."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        head = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if "rule" not in head or "tp" not in head or "fp" not in head:
            continue
        itp, ifp, irule = head.index("tp"), head.index("fp"), head.index("rule")
        out = {}  # type: Dict[str, dict]
        for row in lines[i + 2:]:
            if not row.lstrip().startswith("|"):
                break
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            try:
                tp, fp = int(cells[itp]), int(cells[ifp])
            except (ValueError, IndexError):
                continue
            out[cells[irule]] = {"tp": tp, "fp": fp, "n": tp + fp}
        return out
    return {}


def precision(stat: dict) -> Optional[float]:
    return stat["tp"] / stat["n"] if stat["n"] else None


def make_floors(stats: Dict[str, dict]) -> Dict[str, dict]:
    out = {}
    for rule in sorted(stats):
        p = precision(stats[rule])
        if p is not None:
            out[rule] = {"floor": round(max(0.0, p - MARGIN), 4), "n": stats[rule]["n"]}
    return out


def measured_level(p: float, n: int = MIN_N) -> str:
    if p >= 0.80:
        return "high" if n >= MIN_N else "medium"
    return "medium" if p >= 0.50 else "low"


def make_measured(stats: Dict[str, dict]) -> Dict[str, str]:
    out = {}
    for r, s in sorted(stats.items()):
        p = precision(s)
        if p is not None and s["n"] >= MIN_MEASURED_N:
            out[r] = measured_level(p, s["n"])
    return out


def measured_versions(text: str) -> Dict[str, str]:
    """Rule versions from the report's Environment line ("rule versions: a=1, b=2")."""
    m = re.search(r"rule versions:\s*([^\n]+)", text)
    out = {}  # type: Dict[str, str]
    if m:
        for part in m.group(1).split(","):
            k, _, v = part.strip().partition("=")
            if k and v:
                out[k.strip()] = v.strip().rstrip(".")
    return out


def stale_rules(measured: Dict[str, str], current: Dict[str, str],
                stats: Dict[str, dict]) -> List[str]:
    """Rules measured at another version than the registry's now. Rules absent from the
    report's rule table count as unmeasured, not stale."""
    out = []
    for rule in sorted(measured):
        if rule in stats and rule in current and current[rule] != measured[rule]:
            out.append("precision is stale for %s (measured v%s, now v%s): re-measure"
                       % (rule, measured[rule], current[rule]))
    return out


def _scan_fixture() -> dict:
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    from dfscan.report import scan
    return scan(os.path.join(REPO, "tests", "fixtures", "monorepo"), use_git=False)


def fingerprints(result: dict) -> List[List[str]]:
    return sorted([s["rule"], s["file"], s.get("fingerprint", "")] for s in result["signals"])


def missed_must_find(result: dict, expected_path: str) -> List[List[str]]:
    with open(expected_path, encoding="utf-8") as fh:
        must = json.load(fh)["monorepo"]["must_find"]
    got = {(s["rule"], s["file"]) for s in result["signals"]}
    return [list(x) for x in must if tuple(x) not in got]


def check_floors(stats: Dict[str, dict], floors: Dict[str, dict]) -> List[str]:
    problems = []
    for rule, fl in sorted(floors.items()):
        s = stats.get(rule)
        if s is None:
            print("note: %s has a floor but is missing from the report (not gated)" % rule)
            continue
        if s["n"] < MIN_N:
            print("note: %s has n=%d < %d decided labels (floor not enforced)"
                  % (rule, s["n"], MIN_N))
            continue
        p = precision(s)
        if p is not None and p + 1e-9 < fl["floor"]:
            problems.append("%s: precision %.0f%% is below its floor %.0f%% (n=%d)"
                            % (rule, 100 * p, 100 * fl["floor"], s["n"]))
    return problems


def _dump(path: str, data: object) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--floors", default=os.path.join(HERE, "precision-floors.json"))
    ap.add_argument("--report", default=os.path.join(HERE, "precision.md"))
    ap.add_argument("--fingerprints", default=os.path.join(HERE, "fingerprints-monorepo.json"))
    ap.add_argument("--expected", default=os.path.join(HERE, "scanner-expected.json"))
    ap.add_argument("--measured", default=MEASURED)
    ap.add_argument("--write-floors", action="store_true")
    ap.add_argument("--write-measured", action="store_true")
    ap.add_argument("--write-fingerprints", action="store_true")
    args = ap.parse_args(argv)
    if args.write_fingerprints and not (args.write_floors or args.write_measured):
        _dump(args.fingerprints, fingerprints(_scan_fixture()))
        return 0
    try:
        with open(args.report, encoding="utf-8") as fh:
            report_text = fh.read()
        stats = parse_report(report_text)
    except OSError as exc:
        print("cannot read report: %s" % exc, file=sys.stderr)
        return 2
    if args.write_floors or args.write_measured or args.write_fingerprints:
        if args.write_floors:
            _dump(args.floors, make_floors(stats))
        if args.write_measured:
            _dump(args.measured, make_measured(stats))
        if args.write_fingerprints:
            _dump(args.fingerprints, fingerprints(_scan_fixture()))
        return 0
    try:
        with open(args.floors, encoding="utf-8") as fh:
            floors = json.load(fh)
        with open(args.fingerprints, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError) as exc:
        print("cannot read gate input: %s" % exc, file=sys.stderr)
        return 2
    result = _scan_fixture()
    from dfscan import registry
    measured = measured_versions(report_text)
    if stats and not measured:
        print("cannot check staleness: the report has no 'rule versions:' line "
              "(regenerate it with evals/run_precision.py report)", file=sys.stderr)
        return 2
    problems = stale_rules(measured, {r: m.version for r, m in registry.RULES.items()}, stats)
    problems += check_floors(stats, floors)
    for m in missed_must_find(result, args.expected):
        problems.append("fixture recall dropped: %s no longer found in %s" % (m[0], m[1]))
    if fingerprints(result) != stored:
        problems.append("fingerprints of tests/fixtures/monorepo changed "
                        "(run --write-fingerprints if intended)")
    for p in problems:
        print("FAIL: " + p)
    if not problems:
        print("regression gate: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
