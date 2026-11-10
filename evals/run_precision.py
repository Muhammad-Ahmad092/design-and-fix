#!/usr/bin/env python3
"""
run_precision.py - precision study over the evaluation corpus (offline; see fetch_corpus.py).

Usage:
    python evals/run_precision.py sample [--corpus DIR] [--per-rule 20] [--seed 1002]
                                         [--out evals/labels/baseline] [--force]
    python evals/run_precision.py report [--labels evals/labels/baseline] [--fn evals/labels/fn]
                                         [--out evals/precision-baseline.md] [--corpus DIR]
                                         [--no-measure] [--tag TAG] [--append NOTES.md]

sample  scans every corpus repo, draws up to N signals per rule across the corpus (round-robin
        over repos, fixed seed) and writes one labelling sheet per rule, `<rule>.json`, plus
        `_population.json` (signal counts per repo and rule, used for extrapolation). It warns
        when corpus.json `coverage` no longer matches the rules that fired.
report  reads the labelled sheets (label: tp | fp | unsure, reason in note) and the known-missed
        findings in evals/labels/fn/ (label: fn) and writes a Markdown report: precision, recall
        and F1 per rule with sample sizes, results per language and per framework, extrapolated
        false positives per repo, recall on the fixture monorepo, repeat-run consistency, scan
        time and peak memory. --append adds a hand-written Markdown file at the end, so analysis
        that the generator cannot produce survives regeneration.

Labelling rules (tp / fp / unsure / fn) are in evals/LABELLING.md; its `Guide version:` line is
recorded in the report and in _population.json.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import platform
import random
import re
import statistics
import sys
import time
import tracemalloc
from typing import Callable, Dict, List, Optional, Tuple

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
from dfscan.walker import CODE_EXTS  # noqa: E402
from fetch_corpus import DEFAULT_DEST, load_corpus, repo_root  # noqa: E402

DEFAULT_CORPUS = DEFAULT_DEST
DEFAULT_LABELS = os.path.join(HERE, "labels", "baseline")
DEFAULT_FN = os.path.join(HERE, "labels", "fn")
GUIDE = os.path.join(HERE, "LABELLING.md")
POPULATION = "_population.json"
LABELS = ("tp", "fp", "unsure")
SHEET_KEYS = ("repo", "file", "line", "snippet", "fingerprint", "label", "note")
CI_FILE_RE = re.compile(r"(?:^|/)(?:\.github/workflows/[^/]+|\.gitlab-ci\.yml|\.circleci/[^/]+)$")
CONFIG_EXTS = (".json", ".txt", ".toml", ".lock", ".properties", ".pem", ".key", ".xml", ".ini",
               ".cfg", ".env", ".gradle", ".csproj", ".mod", ".sum")


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


def _ratio(num: int, den: int) -> Optional[float]:
    return (num / den) if den else None


def _f1(precision: Optional[float], recall: Optional[float]) -> Optional[float]:
    if precision is None or recall is None:
        return None
    return (2 * precision * recall / (precision + recall)) if precision + recall else 0.0


def _check_fn(entries: List[dict]) -> int:
    for e in entries:
        if e.get("label") != "fn":
            raise ValueError("known-missed entries must have label 'fn': %r" % (e,))
    return len(entries)


def recall_stats(labels: Dict[str, List[dict]], fn: Dict[str, List[dict]],
                 reviewed: Optional[Dict[str, List[str]]] = None) -> Dict[str, dict]:
    """Per rule: tp, fp, fn, n (labelled sample size), precision, recall, f1.

    `fn` maps a rule to its known-missed findings (label `fn`). A rule absent from `fn` was never
    reviewed for misses, so its recall and F1 are None, never 100%. Precision uses every label;
    when `reviewed` (rule -> repos reviewed for misses) is given, recall counts only the tp
    labels from those repos, so tp and fn come from the same repos.
    """
    out: Dict[str, dict] = {}
    for rule in sorted(set(labels) | set(fn)):
        st = rule_stats(labels.get(rule, []))
        misses = _check_fn(fn[rule]) if rule in fn else 0
        recall = None
        if rule in fn:
            if reviewed is None:
                tp_rev = st["tp"]
            else:
                repos = set(reviewed.get(rule, []))
                tp_rev = sum(1 for e in labels.get(rule, [])
                             if e.get("label") == "tp" and e.get("repo") in repos)
            recall = _ratio(tp_rev, tp_rev + misses)
        out[rule] = {"tp": st["tp"], "fp": st["fp"], "fn": misses, "n": st["n"],
                     "precision": st["precision"], "recall": recall,
                     "f1": _f1(st["precision"], recall)}
    return out


def language_of(entry: dict, corpus_by_name: Dict[str, dict]) -> str:
    """Language of a labelled entry, from its file; folder-level entries (no-tests) and unknown
    file types take the repo's first language in corpus.json."""
    path = entry.get("file", "").replace("\\", "/")
    base = path.rsplit("/", 1)[-1]
    ext = os.path.splitext(base)[1].lower()
    if CI_FILE_RE.search(path):
        return "CI config"
    if base.startswith("Dockerfile") or ext == ".dockerfile":
        return "Dockerfile"
    if ext in CODE_EXTS:
        return CODE_EXTS[ext].split("/")[0].split(" (")[0]
    if ext in (".yml", ".yaml"):
        return "YAML"
    if ext in CONFIG_EXTS:
        return "Manifest or config"
    langs = corpus_by_name.get(entry.get("repo", ""), {}).get("languages") or []
    return langs[0] if langs else "Unknown"


