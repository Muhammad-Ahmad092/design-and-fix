#!/usr/bin/env python3
"""
run_precision.py - precision study over the evaluation corpus (offline; see fetch_corpus.py).

Usage:
    python evals/run_precision.py sample [--corpus DIR] [--per-rule 20] [--seed 1002]
                                         [--out evals/labels/baseline] [--force]
    python evals/run_precision.py report [--labels evals/labels/baseline]
                                         [--out evals/precision-baseline.md] [--corpus DIR]
                                         [--no-measure] [--tag TAG] [--append NOTES.md]

sample  scans every corpus repo, draws up to N signals per rule across the corpus (round-robin
        over repos, fixed seed) and writes one labelling sheet per rule, `<rule>.json`, plus
        `_population.json` (signal counts per repo and rule, used for extrapolation).
report  reads the labelled sheets (label: tp | fp | unsure, reason in note) and writes a Markdown
        report: precision per rule with sample sizes, extrapolated false positives per repo,
        recall on the fixture monorepo, repeat-run consistency, scan time and peak memory.
        --append adds a hand-written Markdown file at the end, so analysis that the generator
        cannot produce survives regeneration.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import platform
import random
import statistics
import sys
import time
import tracemalloc
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "skills", "design-and-fix", "scripts")
FIXTURES = os.path.join(REPO, "tests", "fixtures")
for _p in (HERE, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dfscan import registry  # noqa: E402
from dfscan.meta import PLUGIN_VERSION  # noqa: E402
from dfscan.report import scan  # noqa: E402
from fetch_corpus import DEFAULT_DEST, load_corpus, repo_root  # noqa: E402

DEFAULT_CORPUS = DEFAULT_DEST
DEFAULT_LABELS = os.path.join(HERE, "labels", "baseline")
POPULATION = "_population.json"
LABELS = ("tp", "fp", "unsure")
SHEET_KEYS = ("repo", "file", "line", "snippet", "fingerprint", "label", "note")


# ---------------------------------------------------------------- scanning
def scan_repo(corpus_dir: str, entry: dict) -> dict:
    root = repo_root(corpus_dir, entry["name"])
    scope = [entry["subdir"]] if entry.get("subdir") else None
    return scan(root, scope=scope, use_git=False)


def rule_counts(result: dict) -> Dict[str, int]:
    """Signals per rule before the per-rule cap (the cap only truncates the list)."""
    counts: Dict[str, int] = {}
    for s in result["signals"]:
        counts[s["rule"]] = counts.get(s["rule"], 0) + 1
    counts.update(result["summary"].get("truncated", {}))
    return counts


# ---------------------------------------------------------------- sampling
def sample_signals(signals_by_repo: Dict[str, List[dict]], per_rule: int,
                   seed: int) -> Dict[str, List[dict]]:
    """Up to per_rule signals per rule, stratified across repos, deterministic for a seed.

    Each repo's signals for a rule are shuffled with a rule-specific RNG; repos are then
    visited round-robin (in a shuffled order) taking one signal each until per_rule is reached,
    so small repos are fully represented and large ones cannot crowd them out.
    """
    rules = sorted({s["rule"] for sigs in signals_by_repo.values() for s in sigs})
    out: Dict[str, List[dict]] = {}
    for rule in rules:
        rng = random.Random("%d:%s" % (seed, rule))
        pools: Dict[str, List[dict]] = {}
        for repo in sorted(signals_by_repo):
            pool = sorted((s for s in signals_by_repo[repo] if s["rule"] == rule),
                          key=lambda s: (s["file"], s["line"], s.get("fingerprint", "")))
            if pool:
                rng.shuffle(pool)
                pools[repo] = pool
        order = sorted(pools)
        rng.shuffle(order)
        picked: List[dict] = []
        while len(picked) < per_rule and any(pools.values()):
            for repo in order:
                if pools[repo] and len(picked) < per_rule:
                    s = pools[repo].pop()
                    picked.append({"repo": repo, "file": s["file"], "line": s["line"],
                                   "snippet": s["snippet"], "fingerprint": s.get("fingerprint", ""),
                                   "label": "", "note": ""})
        out[rule] = sorted(picked, key=lambda e: (e["repo"], e["file"], e["line"], e["fingerprint"]))
    return out


# ---------------------------------------------------------------- report math
def rule_stats(entries: List[dict]) -> dict:
    counts = {"tp": 0, "fp": 0, "unsure": 0, "unlabelled": 0}
    for e in entries:
        label = e.get("label", "")
        if label == "":
            counts["unlabelled"] += 1
        elif label in LABELS:
            counts[label] += 1
        else:
            raise ValueError("bad label %r (use tp, fp or unsure) in %r" % (label, e))
    decided = counts["tp"] + counts["fp"]
    return dict(counts, n=len(entries),
                precision=(counts["tp"] / decided) if decided else None)


def wilson(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def fp_estimates(stats: Dict[str, dict], pop: Dict[str, Dict[str, int]]) -> Dict[str, dict]:
    """Per repo: signals, estimated FPs = sum over rules of (fp rate of rule x repo's signals of
    that rule), and the signals of rules with no labelled evidence (left out of the estimate)."""
    out: Dict[str, dict] = {}
    for repo, counts in pop.items():
        est, unestimated = 0.0, 0
        for rule, n in counts.items():
            st = stats.get(rule)
            if st is None or st["precision"] is None:
                unestimated += n
            else:
                est += n * (1.0 - st["precision"])
        out[repo] = {"signals": sum(counts.values()), "est_fp": est, "unestimated": unestimated}
    return out


# ---------------------------------------------------------------- recall, consistency, timing
def fixture_recall() -> dict:
    with open(os.path.join(HERE, "scanner-expected.json"), encoding="utf-8") as fh:
        exp = json.load(fh)
    got = {(s["rule"], s["file"]) for s in scan(os.path.join(FIXTURES, "monorepo"), use_git=False)["signals"]}
    must = [tuple(x) for x in exp["monorepo"]["must_find"]]
    clean = scan(os.path.join(FIXTURES, "clean-service"), use_git=False)["signals"]
    return {"found": sum(1 for x in must if x in got), "total": len(must),
            "missed": [list(x) for x in must if x not in got],
            "false_hits": [list(x) for x in exp["monorepo"]["must_not_find"] if tuple(x) in got],
            "clean_signals": len(clean)}


def canonical(result: dict) -> str:
    r = json.loads(json.dumps(result))
    r.get("meta", {}).pop("timestamp", None)
    return json.dumps(r, sort_keys=True)


def measure(corpus_dir: str, corpus: dict) -> List[dict]:
    """Per repo: a timed scan, then a second scan under tracemalloc (peak memory), compared."""
    rows = []
    for entry in corpus["repos"]:
        t0 = time.perf_counter()
        first = scan_repo(corpus_dir, entry)
        seconds = time.perf_counter() - t0
        tracemalloc.start()
        try:
            second = scan_repo(corpus_dir, entry)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        root = repo_root(corpus_dir, entry["name"])
        on_disk = sum(len(fs) for _, _, fs in os.walk(root))
        rows.append({"repo": entry["name"], "on_disk": on_disk,
                     "files": first["summary"]["files_scanned"],
                     "loc": first["summary"]["approx_loc"], "seconds": seconds,
                     "peak_mb": peak / (1024.0 * 1024.0),
                     "identical": canonical(first) == canonical(second)})
    return rows


# ---------------------------------------------------------------- sheets
def write_json(path: str, data: object) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_labels(labels_dir: str) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for fn in sorted(os.listdir(labels_dir)):
        if fn.endswith(".json") and fn != POPULATION:
            with open(os.path.join(labels_dir, fn), encoding="utf-8") as fh:
                out[fn[:-5]] = json.load(fh)
    return out


def cmd_sample(args: argparse.Namespace) -> int:
    corpus = load_corpus()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    if not args.force:
        for rule, entries in load_labels(out).items():
            if any(e.get("label") for e in entries):
                print("error: %s already holds labels (%s.json); use --force to overwrite"
                      % (out, rule), file=sys.stderr)
                return 2
    by_repo: Dict[str, List[dict]] = {}
    repos: Dict[str, dict] = {}
    for entry in corpus["repos"]:
        result = scan_repo(args.corpus, entry)
        by_repo[entry["name"]] = result["signals"]
        repos[entry["name"]] = {"url": entry["url"], "sha256": entry["sha256"],
                                "subdir": entry["subdir"],
                                "files_scanned": result["summary"]["files_scanned"],
                                "signals": dict(sorted(rule_counts(result).items()))}
    for fn in os.listdir(out):
        if fn.endswith(".json"):
            os.remove(os.path.join(out, fn))
    sheets = sample_signals(by_repo, args.per_rule, args.seed)
    for rule, entries in sheets.items():
        write_json(os.path.join(out, rule + ".json"), entries)
    write_json(os.path.join(out, POPULATION), {
        "scanner_version": PLUGIN_VERSION, "seed": args.seed, "per_rule": args.per_rule,
        "rule_versions": {r: m.version for r, m in sorted(registry.RULES.items())},
        "repos": repos})
    total = sum(len(v) for v in sheets.values())
    print("wrote %d sheets (%d signals to label) to %s" % (len(sheets), total, out))
    return 0


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.0f%%" % (100 * x)


def render_report(labels: Dict[str, List[dict]], pop_doc: dict, recall: dict,
                  rows: Optional[List[dict]], corpus: dict, args: argparse.Namespace) -> str:
    pop = {name: r["signals"] for name, r in pop_doc["repos"].items()}
    stats = {rule: rule_stats(entries) for rule, entries in labels.items()}
    rules = sorted(set(registry.RULES) | set(stats))
    total_signals = {rule: sum(c.get(rule, 0) for c in pop.values()) for rule in rules}
    out = ["# Precision study (%s)" % args.tag, "",
           "Generated by `evals/run_precision.py report` on %s. Scanner %s, seed %s, up to %s "
           "labelled signals per rule across the whole corpus (stratified round-robin over repos). "
           "Every number below comes with the sample it was computed from." % (
               datetime.date.today().isoformat(), pop_doc.get("scanner_version"),
               pop_doc.get("seed"), pop_doc.get("per_rule")), ""]
    out += ["## Environment", "",
            "- OS: %s" % platform.platform(),
            "- Python: %s (%s)" % (platform.python_version(), platform.python_implementation()),
            "- CPU count: %s" % (os.cpu_count() or "unknown"),
            "- Scanner (plugin) version: %s; rule versions: %s" % (
                pop_doc.get("scanner_version"),
                ", ".join("%s=%s" % kv for kv in sorted(pop_doc.get("rule_versions", {}).items()))),
            ""]
    out += ["## Precision per rule", "",
            "precision = tp / (tp + fp); `unsure` labels are excluded from the ratio. "
            "The 95% interval is the Wilson score interval on the decided labels; with n = 20 "
            "it is wide, so read precision as a band, not a point.", "",
            "| Rule | Corpus signals | Sample n | tp | fp | unsure | Precision | 95% CI |",
            "|---|---|---|---|---|---|---|---|"]
    for rule in rules:
        st = stats.get(rule) or rule_stats([])
        decided = st["tp"] + st["fp"]
        ci = "n/a" if not decided else "%s-%s" % tuple(_pct(v) for v in wilson(st["tp"], decided))
        out.append("| %s | %d | %d | %d | %d | %d | %s | %s |" % (
            rule, total_signals.get(rule, 0), st["n"], st["tp"], st["fp"], st["unsure"],
            _pct(st["precision"]), ci))
    unl = sum(st["unlabelled"] for st in stats.values())
    out += ["", "Unlabelled entries: %d." % unl if unl else "All sampled entries are labelled.",
            "Rules with 0 corpus signals could not be measured (no evidence either way).", ""]
    est = fp_estimates(stats, pop)
    labelled_from = {}
    for entries in labels.values():
        for e in entries:
            if e.get("label") in ("tp", "fp"):
                labelled_from[e["repo"]] = labelled_from.get(e["repo"], 0) + 1
    out += ["## False positives per repository (extrapolated)", "",
            "Estimated FPs = sum over rules of (rule fp rate from the sample x the repo's signals "
            "of that rule). It is an extrapolation from the per-rule samples, not a count; the "
            "`labels from repo` column is how many decided labels were drawn from that repo itself. "
            "The rule fp rate is pooled with equal weight per sampled signal; because the sample is "
            "drawn round-robin over repos, small repos are over-represented relative to their share "
            "of the signals. The sample is drawn from the listed signals (at most 200 per rule per "
            "repo), while the signal counts here are uncapped.", "",
            "| Repo | Signals | Estimated FPs | Labels from repo (tp+fp) | Signals of unmeasured rules |",
            "|---|---|---|---|---|"]
    for repo in sorted(est):
        e = est[repo]
        out.append("| %s | %d | %.1f | %d | %d |" % (
            repo, e["signals"], e["est_fp"], labelled_from.get(repo, 0), e["unestimated"]))
    if est:
        mean_fp = statistics.mean(e["est_fp"] for e in est.values())
        n_dec = sum(st["tp"] + st["fp"] for st in stats.values())
        out += ["", "Estimated FPs per repo: mean **%.1f**, median **%.1f** (%d repos; extrapolated "
                "from %d decided labels over %d rules)." % (
                    mean_fp, statistics.median(e["est_fp"] for e in est.values()), len(est), n_dec,
                    sum(1 for st in stats.values() if st["tp"] + st["fp"]))]
    out += ["", "## Recall on the fixture monorepo", "",
            "Known issues in `tests/fixtures/monorepo` (`evals/scanner-expected.json` must_find): "
            "**%d / %d** found. must_not_find hits: %d. Signals on `clean-service`: %d." % (
                recall["found"], recall["total"], len(recall["false_hits"]), recall["clean_signals"])]
    if recall["missed"]:
        out += ["", "Missed: " + ", ".join("`%s %s`" % tuple(m) for m in recall["missed"])]
    out += ["", "## Repeat-run consistency, scan time and memory", ""]
    if rows is None:
        out.append("Not measured in this run (`--no-measure` or corpus missing).")
    else:
        out += ["Each repo was scanned twice in one process: a timed scan, then a scan under "
                "`tracemalloc` (peak Python heap). The two JSON results are compared with "
                "`meta.timestamp` removed. Scan time excludes git (`use_git=False`). Files in repo "
                "counts every extracted file; files scanned counts the code, manifest and config "
                "files the scanner lists inside the scope.", "",
                "| Repo | Files in repo | Files scanned | Approx LOC | Scan time (s) | Peak memory (MB) | Identical rerun |",
                "|---|---|---|---|---|---|---|"]
        for r in rows:
            out.append("| %s | %d | %d | %d | %.2f | %.1f | %s |" % (
                r["repo"], r["on_disk"], r["files"], r["loc"], r["seconds"], r["peak_mb"],
                "yes" if r["identical"] else "NO"))
        largest = max(rows, key=lambda r: r["on_disk"])
        out += ["", "Median scan time: **%.2f s** over %d repos. Largest repo: %s (%d files, %d "
                "scanned) in **%.2f s**, peak %.1f MB. Identical reruns: %d / %d." % (
                    statistics.median(r["seconds"] for r in rows), len(rows), largest["repo"],
                    largest["on_disk"], largest["files"], largest["seconds"], largest["peak_mb"],
                    sum(1 for r in rows if r["identical"]), len(rows))]
    out += ["", "## Corpus", "",
            "| Repo | Archive (pinned) | SHA-256 | Scope | Kind | Note |", "|---|---|---|---|---|---|"]
    for e in corpus["repos"]:
        out.append("| %s | %s | `%s` | %s | %s | %s |" % (
            e["name"], e["url"], e["sha256"] or "(unrecorded)",
            "`%s`" % e["subdir"] if e["subdir"] else "whole repo", e["kind"],
            e.get("note", "").replace("|", "\\|")))
    out += ["", "## Reproduce", "", "```bash",
            "python evals/fetch_corpus.py            # downloads into ../design-and-fix-corpus/",
            "python evals/run_precision.py sample --per-rule %s --seed %s --out %s" % (
                pop_doc.get("per_rule"), pop_doc.get("seed"), args.labels_rel),
            "# label every entry: tp | fp | unsure, with a one-line note",
            "python evals/run_precision.py report --labels %s --out %s" % (args.labels_rel, args.out_rel),
            "```", ""]
    return "\n".join(out)


def _rel(path: str) -> str:
    try:
        rel = os.path.relpath(os.path.abspath(path), REPO)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def default_tag(out: str, labels_dir: str) -> str:
    """`precision-baseline.md` -> `baseline`; a plain `precision.md` takes the labels folder
    name (`labels/after` -> `after`)."""
    base = os.path.splitext(os.path.basename(out))[0]
    if "-" in base:
        return base.split("-", 1)[1]
    return os.path.basename(os.path.normpath(labels_dir)) or base


def append_notes(text: str, notes_path: str) -> str:
    with open(notes_path, encoding="utf-8") as fh:
        notes = fh.read().strip("\n")
    return text.rstrip("\n") + "\n\n" + notes + "\n"


def cmd_report(args: argparse.Namespace) -> int:
    labels = load_labels(args.labels)
    with open(os.path.join(args.labels, POPULATION), encoding="utf-8") as fh:
        pop_doc = json.load(fh)
    corpus = load_corpus()
    rows = None
    if not args.no_measure and os.path.isdir(args.corpus):
        rows = measure(args.corpus, corpus)
    args.labels_rel, args.out_rel = _rel(args.labels), _rel(args.out)
    if args.tag is None:
        args.tag = default_tag(args.out, args.labels)
    text = render_report(labels, pop_doc, fixture_recall(), rows, corpus, args)
    if getattr(args, "append", None):
        text = append_notes(text, args.append)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("wrote %s" % args.out)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    s = sub.add_parser("sample", help="Scan the corpus and write labelling sheets")
    s.add_argument("--corpus", default=DEFAULT_CORPUS)
    s.add_argument("--per-rule", type=int, default=20)
    s.add_argument("--seed", type=int, default=1002)
    s.add_argument("--out", default=DEFAULT_LABELS)
    s.add_argument("--force", action="store_true", help="Overwrite sheets that hold labels")
    r = sub.add_parser("report", help="Compute the precision report from labelled sheets")
    r.add_argument("--labels", default=DEFAULT_LABELS)
    r.add_argument("--out", default=os.path.join(HERE, "precision-baseline.md"))
    r.add_argument("--corpus", default=DEFAULT_CORPUS)
    r.add_argument("--tag", default=None, help="Report title tag (default: from --out name)")
    r.add_argument("--no-measure", action="store_true", help="Skip timing/memory/consistency scans")
    r.add_argument("--append", default=None,
                   help="Hand-written Markdown appended to the report (kept across regeneration)")
    args = parser.parse_args(argv)
    if args.cmd == "sample":
        return cmd_sample(args)
    if args.cmd == "report":
        return cmd_report(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
