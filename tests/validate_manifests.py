"""Stdlib validator for the plugin manifests (Claude Code, Codex, Cursor)."""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, List, Optional

VERSION = "1.0.1"
OPENAI_KEYS = {"interface", "policy", "dependencies"}
INTERFACE_REQUIRED = [
    "displayName", "shortDescription", "longDescription", "developerName",
    "category", "capabilities", "composerIcon", "logo",
]
INSTALL_VALUES = {"AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE"}


def _load(root: str, rel: str, errors: List[str]) -> Optional[Any]:
    path = os.path.join(root, *rel.split("/"))
    if not os.path.isfile(path):
        errors.append("%s: file is missing" % rel)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError) as exc:
        errors.append("%s: invalid JSON (%s)" % (rel, exc))
        return None


def _check_asset(base: str, rel: Any, where: str, errors: List[str]) -> None:
    if not isinstance(rel, str) or not rel.startswith("./"):
        errors.append("%s: path %r must be a string starting with './'" % (where, rel))
        return
    if not os.path.isfile(os.path.join(base, *rel[2:].split("/"))):
        errors.append("%s: asset %s does not exist" % (where, rel))


def _check_version(data: dict, rel: str, errors: List[str]) -> None:
    if data.get("version") != VERSION:
        errors.append("%s: version is %r, expected %r" % (rel, data.get("version"), VERSION))


def _check_codex(root: str, errors: List[str]) -> None:
    rel = ".codex-plugin/plugin.json"
    data = _load(root, rel, errors)
    if not isinstance(data, dict):
        return
    _check_version(data, rel, errors)
    for key in ("name", "description"):
        if not isinstance(data.get(key), str) or not data[key]:
            errors.append("%s: %s is required" % (rel, key))
    author = data.get("author")
    if not isinstance(author, dict) or not author.get("name"):
        errors.append("%s: author must be an object with a name" % rel)
    skills = data.get("skills")
    ok = isinstance(skills, str) or (
        isinstance(skills, list) and all(isinstance(s, str) for s in skills))
    if not ok:
        errors.append("%s: skills must be a string or a list of strings" % rel)
    else:
        for s in ([skills] if isinstance(skills, str) else skills):
            if not s.startswith("./") or not os.path.isdir(
                    os.path.join(root, *s[2:].split("/"))):
                errors.append("%s: skills path %s must be an existing './' directory" % (rel, s))
    iface = data.get("interface")
    if not isinstance(iface, dict):
        errors.append("%s: interface is required" % rel)
        return
    for key in INTERFACE_REQUIRED:
        if key not in iface:
            errors.append("%s: interface.%s is required" % (rel, key))
    short = iface.get("shortDescription")
    if isinstance(short, str) and len(short) > 30:
        errors.append("%s: interface.shortDescription is %d chars, max 30" % (rel, len(short)))
    if "capabilities" in iface and not (
            isinstance(iface["capabilities"], list)
            and all(isinstance(c, str) for c in iface["capabilities"])):
        errors.append("%s: interface.capabilities must be a list of strings" % rel)
    prompts = iface.get("defaultPrompt")
    if prompts is not None:
        plist = [prompts] if isinstance(prompts, str) else prompts
        if not isinstance(plist, list) or not all(isinstance(p, str) for p in plist):
            errors.append("%s: interface.defaultPrompt must be a string or list of strings" % rel)
        else:
            if len(plist) > 3:
                errors.append("%s: interface.defaultPrompt has %d items, max 3" % (rel, len(plist)))
            for p in plist:
                if len(p) > 128:
                    errors.append("%s: a defaultPrompt is %d chars, max 128" % (rel, len(p)))
    color = iface.get("brandColor")
    if color is not None and not (isinstance(color, str) and re.match(r"^#[0-9A-Fa-f]{6}$", color)):
        errors.append("%s: interface.brandColor must be #RRGGBB" % rel)
    for key in ("composerIcon", "logo"):
        if key in iface:
            _check_asset(root, iface[key], "%s interface.%s" % (rel, key), errors)


def _check_claude(root: str, errors: List[str]) -> None:
    rel = ".claude-plugin/plugin.json"
    data = _load(root, rel, errors)
    if isinstance(data, dict):
        _check_version(data, rel, errors)
        if not data.get("name"):
            errors.append("%s: name is required" % rel)
        if "skills" in data:
            errors.append("%s: must not declare 'skills' (it adds to the default skills/ scan)" % rel)
    rel = ".claude-plugin/marketplace.json"
    data = _load(root, rel, errors)
    if isinstance(data, dict):
        plugins = data.get("plugins")
        if not isinstance(plugins, list) or not plugins:
            errors.append("%s: plugins must be a non-empty list" % rel)
            return
        for entry in plugins:
            if isinstance(entry, dict) and "version" in entry:
                errors.append("%s: marketplace entry %r must not set version "
                              "(plugin.json owns it)" % (rel, entry.get("name")))


def _check_cursor(root: str, errors: List[str]) -> None:
    rel = ".cursor-plugin/plugin.json"
    data = _load(root, rel, errors)
    if not isinstance(data, dict):
        return
    _check_version(data, rel, errors)
    if not data.get("name"):
        errors.append("%s: name is required" % rel)
    if not isinstance(data.get("author"), dict):
        errors.append("%s: author must be an object" % rel)
    if "skills" in data:
        errors.append("%s: must not declare 'skills' (default skills/ is discovered)" % rel)


def _check_codex_marketplace(root: str, errors: List[str]) -> None:
    rel = ".agents/plugins/marketplace.json"
    data = _load(root, rel, errors)
    if not isinstance(data, dict):
        return
    if not data.get("name"):
        errors.append("%s: name is required" % rel)
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append("%s: plugins must be a non-empty list" % rel)
        return
    for entry in plugins:
        if not isinstance(entry, dict):
            errors.append("%s: plugin entry must be an object" % rel)
            continue
        src = entry.get("source")
        if not (isinstance(src, dict) and src.get("source") == "local"
                and isinstance(src.get("path"), str) and src["path"].startswith("./")):
            errors.append("%s: source must be local with a './' path" % rel)
        policy = entry.get("policy")
        if not (isinstance(policy, dict) and policy.get("installation") in INSTALL_VALUES
                and policy.get("authentication")):
            errors.append("%s: policy needs a valid installation and an authentication" % rel)
        if not entry.get("category"):
            errors.append("%s: category is required" % rel)


def _check_openai_yaml(root: str, errors: List[str]) -> None:
    rel = "skills/design-and-fix/agents/openai.yaml"
    path = os.path.join(root, *rel.split("/"))
    if not os.path.isfile(path):
        errors.append("%s: file is missing" % rel)
        return
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for key in re.findall(r"^([A-Za-z_]+):", text, re.MULTILINE):
        if key not in OPENAI_KEYS:
            errors.append("%s: unknown top-level key '%s'" % (rel, key))
    base = os.path.join(root, "skills", "design-and-fix")
    for m in re.finditer(r"^\s+icon_(?:small|large):\s*[\"']?([^\"'\s]+)", text, re.MULTILINE):
        _check_asset(base, m.group(1), rel, errors)


def validate(repo_root: str) -> List[str]:
    errors: List[str] = []
    _check_claude(repo_root, errors)
    _check_codex(repo_root, errors)
    _check_cursor(repo_root, errors)
    _check_codex_marketplace(repo_root, errors)
    _check_openai_yaml(repo_root, errors)
    return errors


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems = validate(root)
    for problem in problems:
        print(problem)
    sys.exit(1 if problems else 0)