def group_rows(labels: Dict[str, List[dict]], fn: Dict[str, List[dict]],
               reviewed: Dict[str, List[str]], key: Callable[[dict], str]) -> Dict[str, dict]:
    """tp/fp/unsure/fn per group (key(entry) -> group name). Recall per group counts only tp
    labels from (rule, repo) pairs reviewed for misses."""
    out: Dict[str, dict] = {}

    def row(name: str) -> dict:
        return out.setdefault(name, {"tp": 0, "fp": 0, "unsure": 0, "fn": 0, "tp_reviewed": 0})

    for rule, entries in labels.items():
        repos = set(reviewed.get(rule, []))
        for e in entries:
            label = e.get("label", "")
            if label in LABELS:
                r = row(key(e))
                r[label] += 1
                if label == "tp" and rule in fn and e.get("repo") in repos:
                    r["tp_reviewed"] += 1
    for entries in fn.values():
        for e in entries:
            row(key(e))["fn"] += 1
    for r in out.values():
        r["precision"] = _ratio(r["tp"], r["tp"] + r["fp"])
        r["recall"] = _ratio(r["tp_reviewed"], r["tp_reviewed"] + r["fn"])
    return out


# ---------------------------------------------------------------- corpus coverage
def coverage_from_population(pop_repos: Dict[str, dict]) -> Dict[str, List[str]]:
    """rule -> sorted repos with at least one signal, from _population.json `repos`."""
    out: Dict[str, List[str]] = {}
    for repo, info in pop_repos.items():
        for rule, n in info.get("signals", {}).items():
            if n:
                out.setdefault(rule, []).append(repo)
    return {rule: sorted(repos) for rule, repos in sorted(out.items())}


def coverage_problems(corpus: dict, rules: List[str]) -> List[str]:
    """Every rule must be in corpus.json `coverage` (repos that produce its signals) or in
    `unmeasured` (with a reason), not both. Returns human-readable problems; empty is fine."""
    names = {r["name"] for r in corpus.get("repos", [])}
    coverage = corpus.get("coverage", {})
    unmeasured = corpus.get("unmeasured", {})
    problems = []
    for rule in sorted(set(rules) | set(coverage) | set(unmeasured)):
        covered, reason = rule in coverage, rule in unmeasured
        if rule not in rules:
            problems.append("%s: listed in corpus.json but not a registered rule" % rule)
        elif covered and reason:
            problems.append("%s: in both coverage and unmeasured" % rule)
        elif covered:
            repos = coverage[rule]
            if not repos:
                problems.append("%s: coverage lists no repo" % rule)
            unknown = [r for r in repos if r not in names]
            if unknown:
                problems.append("%s: coverage names unknown repos %s" % (rule, ", ".join(unknown)))
        elif reason:
            if not str(unmeasured[rule]).strip():
                problems.append("%s: unmeasured without a reason" % rule)
        else:
            problems.append("%s: no corpus repo produces it and no unmeasured reason" % rule)
    return problems


