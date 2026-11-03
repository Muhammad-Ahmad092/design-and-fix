from __future__ import annotations

import hashlib
import re
from typing import List, Optional

_STR_RE = re.compile(r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`""")
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_DEF_RES = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"),
    re.compile(r"^\s*(?:(?:public|private|protected|internal|static|final|async|override|"
               r"virtual|abstract)\s+)+[\w<>\[\],.?]+\s+(\w+)\s*\("),
]


def normalise(text: str) -> str:
    text = _STR_RE.sub("S", text.strip())
    text = _NUM_RE.sub("N", text)
    return re.sub(r"\s+", " ", text)


def enclosing_name(lines: List[str], line: int, lang: Optional[str]) -> str:
    """Name of the nearest def/function/method/class at or above `line` (1-based), or ""."""
    start = min(line, len(lines)) - 1
    for i in range(start, -1, -1):
        for rx in _DEF_RES:
            m = rx.match(lines[i])
            if m:
                return m.group(1)
    return ""


def signal_fingerprint(rule: str, rel: str, text: str, line: int, lang: Optional[str],
                       key: Optional[str] = None, occurrence: int = 0) -> str:
    """Line-number-independent id: sha1(rule|rel|enclosing_name|normalised line|n)[:16].

    `text` is the whole file content, split on LF only exactly like the scanner numbers lines
    (a trailing CR is dropped). Line 0 (folder-level signals) uses only rule|rel. An explicit
    `key` replaces the enclosing-name and line-text parts (whole-module findings). `occurrence`
    is the 0-based count of earlier identical keys in the file, so repeated identical lines in
    one function stay distinct."""
    if line <= 0:
        material = "%s|%s" % (rule, rel)
    elif key is not None:
        material = "%s|%s|%s|%d" % (rule, rel, key, occurrence)
    else:
        lines = text.split("\n")
        own = lines[line - 1].rstrip("\r") if line - 1 < len(lines) else ""
        material = "%s|%s|%s|%s|%d" % (rule, rel, enclosing_name(lines, line, lang),
                                       normalise(own), occurrence)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def enclosing_names(lines: List[str]) -> List[str]:
    """enclosing_name for every line at once (index i is line i + 1): one forward pass that keeps
    the last def seen, so a file with many signals is not searched backwards once per signal."""
    out: List[str] = []
    last = ""
    for ln in lines:
        for rx in _DEF_RES:
            m = rx.match(ln)
            if m:
                last = m.group(1)
                break
        out.append(last)
    return out


def line_fingerprint(rule: str, rel: str, lines: List[str], names: List[str], line: int,
                     occurrence: int = 0) -> str:
    """signal_fingerprint for a line signal, from pre-split `lines` and `enclosing_names(lines)`.
    Gives exactly the same id as signal_fingerprint over the LF-joined lines."""
    if line <= 0:
        return signal_fingerprint(rule, rel, "", 0, None)
    if line - 1 < len(lines):
        own = lines[line - 1].rstrip("\r")
        name = names[line - 1]
    else:
        own = ""
        name = names[-1] if names else ""
    material = "%s|%s|%s|%s|%d" % (rule, rel, name, normalise(own), occurrence)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
