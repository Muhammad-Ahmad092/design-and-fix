"""Evaluation corpus tooling (evals/fetch_corpus.py, evals/run_precision.py). No network."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile

from helpers import REPO

EVALS = os.path.join(REPO, "evals")
if EVALS not in sys.path:
    sys.path.insert(0, EVALS)

import fetch_corpus  # noqa: E402
import run_precision  # noqa: E402


def _zip(path, entries):
    """entries: list of (name, data, unix_mode or None)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            zf.writestr(info, data)


def _tar(path, entries):
    """entries: list of (name, kind, data_or_target)."""
    with tarfile.open(path, "w:gz") as tf:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = payload.encode("utf-8")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tf.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = payload
                tf.addfile(info)


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _refuses(self, archive):
        with self.assertRaises(ValueError):
            fetch_corpus.safe_extract(archive, self.dest)
        # Nothing may be written outside dest, and nothing partial inside it either.
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x")))
        self.assertFalse(os.path.exists(self.dest) and os.listdir(self.dest))

    def test_extract_refuses_traversal(self):
        z = os.path.join(self.tmp, "a.zip")
        _zip(z, [("repo-1/ok.py", "x = 1\n", None), ("../x", "pwned", None)])
        self._refuses(z)
        t = os.path.join(self.tmp, "b.tar.gz")
        _tar(t, [("repo-1/", "dir", ""), ("repo-1/link", "sym", "/etc/passwd")])
        self._refuses(t)

    def test_extract_refuses_absolute_hardlink_and_zip_symlink(self):
        z = os.path.join(self.tmp, "abs.zip")
        _zip(z, [("/x", "pwned", None)])
        self._refuses(z)
        z = os.path.join(self.tmp, "drive.zip")
        _zip(z, [("C:/x", "pwned", None)])
        self._refuses(z)
        z = os.path.join(self.tmp, "nested.zip")
        _zip(z, [("repo-1/a/../../x", "pwned", None)])
        self._refuses(z)
        z = os.path.join(self.tmp, "symlink.zip")
        _zip(z, [("repo-1/link", "../x", 0o120777)])
        self._refuses(z)
        t = os.path.join(self.tmp, "hard.tar.gz")
        _tar(t, [("repo-1/f", "file", "a"), ("repo-1/h", "hard", "repo-1/f")])
        self._refuses(t)
        t = os.path.join(self.tmp, "trav.tar.gz")
        _tar(t, [("repo-1/../../x", "file", "pwned")])
        self._refuses(t)

    def test_extract_ok_returns_top_folder(self):
        t = os.path.join(self.tmp, "good.tar.gz")
        _tar(t, [("repo-1/", "dir", ""), ("repo-1/src/a.py", "file", "x = 1\n")])
        root = fetch_corpus.safe_extract(t, self.dest)
        self.assertEqual(os.path.normpath(root), os.path.normpath(os.path.join(self.dest, "repo-1")))
        with open(os.path.join(root, "src", "a.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "x = 1\n")
        z = os.path.join(self.tmp, "good.zip")
        dest2 = os.path.join(self.tmp, "out2")
        _zip(z, [("repo-2/", "", None), ("repo-2/b.js", "let b\n", None)])
        root = fetch_corpus.safe_extract(z, dest2)
        self.assertTrue(os.path.isfile(os.path.join(root, "b.js")))

    def test_skip_links_opt_in_never_creates_links(self):
        t = os.path.join(self.tmp, "links.tar.gz")
        _tar(t, [("repo-1/a.py", "file", "x = 1\n"), ("repo-1/link", "sym", "/etc/passwd"),
                 ("repo-1/hard", "hard", "repo-1/a.py")])
        skipped = []
        root = fetch_corpus.safe_extract(t, self.dest, skip_links=True, skipped=skipped)
        self.assertEqual(sorted(os.listdir(root)), ["a.py"])
        self.assertEqual(skipped, ["repo-1/link", "repo-1/hard"])
        z = os.path.join(self.tmp, "trav.zip")
        _zip(z, [("repo-1/link", "../x", 0o120777), ("../x", "pwned", None)])
        with self.assertRaises(ValueError):
            fetch_corpus.safe_extract(z, os.path.join(self.tmp, "o3"), skip_links=True)

    def test_sha256_verify_and_https_only(self):
        p = os.path.join(self.tmp, "f.bin")
        with open(p, "wb") as fh:
            fh.write(b"abc")
        digest = fetch_corpus.sha256_file(p)
        self.assertEqual(digest, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        fetch_corpus.verify_sha256(p, digest)
        with self.assertRaises(ValueError):
            fetch_corpus.verify_sha256(p, "0" * 64)
        with self.assertRaises(ValueError):
            fetch_corpus.download("http://codeload.github.com/x/y/tar.gz/v1", p)

    def test_download_host_allow_list(self):
        fetch_corpus.check_url("https://codeload.github.com/x/y/tar.gz/v1")
        for bad in ("https://evil.example/x.tar.gz", "https://codeload.github.com.evil.io/x"):
            with self.assertRaises(ValueError):
                fetch_corpus.check_url(bad)
        with self.assertRaises(ValueError):
            fetch_corpus.download("https://evil.example/x.tar.gz", os.path.join(self.tmp, "a"))

    def test_copy_capped(self):
        out = io.BytesIO()
        self.assertEqual(fetch_corpus.copy_capped(io.BytesIO(b"x" * 10), out, 10), 10)
        with self.assertRaises(ValueError):
            fetch_corpus.copy_capped(io.BytesIO(b"x" * 11), io.BytesIO(), 10)

    def test_extract_caps_members_and_size(self):
        t = os.path.join(self.tmp, "big.tar.gz")
        _tar(t, [("repo-1/a.txt", "file", "a" * 100), ("repo-1/b.txt", "file", "b")])
        with self.assertRaises(ValueError):
            fetch_corpus.safe_extract(t, os.path.join(self.tmp, "c1"), max_members=1)
        with self.assertRaises(ValueError):
            fetch_corpus.safe_extract(t, os.path.join(self.tmp, "c2"), max_bytes=50)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "c2")))
        fetch_corpus.safe_extract(t, os.path.join(self.tmp, "c3"), max_bytes=101)

    @unittest.skipUnless(os.name == "nt", "NTFS alternate data streams are Windows-only")
    def test_extract_refuses_colon_on_windows(self):
        t = os.path.join(self.tmp, "ads.tar.gz")
        _tar(t, [("repo-1/a.txt:stream", "file", "x")])
        with self.assertRaises(ValueError):
            fetch_corpus.safe_extract(t, os.path.join(self.tmp, "ads"))

    def test_bad_archive_removed_after_sha_mismatch(self):
        dest = os.path.join(self.tmp, "corpus")
        archive = os.path.join(dest, "archives", "r.tar.gz")
        os.makedirs(os.path.dirname(archive))
        with open(archive, "wb") as fh:
            fh.write(b"truncated")
        entry = {"name": "r", "url": "https://codeload.github.com/o/r/tar.gz/v1",
                 "sha256": "0" * 64}
        with self.assertRaises(ValueError):
            fetch_corpus.fetch_one(entry, dest, record=False)
        self.assertFalse(os.path.exists(archive))