def guide_version(path: str = GUIDE) -> Optional[str]:
    """The `Guide version: N` line of evals/LABELLING.md (None when missing)."""
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r"(?m)^Guide version:\s*(\S+)", fh.read())
    except OSError:
        return None
    return m.group(1) if m else None


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


def _fn_docs(fn_dir: str) -> List[dict]:
    """The per-repo known-missed files in fn_dir (`<repo>.json`; `_`-prefixed files skipped)."""
    if not os.path.isdir(fn_dir):
        return []
    docs = []
    for name in sorted(os.listdir(fn_dir)):
        if name.endswith(".json") and not name.startswith("_"):
            with open(os.path.join(fn_dir, name), encoding="utf-8") as fh:
                doc = json.load(fh)
            reviewed = set(doc.get("reviewed_rules", []))
            for e in doc.get("missed", []):
                if e.get("rule") not in reviewed:
                    raise ValueError("%s: miss for rule %r, which is not in reviewed_rules"
                                     % (name, e.get("rule")))
            docs.append(doc)
    return docs


def load_fn(fn_dir: str) -> Dict[str, List[dict]]:
    """rule -> known-missed findings (each tagged with its repo). A rule some repo was reviewed
    for, with no misses found, maps to []; a rule never reviewed is absent."""
    out: Dict[str, List[dict]] = {}
    for doc in _fn_docs(fn_dir):
        for rule in doc.get("reviewed_rules", []):
            out.setdefault(rule, [])
        for e in doc.get("missed", []):
            out[e["rule"]].append(dict(e, repo=doc["repo"]))
    return out


def load_reviewed(fn_dir: str) -> Dict[str, List[str]]:
    """rule -> repos reviewed for misses of that rule."""
    out: Dict[str, List[str]] = {}
    for doc in _fn_docs(fn_dir):
        for rule in doc.get("reviewed_rules", []):
            out.setdefault(rule, []).append(doc["repo"])
    return {rule: sorted(repos) for rule, repos in out.items()}


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
        "guide_version": guide_version(),
        "rule_versions": {r: m.version for r, m in sorted(registry.RULES.items())},
        "repos": repos})
    total = sum(len(v) for v in sheets.values())
    print("wrote %d sheets (%d signals to label) to %s" % (len(sheets), total, out))
    fired = coverage_from_population(repos)
    recorded = corpus.get("coverage", {})
    stale = sorted(r for r in set(fired) | set(recorded) if fired.get(r) != recorded.get(r))
    if stale:
        print("note: corpus.json coverage differs from this scan for: %s" % ", ".join(stale),
              file=sys.stderr)
    return 0


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.0f%%" % (100 * x)


def _group_table(title: str, rows: Dict[str, dict], intro: str) -> List[str]:
    out = ["## " + title, "", intro, "",
           "| %s | Decided (tp+fp) | tp | fp | unsure | Known misses (fn) | Precision | Recall |"
           % title.rsplit(" ", 1)[-1].capitalize(),
           "|---|---|---|---|---|---|---|---|"]
    for name in sorted(rows):
        r = rows[name]
        out.append("| %s | %d | %d | %d | %d | %d | %s | %s |" % (
            name, r["tp"] + r["fp"], r["tp"], r["fp"], r["unsure"], r["fn"],
            _pct(r["precision"]), _pct(r["recall"])))
    if not rows:
        out.append("| (no labels) | 0 | 0 | 0 | 0 | 0 | n/a | n/a |")
    return out + [""]


