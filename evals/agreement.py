#!/usr/bin/env python3
"""
agreement.py - second-labeller agreement for the precision study (offline).

Usage:
    python evals/agreement.py evals/labels/after evals/labels/second
    python evals/agreement.py --blind-copy evals/labels/after evals/labels/second

The first form pairs entries of the two label directories by (repo, file, line, fingerprint)
and prints, per rule, how many pairs there are, the raw agreement and Cohen's kappa over
tp / fp / unsure. The second form writes the blind sheets for the second labeller: the same
entries with `label` and `note` removed, so the second pass cannot see the first.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

CLASSES = ("tp", "fp", "unsure")


def kappa(a: List[str], b: List[str]) -> float:
    """Cohen's kappa for two equally long label lists (1.0 perfect, 0 chance level)."""
    if len(a) != len(b):
        raise ValueError("label lists differ in length")
    n = len(a)
    if n == 0:
        return 1.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def _key(e: dict) -> Tuple:
    return (e.get("repo"), e.get("file"), e.get("line"), e.get("fingerprint"))


def _load(d: str) -> Dict[str, List[dict]]:
    out = {}  # type: Dict[str, List[dict]]
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and not name.startswith("_"):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                out[name[:-5]] = json.load(fh)
    return out


def compare(first: Dict[str, List[dict]], second: Dict[str, List[dict]]) -> List[dict]:
    rows = []
    for rule in sorted(set(first) & set(second)):
        sec = {_key(e): e.get("label") for e in second[rule] if e.get("label") in CLASSES}
        a, b = [], []  # type: List[str]
        for e in first[rule]:
            if e.get("label") in CLASSES and _key(e) in sec:
                a.append(e["label"])
                b.append(sec[_key(e)])
        agree = sum(1 for x, y in zip(a, b) if x == y)
        rows.append({"rule": rule, "n": len(a), "agree": agree,
                     "kappa": kappa(a, b) if a else None})
    return rows


def blind_copy(src: str, dst: str) -> int:
    os.makedirs(dst, exist_ok=True)
    count = 0
    for name in sorted(os.listdir(src)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        with open(os.path.join(src, name), encoding="utf-8") as fh:
            entries = json.load(fh)
        blind = [{k: v for k, v in e.items() if k not in ("label", "note")} for e in entries]
        with open(os.path.join(dst, name), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(blind, fh, indent=2)
            fh.write("\n")
        count += len(blind)
    return count


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    blind = "--blind-copy" in args
    args = [a for a in args if a != "--blind-copy"]
    if len(args) != 2 or not os.path.isdir(args[0]):
        print(__doc__, file=sys.stderr)
        return 2
    if blind:
        print("wrote %d blind entries to %s" % (blind_copy(args[0], args[1]), args[1]))
        return 0
    if not os.path.isdir(args[1]):
        print("no such directory: %s" % args[1], file=sys.stderr)
        return 2
    print("| Rule | Pairs | Agreement | Kappa |\n|---|---|---|---|")
    for r in compare(_load(args[0]), _load(args[1])):
        k = "n/a" if r["kappa"] is None else "%.2f" % r["kappa"]
        pct = "n/a" if not r["n"] else "%d%%" % round(100.0 * r["agree"] / r["n"])
        print("| %s | %d | %s | %s |" % (r["rule"], r["n"], pct, k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
