"""Architecture Health Score rubric: one implementation shared by the output linter and tests.

Mirrors references/health-score-rubric.md. Stdlib only, no dependency on dfscan.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Dict, Iterable, Optional

# Category name -> issue ID prefix, in table order.
CATEGORY_PREFIX = {
    "System Design": "SYS", "API Design": "API", "Scalability": "SCL", "Reliability": "REL",
    "Security": "SEC", "Observability": "OBS", "Maintainability": "MNT",
}
CATEGORIES = list(CATEGORY_PREFIX)

SUBSCORE_BAND = {10: "Strong", 9: "Strong", 8: "Good", 7: "Good", 6: "Mixed", 5: "Mixed",
                 4: "Weak", 3: "Weak", 2: "Severe", 1: "Severe"}

# Context keyword -> category weights (spec section 12). A keyword found in the Context
# line raises the listed categories; unlisted categories stay 1.0, and when several
# keywords raise the same category the highest weight wins. Keywords match
# case-insensitively as whole words/phrases (a hyphen in a keyword also matches a space or
# underscore), so "health check" or "pii-free" do not match. A keyword negated in its own
# clause ("no PII", "not real-time", "without payments", "free of PHI": one of NEGATIONS
# within the 3 words before it, counting back to the last , ; : . or bracket) does not count.
_SECURITY = {"Security": 2.0}
_LATENCY = {"Scalability": 1.5, "Reliability": 1.5}
CONTEXT_WEIGHTS = {
    "payment": _SECURITY,
    "payments": _SECURITY,
    "healthcare": _SECURITY,
    "health data": _SECURITY,
    "phi": _SECURITY,
    "pii": _SECURITY,
    "pci": _SECURITY,
    "cardholder": _SECURITY,
    "hipaa": _SECURITY,
    "gdpr": _SECURITY,
    "fintech": _SECURITY,
    "latency-critical": _LATENCY,
    "realtime": _LATENCY,
    "real-time": _LATENCY,
    "real time": _LATENCY,
}
NEGATIONS = ("no", "not", "non", "without", "free of")


def subscore(severities: Iterable[str]) -> int:
    """Category sub-score from the rubric lookup table, given its issues' severities."""
    sev = list(severities)
    crit, high, med = sev.count("Critical"), sev.count("High"), sev.count("Medium")
    if crit:
        return {1: 3, 2: 2}.get(crit, 1)
    if high >= 3:
        return 3
    if high == 2:
        return 4
    if high == 1:
        return 6 if med <= 2 else 5
    if med:
        return 8 if med <= 2 else 7
    return 9 if sev else 10


def overall(subscores: Dict[str, int], weights: Optional[Dict[str, float]] = None) -> float:
    """Weighted mean of the assessed sub-scores, rounded half up to one decimal.

    `weights` maps category name to weight; missing categories weigh 1.0."""
    if not subscores:
        raise ValueError("no assessed sub-scores")
    weights = weights or {}
    num = Fraction(0)
    den = Fraction(0)
    for cat, score in subscores.items():
        w = Fraction(str(weights.get(cat, 1.0)))
        num += w * score
        den += w
    tenths = int(num * 10 / den + Fraction(1, 2))  # exact arithmetic: half up, no float drift
    return tenths / 10.0


def band(score: float) -> str:
    if score >= 8.0:
        return "Healthy"
    if score >= 6.0:
        return "Stable with risks"
    if score >= 4.0:
        return "Fragile"
    return "Unsafe"


def _keyword_re(keyword: str):
    body = r"[-_ ]".join(re.escape(part) for part in keyword.split("-"))
    return re.compile(r"(?<![\w-])%s(?![\w-])" % body, re.IGNORECASE)


def _negated(text: str, start: int) -> bool:
    """True when one of NEGATIONS sits within 3 words before `start`, in the same clause."""
    clause = re.split(r"[,;:.()\[\]]", text[:start])[-1]
    words = re.findall(r"[a-z]+", clause.lower())[-3:]
    joined = " %s " % " ".join(words)
    return any(" %s " % neg in joined for neg in NEGATIONS)


def weights_for(context_line: str) -> Dict[str, float]:
    """Category weights implied by a Context line; all 1.0 when no keyword matches."""
    out = {cat: 1.0 for cat in CATEGORIES}
    for keyword, raised in CONTEXT_WEIGHTS.items():
        if any(not _negated(context_line, m.start())
               for m in _keyword_re(keyword).finditer(context_line)):
            for cat, w in raised.items():
                out[cat] = max(out[cat], w)
    return out