def render_report(labels: Dict[str, List[dict]], pop_doc: dict, recall: dict,
                  rows: Optional[List[dict]], corpus: dict, args: argparse.Namespace,
                  fn: Optional[Dict[str, List[dict]]] = None,
                  reviewed: Optional[Dict[str, List[str]]] = None) -> str:
    """Markdown report. `fn` (rule -> known misses) and `reviewed` (rule -> repos reviewed for
    misses; default: the repos named in `fn`) add recall and F1 where misses were reviewed."""
    fn = fn or {}
    if reviewed is None:
        reviewed = {rule: sorted({e.get("repo", "") for e in es}) for rule, es in fn.items()}
    pop = {name: r["signals"] for name, r in pop_doc["repos"].items()}
    stats = {rule: rule_stats(entries) for rule, entries in labels.items()}
    rstats = recall_stats(labels, fn, reviewed)
    rules = sorted(set(registry.RULES) | set(stats) | set(fn))
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
            "- Labelling guide (`evals/LABELLING.md`): %s; current Guide version %s" % (
                "sheets sampled under Guide version %s" % pop_doc["guide_version"]
                if pop_doc.get("guide_version") else "sheets predate the written guide (version 1)",
                guide_version() or "unknown"),
            ""]
    out += ["## Precision and recall per rule", "",
            "precision = tp / (tp + fp); `unsure` labels are excluded from the ratio. "
            "The 95% interval is the Wilson score interval on the decided labels; with n = 20 "
            "it is wide, so read precision as a band, not a point. fn counts the known-missed "
            "findings recorded in `evals/labels/fn/`; recall = tp / (tp + fn) over the repos "
            "reviewed for misses of that rule only, and F1 = 2PR / (P + R). Recall is an upper "
            "bound (only misses someone found are counted) and is n/a for rules nobody reviewed "
            "for misses.", "",
            "| Rule | Corpus signals | Sample n | tp | fp | unsure | Precision | 95% CI | fn | Recall | F1 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for rule in rules:
        st = stats.get(rule) or rule_stats([])
        rs = rstats.get(rule) or {"fn": 0, "recall": None, "f1": None}
        decided = st["tp"] + st["fp"]
        ci = "n/a" if not decided else "%s-%s" % tuple(_pct(v) for v in wilson(st["tp"], decided))
        out.append("| %s | %d | %d | %d | %d | %d | %s | %s | %d | %s | %s |" % (
            rule, total_signals.get(rule, 0), st["n"], st["tp"], st["fp"], st["unsure"],
            _pct(st["precision"]), ci, rs["fn"], _pct(rs["recall"]), _pct(rs["f1"])))
    unl = sum(st["unlabelled"] for st in stats.values())
    out += ["", "Unlabelled entries: %d." % unl if unl else "All sampled entries are labelled.",
            "Rules with 0 corpus signals could not be measured (no evidence either way).", ""]
    unmeasured = corpus.get("unmeasured", {})
    if unmeasured:
        out += ["Rules no corpus repo triggers (from `evals/corpus.json`):", ""]
        out += ["- %s: %s" % (rule, unmeasured[rule]) for rule in sorted(unmeasured)]
        out.append("")
    by_name = {e["name"]: e for e in corpus.get("repos", [])}
    out += _group_table(
        "Results per language", group_rows(labels, fn, reviewed, lambda e: language_of(e, by_name)),
        "Language of each labelled finding, from its file extension (Dockerfiles, CI configs and "
        "YAML are their own rows; folder-level findings such as no-tests take the repo's first "
        "language in corpus.json). Counts are labels, not corpus signals; the sample is drawn per "
        "rule, so a language's row mixes rules in whatever proportion the sample drew them.")
    out += _group_table(
        "Results per framework",
        group_rows(labels, fn, reviewed,
                   lambda e: by_name.get(e.get("repo", ""), {}).get("framework") or "(none)"),
        "Framework of the repo each label came from (the corpus.json `framework` field).")
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
            "# label every entry: tp | fp | unsure, with a one-line note (evals/LABELLING.md)",
            "# record known misses per repo in evals/labels/fn/<repo>.json (label: fn)",
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
    text = render_report(labels, pop_doc, fixture_recall(), rows, corpus, args,
                         fn=load_fn(args.fn), reviewed=load_reviewed(args.fn))
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
    r.add_argument("--fn", default=DEFAULT_FN,
                   help="Folder of known-missed findings per repo (see evals/LABELLING.md)")
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
