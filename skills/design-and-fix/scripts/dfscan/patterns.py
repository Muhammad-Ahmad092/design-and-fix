from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

from .imports import _read
from .model import FileInfo, Signal
from .routes import find_routes
from .text import strip_comments

# Rules are leads, not verdicts: every rule below lists its false-positive risks.
# All rules except hardcoded-secret skip test files; config files get only hardcoded-secret.
# Every rule matches against comment-stripped text (strings are kept, line numbers are preserved).

LOOP_BODY_MAX_LINES = 30
CALL_MAX_LINES = 10
_SKIP_LANGS = {"SQL", "GraphQL", "Protocol Buffers (gRPC likely)"}


def _kind(fi: FileInfo) -> Optional[str]:
    if fi.lang in _SKIP_LANGS or fi.lang is None:
        return None
    if fi.ext == ".py":
        return "py"
    if fi.ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        return "js"
    if fi.ext == ".go":
        return "go"
    if fi.ext in (".java", ".kt", ".cs", ".php"):
        return "c"
    return None


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _line_text(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:n if (n := end) >= 0 else len(text)]


def _match_end(text: str, open_idx: int, max_lines: int) -> int:
    """Index of the bracket closing text[open_idx], or the max_lines cut-off."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    o = text[open_idx]
    c = pairs[o]
    depth, lines = 0, 0
    for j in range(open_idx, len(text)):
        ch = text[j]
        if ch == o:
            depth += 1
        elif ch == c:
            depth -= 1
            if depth == 0:
                return j
        elif ch == "\n":
            lines += 1
            if lines >= max_lines:
                return j
    return len(text)


def _sig(rule: str, cat: str, sev: str, fi: FileInfo, text: str, pos: int, conf: str,
         snippet: Optional[str] = None) -> Signal:
    return Signal(rule, cat, sev, fi.rel, _line_of(text, pos),
                  snippet if snippet is not None else _line_text(text, pos), conf)


# ---------------------------------------------------------------- http-no-timeout
_FETCH_RE = re.compile(r"(?<![\w.$])fetch\(")
_AXIOS_RE = re.compile(r"(?<![\w$])axios(?:\.(?:get|post|put|patch|delete|head|options|request))?\(")
_PYHTTP_RE = re.compile(r"\b(?:requests|httpx)\.(?:get|post|put|patch|delete|head|options|request)\(")
_GO_CALL_RE = re.compile(r"\bhttp\.(?:Get|Post|PostForm|Head)\(|\bhttp\.DefaultClient\b")
_GO_CLIENT_RE = re.compile(r"&http\.Client\{")


def rule_http_no_timeout(fi: FileInfo, text: str) -> List[Signal]:
    """Outbound HTTP call with no timeout/signal.

    False-positive risks: a timeout configured elsewhere (axios.create defaults, a shared
    session, a wrapper or a context deadline) is invisible here; `requests.get(...)` with
    **kwargs forwarding timeout is flagged.
    """
    kind = _kind(fi)
    out: List[Signal] = []

    def emit(pos: int) -> None:
        out.append(_sig("http-no-timeout", "Reliability", "High", fi, text, pos, "medium"))

    def call_text(m: "re.Match[str]") -> str:
        open_idx = m.end() - 1
        return text[open_idx:_match_end(text, open_idx, CALL_MAX_LINES) + 1]

    if kind == "js":
        for m in _FETCH_RE.finditer(text):
            if not re.search(r"signal|timeout", call_text(m), re.I):
                emit(m.start())
        for m in _AXIOS_RE.finditer(text):
            if "timeout" not in call_text(m).lower():
                emit(m.start())
    elif kind == "py":
        for m in _PYHTTP_RE.finditer(text):
            if not re.search(r"\btimeout\s*=", call_text(m)):
                emit(m.start())
    elif kind == "go":
        for m in _GO_CALL_RE.finditer(text):
            emit(m.start())
        for m in _GO_CLIENT_RE.finditer(text):
            open_idx = m.end() - 1
            if "Timeout:" not in text[open_idx:_match_end(text, open_idx, CALL_MAX_LINES) + 1]:
                emit(m.start())
    return out


# ---------------------------------------------------------------- loops (shared)
_DB_RECEIVER = (r"(?:\b(?:db|conn|connection|session|cursor|repo|repository|knex|collection|objects"
                r"|prisma(?:\.\w+)?)|\b\w*(?:Repository|Repo|Model)|\b[A-Z]\w*)")
_DB_CALL_RE = re.compile(
    r"\.(?:query|execute|findOne|findMany)\(|session\.query|objects\.(?:get|filter)|db\.Query|"
    + _DB_RECEIVER + r"\.(?:get|filter|find|findById)\(")
_BRACE_LOOP_RE = re.compile(r"(?m)^[ \t]*(?:\}[ \t]*)?(?:for|while)\b")
_ITER_CALL_RE = re.compile(r"\.(?:forEach|map)\(")
_PY_LOOP_RE = re.compile(r"^(\s*)(?:async\s+)?(?:for|while)\b.*:\s*$")

Loop = Tuple[int, List[Tuple[int, str]]]  # (header line, [(line number, line text)])


def _body_lines(text: str, start: int, end: int) -> List[Tuple[int, str]]:
    first = _line_of(text, start)
    return [(first + k, ln) for k, ln in enumerate(text[start:end].split("\n"))]


def _loops(text: str, kind: str) -> List[Loop]:
    """Loop bodies (<= 30 lines). Brace-scoped for js/go/c, indentation-scoped for py."""
    loops: List[Loop] = []
    if kind == "py":
        lines = text.split("\n")
        for idx, ln in enumerate(lines):
            m = _PY_LOOP_RE.match(ln)
            if not m:
                continue
            indent = len(m.group(1).expandtabs())
            body: List[Tuple[int, str]] = []
            for k in range(idx + 1, min(len(lines), idx + 1 + LOOP_BODY_MAX_LINES)):
                cur = lines[k]
                if cur.strip() and len(cur) - len(cur.lstrip()) <= indent:
                    break
                body.append((k + 1, cur))
            loops.append((idx + 1, body))
        return loops
    for m in _BRACE_LOOP_RE.finditer(text):
        kw_end = m.end()
        eol = text.find("\n", kw_end)
        eol = len(text) if eol < 0 else eol
        if kind == "go":
            brace = text.rfind("{", kw_end, eol)
        else:
            paren = text.find("(", kw_end, eol)
            if paren < 0:
                continue
            close = _match_end(text, paren, 3)
            brace = close + 1
            while brace < len(text) and text[brace] in " \t\r\n":
                brace += 1
            if brace >= len(text) or text[brace] != "{":
                brace = -1
        if brace < 0:
            continue
        end = _match_end(text, brace, LOOP_BODY_MAX_LINES)
        loops.append((_line_of(text, m.start()), _body_lines(text, brace + 1, end)))
    if kind != "go":
        for m in _ITER_CALL_RE.finditer(text):
            open_idx = m.end() - 1
            end = _match_end(text, open_idx, LOOP_BODY_MAX_LINES)
            loops.append((_line_of(text, m.start()), _body_lines(text, open_idx + 1, end)))
    return loops


def rule_query_in_loop(fi: FileInfo, text: str) -> List[Signal]:
    """DB-looking call inside a loop body (N+1 queries).

    Explicit query verbs (.query, .execute, .findOne, .findMany, session.query, objects.get|filter,
    db.Query) always count; generic verbs (.get, .filter, .find, .findById) count only with a DB-like
    receiver (db, conn, session, cursor, repo, prisma.x, knex, collection, objects, *Repository/*Repo/
    *Model, or a Capitalised class such as User.find).
    False-positive risks: a Capitalised receiver can be a non-DB class (Cache.get), a differently named
    DB handle (store.get) is missed, a loop over a handful of items is harmless, and batching built
    from the loop is not distinguished.
    """
    kind = _kind(fi)
    if kind is None:
        return []
    out: List[Signal] = []
    seen = set()
    for _, body in _loops(text, kind):
        for lineno, ln in body:
            if lineno not in seen and _DB_CALL_RE.search(ln):
                seen.add(lineno)
                out.append(Signal("query-in-loop", "Scalability", "High", fi.rel, lineno, ln, "medium"))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- unbounded-query
_SELECT_RE = re.compile(r"(?:SELECT\b.*?\bFROM|select\b.*?\bfrom)\s+[\w\"`\[(]")
_LIMIT_RE = re.compile(r"\bLIMIT\b|\bTOP\s*\(?\s*\d|FETCH\s+FIRST|COUNT\s*\(", re.I)
_FIND_ALL_RE = re.compile(r"\.find\(\s*\{\s*\}\s*\)")
_FIND_MANY_RE = re.compile(r"\.findMany\(")
_OBJECTS_ALL_RE = re.compile(r"\bobjects\.all\(\)")


def _statement(text: str, pos: int) -> str:
    """SQL statement text starting at pos: rest of the line, extended over a multi-line string."""
    lines = text[pos:].split("\n")
    stmt = [lines[0]]
    if not re.search(r"[\"'`]", lines[0]):
        for ln in lines[1:6]:
            stmt.append(ln)
            if re.search(r"[\"'`]", ln):
                break
    return "\n".join(stmt).split(";")[0]


def rule_unbounded_query(fi: FileInfo, text: str) -> List[Signal]:
    """SELECT without LIMIT/TOP/FETCH FIRST/COUNT, .find({}), findMany without take, objects.all().

    False-positive risks: single-row lookups by primary key, small lookup tables, queries
    whose limit is applied by a wrapper or a later slice, English prose containing
    "select ... from", and `objects.all()` consumed with a later slice are all flagged.
    """
    kind = _kind(fi)
    if kind is None:
        return []
    out: List[Signal] = []
    for m in _SELECT_RE.finditer(text):
        if not _LIMIT_RE.search(_statement(text, m.start())):
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "low"))
    for m in _FIND_ALL_RE.finditer(text):
        out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "medium"))
    for m in _FIND_MANY_RE.finditer(text):
        open_idx = m.end() - 1
        if not re.search(r"\b(?:take|limit)\b", text[open_idx:_match_end(text, open_idx, CALL_MAX_LINES) + 1]):
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "medium"))
    for m in _OBJECTS_ALL_RE.finditer(text):
        out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "low"))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- hardcoded-secret
_SECRET_RE = re.compile(
    r"[\w.-]*(?:password|passwd|secret|api_key|apikey|token|private_key)(?:[._-]?key)?(?![\w.-])"
    r"[\"']?\s*[:=]\s*"
    r"[\"']([^\"']{8,})[\"']",
    re.I)
_SECRET_IGNORE_RE = re.compile(r"example|changeme|placeholder|xxx|your_|<|\$\{|process\.env|os\.environ", re.I)
# Config files only: unquoted dotenv `KEY=value` and YAML/INI `key: value` lines.
_UNQUOTED_SECRET_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?[\w.-]*(?:password|passwd|secret|api_key|apikey|token|private_key)"
    r"(?:[._-]?key)?(?![\w.-])[ \t]*[:=][ \t]*([^\s\"'#][^\s#]{7,})[ \t]*$",
    re.I | re.M)
_NON_SECRET_VALUE_RE = re.compile(r"^(?:\d+|true|false|null|none)$|^[$%]", re.I)
# Values that are locations, not credentials: URLs and absolute file paths.
_LOCATION_VALUE_RE = re.compile(r"^(?:https?://|/|[A-Za-z]:\\)", re.I)
_AWS_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")


def rule_hardcoded_secret(fi: FileInfo, text: str) -> List[Signal]:
    """Credential-looking assignment, AWS access key id or PEM private key header.

    Runs on code, test and config files (.env, YAML, JSON, TOML, INI, properties); matches in test
    files get confidence "low". Config files also match unquoted `KEY=value` / `key: value` lines.
    Placeholders, values containing whitespace (labels such as "Password must be ..."), `${...}`
    references and env lookups are skipped. The snippet masks the value so the report never
    repeats the secret.
    The key may have any prefix (DB_PASSWORD, stripe_secret) but after the secret word only an optional
    `key` (`_key`, `-key`, `Key`) may follow, so tokenUrl, password_file, password_hash, token_expiry,
    tokenizer and tokens never match. Values that are URLs or absolute file paths are skipped.
    False-positive risks: non-secret values under a secret-sounding key (e.g. `token: "Bearer-type"`),
    hashes, public identifiers and test fixtures. Secrets inside comments are not seen because
    comments are stripped.
    """
    conf = "low" if fi.is_test else "high"
    out: List[Signal] = []

    def emit(pos: int, value: str) -> None:
        masked = _line_text(text, pos).replace(value, "<redacted>")
        out.append(_sig("hardcoded-secret", "Security", "Critical", fi, text, pos, conf, masked))

    for m in _SECRET_RE.finditer(text):
        if (_SECRET_IGNORE_RE.search(m.group(1)) or re.search(r"\s", m.group(1))
                or _LOCATION_VALUE_RE.search(m.group(1))):
            continue
        emit(m.start(), m.group(1))
    if fi.is_config:
        for m in _UNQUOTED_SECRET_RE.finditer(text):
            value = m.group(1)
            if (_SECRET_IGNORE_RE.search(value) or _NON_SECRET_VALUE_RE.search(value)
                    or _LOCATION_VALUE_RE.search(value)):
                continue
            emit(m.start(1), value)
    for rx, label in ((_AWS_RE, "AWS access key id <redacted>"), (_PEM_RE, "private key header")):
        for m in rx.finditer(text):
            out.append(_sig("hardcoded-secret", "Security", "Critical", fi, text, m.start(), conf, label))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- sql-string-concat
_SQL_WORDS_RE = re.compile(
    r"\bSELECT\b.*\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\b.*\bSET\b|\bDELETE\s+FROM\b")
_LOG_LINE_RE = re.compile(
    r"^\s*(?:console\.\w+|print|(?:\w+\.)?(?:log|logger|logging)\w*\.\w+|fmt\.Print\w*)\s*\(", re.I)
_CONCAT_RE = re.compile(r"[\"'`]\s*\+|\+\s*[\"'`]|(?<!\w)[fF][rR]?[\"'].*\{|\$\{")


def _blank_tagged_templates(text: str) -> str:
    """Blank the bodies of tagged template literals (sql`...`, prisma.$queryRaw`...`).

    Tagged templates pass `${}` values as bound parameters, so they are not concatenation.
    Backticks are paired in order, skipping backticks inside single-line '...' or "..." string
    literals; the text is already comment-stripped, so comments cannot hold stray backticks.
    A regex literal containing a quote can still confuse the string tracking.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        elif c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            if j < n and i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_$"):
                for k in range(i + 1, j):
                    if out[k] != "\n":
                        out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def rule_sql_string_concat(fi: FileInfo, text: str) -> List[Signal]:
    """SQL text built with +, f-strings or untagged template literals (injection lead).

    Keywords must be UPPERCASE and SQL-shaped (SELECT..FROM, INSERT INTO, UPDATE..SET, DELETE FROM).
    Lines that are only console/print/log calls are skipped, so English text is not flagged.
    Tagged templates (sql`...`, SQL`...`, prisma.$queryRaw`...`) are parameterised and skipped.
    False-positive risks: interpolating a constant table name or a placeholder list is flagged.
    Lowercase SQL and statements assembled over several lines can be missed.
    """
    kind = _kind(fi)
    if kind is None:
        return []
    if kind == "js":
        text = _blank_tagged_templates(text)
    out: List[Signal] = []
    for idx, ln in enumerate(text.split("\n")):
        if _SQL_WORDS_RE.search(ln) and _CONCAT_RE.search(ln) and not _LOG_LINE_RE.match(ln):
            out.append(Signal("sql-string-concat", "Security", "Critical", fi.rel, idx + 1, ln, "medium"))
    return out


# ---------------------------------------------------------------- retry-no-backoff
_RETRY_WORD_RE = re.compile(r"retry|retries|attempt", re.I)
_NET_CALL_RE = re.compile(
    r"\bfetch\(|\baxios\b|\b(?:requests|httpx)\.\w+\(|\bhttp\.(?:Get|Post|Do|Head)\(|\.Do\(|"
    r"\burlopen\(|\.request\(")
_BACKOFF_RE = re.compile(r"sleep|setTimeout|backoff|time\.Sleep", re.I)


def rule_retry_no_backoff(fi: FileInfo, text: str) -> List[Signal]:
    """Retry/attempt loop around a network call with no sleep/backoff in the loop.

    False-positive risks: backoff implemented in a helper called from the loop or by the
    HTTP client library (e.g. urllib3 Retry, axios-retry); loops that merely mention
    "attempt" (login attempts) next to a network call.
    """
    kind = _kind(fi)
    if kind is None:
        return []
    lines = text.split("\n")
    out: List[Signal] = []
    seen = set()
    for header, body in _loops(text, kind):
        blob = lines[header - 1] + "\n" + "\n".join(b for _, b in body)
        if (header not in seen and _RETRY_WORD_RE.search(blob) and _NET_CALL_RE.search(blob)
                and not _BACKOFF_RE.search(blob)):
            seen.add(header)
            out.append(Signal("retry-no-backoff", "Reliability", "Medium", fi.rel, header,
                              lines[header - 1], "low"))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- swallowed-error
_CATCH_RE = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")
_EXCEPT_RE = re.compile(
    r"except(?!\s+(?:ImportError|ModuleNotFoundError)\s*(?:as\s+\w+)?\s*:)[^:\n]*:\s*pass\b")
_GO_ERR_RE = re.compile(r"(?<![\w.])_\s*=\s*err\b|if\s+err\s*!=\s*nil\s*\{\s*\}")


def rule_swallowed_error(fi: FileInfo, text: str) -> List[Signal]:
    """Empty catch block, `except: pass`, Go `_ = err` or an empty `if err != nil {}`.

    `except ImportError: pass` and `except ModuleNotFoundError: pass` are exempt.
    False-positive risks: deliberate best-effort cleanup (closing a file, an optional parse) is
    reported although intentional. A comment inside the block is stripped, so a documented
    "ignore on purpose" catch still fires.
    """
    kind = _kind(fi)
    rx = {"js": _CATCH_RE, "c": _CATCH_RE, "py": _EXCEPT_RE, "go": _GO_ERR_RE}.get(kind or "")
    if rx is None:
        return []
    return [_sig("swallowed-error", "Reliability", "Medium", fi, text, m.start(), "high")
            for m in rx.finditer(text)]


# ---------------------------------------------------------------- folder-level rules
_BARE_LOG_RE = re.compile(r"console\.log|(?<![\w.])print\(|fmt\.Println")
_LOG_LIB_RE = re.compile(
    r"(?:^[ \t]*(?:import|from)[ \t]+[^\n]*|[\"'`][^\"'`\n]*)"
    r"\b(?:winston|pino|bunyan|logging|structlog|loguru|zap|zerolog|logrus|slog|log4)",
    re.M)


_TOP_TEST_DIRS = ("tests", "test", "__tests__")


def folder_units(folders: List[dict], scopes: Optional[List[str]] = None) -> List[str]:
    """Folders the folder-level rules judge.

    Depth-2 folders; plus each scope root deeper than depth 2 (its depth-2 parent is out of scope).
    With neither (a flat repo), depth-1 folders containing code, or "." when code sits only at root.
    """
    units = {f["path"] for f in folders if f["path"].count("/") == 1}
    for s in scopes or []:
        if s.count("/") >= 2 and not any(s.startswith(u + "/") for u in units):
            units.add(s)
    if not units:
        units = {f["path"] for f in folders if "/" not in f["path"] and f["path"] != "."}
    if not units and any(f["path"] == "." for f in folders):
        units = {"."}
    return sorted(units)


def _folder_files(files: List[FileInfo], folder: str) -> List[FileInfo]:
    if folder == ".":
        return [f for f in files if f.lang is not None]
    return [f for f in files if f.rel.startswith(folder + "/") and f.lang is not None]


def rule_console_logging_only(files: List[FileInfo], units: List[str],
                              texts: Optional[Dict[str, str]] = None) -> List[Signal]:
    """Folder unit (routes or >= 100 LOC) that logs only via console.log/print/fmt.Println.

    False-positive risks: a logger configured in another folder or injected by the platform,
    CLIs/scripts where printing is the interface, and logger imports written in unusual
    forms (dynamic require, vendored wrapper) go undetected.
    """
    out: List[Signal] = []
    for folder in units:
        code = [f for f in _folder_files(files, folder) if not f.is_test and _kind(f)]
        if not code:
            continue
        has_routes = bool(find_routes(code))
        if not (has_routes or sum(f.loc for f in code) >= 100):
            continue
        stripped = {f.rel: (texts[f.rel] if texts and f.rel in texts
                            else strip_comments(_read(f), f.lang or "")) for f in code}
        if any(_LOG_LIB_RE.search(t) for t in stripped.values()):
            continue
        if any(_BARE_LOG_RE.search(t) for t in stripped.values()):
            out.append(Signal("console-logging-only", "Observability", "Low", folder, 0,
                              "only console/print logging in " + folder, "low"))
    return out


def rule_no_tests(files: List[FileInfo], units: List[str],
                  all_files: Optional[List[FileInfo]] = None) -> List[Signal]:
    """Folder unit with non-test code and no test file beneath it.

    A depth-1 or "." unit also counts as tested when the repo has a top-level tests/, test/ or
    __tests__/ folder containing test files.
    False-positive risks: tests kept in a sibling package are not seen; folders of generated,
    config or migration code have no tests by design.
    """
    top_tests = any(f.is_test and f.lang is not None and f.rel.split("/")[0] in _TOP_TEST_DIRS
                    for f in (all_files if all_files is not None else files))
    out: List[Signal] = []
    for folder in units:
        inside = _folder_files(files, folder)
        if not inside or any(f.is_test for f in inside) or all(f.is_test for f in inside):
            continue
        if "/" not in folder and top_tests:
            continue
        out.append(Signal("no-tests", "Maintainability", "Medium", folder, 0,
                          "no test files under " + folder, "medium"))
    return out


# ---------------------------------------------------------------- driver
FILE_RULES: List[Callable[[FileInfo, str], List[Signal]]] = [
    rule_http_no_timeout, rule_query_in_loop, rule_unbounded_query, rule_sql_string_concat,
    rule_retry_no_backoff, rule_swallowed_error,
]


def run_rules(files: List[FileInfo], folders: List[dict], scopes: Optional[List[str]] = None,
              all_files: Optional[List[FileInfo]] = None) -> List[Signal]:
    """Run every rule. `folders` are candidate folders of `files`; `scopes` are the scan scope
    roots; `all_files` is the unscoped walk (used to find a top-level tests folder)."""
    signals: List[Signal] = []
    texts: Dict[str, str] = {}
    for fi in files:
        if fi.is_config:
            raw = _read(fi)
            text = raw if fi.ext == ".json" else strip_comments(raw, "Python")
            signals.extend(rule_hardcoded_secret(fi, text))
            continue
        if fi.lang is None or fi.lang in _SKIP_LANGS:
            continue
        text = strip_comments(_read(fi), fi.lang)
        texts[fi.rel] = text
        if not fi.is_test:
            for rule in FILE_RULES:
                signals.extend(rule(fi, text))
        signals.extend(rule_hardcoded_secret(fi, text))
    units = folder_units(folders, scopes)
    signals.extend(rule_console_logging_only(files, units, texts))
    signals.extend(rule_no_tests(files, units, all_files))
    return signals
