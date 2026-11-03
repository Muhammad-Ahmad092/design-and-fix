#!/usr/bin/env python3
"""Lint generated design-and-fix Markdown output against references/formatting-rules.md
and the health-score rubric.

Usage: lint_output.py <dir-or-file> [--repo ROOT] [--format text|json] [--include-history]
Exit codes: 0 clean, 1 findings (printed as `file:line: message`), 2 usage error.

Folders named `history` (archived runs, references/archive-and-diff.md) are skipped unless
--include-history is given: their Evidence points at code that has since changed.

Checks that cover 1.0.2-only fields (Status, Source, Verify, detail lines, RC-n, Audit line,
ADR Alternatives and Assumptions, Weight column requirement) apply only to files whose header
block contains `> Format: 1.0.2`. Audit files (01/02/03 of Mode 2, quick-audit.md) outside an
archive folder must carry that line. 1.0.1-format files (archived runs) are checked against the
older rules only, including the `1.0.1/<file>` header rows of formatting-rules.md. ADRs without
`> Format: 1.0.2` (written by hand or by another tool) get only the supersede pairing check.
Issues with Status `not-reproducible` are kept for one run but excluded from the score, the
roadmap, the Verify/detail requirement and the --repo Evidence checks.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import rubric  # noqa: E402

RULES_PATH = os.path.join(SCRIPT_DIR, "..", "references", "formatting-rules.md")
ADR_KEY = "NNNN-<kebab-title>.md"
RERUN_ONLY = " (re-run only)"
LEGACY_PREFIX = "1.0.1/"  # formatting-rules.md rows for files without `> Format: 1.0.2`
PREFIX_MATCH_HEADERS = ("Architecture Health Score",)

SEVERITIES = ("Critical", "High", "Medium", "Low")
EFFORTS = ("S", "M", "L", "XL")
IMPACTS = ("Low", "Medium", "High")
STATUSES = ("confirmed", "likely", "needs-review", "not-reproducible")
HORIZONS = (("Quick Wins", ("S",)), ("Medium-term", ("M",)), ("Long-term", ("L", "XL")))
MERMAID_HEADERS = ("flowchart", "graph", "sequenceDiagram", "erDiagram", "classDiagram",
                   "stateDiagram", "stateDiagram-v2", "journey", "gantt", "timeline", "mindmap",
                   "pie", "gitGraph")
RETIRED = "not-reproducible"   # Status kept for one run; not scored, not on the roadmap
EMPTY_VERIFY = ("", "—", "–", "-", "n/a", "none", "tbd")
AUDIT_KINDS = ("01-architecture-issues.md", "02-api-optimization.md",
               "03-improvement-roadmap.md", "quick-audit.md")
HISTORY_DIR = "history"
# An archived run: .../history/<YYYY-MM-DD>[-N]/ (matched on a "/"-separated path).
ARCHIVE_RE = re.compile(r"(?:^|/)history/\d{4}-\d{2}-\d{2}(?:-\d+)?(?:/|$)")
MAX_NODES = 12

ID_RE = re.compile(r"\b(?:SYS|API|SCL|REL|SEC|OBS|MNT)-\d{3}\b")
ID_ANY_RE = re.compile(r"\b[A-Z]{3}-\d+\b")
ID_FULL_RE = re.compile(r"^(?:SYS|API|SCL|REL|SEC|OBS|MNT)-\d{3}$")
# Evidence is `path:line[-line]`; any path characters are allowed (Next.js `[id]`, route groups
# `(auth)`, spaces, non-ASCII) and --repo decides whether the file exists.
EVIDENCE_RE = re.compile(r"^(\S(?:.*\S)?):(\d+)(?:-(\d+))?$")
# Absence evidence (no tests, no health route, no lockfile) cites the folder or file inspected.
ABSENCE_RE = re.compile(r"^(?=[^:]*[/.])(\S[^:]*?)/?$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
AUDIT_LINE_RE = re.compile(r"^> Audit: plugin [^\s·]+ · (?:scanner schema [^\s·]+|scanner not run)"
                           r" · rev [^\s·]+(?: · .+)?$")
_RULES = r"[\w.-]+(?:,\s*[\w.-]+)*"  # one or more scanner rule ids
SOURCE_RE = re.compile(r"^(?:agent|scanner:%s|scanner\+agent(?::%s)?)$" % (_RULES, _RULES))
HEADLINE_RE = re.compile(
    r"(Healthy|Stable with risks|Fragile|Unsafe) \((\d+(?:\.\d)?) / 10\)")
RC_RE = re.compile(r"\bRC-\d+\b")
RC_DEF_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\|\s*)?\**(RC-\d+)\**\s*(?:[:|]|—|–|- )")
FORMAT_102_RE = re.compile(r"^>\s*Format:\s*1\.0\.2\s*$")
ADR_NAME_RE = re.compile(r"^(\d{4})-.+\.md$")
SUPERSEDES_RE = re.compile(r"^Supersedes:?\s+(\d{4})\s*$")
SUPERSEDED_BY_RE = re.compile(r"^Status: Superseded by (\d{4})\s*$")
ARROW_RE = re.compile(r"<?-{2,}>?|<?={2,}>?|-\.+->?|~{3,}|&")
FLOW_SKIP = ("subgraph", "end", "direction", "style", "classDef", "class", "linkStyle",
             "click", "flowchart", "graph")


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    message: str


# --- Markdown parsing --------------------------------------------------------

@dataclass
class Table:
    header: List[str]
    header_line: int  # 1-based
    rows: List[Tuple[int, List[str]]]


class Doc:
    def __init__(self, path: str, rel: str, text: str):
        self.path = path
        self.rel = rel
        self.lines = text.splitlines()
        self.in_fence = []  # type: List[bool]
        fence = False
        for line in self.lines:
            if line.lstrip().startswith("```"):
                self.in_fence.append(True)
                fence = not fence
            else:
                self.in_fence.append(fence)
        self.sections = self._sections()
        first = self.sections[0][1] if self.sections else len(self.lines)
        self.header_block = self.lines[:first]
        self.is_102 = any(FORMAT_102_RE.match(ln.strip()) for ln in self.header_block)
        self.archived = bool(ARCHIVE_RE.search(os.path.abspath(path).replace(os.sep, "/")))

    def _sections(self) -> List[Tuple[str, int, int]]:
        starts = [(self.lines[i][3:].strip(), i) for i in range(len(self.lines))
                  if not self.in_fence[i] and self.lines[i].startswith("## ")]
        out = []
        for n, (title, i) in enumerate(starts):
            end = starts[n + 1][1] if n + 1 < len(starts) else len(self.lines)
            out.append((title, i, end))
        return out

    def headings(self) -> List[str]:
        return [s[0] for s in self.sections]

    def section(self, prefix: str, exact: bool = False) -> Optional[Tuple[str, int, int]]:
        for sec in self.sections:
            if (sec[0] == prefix) if exact else sec[0].startswith(prefix):
                return sec
        return None

    def tables(self, lo: int = 0, hi: Optional[int] = None) -> List[Table]:
        hi = len(self.lines) if hi is None else hi
        out = []
        block = []  # type: List[Tuple[int, str]]
        for i in range(lo, hi + 1):
            line = self.lines[i] if i < hi else ""
            if i < hi and not self.in_fence[i] and line.startswith("|"):
                block.append((i, line))
                continue
            if len(block) >= 2:
                rows = [(n, [c.strip() for c in ln.strip().strip("|").split("|")])
                        for n, ln in block]
                out.append(Table(rows[0][1], rows[0][0] + 1, [(n + 1, c) for n, c in rows[2:]]))
            block = []
        return out

    def section_tables(self, prefix: str, exact: bool = False) -> List[Table]:
        sec = self.section(prefix, exact)
        return self.tables(sec[1] + 1, sec[2]) if sec else []

    def context_line(self) -> str:
        for ln in self.header_block:
            if ln.startswith("> Context:"):
                return ln[len("> Context:"):].strip()
        return ""


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


# --- Evidence (shared with compare_audits.py) ---------------------------------

def split_evidence(cell: str) -> List[str]:
    """Tokens of an Evidence cell: backticks dropped, split on `, `, `;` and `<br>`."""
    parts = re.split(r",\s*|<br\s*/?>|;\s*", cell.replace("`", ""))
    return [p.strip() for p in parts if p.strip()]


def parse_evidence(cell: str) -> List[Tuple[str, int, int, str]]:
    """Parsed Evidence entries (path, first, last, token). A bare folder/file path (absence
    evidence) has first = last = 0. A bare line number (`a.py:3, 9-11`) continues the previous
    path. Tokens that are neither (prose, `Not verified ...`) are left out."""
    return parse_evidence_full(cell)[0]


def parse_evidence_full(cell: str) -> Tuple[List[Tuple[str, int, int, str]], List[str]]:
    """(parse_evidence entries, rejected tokens); `Not verified ...` is neither."""
    out = []  # type: List[Tuple[str, int, int, str]]
    rejected = []  # type: List[str]
    last_path = None  # type: Optional[str]
    for tok in split_evidence(cell):
        if tok.startswith("Not verified"):
            last_path = None
            continue
        if last_path and re.match(r"^\d+(?:-\d+)?$", tok):
            tok = "%s:%s" % (last_path, tok)
        m = EVIDENCE_RE.match(tok)
        if m and not CONTROL_RE.search(m.group(1)):
            first = int(m.group(2))
            out.append((m.group(1), first, int(m.group(3)) if m.group(3) else first, tok))
            last_path = m.group(1)
            continue
        last_path = None
        bare = ABSENCE_RE.match(tok)
        if bare and not CONTROL_RE.search(bare.group(1)):
            out.append((bare.group(1), 0, 0, tok))
        else:
            rejected.append(tok)
    return out, rejected


def evidence_path_problem(path: str) -> Optional[str]:
    """Why an Evidence path is not a root-relative path, or None when it is fine."""
    if path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        return "is absolute"
    if ".." in re.split(r"[\\/]", path):
        return "climbs out with '..'"
    return None


# --- Rules file --------------------------------------------------------------

REQUIRED_RE = re.compile(r"^- Required in `([^`]+\.md)`: (.+)$")


def load_required() -> Dict[str, List[str]]:
    """Headers that may never be omitted, from the `- Required in `<file>`: A, B` lines of
    formatting-rules.md (Required Headers)."""
    out = {}  # type: Dict[str, List[str]]
    for line in read_text(RULES_PATH).splitlines():
        m = REQUIRED_RE.match(line.strip())
        if m:
            out[m.group(1)] = [h.strip() for h in m.group(2).split(",") if h.strip()]
    return out


def load_headers() -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """(expected headers per file, re-run-only headers per file) from formatting-rules.md."""
    row_re = re.compile(r"^\|\s*`([^`]+\.md)`\s*\|\s*([^|]+?)\s*\|\s*$")
    expected = {}  # type: Dict[str, List[str]]
    optional = {}  # type: Dict[str, List[str]]
    for line in read_text(RULES_PATH).splitlines():
        m = row_re.match(line.strip())
        if not m:
            continue
        heads = [h.strip() for h in m.group(2).split(",") if h.strip()]
        expected[m.group(1)] = [h[:-len(RERUN_ONLY)] if h.endswith(RERUN_ONLY) else h
                                for h in heads]
        optional[m.group(1)] = [h[:-len(RERUN_ONLY)] for h in heads if h.endswith(RERUN_ONLY)]
    return expected, optional


def header_mismatch(actual: List[str], expected: List[str],
                    optional: Iterable[str] = (), allow_missing: bool = False) -> Optional[str]:
    """None when `actual` fits `expected`. Strict mode: every non-optional header present, in
    order. allow_missing: headers may be omitted (sections that do not apply), but the ones
    present must be standard headers in the standard order."""
    def same(a: str, e: str) -> bool:
        return a.startswith(e) if e in PREFIX_MATCH_HEADERS else a == e

    if allow_missing:
        last = -1
        for a in actual:
            pos = next((k for k, e in enumerate(expected) if same(a, e)), None)
            if pos is None:
                return "unknown header %r (standard headers: %s)" % (a, ", ".join(expected))
            if pos <= last:
                return "%r is out of order (standard order: %s)" % (a, ", ".join(expected))
            last = pos
        return None
    opt = set(optional)
    i = 0
    for e in expected:
        if i < len(actual) and same(actual[i], e):
            i += 1
        elif e not in opt:
            return "expected %r at position %d, got %r" % (
                e, i, actual[i] if i < len(actual) else None)
    if i != len(actual):
        return "unexpected extra headers: %r" % (actual[i:],)
    return None


# --- Linter ------------------------------------------------------------------

class Issue:
    def __init__(self, doc: Doc, line: int, rec: Dict[str, str], table: Table, severity: str):
        self.doc = doc
        self.line = line
        self.rec = rec
        self.table = table
        self.id = rec.get("ID", "")
        self.severity = severity
        # `not-reproducible` (gone on a re-run): kept one run, not scored, not on the roadmap
        self.retired = doc.is_102 and rec.get("Status", "").strip() == RETIRED


class Linter:
    def __init__(self, repo: Optional[str]):
        self.repo = repo
        self.findings = []  # type: List[Finding]
        self.expected, self.optional = load_headers()
        self.required = load_required()
        self._line_counts = {}  # type: Dict[str, Optional[int]]

    def add(self, doc: Doc, line: int, message: str) -> None:
        self.findings.append(Finding(doc.rel, line, message))

    # -- kinds
    def kind(self, name: str) -> Optional[str]:
        if name in self.expected and name != ADR_KEY:
            return name
        if ADR_NAME_RE.match(name) and not name.startswith("0000-"):
            return ADR_KEY
        return None

    # -- per-file checks
    def lint_doc(self, doc: Doc, kind: str) -> None:
        if kind == ADR_KEY:
            if not doc.is_102:
                return  # a pre-existing ADR in another format: only the supersede pairing
            self.lint_adr(doc)
        else:
            self.lint_headers(doc, kind)
            self.lint_scope_context(doc)
            self.lint_format_audit(doc, kind)
            self.lint_vocab(doc)
        self.lint_mermaid(doc)

    def lint_headers(self, doc: Doc, kind: str) -> None:
        for req in self.required.get(kind, ()):
            if not any(h == req or (req in PREFIX_MATCH_HEADERS and h.startswith(req))
                       for h in doc.headings()):
                self.add(doc, 1, "standard headers: required header %r is missing" % req)
        if not doc.is_102 and LEGACY_PREFIX + kind in self.expected:
            kind = LEGACY_PREFIX + kind
        problem = header_mismatch(doc.headings(), self.expected[kind], self.optional.get(kind, ()),
                                  allow_missing=True)
        if problem:
            self.add(doc, 1, "standard headers: " + problem)

    def lint_scope_context(self, doc: Doc) -> None:
        for label in ("Scope", "Context"):
            if not any(ln.startswith("> %s:" % label) for ln in doc.header_block):
                self.add(doc, 1, "missing `> %s:` line at the top of the file" % label)

    def lint_format_audit(self, doc: Doc, kind: str) -> None:
        if not doc.is_102:
            if kind in AUDIT_KINDS and not doc.archived:
                self.add(doc, 1, "missing `> Format: 1.0.2` line in the header block (without it "
                         "the 1.0.2 checks are skipped; only archived runs may omit it)")
            return
        audit = [(i + 1, ln) for i, ln in enumerate(doc.header_block) if ln.startswith("> Audit:")]
        if not audit:
            self.add(doc, 1, "missing `> Audit:` line in the header block (File Skeleton)")
        elif not AUDIT_LINE_RE.match(audit[0][1].rstrip()):
            self.add(doc, audit[0][0], "`> Audit:` line must read `plugin <version> · scanner "
                     "schema <n>` (or `scanner not run`) ` · rev <sha|unknown>`, then optional "
                     "`· config <hash>` and `· <timestamp>`")

    def lint_vocab(self, doc: Doc) -> None:
        for table in doc.tables():
            for col, allowed in (("Severity", SEVERITIES), ("Effort", EFFORTS),
                                 ("Impact", IMPACTS)):
                if col not in table.header:
                    continue
                idx = table.header.index(col)
                for line, cells in table.rows:
                    if idx < len(cells) and cells[idx] not in allowed:
                        who = cells[0] if cells else "row"
                        self.add(doc, line, "%s: %s %r is not one of %s"
                                 % (who, col.lower(), cells[idx], ", ".join(allowed)))

    # -- ADRs
    def lint_adr(self, doc: Doc) -> None:
        number = ADR_NAME_RE.match(os.path.basename(doc.path)).group(1)
        base = self.expected[ADR_KEY]
        heads = doc.headings()
        if doc.is_102:
            for extra in ("Alternatives", "Assumptions"):
                if extra not in heads:
                    self.add(doc, 1, "ADR is missing the %s section (Format 1.0.2)" % extra)
            heads = [h for h in heads if h not in ("Alternatives", "Assumptions")]
        problem = header_mismatch(heads, base)
        if problem:
            self.add(doc, 1, "ADR sections: " + problem)
        title = doc.lines[0] if doc.lines else ""
        if not title.startswith("# %s. " % number):
            self.add(doc, 1, "ADR title must start with '# %s. '" % number)
        if not any(re.match(r"^Date: \d{4}-\d{2}-\d{2}$", ln) for ln in doc.header_block):
            self.add(doc, 1, "ADR needs a `Date: YYYY-MM-DD` line")
        if not any(re.match(r"^Status: (Accepted|Superseded by \d{4})$", ln)
                   for ln in doc.header_block):
            self.add(doc, 1, "ADR Status must be `Accepted` or `Superseded by NNNN`")

    def lint_adr_pairs(self, docs: List[Doc]) -> None:
        by_num = {}  # type: Dict[str, Doc]
        for d in docs:
            m = ADR_NAME_RE.match(os.path.basename(d.path))
            if m:
                by_num[m.group(1)] = d

        def find(doc: Doc, rx) -> Optional[Tuple[int, str]]:
            for i, ln in enumerate(doc.header_block):
                m = rx.match(ln.strip())
                if m:
                    return i + 1, m.group(1)
            return None

        for num, doc in by_num.items():
            sup = find(doc, SUPERSEDES_RE)
            if sup:
                line, target = sup
                old = by_num.get(target)
                if old is None:
                    self.add(doc, line, "Supersedes %s but no ADR %s exists in this folder"
                             % (target, target))
                else:
                    back = find(old, SUPERSEDED_BY_RE)
                    if not back or back[1] != num:
                        self.add(doc, line, "ADR %s supersedes %s, but %s is not marked "
                                 "`Status: Superseded by %s`" % (num, target, target, num))
            by = find(doc, SUPERSEDED_BY_RE)
            if by:
                line, target = by
                new = by_num.get(target)
                if new is not None and new.is_102:
                    fwd = find(new, SUPERSEDES_RE)
                    if not fwd or fwd[1] != num:
                        self.add(new, 1, "ADR %s is superseded by %s but %s has no "
                                 "`Supersedes: %s` line" % (num, target, target, num))

    # -- Mermaid
    def lint_mermaid(self, doc: Doc) -> None:
        i = 0
        core = doc.section("Core Components", exact=True)
        components = None  # type: Optional[List[str]]
        if core:
            for t in doc.tables(core[1] + 1, core[2]):
                if t.header and t.header[0] == "Component":
                    components = [r[1][0].strip("`").lower() for r in t.rows if r[1]]
        n = len(doc.lines)
        while i < n:
            if doc.lines[i].strip() == "```mermaid":
                j = i + 1
                while j < n and not doc.lines[j].strip().startswith("```"):
                    j += 1
                in_core = bool(core and core[1] < i < core[2])
                self.check_block(doc, i + 2, doc.lines[i + 1:j],
                                 components if in_core else None)
                i = j
            i += 1

    def check_block(self, doc: Doc, first_line: int, block: List[str],
                    components: Optional[List[str]]) -> None:
        if block and block[0].strip() == "---":  # Mermaid frontmatter (title, config)
            end = next((k for k in range(1, len(block)) if block[k].strip() == "---"), None)
            if end is not None:
                first_line += end + 1
                block = block[end + 1:]
        body = [(first_line + k, ln) for k, ln in enumerate(block)
                if ln.strip() and not ln.strip().startswith("%%")]
        if not body:
            self.add(doc, first_line, "mermaid block is empty")
            return
        kind = body[0][1].split()[0]
        if kind.startswith("C4"):
            self.add(doc, body[0][0], "mermaid: %r is Mermaid's experimental C4 syntax; draw C4 "
                     "diagrams as `flowchart LR` with C4 labels (formatting-rules.md)" % kind)
        elif kind not in MERMAID_HEADERS:
            self.add(doc, body[0][0], "mermaid block starts with unknown diagram header %r "
                     "(expected one of %s)" % (kind, ", ".join(MERMAID_HEADERS)))
        flow = kind in ("flowchart", "graph")
        depth = 0
        nodes = set()  # type: Set[str]
        # Text that must balance: for sequence diagrams only what precedes the first ":" of
        # a line (message text is free prose); other diagram types check whole lines.
        checkable = []
        for lineno, ln in body:
            if kind == "sequenceDiagram":
                ln = ln.split(":", 1)[0]
            checkable.append((lineno, ln))
        text = "\n".join(ln for _n, ln in checkable)
        line_of = [n for n, _ln in checkable]
        # Quoted labels may span lines, so balance quotes over the whole block.
        bare = re.sub(r'"[^"]*"', lambda m: '""' + "\n" * m.group(0).count("\n"), text)
        if '"' in bare.replace('""', ""):
            at = next((i for i, ln in enumerate(bare.split("\n")) if '"' in ln.replace('""', "")), 0)
            self.add(doc, line_of[min(at, len(line_of) - 1)], "mermaid: unbalanced quotes")
            bare = bare.replace('"', "")
        bad = self.unbalanced_line(bare)
        if bad is not None:
            self.add(doc, line_of[min(bad, len(line_of) - 1)], "mermaid: unbalanced brackets")
        for lineno, ln in body:
            if "\\|" in ln:
                self.add(doc, lineno, "mermaid: escaped pipe `\\|` in an edge; use "
                         '-->|"label"| without escaping')
            if flow:
                word = ln.strip().split()[0]
                if word == "subgraph":
                    depth += 1
                elif ln.strip() == "end":
                    depth -= 1
                    if depth < 0:
                        self.add(doc, lineno, "mermaid: `end` without a matching subgraph")
                        depth = 0
                nodes |= self.flow_nodes(re.sub(r'"[^"]*"', '""', ln))
                if components is not None:
                    self.check_container(doc, lineno, ln, components)
        if flow and depth > 0:
            self.add(doc, first_line, "mermaid: subgraph without a matching `end`")
        if flow and len(nodes) > MAX_NODES:
            self.add(doc, first_line, "mermaid: diagram has %d nodes (max %d); split it"
                     % (len(nodes), MAX_NODES))

    @staticmethod
    def unbalanced_line(text: str) -> Optional[int]:
        """0-based line index of the first unbalanced () or [], else None."""
        pairs = {")": "(", "]": "["}
        stack = []  # type: List[Tuple[str, int]]
        line = 0
        for ch in text:
            if ch == "\n":
                line += 1
            elif ch in "([":
                stack.append((ch, line))
            elif ch in ")]":
                if not stack or stack.pop()[0] != pairs[ch]:
                    return line
        return stack[0][1] if stack else None

    @staticmethod
    def flow_nodes(bare: str) -> Set[str]:
        stripped = bare.strip()
        if not stripped or stripped.split()[0] in FLOW_SKIP:
            return set()
        text = re.sub(r"\|[^|]*\|", "", stripped)
        prev = None
        while prev != text:
            prev = text
            text = re.sub(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}", "", text)
        text = re.sub(r":::\w+", "", text)
        found = set()
        for piece in ARROW_RE.split(text):
            piece = piece.strip().rstrip(";")
            if re.match(r"^[A-Za-z_][\w]*$", piece):
                found.add(piece)
        return found

    def check_container(self, doc: Doc, lineno: int, line: str, components: List[str]) -> None:
        for m in re.finditer(r'"Container:\s*([^"]+)"', line):
            label = m.group(1).strip()
            tech = re.match(r"^(.*?)\s*\((.*)\)\s*$", label)
            cands = [tech.group(1)] + tech.group(2).split(",") if tech else [label]
            cands = [c.strip().lower() for c in cands if c.strip()]
            if not any(re.search(r"\b%s\b" % re.escape(c), cell)
                       for c in cands for cell in components):
                self.add(doc, lineno, "container %r in the diagram does not appear in the "
                         "Core Components table" % label)

    # -- issues
    def issues_of(self, doc: Doc, kind: str) -> List[Issue]:
        if kind == "01-architecture-issues.md":
            specs = (("Critical Issues", "Critical"), ("High Issues", "High"),
                     ("Medium / Low Issues", ""))
        elif kind == "02-api-optimization.md":
            specs = (("Issues Found", ""),)
        elif kind == "quick-audit.md":
            specs = (("Top Issues", ""),)
        else:
            return []
        out = []
        for heading, sev in specs:
            for table in doc.section_tables(heading):
                if not table.header or table.header[0] != "ID":
                    continue
                for line, cells in table.rows:
                    rec = dict(zip(table.header, cells))
                    out.append(Issue(doc, line, rec, table, rec.get("Severity", sev) or sev))
        return out

    def lint_issue_rows(self, issues: List[Issue], kind: str, rc_defined: Set[str]) -> None:
        for iss in issues:
            doc = iss.doc
            if not ID_FULL_RE.match(iss.id):
                self.add(doc, iss.line, "%r is a malformed ID (expected PREFIX-NNN, e.g. SEC-001)"
                         % iss.id)
            elif kind == "02-api-optimization.md" and not iss.id.startswith("API-"):
                self.add(doc, iss.line, "%s: only API-* issues are defined in "
                         "02-api-optimization.md" % iss.id)
            elif kind == "01-architecture-issues.md" and iss.id.startswith("API-"):
                self.add(doc, iss.line, "%s: API-* issues are defined only in "
                         "02-api-optimization.md" % iss.id)
            if iss.severity not in SEVERITIES and "Severity" not in iss.table.header:
                # (a Severity column value is already reported by the vocabulary check)
                self.add(doc, iss.line, "%s: severity %r is not one of %s"
                         % (iss.id, iss.severity, ", ".join(SEVERITIES)))
            self.lint_evidence(iss)
            if doc.is_102:
                self.lint_new_fields(iss, rc_defined)

    def lint_evidence(self, iss: Issue) -> None:
        cell = iss.rec.get("Evidence")
        if cell is None:
            return
        if not split_evidence(cell):
            self.add(iss.doc, iss.line, "%s: Evidence cell is empty" % iss.id)
        entries, rejected = parse_evidence_full(cell)
        for tok in rejected:
            self.add(iss.doc, iss.line, "%s: evidence %r is not path:line[-line] "
                     "or a folder/file path" % (iss.id, tok))
        for path, first, last, tok in entries:
            problem = evidence_path_problem(path)
            if problem:
                self.add(iss.doc, iss.line, "%s: evidence path %r %s; cite it relative to the "
                         "repo root" % (iss.id, path, problem))
                continue
            if last < first:
                self.add(iss.doc, iss.line, "%s: evidence %r has an inverted line range"
                         % (iss.id, tok))
            if not self.repo or iss.retired:
                continue  # not-reproducible rows may cite code that is gone
            if first == 0:
                if not os.path.exists(os.path.join(self.repo, *path.rstrip("/").split("/"))):
                    self.add(iss.doc, iss.line, "%s: evidence path %r does not exist under "
                             "the repo root" % (iss.id, path))
                continue
            count = self.line_count(path)
            if count is None:
                self.add(iss.doc, iss.line, "%s: evidence file %r does not exist under "
                         "the repo root" % (iss.id, path))
            elif first < 1 or last > count:
                self.add(iss.doc, iss.line, "%s: evidence %s is beyond the file's %d lines"
                         % (iss.id, tok, count))

    def line_count(self, rel: str) -> Optional[int]:
        if rel not in self._line_counts:
            path = os.path.join(self.repo or "", *rel.split("/"))
            count = None
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    count = len(fh.read().splitlines())
            self._line_counts[rel] = count
        return self._line_counts[rel]

    def lint_new_fields(self, iss: Issue, rc_defined: Set[str]) -> None:
        doc, header = iss.doc, iss.table.header
        for col in ("Status", "Source"):
            if col not in header:
                if iss.line == iss.table.rows[0][0]:
                    self.add(doc, iss.table.header_line,
                             "issue table is missing the %s column (Format 1.0.2)" % col)
        status = iss.rec.get("Status")
        if status is not None and status not in STATUSES:
            self.add(doc, iss.line, "%s: status %r is not one of %s"
                     % (iss.id, status, ", ".join(STATUSES)))
        source = iss.rec.get("Source")
        if source is not None and not SOURCE_RE.match(source):
            self.add(doc, iss.line, "%s: source %r must be scanner:<rule>, agent or "
                     "scanner+agent[:<rule>]" % (iss.id, source))
        if iss.severity in ("Critical", "High") and not iss.retired:
            verify = iss.rec.get("Verify", "").strip()
            prose = [ln for ln in doc.lines if not ln.lstrip().startswith("|")]
            in_detail = any(iss.id in ln and re.search(r"verify:", ln, re.I) for ln in prose)
            if verify.lower() in EMPTY_VERIFY and not in_detail:
                self.add(doc, iss.line, "%s: %s issue needs a Verify step (column or detail "
                         "list entry `Verify:`; `—` is allowed only for Medium/Low)"
                         % (iss.id, iss.severity))
            head = re.compile(r"^\s*[-*]\s*\**%s\**:" % re.escape(iss.id))
            if not any(head.match(ln) and "Observed:" in ln and "Inference:" in ln
                       for ln in prose):
                self.add(doc, iss.line, "%s: %s issue needs a detail line `- %s: Observed: ... "
                         "Inference: ... Limitations: ...`" % (iss.id, iss.severity, iss.id))
        for rc in RC_RE.findall(" ".join(iss.rec.values())):
            if rc not in rc_defined:
                self.add(doc, iss.line, "%s: %s is referenced but never defined" % (iss.id, rc))

    # -- health score
    def lint_score(self, doc: Doc, issues: List[Issue], have_api: bool) -> None:
        sec = doc.section("Architecture Health Score")
        if not sec:
            return
        line_no = sec[1] + 1
        m = re.match(r"^Architecture Health Score: (.+)$", sec[0])
        head = HEADLINE_RE.fullmatch(m.group(1)) if m else None
        if not head:
            self.add(doc, line_no, "headline must be `## Architecture Health Score: <Band> "
                     "(<X.X> / 10)`")
        tables = [t for t in doc.tables(sec[1] + 1, sec[2]) if t.header[:1] == ["Category"]]
        if not tables:
            self.add(doc, line_no, "Architecture Health Score has no Category table")
            return
        table = tables[0]
        core_cols = [h for h in table.header if h != "Weight"]
        if core_cols != ["Category", "Score", "Band", "Main driver (IDs)"]:
            self.add(doc, table.header_line, "score table columns must be Category, Score, "
                     "Band, Main driver (IDs), plus an optional Weight column")
        expected_w = rubric.weights_for(doc.context_line())
        has_weight = "Weight" in table.header
        if not has_weight and doc.is_102 and any(w != 1.0 for w in expected_w.values()):
            self.add(doc, table.header_line, "Context line implies category weights %s but the "
                     "score table has no Weight column"
                     % {k: v for k, v in expected_w.items() if v != 1.0})
        names = [r[1][0] for r in table.rows]
        if names != rubric.CATEGORIES:
            self.add(doc, table.header_line, "score table rows must be %s in order"
                     % ", ".join(rubric.CATEGORIES))
        scores = {}  # type: Dict[str, int]
        weights = {}  # type: Dict[str, float]
        for line, cells in table.rows:
            rec = dict(zip(table.header, cells))
            cat = rec.get("Category", "")
            prefix = rubric.CATEGORY_PREFIX.get(cat)
            if not prefix:
                continue
            if prefix == "API" and not have_api:
                continue  # API issues live in 02, which is not in this folder
            sevs = [i.severity for i in issues if i.id.startswith(prefix + "-") and not i.retired]
            cell = rec.get("Score", "")
            if cell == "Not assessed":
                if sevs:
                    self.add(doc, line, "%s is Not assessed but has %d issue(s)" % (cat, len(sevs)))
                continue
            if not cell.isdigit():
                self.add(doc, line, "%s: score %r is not an integer or 'Not assessed'" % (cat, cell))
                continue
            want = rubric.subscore(sevs)
            if int(cell) != want:
                self.add(doc, line, "%s sub-score is %s but the lookup table gives %d for "
                         "its issues" % (cat, cell, want))
            wb = rubric.SUBSCORE_BAND.get(int(cell))
            if rec.get("Band") != wb:
                self.add(doc, line, "%s: band %r does not match score %s (%s)"
                         % (cat, rec.get("Band"), cell, wb))
            scores[cat] = int(cell)
            weights[cat] = expected_w[cat]
            if has_weight:
                try:
                    declared = float(rec.get("Weight", ""))
                except ValueError:
                    self.add(doc, line, "%s: weight %r is not a number" % (cat, rec.get("Weight")))
                    continue
                if declared != expected_w[cat]:
                    self.add(doc, line, "%s: weight %s does not match the Context line "
                             "(rubric table gives %s)" % (cat, rec.get("Weight"), expected_w[cat]))
                weights[cat] = declared
        if not head or not scores:
            return
        score_col = table.header.index("Score") if "Score" in table.header else 1
        pending = [r for r in table.rows if r[1][score_col:score_col + 1] != ["Not assessed"]
                   and (have_api or r[1][0] != "API Design")]
        if len(scores) < len(pending):
            return  # a row failed to parse; avoid cascading mean errors
        want_overall = rubric.overall(scores, weights)
        got = float(head.group(2))
        if abs(got - want_overall) > 1e-9:
            self.add(doc, line_no, "overall score is %.1f but the assessed sub-scores%s give %.1f"
                     % (got, " (weighted)" if has_weight else "", want_overall))
        if head.group(1) != rubric.band(got):
            self.add(doc, line_no, "headline band %r does not match score %.1f (%s)"
                     % (head.group(1), got, rubric.band(got)))

    def lint_quick_headline(self, doc: Doc) -> None:
        sec = doc.section("Health Band")
        if not sec:
            return
        text = "\n".join(doc.lines[sec[1]:sec[2]])
        m = HEADLINE_RE.search(text)
        if m and m.group(1) != rubric.band(float(m.group(2))):
            self.add(doc, sec[1] + 1, "headline band %r does not match score %s (%s)"
                     % (m.group(1), m.group(2), rubric.band(float(m.group(2)))))

    # -- roadmap
    def lint_roadmap(self, doc: Doc, defined: Dict[str, Issue], have: Set[str]) -> None:
        cited = {}  # type: Dict[str, int]
        first_line = {}  # type: Dict[str, int]
        for horizon, efforts in HORIZONS:
            for table in doc.section_tables(horizon):
                if table.header != ["Action (IDs)", "Effort", "Impact", "Why"]:
                    self.add(doc, table.header_line, "%s table columns must be Action (IDs), "
                             "Effort, Impact, Why" % horizon)
                    continue
                for line, cells in table.rows:
                    if len(cells) < 4:
                        continue
                    ids = ID_RE.findall(cells[0].split(":", 1)[0])
                    if not ids:
                        self.add(doc, line, "Action cell must start with the issue IDs it "
                                 "resolves: %r" % cells[0])
                    if cells[1] not in EFFORTS or cells[2] not in IMPACTS:
                        continue  # vocabulary finding already reported
                    for i in ids:
                        cited[i] = cited.get(i, 0) + 1
                        first_line.setdefault(i, line)
                        if cells[1] not in efforts:
                            want = [h for h, e in HORIZONS if cells[1] in e][0]
                            self.add(doc, line, "%s has effort %s but sits in %s (belongs in %s)"
                                     % (i, cells[1], horizon, want))
        for i, n in cited.items():
            if n > 1:
                self.add(doc, first_line[i], "%s appears in %d roadmap rows (expected 1)" % (i, n))
            if i not in defined and ((i.startswith("API-") and "02" in have)
                                     or (not i.startswith("API-") and "01" in have)):
                self.add(doc, first_line[i], "roadmap cites undefined ID %s" % i)
        for i, iss in defined.items():
            if i not in cited:
                self.add(doc, 1, "%s is defined (%s) but missing from the roadmap horizon rows"
                         % (i, iss.doc.rel))

    def lint_quick_audit(self, doc: Doc, issues: List[Issue]) -> None:
        text = ""
        for heading in ("Quick Wins", "Next Steps"):
            sec = doc.section(heading)
            if sec:
                text += "\n".join(doc.lines[sec[1] + 1:sec[2]]) + "\n"
        counts = {}  # type: Dict[str, int]
        for i in ID_RE.findall(text):
            counts[i] = counts.get(i, 0) + 1
        for iss in issues:
            n = counts.get(iss.id, 0)
            if n != 1:
                self.add(doc, iss.line, "%s is referenced %d times in Quick Wins and Next Steps "
                         "(expected exactly 1)" % (iss.id, n))


def collect_docs(folder: str, linter: Linter, targets: Optional[List[str]],
                 base: str) -> List[Tuple[Doc, str]]:
    out = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.endswith(".md"):
            kind = linter.kind(name)
            if kind:
                rel = os.path.relpath(path, base).replace(os.sep, "/")
                out.append((Doc(path, rel, read_text(path)), kind))
    return out


def lint(path: str, repo: Optional[str] = None, include_history: bool = False) -> List[Finding]:
    """Lint a docs folder (recursively) or one Markdown file; returns findings.

    Folders named `history` (archived runs) are skipped unless include_history is set. For a
    single file the sibling files in its folder are read for cross-file checks, but only
    findings in the given file are returned."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    linter = Linter(repo)
    if os.path.isdir(path):
        base = os.path.abspath(path)
        folders = []
        for dirpath, dirs, _files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__"
                             and (include_history or d != HISTORY_DIR))
            folders.append(dirpath)
        only = None  # type: Optional[str]
    else:
        base = os.path.dirname(os.path.abspath(path))
        folders = [base]
        only = os.path.relpath(os.path.abspath(path), base).replace(os.sep, "/")
    for folder in folders:
        lint_folder(linter, folder, base)
    found = linter.findings
    if only is not None:
        found = [f for f in found if f.file == only]
    return sorted(found, key=lambda f: (f.file, f.line, f.message))