def _sig(rule, file, line):
    return {"rule": rule, "file": file, "line": line, "snippet": "s%d" % line,
            "fingerprint": "%s:%s:%d" % (rule, file, line)}


class SampleTests(unittest.TestCase):
    def _fake(self):
        return {
            "big": [_sig("r1", "a.py", i) for i in range(1, 51)] + [_sig("r2", "b.py", 1)],
            "mid": [_sig("r1", "m.py", i) for i in range(1, 6)],
            "small": [_sig("r1", "s.py", 1)],
            "none": [],
        }

    def test_sample_stratified_and_seeded(self):
        a = run_precision.sample_signals(self._fake(), per_rule=20, seed=1002)
        b = run_precision.sample_signals(self._fake(), per_rule=20, seed=1002)
        self.assertEqual(a, b)
        self.assertEqual(set(a), {"r1", "r2"})
        self.assertEqual(len(a["r1"]), 20)
        self.assertEqual(len(a["r2"]), 1)
        repos = [e["repo"] for e in a["r1"]]
        # Every repo with r1 signals is represented, small ones fully.
        self.assertEqual(repos.count("small"), 1)
        self.assertEqual(repos.count("mid"), 5)
        self.assertEqual(repos.count("big"), 14)
        for e in a["r1"]:
            self.assertEqual(set(e), {"repo", "file", "line", "snippet", "fingerprint", "label", "note"})
            self.assertEqual((e["label"], e["note"]), ("", ""))
        self.assertEqual(len({(e["repo"], e["fingerprint"]) for e in a["r1"]}), 20)
        c = run_precision.sample_signals(self._fake(), per_rule=20, seed=7)
        self.assertNotEqual([e["line"] for e in a["r1"] if e["repo"] == "big"],
                            [e["line"] for e in c["r1"] if e["repo"] == "big"])

    def test_rule_counts_include_capped_signals(self):
        result = {"signals": [_sig("r1", "a.py", 1), _sig("r1", "a.py", 2), _sig("r2", "b.py", 1)],
                  "summary": {"truncated": {"r2": 250}}}
        self.assertEqual(run_precision.rule_counts(result), {"r1": 2, "r2": 250})


