"""Built-in configuration profiles (`--profile NAME` or `"profile": "NAME"` in the config file).

A profile is a partial config: it only turns rules off (`"enabled": false`) or changes `limits`.
It never changes a rule's severity or detection logic, and it never turns on a rule that is off by
default. Merge order: built-in defaults < profile < config file < CLI flags (the config file wins
on every key it sets, rule entries merged key by key).

Focused profiles keep the rules of their focus categories plus a few named extras and disable the
rest. `tests/test_config_sarif.py` checks that every registered rule is classified, so a new rule
must be added to each focused profile's disabled list when its category is out of focus.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# profile -> (categories kept, extra rule ids kept)
FOCUS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "security-focused": (("Security",), ("missing-lockfile", "unpinned-dependency")),
    "api-focused": (("API Design",), ("unauth-write-route",)),
    "performance-focused": (("Scalability",), ("http-no-timeout", "list-route-no-pagination")),
    "architecture-focused": (("System Design", "Maintainability"), ()),
}


def _off(ids: List[str]) -> dict:
    return {"rules": {rid: {"enabled": False} for rid in ids}}


PROFILES: Dict[str, dict] = {
    # The defaults: every rule at its default on/off state, default limits.
    "balanced": {},
    "security-focused": _off([
        "ci-no-test-step", "console-logging-only", "docker-latest-tag", "docker-no-healthcheck",
        "god-module", "http-no-timeout", "import-cycle", "k8s-no-limits", "k8s-no-probes",
        "list-route-no-pagination", "mixed-error-shapes", "no-health-endpoint", "no-tests",
        "no-version-prefix", "openapi-drift", "query-in-loop", "retry-no-backoff",
        "swallowed-error", "unbounded-query", "unvalidated-body"]),
    "api-focused": _off([
        "ci-no-test-step", "console-logging-only", "docker-latest-tag", "docker-no-healthcheck",
        "docker-root-user", "god-module", "hardcoded-secret", "http-no-timeout", "import-cycle",
        "k8s-no-limits", "k8s-no-probes", "missing-lockfile", "no-health-endpoint", "no-tests",
        "query-in-loop", "retry-no-backoff", "sql-string-concat", "swallowed-error",
        "unbounded-query", "unpinned-dependency"]),
    "performance-focused": _off([
        "ci-no-test-step", "console-logging-only", "docker-latest-tag", "docker-no-healthcheck",
        "docker-root-user", "god-module", "hardcoded-secret", "import-cycle", "k8s-no-probes",
        "missing-lockfile", "mixed-error-shapes", "no-health-endpoint", "no-tests",
        "no-version-prefix", "openapi-drift", "retry-no-backoff", "sql-string-concat",
        "swallowed-error", "unauth-write-route", "unpinned-dependency", "unvalidated-body"]),
    "architecture-focused": _off([
        "console-logging-only", "docker-latest-tag", "docker-no-healthcheck", "docker-root-user",
        "hardcoded-secret", "http-no-timeout", "k8s-no-limits", "k8s-no-probes",
        "list-route-no-pagination", "mixed-error-shapes", "no-health-endpoint",
        "no-version-prefix", "openapi-drift", "query-in-loop", "retry-no-backoff",
        "sql-string-concat", "swallowed-error", "unauth-write-route", "unbounded-query",
        "unvalidated-body"]),
    # Every rule, tighter budgets: a first look at a large repo in about a minute.
    "quick": {"limits": {"max_files": 20000, "max_file_bytes": 262144, "timeout_seconds": 60}},
}