def lint_folder(linter: Linter, folder: str, base: str) -> None:
    docs = collect_docs(folder, linter, None, base)
    if not docs:
        return
    by_kind = {kind: doc for doc, kind in docs}
    rc_defined = set()  # type: Set[str]
    for doc, _kind in docs:
        for ln in doc.lines:
            m = RC_DEF_RE.match(ln)
            if m:
                rc_defined.add(m.group(1))
    for doc, kind in docs:
        linter.lint_doc(doc, kind)
    issues = {}  # type: Dict[str, List[Issue]]
    for doc, kind in docs:
        found = linter.issues_of(doc, kind)
        if found:
            issues[kind] = found
            linter.lint_issue_rows(found, kind, rc_defined)
    # Single-definition rule across the folder.
    seen = {}  # type: Dict[str, Issue]
    for kind in ("01-architecture-issues.md", "02-api-optimization.md", "quick-audit.md"):
        for iss in issues.get(kind, []):
            if iss.id in seen:
                linter.add(iss.doc, iss.line, "%s is defined more than once (also %s:%d)"
                           % (iss.id, seen[iss.id].doc.rel, seen[iss.id].line))
            else:
                seen[iss.id] = iss
    one = "01-architecture-issues.md"
    if one in by_kind:
        two = "02-api-optimization.md" in by_kind
        scored = issues.get(one, []) + issues.get("02-api-optimization.md", [])
        linter.lint_score(by_kind[one], scored, have_api=two)
    if "quick-audit.md" in by_kind:
        linter.lint_quick_headline(by_kind["quick-audit.md"])
        linter.lint_quick_audit(by_kind["quick-audit.md"], issues.get("quick-audit.md", []))
    road = "03-improvement-roadmap.md"
    if road in by_kind:
        have = {k for k, f in (("01", one), ("02", "02-api-optimization.md")) if f in by_kind}
        defined = {i: s for i, s in seen.items()
                   if s.doc.rel.rsplit("/", 1)[-1] != "quick-audit.md" and not s.retired}
        linter.lint_roadmap(by_kind[road], defined, have)
    linter.lint_adr_pairs([d for d, k in docs if k == ADR_KEY])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint design-and-fix Markdown output (headers, vocabularies, IDs, "
                    "score arithmetic, roadmap placement, evidence, Mermaid, ADRs).")
    parser.add_argument("target", help="docs/architecture folder or one Markdown file")
    parser.add_argument("--repo", help="repo root: verify evidence files and line numbers")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--include-history", action="store_true",
                        help="also lint archived runs under history/ (skipped by default)")
    args = parser.parse_args(argv)
    if args.repo and not os.path.isdir(args.repo):
        print("error: --repo %s is not a directory" % args.repo, file=sys.stderr)
        return 2
    try:
        findings = lint(args.target, args.repo, include_history=args.include_history)
    except FileNotFoundError as exc:
        print("error: %s does not exist" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps([{"file": f.file, "line": f.line, "message": f.message}
                          for f in findings], indent=2))
    else:
        for f in findings:
            print("%s:%d: %s" % (f.file, f.line, f.message))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
