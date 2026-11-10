from __future__ import annotations

import sys
from collections import OrderedDict
from typing import Callable, Dict, Hashable, Optional, Tuple

from . import text as _text
from .model import FileInfo

DEFAULT_MAX_BYTES = 64 * 1024 * 1024


def read_file(fi: FileInfo) -> str:
    """Whole file as UTF-8 text (undecodable bytes dropped), without a leading BOM; "" when
    unreadable. A UTF-8 BOM is not text: dropping it keeps line numbers and lets line 1 hold a
    comment."""
    try:
        with open(fi.abs, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return ""
    return text[1:] if text.startswith("\ufeff") else text


def _size(value: object) -> int:
    """Approximate memory held by a cached value. Only a str, or a list/tuple of str, is
    counted fully, so `derived` values must be one of those (other objects count only their
    shallow size and would escape the budget)."""
    n = sys.getsizeof(value)
    if isinstance(value, (list, tuple)):
        n += sum(sys.getsizeof(v) for v in value if isinstance(v, str))
    return n


def _hash_style(lang: str) -> bool:
    """strip_comments only distinguishes # languages from // languages."""
    return bool(lang) and lang.startswith(("Python", "Ruby", "Elixir"))


class FileCache:
    """Per-scan cache of file texts and values derived from them, so each file is read and
    comment-stripped once per scan.

    Memory is bounded: entries are evicted least-recently-used once their total size passes
    `max_bytes` (a value larger than the whole budget is returned without being stored). An
    evicted entry is simply recomputed on its next use, so the scan output never depends on
    the budget. Small per-key facts (booleans, short tuples) go to `memo`, which is not
    evicted. Files are assumed not to change during the scan.
    """

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.max_bytes = max_bytes
        self.bytes = 0
        self._lru: "OrderedDict[Tuple[Hashable, ...], Tuple[object, int]]" = OrderedDict()
        self._memo: Dict[Hashable, object] = {}

    def _get(self, key: Tuple[Hashable, ...], build: Callable[[], object]) -> object:
        hit = self._lru.get(key)
        if hit is not None:
            self._lru.move_to_end(key)
            return hit[0]
        value = build()
        size = _size(value)
        if size <= self.max_bytes:
            self._lru[key] = (value, size)
            self.bytes += size
            while self.bytes > self.max_bytes:
                _, (_, old) = self._lru.popitem(last=False)
                self.bytes -= old
        return value

    def text(self, fi: FileInfo) -> str:
        """The file's text, as `read_file` returns it."""
        return self._get(("text", fi.abs), lambda: read_file(fi))  # type: ignore[return-value]

    def code(self, fi: FileInfo, lang: Optional[str] = None) -> str:
        """`text.strip_comments` of the file's text in `lang` (default: the file's language)."""
        lang = lang if lang is not None else (fi.lang or "")
        return self._get(("code", fi.abs, _hash_style(lang)),  # type: ignore[return-value]
                         lambda: _text.strip_comments(self.text(fi), lang))

    def derived(self, fi: FileInfo, name: str, build: Callable[[], object]) -> object:
        """A value computed from the file by `build` (named `name`), cached like the text."""
        return self._get(("derived", fi.abs, name), build)

    def memo(self, key: Hashable, build: Callable[[], object]) -> object:
        """A small fact kept for the whole scan (never evicted)."""
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]


def text_of(fi: FileInfo, cache: Optional[FileCache]) -> str:
    """The file's text through `cache` when given, else read directly."""
    return cache.text(fi) if cache is not None else read_file(fi)


def code_of(fi: FileInfo, cache: Optional[FileCache], lang: Optional[str] = None) -> str:
    """Comment-stripped text through `cache` when given, else computed directly."""
    if cache is not None:
        return cache.code(fi, lang)
    return _text.strip_comments(read_file(fi), lang if lang is not None else (fi.lang or ""))
