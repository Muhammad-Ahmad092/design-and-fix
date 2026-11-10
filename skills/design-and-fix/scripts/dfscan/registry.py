from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .model import Signal

CATEGORIES = ("System Design", "API Design", "Scalability", "Reliability", "Security",
              "Observability", "Maintainability")
SEVERITIES = ("Critical", "High", "Medium", "Low")
CONFIDENCES = ("high", "medium", "low")


@dataclass
class RuleMeta:
    id: str
    version: str
    category: str
    default_severity: str
    confidence: str
    description: str
    limitations: str
    remediation: str
    languages: List[str] = field(default_factory=list)
    default_enabled: bool = True  # False: the rule's signals are dropped unless config enables it


RULES: Dict[str, RuleMeta] = {}

# rule id -> confidence from measured precision (evals/regression.py --write-measured)
MEASURED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "measured.json")
_measured: Optional[Dict[str, str]] = None


def measured_confidence(rule_id: str) -> Optional[str]:
    """Confidence from measured precision (>= 80% high, 50-79% medium, < 50% low), or None
    when the rule is not in measured.json (the registry value then applies)."""
    global _measured
    if _measured is None:
        try:
            with open(MEASURED_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            _measured = {k: v for k, v in data.items() if v in CONFIDENCES}
        except (OSError, ValueError, AttributeError):
            _measured = {}
    return _measured.get(rule_id)


def register(meta: RuleMeta) -> None:
    # Measured precision can only lower the registry confidence, never raise it. report._stamp
    # caps every signal at meta.confidence, so the measured value feeds that same cap.
    m = measured_confidence(meta.id)
    if m is not None and CONFIDENCES.index(m) > CONFIDENCES.index(meta.confidence):
        meta.confidence = m
    RULES[meta.id] = meta


def is_enabled(rule_id: str, rule_cfg: Dict[str, Dict[str, Any]]) -> bool:
    """Config `"enabled"` wins when set; otherwise the rule's `default_enabled`.
    An unregistered rule id with no config entry counts as enabled."""
    enabled = rule_cfg.get(rule_id, {}).get("enabled")
    if enabled is not None:
        return bool(enabled)
    meta = RULES.get(rule_id)
    return meta.default_enabled if meta is not None else True


def default_off_rules(rule_cfg: Dict[str, Dict[str, Any]]) -> List[str]:
    """Sorted ids of rules that are off by default and that the config does not mention via
    `"enabled"` (a config that disables one explicitly is disclosed by its own note)."""
    return sorted(rid for rid, m in RULES.items()
                  if not m.default_enabled and rule_cfg.get(rid, {}).get("enabled") is None)


def ensure_loaded() -> None:
    """Import every rule module so RULES is complete (rules register themselves on import;
    `report` imports them all). Safe to call repeatedly."""
    from . import report  # noqa: F401  (lazy: report imports registry)


def _is_path_or_folder(path: str, root_files: Set[str]) -> bool:
    if path in root_files:
        return True
    if path in ("", "."):
        return path == "." and bool(root_files)
    prefix = path.rstrip("/") + "/"
    return any(f.startswith(prefix) for f in root_files)


def validate_signal(sig: Signal, root_files: Set[str]) -> Optional[str]:
    """Return an error string if the signal is malformed, else None."""
    if sig.rule not in RULES:
        return "unregistered rule %r" % (sig.rule,)
    if sig.category not in CATEGORIES:
        return "%s: bad category %r" % (sig.rule, sig.category)
    if sig.severity_hint not in SEVERITIES:
        return "%s: bad severity %r" % (sig.rule, sig.severity_hint)
    if sig.confidence not in CONFIDENCES:
        return "%s: bad confidence %r" % (sig.rule, sig.confidence)
    if not isinstance(sig.line, int) or isinstance(sig.line, bool) or sig.line < 0:
        return "%s: bad line %r" % (sig.rule, sig.line)
    if not _is_path_or_folder(sig.file, root_files):
        return "%s: file %r not in scanned files" % (sig.rule, sig.file)
    return None