class ReportMathTests(unittest.TestCase):
    def test_report_math(self):
        labels = [
            {"repo": "x", "label": "tp"}, {"repo": "x", "label": "tp"},
            {"repo": "y", "label": "tp"}, {"repo": "y", "label": "fp"},
            {"repo": "y", "label": "unsure"}, {"repo": "x", "label": ""},
        ]
        st = run_precision.rule_stats(labels)
        self.assertEqual((st["n"], st["tp"], st["fp"], st["unsure"], st["unlabelled"]), (6, 3, 1, 1, 1))
        self.assertAlmostEqual(st["precision"], 0.75)
        self.assertIsNone(run_precision.rule_stats([{"repo": "x", "label": "unsure"}])["precision"])
        with self.assertRaises(ValueError):
            run_precision.rule_stats([{"repo": "x", "label": "maybe"}])

        stats = {"r1": st, "r2": run_precision.rule_stats([{"repo": "x", "label": "fp"}]),
                 "r3": run_precision.rule_stats([])}
        pop = {"x": {"r1": 8, "r2": 3, "r3": 4}, "y": {"r1": 4}}
        est = run_precision.fp_estimates(stats, pop)
        # x: 8 * 0.25 + 3 * 1.0 = 5.0, r3 has no labelled evidence (4 signals unestimated).
        self.assertAlmostEqual(est["x"]["est_fp"], 5.0)
        self.assertEqual(est["x"]["signals"], 15)
        self.assertEqual(est["x"]["unestimated"], 4)
        self.assertAlmostEqual(est["y"]["est_fp"], 1.0)
        lo, hi = run_precision.wilson(3, 4)
        self.assertAlmostEqual(lo, 0.3006, places=3)
        self.assertAlmostEqual(hi, 0.9544, places=3)

    def test_default_tag_and_appended_notes(self):
        self.assertEqual(run_precision.default_tag("evals/precision-baseline.md", "x"), "baseline")
        self.assertEqual(run_precision.default_tag("evals/precision.md",
                                                   os.path.join("evals", "labels", "after")), "after")
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "notes.md")
            with open(notes, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n## Baseline vs after\n\nhand-written\n\n")
            text = run_precision.append_notes("# Report\n\nbody\n", notes)
            self.assertEqual(text, "# Report\n\nbody\n\n## Baseline vs after\n\nhand-written\n")

    def test_report_states_sample_weighting(self):
        import argparse
        args = argparse.Namespace(tag="after", labels_rel="evals/labels/after",
                                  out_rel="evals/precision.md")
        pop_doc = {"repos": {"x": {"signals": {"god-module": 2}}}, "per_rule": 20, "seed": 1}
        recall = {"found": 1, "total": 1, "false_hits": [], "clean_signals": 0, "missed": []}
        labels = {"god-module": [{"repo": "x", "label": "fp"}]}
        text = run_precision.render_report(labels, pop_doc, recall, None, {"repos": []}, args)
        self.assertIn("# Precision study (after)", text)
        self.assertIn("equal weight per sampled signal", text)
        self.assertIn("at most 200 per rule per repo", text)

    def test_recall_on_fixture(self):
        r = run_precision.fixture_recall()
        self.assertEqual(r["found"], r["total"])
        self.assertEqual(r["false_hits"], [])
        self.assertEqual(r["clean_signals"], 0)


class CorpusJsonTests(unittest.TestCase):
    def test_corpus_json_shape(self):
        with open(os.path.join(EVALS, "corpus.json"), encoding="utf-8") as fh:
            corpus = json.load(fh)
        repos = corpus["repos"]
        self.assertGreaterEqual(len(repos), 8)
        names = [r["name"] for r in repos]
        self.assertEqual(len(names), len(set(names)))
        for r in repos:
            for key in ("name", "url", "sha256", "subdir", "languages", "kind", "framework", "note"):
                self.assertIn(key, r, r.get("name"))
            self.assertRegex(r["name"], r"^[a-z0-9][a-z0-9_.-]*$")
            self.assertTrue(r["url"].startswith("https://codeload.github.com/"), r["url"])
            self.assertRegex(r["url"], r"/(?:tar\.gz|zip)/(?:refs/tags/[^/]+|[0-9a-f]{40})$")
            self.assertRegex(r["sha256"], r"^(?:[0-9a-f]{64})?$")
            self.assertIn(r["kind"], ("service", "library", "monorepo", "large"))
            self.assertIsInstance(r["languages"], list)
            self.assertFalse(r["subdir"].startswith(("/", "\\")) or ".." in r["subdir"].split("/"))
        self.assertTrue(any(r["url"].split("/")[3:5] == ["pallets", "flask"] for r in repos))
        self.assertTrue(any(r["kind"] == "large" for r in repos))
        langs = {lang for r in repos for lang in r["languages"]}
        self.assertTrue({"Python", "TypeScript", "Go"} <= langs)

    def test_default_dest_outside_repo(self):
        dest = os.path.normcase(os.path.abspath(fetch_corpus.DEFAULT_DEST))
        repo = os.path.normcase(os.path.abspath(REPO))
        self.assertFalse(dest == repo or dest.startswith(repo + os.sep))
        self.assertEqual(os.path.basename(dest), "design-and-fix-corpus")
        self.assertEqual(os.path.normcase(os.path.abspath(run_precision.DEFAULT_CORPUS)), dest)
        # fetch_corpus is the only evals tool that touches the network.
        with open(os.path.join(EVALS, "run_precision.py"), encoding="utf-8") as fh:
            self.assertIsNone(re.search(r"urllib|http\.client|socket", fh.read()))


class RuleDocsTests(unittest.TestCase):
    def test_rule_docs_in_sync(self):
        scripts = os.path.join(REPO, "skills", "design-and-fix", "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import gen_rule_docs
        with open(gen_rule_docs.OUT_PATH, encoding="utf-8", newline="") as fh:
            current = fh.read()
        self.assertEqual(current, gen_rule_docs.render(),
                         "docs/rules.md is stale; run skills/design-and-fix/scripts/gen_rule_docs.py")
        self.assertNotIn(chr(13), current)
        self.assertEqual(gen_rule_docs.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
