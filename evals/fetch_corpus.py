#!/usr/bin/env python3
"""
fetch_corpus.py - download the precision-study corpus (dev tool; the only evals tool that uses the network).

Downloads each repo in evals/corpus.json as a GitHub codeload archive over HTTPS (no git),
verifies its SHA-256 and extracts it, refusing absolute paths, `..`, symlinks and hardlinks.
A repo whose archive contains links needs `"skip_links": true` in corpus.json (links are then
skipped, never created).

Usage:
    python evals/fetch_corpus.py [--dest DIR] [--only NAME]... [--record-sha]

Layout under DEST (default: ../design-and-fix-corpus/ next to the repo, never inside it):
    DEST/archives/<name>.<ext>   downloaded archive
    DEST/<name>/<top folder>/    extracted repo (scan root)

--record-sha writes the SHA-256 of archives whose `sha256` is still empty into corpus.json.
A recorded SHA-256 that does not match always fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from typing import List, Optional, Tuple
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CORPUS_JSON = os.path.join(HERE, "corpus.json")
DEFAULT_DEST = os.path.normpath(os.path.join(REPO, "..", "design-and-fix-corpus"))
CHUNK = 1 << 16
TIMEOUT_SECONDS = 120
# Caps against a hostile mirror or redirect filling the disk (the SHA-256 is only
# checked after the download). The largest corpus archive is far below these.
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 200000
ALLOWED_HOSTS = ("codeload.github.com", "github.com")


def load_corpus(path: str = CORPUS_JSON) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_corpus(corpus: dict, path: str = CORPUS_JSON) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(corpus, fh, indent=2)
        fh.write("\n")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def verify_sha256(path: str, expected: str) -> None:
    got = sha256_file(path)
    if got != expected:
        raise ValueError("sha256 mismatch for %s: expected %s, got %s" % (path, expected, got))


def check_url(url: str, what: str = "URL") -> None:
    """Raise ValueError unless url is HTTPS on an allowed GitHub host."""
    if not url.startswith("https://"):
        raise ValueError("refusing non-HTTPS %s: %s" % (what, url))
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError("refusing %s on host %r (allowed: %s)"
                         % (what, host, ", ".join(ALLOWED_HOSTS)))


def copy_capped(src, out, limit: int) -> int:
    """Copy src to out in chunks; raise ValueError once more than limit bytes arrive."""
    total = 0
    while True:
        block = src.read(CHUNK)
        if not block:
            return total
        total += len(block)
        if total > limit:
            raise ValueError("data exceeds the %d-byte cap" % limit)
        out.write(block)


def download(url: str, path: str, max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
    """Download url (HTTPS, GitHub hosts only, at most max_bytes) to path atomically.

    A partial `.part` file is removed on any failure."""
    check_url(url)
    tmp = path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "design-and-fix-corpus/1.0.2"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp, open(tmp, "wb") as out:
            check_url(resp.geturl(), "download redirect")
            copy_capped(resp, out, max_bytes)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def archive_ext(url: str) -> str:
    return "zip" if "/zip/" in url else "tar.gz"


# ---------------------------------------------------------------- safe extraction
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _check_name(name: str) -> str:
    """Return the member name as a relative POSIX path, or raise ValueError."""
    norm = name.replace("\\", "/")
    if not norm or norm.startswith("/") or _DRIVE_RE.match(norm):
        raise ValueError("archive entry with absolute path: %r" % name)
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValueError("archive entry escapes the target folder: %r" % name)
    if os.name == "nt" and ":" in norm:
        # Would write an NTFS alternate data stream.
        raise ValueError("archive entry name contains ':': %r" % name)
    return "/".join(parts)


def _check_totals(count: int, size: int, max_members: int, max_bytes: int) -> None:
    if count > max_members:
        raise ValueError("archive has more than %d entries" % max_members)
    if size > max_bytes:
        raise ValueError("archive expands to more than %d bytes" % max_bytes)


def _members(archive_path: str, skip_links: bool, max_members: int = MAX_MEMBERS,
             max_bytes: int = MAX_EXTRACT_BYTES) -> Tuple[str, List[Tuple[str, bool]], List[str]]:
    """Validate every entry first. Returns (kind, [(relative name, is_dir)], skipped link names).

    Link entries raise ValueError unless skip_links, in which case they are left out (never
    created) after their names pass the same path checks. The entry count and the declared
    uncompressed size are capped (max_members, max_bytes)."""
    out: List[Tuple[str, bool]] = []
    links: List[str] = []
    size = 0

    def link(name: str) -> None:
        _check_name(name)
        if not skip_links:
            raise ValueError("archive entry is a link: %r" % name)
        links.append(name)

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                if stat.S_ISLNK(info.external_attr >> 16):
                    link(info.filename)
                else:
                    out.append((_check_name(info.filename), info.is_dir()))
                    size += info.file_size
                _check_totals(len(out) + len(links), size, max_members, max_bytes)
        return "zip", out, links
    with tarfile.open(archive_path, "r:*") as tf:
        for m in tf.getmembers():
            if m.issym() or m.islnk():
                link(m.name)
            elif m.isfile() or m.isdir():
                out.append((_check_name(m.name), m.isdir()))
                size += m.size if m.isfile() else 0
            else:
                raise ValueError("archive entry is not a regular file or folder: %r" % m.name)
            _check_totals(len(out) + len(links), size, max_members, max_bytes)
    return "tar", out, links


def _target(dest: str, rel: str) -> str:
    target = os.path.abspath(os.path.join(dest, *rel.split("/")))
    if os.path.commonpath([dest, target]) != dest:
        raise ValueError("archive entry escapes the target folder: %r" % rel)
    return target


def safe_extract(archive_path: str, dest: str, skip_links: bool = False,
                 skipped: Optional[List[str]] = None, max_members: int = MAX_MEMBERS,
                 max_bytes: int = MAX_EXTRACT_BYTES) -> str:
    """Extract a .zip or .tar[.gz] archive into dest after validating every entry.

    Refuses (ValueError, nothing written) absolute paths, drive letters, `..` components,
    symlinks, hardlinks, device/fifo entries, and archives over the entry-count or size caps
    (bytes actually written are capped too). With skip_links=True (explicit per-repo opt-in
    in corpus.json) link entries are skipped instead of refused, and their names are appended
    to `skipped`; links are never created either way. Returns the single top-level folder of
    the archive inside dest, or dest itself when the archive has several top-level entries.
    """
    dest = os.path.abspath(dest)
    kind, members, links = _members(archive_path, skip_links, max_members, max_bytes)
    budget = [max_bytes]

    def copy(src, out) -> None:
        budget[0] -= copy_capped(src, out, budget[0])
    if skipped is not None:
        skipped.extend(links)
    for rel, _ in members:
        if rel:
            _target(dest, rel)
    os.makedirs(dest, exist_ok=True)
    if kind == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                rel = _check_name(info.filename)
                if not rel or stat.S_ISLNK(info.external_attr >> 16):
                    continue
                target = _target(dest, rel)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    copy(src, out)
    else:
        with tarfile.open(archive_path, "r:*") as tf:
            for m in tf.getmembers():
                rel = _check_name(m.name)
                if not rel or m.issym() or m.islnk():
                    continue
                target = _target(dest, rel)
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    raise ValueError("cannot read archive entry: %r" % m.name)
                with src, open(target, "wb") as out:
                    copy(src, out)
    tops = {rel.split("/")[0] for rel, _ in members if rel}
    if len(tops) == 1:
        top = tops.pop()
        if os.path.isdir(os.path.join(dest, top)):
            return os.path.join(dest, top)
    return dest


def repo_root(dest: str, name: str) -> str:
    """Scan root of an extracted corpus repo: DEST/<name>/<single top folder>."""
    base = os.path.join(dest, name)
    entries = [e for e in os.listdir(base) if os.path.isdir(os.path.join(base, e))]
    return os.path.join(base, entries[0]) if len(entries) == 1 else base


# ---------------------------------------------------------------- driver
def fetch_one(entry: dict, dest: str, record: bool) -> Optional[str]:
    """Download, verify and extract one corpus entry. Returns a newly recorded sha256 or None."""
    name = entry["name"]
    archives = os.path.join(dest, "archives")
    os.makedirs(archives, exist_ok=True)
    archive = os.path.join(archives, "%s.%s" % (name, archive_ext(entry["url"])))
    if not os.path.isfile(archive):
        print("download %s <- %s" % (name, entry["url"]))
        download(entry["url"], archive)
    recorded = None
    if entry.get("sha256"):
        try:
            verify_sha256(archive, entry["sha256"])
        except ValueError:
            # Remove the bad archive so the next run downloads it again.
            os.remove(archive)
            raise
        digest = entry["sha256"]
    elif record:
        digest = recorded = sha256_file(archive)
    else:
        raise ValueError("%s: no sha256 in corpus.json; run once with --record-sha" % name)
    marker = os.path.join(dest, name + ".extracted")
    out_dir = os.path.join(dest, name)
    done = False
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as fh:
            done = fh.read().strip() == digest
    if not done:
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        skipped: List[str] = []
        root = safe_extract(archive, out_dir, bool(entry.get("skip_links")), skipped)
        if skipped:
            print("%s: skipped %d link entries (skip_links): %s" % (
                name, len(skipped), ", ".join(skipped[:5]) + (" ..." if len(skipped) > 5 else "")))
        with open(marker, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(digest + "\n")
        print("extracted %s -> %s" % (name, root))
    return recorded


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default=DEFAULT_DEST, help="Corpus folder (outside the repo)")
    parser.add_argument("--only", action="append", default=[], help="Fetch only this repo (repeatable)")
    parser.add_argument("--record-sha", action="store_true",
                        help="Write missing sha256 values into corpus.json")
    args = parser.parse_args(argv)
    dest = os.path.abspath(args.dest)
    repo = os.path.abspath(REPO)
    if os.path.normcase(dest) == os.path.normcase(repo) or \
            os.path.normcase(dest).startswith(os.path.normcase(repo) + os.sep):
        print("error: --dest must be outside the plugin repo", file=sys.stderr)
        return 2
    corpus = load_corpus()
    names = {e["name"] for e in corpus["repos"]}
    unknown = [n for n in args.only if n not in names]
    if unknown:
        print("error: unknown repo(s): %s" % ", ".join(unknown), file=sys.stderr)
        return 2
    changed = False
    try:
        for entry in corpus["repos"]:
            if args.only and entry["name"] not in args.only:
                continue
            digest = fetch_one(entry, dest, args.record_sha)
            if digest:
                entry["sha256"] = digest
                changed = True
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    finally:
        if changed:
            save_corpus(corpus)
    return 0


if __name__ == "__main__":
    sys.exit(main())
