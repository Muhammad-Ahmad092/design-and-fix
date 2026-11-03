from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

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


RULES: Dict[str, RuleMeta] = {}


def register(meta: RuleMeta) -> None:
    RULES[meta.id] = meta


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
