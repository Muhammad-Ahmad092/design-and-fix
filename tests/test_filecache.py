from __future__ import annotations

import collections
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from helpers import FIXTURES
from dfscan import text as dftext
from dfscan.filecache import FileCache, read_file
from dfscan.model import FileInfo
from dfscan.report import scan
from dfscan.walker import walk

MONO = os.path.join(FIXTURES, "monorepo")


def _canonical(result: dict) -> str:
    result = json.loads(json.dumps(result))
    result["meta"].pop("timestamp", None)
    return json.dumps(result, sort_keys=True, indent=1)


def _file(folder: str, name: str, body: str, lang: str = "Python") -> FileInfo:
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return FileInfo(name, path, os.path.splitext(name)[1], lang, body.count("\n"), len(body), False)


class FileCacheTests(unittest.TestCase):
    def test_strip_called_once_per_file(self):
        """Every comment strip of a whole file in a scan goes through the cache: no file text
        is stripped twice in the same comment style."""
        original = dftext.strip_comments
        calls = collections.Counter()

        def counting(text, lang):
            calls[(text, bool(lang) and lang.startswith(("Python", "Ruby", "Elixir")))] += 1
            return original(text, lang)

        patches = [mock.patch.object(mod, "strip_comments", counting)
                   for name, mod in list(sys.modules.items())
                   if name.startswith("dfscan") and getattr(mod, "strip_comments", None) is original]
        for p in patches:
            p.start()
        try:
            scan(MONO, use_git=False)
        finally:
            for p in patches:
                p.stop()
        code = [f for f in walk(MONO) if f.lang is not None]
        texts = collections.Counter(read_file(f) for f in code)
        whole = {k: n for k, n in calls.items() if k[0] in texts}
        # Every code file the pattern rules read (all but SQL/GraphQL/proto) was stripped: the
        # patch did catch the scan's strips. Files with identical text share one entry.
        strippable = {read_file(f) for f in code
                      if f.lang not in ("SQL", "GraphQL", "Protocol Buffers (gRPC likely)")}
        self.assertTrue(strippable)
        self.assertEqual(strippable - {t for t, _ in whole}, set())
        for (text, _), n in whole.items():
            self.assertLessEqual(n, texts[text], text[:80])

    def test_cache_evicts_by_bytes(self):  # Review Focus 5: bounded memory
        tmp = tempfile.mkdtemp()
        try:
            files = [_file(tmp, "m%d.py" % i, ("x = %d  # c\n" % i) * 400) for i in range(20)]
            one = sys.getsizeof(read_file(files[0]))
            cache = FileCache(max_bytes=one * 3)
            for f in files:
                self.assertEqual(cache.text(f), read_file(f))
                self.assertEqual(cache.code(f), dftext.strip_comments(read_file(f), "Python"))
                self.assertLessEqual(cache.bytes, cache.max_bytes)
            self.assertLessEqual(len(cache._lru), 3)
            # LRU: the most recently used entry survives, the oldest is gone and re-read.
            self.assertIn(("code", files[-1].abs, True), cache._lru)
            self.assertNotIn(("text", files[0].abs), cache._lru)
            self.assertEqual(cache.text(files[0]), read_file(files[0]))
            # A value larger than the whole budget is returned but never stored.
            tiny = FileCache(max_bytes=10)
            self.assertEqual(tiny.text(files[1]), read_file(files[1]))
            self.assertEqual((tiny.bytes, len(tiny._lru)), (0, 0))
        finally:
            for name in os.listdir(tmp):
                os.remove(os.path.join(tmp, name))
            os.rmdir(tmp)

    def test_code_shared_across_same_comment_style(self):
        tmp = tempfile.mkdtemp()
        try:
            f = _file(tmp, "a.ts", "const a = 1; // c\n/* b */\n", "TypeScript")
            cache = FileCache()
            stripped = cache.code(f)
            self.assertIs(cache.code(f, "JavaScript"), stripped)
            self.assertNotEqual(cache.code(f, "Python"), stripped)
            self.assertEqual(stripped, dftext.strip_comments(read_file(f), "TypeScript"))
        finally:
            os.remove(os.path.join(tmp, "a.ts"))
            os.rmdir(tmp)

    def test_scan_output_identical_with_and_without_cache(self):  # Review Focus 5
        cached = _canonical(scan(MONO, use_git=False))
        uncached = _canonical(scan(MONO, use_git=False, file_cache_bytes=0))
        evicting = _canonical(scan(MONO, use_git=False, file_cache_bytes=4096))
        self.assertEqual(cached, uncached)
        self.assertEqual(cached, evicting)


if __name__ == "__main__":
    unittest.main()
