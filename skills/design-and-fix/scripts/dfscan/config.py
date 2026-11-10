from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import profiles, registry
from .ignore import InvalidPattern, compile_glob
from .walker import MAX_FILE_BYTES

CONFIG_NAME = ".design-and-fix.json"
FAIL_LEVELS = ("Critical", "High", "Medium", "Low", "none")
TOP_KEYS = ("$schema", "profile", "exclude", "include", "rules", "limits", "fail_on", "output")
LIMIT_KEYS = ("max_file_bytes", "max_files", "timeout_seconds")
RULE_KEYS = ("enabled", "severity")
OUTPUT_KEYS = ("verbose",)
DEFAULT_MAX_FILES = 50000


class ConfigError(Exception):
    """Invalid configuration. The message names the key, the problem and the fix."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Config:
    exclude: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)
    rules: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # id -> {"enabled", "severity"}
    limits: Dict[str, Any] = field(default_factory=lambda: {
        "max_file_bytes": MAX_FILE_BYTES, "max_files": DEFAULT_MAX_FILES, "timeout_seconds": None})
    fail_on: str = "none"
    verbose: bool = False
    path: Optional[str] = None  # the file this came from (None: defaults)
    profile: Optional[str] = None  # built-in profile applied under the file (profiles.PROFILES)


def _err(path: str, key: str, problem: str, fix: str) -> ConfigError:
    return ConfigError("%s: key '%s': %s; fix: %s" % (path, key, problem, fix))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _globs(path: str, key: str, value: Any) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise _err(path, key, "must be a list of glob strings",
                   'write it like "%s": ["dist/**", "*.generated.ts"]' % key)
    out = [x.replace("\\", "/") for x in value]
    for i, glob in enumerate(out):
        stripped = glob.lstrip("!").rstrip("/")
        if not stripped:
            continue
        try:
            compile_glob(stripped)
        except InvalidPattern as exc:
            raise _err(path, "%s[%d]" % (key, i), "invalid glob %r (%s)" % (value[i], exc),
                       "fix the bracket expression, e.g. [a-z] or [[:digit:]] (ranges go low to high)")
    return out


def _check_keys(path: str, where: str, data: dict, allowed: tuple) -> None:
    for k in data:
        if k not in allowed:
            raise _err(path, where + k, "unknown key",
                       "remove it or use one of: %s" % ", ".join(allowed))


def _merge(base: dict, over: dict) -> dict:
    """`over` wins on every key it sets; `rules` entries and `limits`/`output` merge key by key."""
    out = dict(base)
    for key, value in over.items():
        prev = out.get(key)
        if key == "rules" and isinstance(value, dict) and isinstance(prev, dict):
            rules = dict(prev)
            for rid, entry in value.items():
                old = rules.get(rid)
                if isinstance(entry, dict) and isinstance(old, dict):
                    merged = dict(old)
                    merged.update(entry)
                    rules[rid] = merged
                else:
                    rules[rid] = entry
            out[key] = rules
        elif key in ("limits", "output") and isinstance(value, dict) and isinstance(prev, dict):
            merged = dict(prev)
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def parse_config(data: Any, path: str, profile: Optional[str] = None) -> Config:
    """Validate `data` (a config file's JSON). `profile` (the --profile flag) or the file's own
    `"profile"` key names a built-in profile applied under the file: defaults < profile < file."""
    if not isinstance(data, dict):
        raise _err(path, "<root>", "the file must contain a JSON object",
                   'start the file with { and use keys such as "exclude" or "fail_on"')
    _check_keys(path, "", data, TOP_KEYS)
    if "profile" in data and (not isinstance(data["profile"], str)
                              or data["profile"] not in profiles.PROFILES):
        raise _err(path, "profile", "unknown profile %r" % (data["profile"],),
                   "use one of: %s" % ", ".join(sorted(profiles.PROFILES)))
    if profile is not None and profile not in profiles.PROFILES:
        raise ConfigError("unknown profile %r; fix: use one of: %s"
                          % (profile, ", ".join(sorted(profiles.PROFILES))))
    name = profile or data.get("profile")
    if name:
        data = _merge(profiles.PROFILES[name], data)
    registry.ensure_loaded()  # rule ids are validated against the full registry
    cfg = Config(path=path, profile=name or None)
    if "$schema" in data and not isinstance(data["$schema"], str):
        raise _err(path, "$schema", "must be a string", "use a URL or path string, or remove it")
    if "exclude" in data:
        cfg.exclude = _globs(path, "exclude", data["exclude"])
    if "include" in data:
        cfg.include = _globs(path, "include", data["include"])
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise _err(path, "rules", "must be an object keyed by rule id",
                   'write it like "rules": {"http-no-timeout": {"enabled": false}}')
    for rid, entry in rules.items():
        key = "rules.%s" % rid
        if rid not in registry.RULES:
            raise _err(path, key, "unknown rule id",
                       "use one of: %s" % ", ".join(sorted(registry.RULES)))
        if not isinstance(entry, dict):
            raise _err(path, key, "must be an object",
                       'write it like {"enabled": false, "severity": "Medium"}')
        _check_keys(path, key + ".", entry, RULE_KEYS)
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise _err(path, key + ".enabled", "must be true or false",
                       "use a JSON boolean, not a string")
        if "severity" in entry and entry["severity"] not in registry.SEVERITIES:
            raise _err(path, key + ".severity", "must be one of %s" % ", ".join(registry.SEVERITIES),
                       "use one of those exact values (case-sensitive)")
        cfg.rules[rid] = dict(entry)
    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        raise _err(path, "limits", "must be an object",
                   'write it like "limits": {"max_files": 20000}')
    _check_keys(path, "limits.", limits, LIMIT_KEYS)
    for k in ("max_file_bytes", "max_files"):
        if k in limits:
            if not _is_int(limits[k]) or limits[k] < 1:
                raise _err(path, "limits." + k, "must be a whole number of at least 1",
                           "use a positive integer such as %d" % cfg.limits[k])
            cfg.limits[k] = limits[k]
    if "timeout_seconds" in limits and limits["timeout_seconds"] is not None:
        t = limits["timeout_seconds"]
        if (isinstance(t, bool) or not isinstance(t, (int, float))
                or not math.isfinite(t) or t < 0):
            raise _err(path, "limits.timeout_seconds", "must be a number of seconds, 0 or more",
                       "use e.g. 120, or 0 / null for no timeout")
        cfg.limits["timeout_seconds"] = t if t > 0 else None
    if "fail_on" in data:
        if data["fail_on"] not in FAIL_LEVELS:
            raise _err(path, "fail_on", "must be one of %s" % ", ".join(FAIL_LEVELS),
                       'use one of those exact values, e.g. "High"')
        cfg.fail_on = data["fail_on"]
    output = data.get("output", {})
    if not isinstance(output, dict):
        raise _err(path, "output", "must be an object", 'write it like "output": {"verbose": true}')
    _check_keys(path, "output.", output, OUTPUT_KEYS)
    if "verbose" in output:
        if not isinstance(output["verbose"], bool):
            raise _err(path, "output.verbose", "must be true or false",
                       "use a JSON boolean, not a string")
        cfg.verbose = output["verbose"]
    return cfg


def load_config(root: str, explicit: Optional[str] = None,
                profile: Optional[str] = None) -> Config:
    """Load `explicit` (must exist) or `<root>/.design-and-fix.json` (missing means defaults),
    over the built-in `profile` when one is named (here or by the file's `"profile"` key)."""
    path = explicit if explicit else os.path.join(root, CONFIG_NAME)
    if not os.path.isfile(path):
        if explicit:
            raise ConfigError("%s: config file not found; fix: pass an existing file to --config "
                              "or omit the flag" % explicit)
        if profile is None:
            return Config()
        cfg = parse_config({}, "<profile %s>" % profile, profile)
        cfg.path = None
        return cfg
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError("%s: invalid JSON at line %d column %d (%s); fix: correct the syntax "
                          "(no comments or trailing commas)" % (path, exc.lineno, exc.colno, exc.msg))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError("%s: cannot read the file (%s); fix: check the permissions and that it "
                          "is UTF-8 text" % (path, getattr(exc, "strerror", None) or exc))
    except (ValueError, RecursionError) as exc:  # e.g. nesting too deep, an integer too long
        raise ConfigError("%s: unreadable JSON (%s); fix: simplify the file (shallow nesting, "
                          "ordinary numbers)" % (path, type(exc).__name__))
    return parse_config(data, path, profile)


def config_hash(cfg: Config) -> str:
    """Stable 12-hex digest of the effective settings (the file path is not part of it)."""
    canon = {"exclude": cfg.exclude, "include": cfg.include, "rules": cfg.rules,
             "limits": cfg.limits, "fail_on": cfg.fail_on, "verbose": cfg.verbose}
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
