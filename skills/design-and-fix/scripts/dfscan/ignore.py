from __future__ import annotations

import os
import re
from typing import List, Tuple

# POSIX bracket classes ([[:digit:]]) as regex class bodies (ASCII, like git's wildmatch).
_POSIX_CLASSES = {
    "alnum": "a-zA-Z0-9", "alpha": "a-zA-Z", "blank": " \\t", "cntrl": "\\x00-\\x1f\\x7f",
    "digit": "0-9", "graph": "\\x21-\\x7e", "lower": "a-z", "print": "\\x20-\\x7e",
    "punct": re.escape("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"), "space": " \\t\\n\\r\\f\\v",
    "upper": "A-Z", "xdigit": "0-9A-Fa-f",
}


class InvalidPattern(ValueError):
    """A glob git would treat as matching nothing (bad range, unknown [:class:])."""


def _bracket(pat: str, i: int) -> Tuple[str, int]:
    """Translate the bracket expression starting at pat[i] == "[".

    Returns (regex, index of the closing "]"), or ("", -1) when there is no closing bracket (the
    "[" is then a literal). Raises InvalidPattern for a reversed range or an unknown class.
    Brackets never match "/" (paths are matched segment-aware, as in git).
    """
    n = len(pat)
    j = i + 1
    negate = j < n and pat[j] in "!^"
    if negate:
        j += 1
    items: List[str] = []
    first = True
    while j < n:
        c = pat[j]
        if c == "]" and not first:
            body = "".join(items)
            return ("[^/" + body + "]" if negate else "[" + body + "]"), j
        first = False
        if c == "[" and pat.startswith("[:", j):
            k = pat.find(":]", j + 2)
            if k != -1:
                name = pat[j + 2:k]
                if name not in _POSIX_CLASSES:
                    raise InvalidPattern("unknown character class [:%s:]" % name)
                items.append(_POSIX_CLASSES[name])
                j = k + 2
                continue
        if c == "\\" and j + 1 < n:
            j += 1
            c = pat[j]
        lo = c
        if j + 2 < n and pat[j + 1] == "-" and pat[j + 2] != "]":
            hi_idx = j + 2
            if pat[hi_idx] == "\\" and hi_idx + 1 < n:
                hi_idx += 1
            hi = pat[hi_idx]
            if ord(hi) < ord(lo):
                raise InvalidPattern("reversed range %s-%s" % (lo, hi))
            items.append(re.escape(lo) + "-" + re.escape(hi))
            j = hi_idx + 1
            continue
        items.append(re.escape(lo))
        j += 1
    return "", -1


def _translate(pat: str) -> str:
    """One gitignore glob (no leading `/`, no trailing `/`) to a regex body over POSIX paths.

    Raises InvalidPattern for a glob git would never match (callers skip it)."""
    i, n, res = 0, len(pat), []
    while i < n:
        c = pat[i]
        if c == "*":
            j = i
            while j < n and pat[j] == "*":
                j += 1
            at_start = i == 0 or pat[i - 1] == "/"
            if j - i >= 2 and at_start and j < n and pat[j] == "/":
                res.append("(?:.*/)?")  # `**/`: zero or more directories
                i = j + 1
                continue
            if j - i >= 2 and at_start and j == n:
                res.append(".*")  # trailing `/**`: everything inside
                i = j
                continue
            res.append("[^/]*")
            i = j
            continue
        if c == "?":
            res.append("[^/]")
        elif c == "\\" and i + 1 < n:
            i += 1
            res.append(re.escape(pat[i]))
        elif c == "[":
            rx, k = _bracket(pat, i)
            if k == -1:
                res.append(re.escape(c))
            else:
                res.append(rx)
                i = k
        else:
            res.append(re.escape(c))
        i += 1
    return "".join(res)


def compile_glob(pattern: str) -> "re.Pattern":
    """Compile one gitignore-syntax glob (leading `/` anchors, trailing `/` stripped by callers).

    Raises InvalidPattern (a ValueError) when git would treat the glob as matching nothing."""
    anchored = "/" in pattern
    body = _translate(pattern.lstrip("/"))
    try:
        return re.compile("^" + ("" if anchored else "(?:.*/)?") + body + "$")
    except re.error as exc:  # defensive: _translate escapes everything it emits
        raise InvalidPattern(str(exc))


class GitIgnore:
    """Root `.gitignore` only (nested `.gitignore` files are not read in 1.0.2).

    Supports `#` comments, blank lines, `*`, `**`, `?`, `[...]` (ranges, `!`/`^` negation, POSIX
    classes such as `[[:digit:]]`), a leading `/` anchor, a trailing `/` (directories only) and `!`
    negation; the last matching rule wins. A file under an ignored directory is ignored (git
    cannot re-include it). Trailing spaces are dropped unless escaped with `\\`; trailing tabs are
    kept, as in git. A pattern git cannot use (a reversed range such as `[z-a]`, an unknown class)
    matches nothing and is listed in `invalid`; the scan goes on.
    """

    def __init__(self, lines: List[str]) -> None:
        self._rules = []  # type: List[Tuple[re.Pattern, bool, bool]]  # regex, negated, dir_only
        self.invalid = []  # type: List[str]
        for raw in lines:
            line = raw.rstrip("\r\n")
            line = re.sub(r"(?<!\\) +$", "", line)
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            elif line.startswith(("\\!", "\\#")):
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            if not line:
                continue
            try:
                rx = compile_glob(line)
            except InvalidPattern:
                self.invalid.append(raw.rstrip("\r\n"))
                continue
            self._rules.append((rx, negated, dir_only))

    @classmethod
    def from_root(cls, root: str) -> "GitIgnore":
        try:
            with open(os.path.join(root, ".gitignore"), "r", encoding="utf-8",
                      errors="ignore") as fh:
                return cls(fh.read().splitlines())
        except OSError:
            return cls([])

    def _match_one(self, rel: str, is_dir: bool) -> bool:
        ignored = False
        for rx, negated, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
            if rx.match(rel):
                ignored = not negated
        return ignored

    def matches(self, rel: str, is_dir: bool) -> bool:
        rel = rel.replace("\\", "/").strip("/")
        if not rel or not self._rules:
            return False
        parts = rel.split("/")
        for i in range(1, len(parts)):
            if self._match_one("/".join(parts[:i]), True):
                return True
        return self._match_one(rel, is_dir)


def is_comment_only(line: str) -> bool:
    """True when the line holds nothing but a comment (`//`, `#` or `/*` first). Only such a
    line can suppress the line below it with `dfscan:ignore`. A leading UTF-8 BOM is ignored."""
    return line.lstrip().lstrip("﻿").lstrip().startswith(("//", "#", "/*"))
