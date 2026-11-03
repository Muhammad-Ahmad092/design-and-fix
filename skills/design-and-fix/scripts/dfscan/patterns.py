from __future__ import annotations

import bisect
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from .imports import _read
from .masking import SECRET_KEY, SECRET_WORDS, mask_secrets  # noqa: F401  (re-export)
from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .routes import find_routes
from .text import strip_comments, strip_config_comments

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
    if fi.ext == ".rb":
        return "rb"
    return None


# Kinds the loop/query rules understand (Ruby gets only the per-language rules: http-no-timeout,
# sql-string-concat, swallowed-error, hardcoded-secret).
_CORE_KINDS = ("py", "js", "go", "c")


_MIGRATION_RE = re.compile(r"(?:^|/)(?:migrations|alembic/versions)/")


def _is_migration(fi: FileInfo) -> bool:
    """Schema migrations run once, in order: query-shape rules do not apply to them."""
    return bool(_MIGRATION_RE.search(fi.rel))


_LINE_STARTS = [None, None]  # type: List[object]  # [text, offsets]: one-entry cache, held strongly


def line_starts(text: str) -> List[int]:
    """Offsets where each line of `text` starts (cached for the most recent text object)."""
    if _LINE_STARTS[0] is not text:
        starts = [0]
        find = text.find
        i = find("\n")
        while i >= 0:
            starts.append(i + 1)
            i = find("\n", i + 1)
        _LINE_STARTS[0], _LINE_STARTS[1] = text, starts
    return _LINE_STARTS[1]  # type: ignore[return-value]


def _line_of(text: str, pos: int) -> int:
    """1-based line number of offset `pos` (bisect over cached line starts, not a count from 0)."""
    return bisect.bisect_right(line_starts(text), pos)


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



# ---------------------------------------------------------------- snippet masking
# mask_secrets lives in masking.py (Signal masks long snippets before truncating them); it is
# re-exported here for callers that import it from patterns.


# ---------------------------------------------------------------- http-no-timeout
_FETCH_RE = re.compile(r"(?<![\w.$])fetch\(")
_AXIOS_RE = re.compile(r"(?<![\w$])axios(?:\.(?:get|post|put|patch|delete|head|options|request))?\(")
_PYHTTP_RE = re.compile(r"\b(?:requests|httpx)\.(?:get|post|put|patch|delete|head|options|request)\(")
_GO_CALL_RE = re.compile(r"\bhttp\.(?:Get|Post|PostForm|Head)\(|\bhttp\.DefaultClient\b")
_GO_CLIENT_RE = re.compile(r"&http\.Client\{")
# Java/Kotlin: java.net.http HttpClient, Spring RestTemplate, OkHttp.
_JAVA_HTTPCLIENT_RE = re.compile(r"\bHttpClient\s*\.\s*new(HttpClient|Builder)\s*\(\s*\)")
_JAVA_REST_RE = re.compile(r"(?<![\w.])(?:new\s+)?RestTemplate\s*\(\s*\)")
_JAVA_OKHTTP_RE = re.compile(r"(?<![\w.])(?:new\s+)?OkHttpClient\s*(?:\.\s*Builder\s*)?\(\s*\)")
_CS_HTTPCLIENT_RE = re.compile(r"\bnew\s+HttpClient\b\s*[({]")
_RB_NET_HTTP_RE = re.compile(r"\bNet::HTTP\s*\.\s*(?:get|get_response|post|post_form|start|new)\b")
_PHP_CURL_RE = re.compile(r"\bcurl_exec\s*\(")
_PHP_FGC_RE = re.compile(r"\bfile_get_contents\s*\(\s*['\"]https?://", re.I)


def _statement_at(text: str, pos: int) -> str:
    """Text from pos to the end of its statement: the first `;`, a `.build()` or ten lines."""
    seg = "\n".join(text[pos:].split("\n", CALL_MAX_LINES)[:CALL_MAX_LINES])
    semi = seg.find(";")
    seg = seg if semi < 0 else seg[:semi]
    build = seg.find(".build()")
    return seg if build < 0 else seg[:build + 8]


def _jvm_no_timeout(text: str) -> List[int]:
    """Positions of Java/Kotlin HTTP clients built without a timeout."""
    out: List[int] = []
    request_timeout = re.search(r"\.\s*timeout\s*\(", text) is not None
    for m in _JAVA_HTTPCLIENT_RE.finditer(text):
        if m.group(1) == "Builder" and re.search(r"connectTimeout\s*\(", _statement_at(text, m.start())):
            continue
        if not request_timeout:
            out.append(m.start())
    if not re.search(r"set(?:Connect|Read)Timeout|setRequestFactory", text):
        out.extend(m.start() for m in _JAVA_REST_RE.finditer(text))
    for m in _JAVA_OKHTTP_RE.finditer(text):
        if not re.search(r"[tT]imeout\s*\(", _statement_at(text, m.start())):
            out.append(m.start())
    return out


def rule_http_no_timeout(fi: FileInfo, text: str) -> List[Signal]:
    """Outbound HTTP call with no timeout/signal.

    Java/Kotlin: HttpClient.newHttpClient()/newBuilder() with no connectTimeout() in the builder
    and no request .timeout() in the file, `new RestTemplate()` with no set*Timeout/
    setRequestFactory in the file, OkHttpClient()/OkHttpClient.Builder() with no *Timeout() in
    the statement. C#: `new HttpClient(...)` with no Timeout in the statement or file. Ruby:
    Net::HTTP calls in a file that never sets read_timeout. PHP: curl_exec in a file without
    CURLOPT_TIMEOUT, file_get_contents('http...') in a file without a 'timeout' context option.
    False-positive risks: a timeout configured elsewhere (axios.create defaults, a shared
    session, a wrapper or a context deadline) is invisible here; `requests.get(...)` with
    **kwargs forwarding timeout is flagged. Library defaults are ignored (OkHttp, .NET HttpClient
    and Ruby Net::HTTP have finite default timeouts that are often too long, so those are leads).
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
    elif fi.ext in (".java", ".kt"):
        for pos in sorted(_jvm_no_timeout(text)):
            emit(pos)
    elif fi.ext == ".cs":
        if not re.search(r"\.\s*Timeout\s*=", text):
            for m in _CS_HTTPCLIENT_RE.finditer(text):
                if "Timeout" not in _statement_at(text, m.start()):
                    emit(m.start())
    elif kind == "rb":
        if "read_timeout" not in text:
            for m in _RB_NET_HTTP_RE.finditer(text):
                emit(m.start())
    elif fi.ext == ".php":
        if not re.search(r"\bCURLOPT_TIMEOUT(?:_MS)?\b", text):
            for m in _PHP_CURL_RE.finditer(text):
                emit(m.start())
        if not re.search(r"['\"]timeout['\"]\s*=>", text):
            for m in _PHP_FGC_RE.finditer(text):
                emit(m.start())
    return out


# ---------------------------------------------------------------- loops (shared)
# A Capitalised receiver must contain a lowercase letter (User, OrderModel) and not follow a dot,
# so constants (SELECT2_TRANSLATIONS.get) and attributes (request.META.get) do not count.
_DB_RECEIVER = (r"(?:\b(?:db|conn|connection|session|cursor|repo|repository|knex|collection|objects"
                r"|prisma(?:\.\w+)?)|\b\w*(?:Repository|Repo|Model)|(?<![\w.])[A-Z][A-Z0-9_]*[a-z]\w*)")  # one split point: linear time
_DB_CALL_RE = re.compile(
    r"\.(?:query|execute|findOne|findMany)\(|session\.query|objects\.(?:get|filter)|db\.Query|"
    + _DB_RECEIVER + r"\.(?:get|filter|find|findById)\(")
_BRACE_LOOP_RE = re.compile(r"(?m)^[ \t]*(?:\}[ \t]*)?(?:for|while)\b")
_ITER_CALL_RE = re.compile(r"\.(?:forEach|map)\(")
_PY_LOOP_RE = re.compile(r"^(\s*)(?:async\s+)?(?:for|while)\b.*:\s*$")
# A loop over statements: `for sql in deferred_sql`, `for sql, params in post_actions`.
# Identifiers naming statements: sql, stmts, statements, ddl, scripts, queries, commands,
# migrations, or a _sql/_stmts/_statements/_commands/_queries suffix or sql_ prefix.
_STMT_IDENT_RE = re.compile(
    r"^(?:sql|stmts?|statements?|ddl|scripts?|queries|commands?|migrations?)$"
    r"|_(?:sql|stmts?|statements?|commands?|queries|scripts?)$|^sql_", re.I)
_STMT_LOOP_RE = re.compile(r"\bfor\b(.*)\b(?:in|of)\b(.*)", re.I)


def _statement_loop(head: str) -> bool:
    """`for sql in deferred_sql`, `for sql, params in post_actions`, `for t in statements`:
    the loop variable or the iterable is named like a list of statements."""
    m = _STMT_LOOP_RE.search(head)
    if not m:
        return False
    return any(_STMT_IDENT_RE.search(w) for w in re.findall(r"\w+", m.group(1) + " " + m.group(2)))
_EXEC_IDENT_ARG_RE = re.compile(r"\.(?:execute|query)\(\s*\w+\s*[,)]")
_SQL_BUILDER_ARG_RE = re.compile(r"\.(?:execute|query)\(\s*\*?(?:self\.|this\.)?\w*_sql\(")
_DDL_ARG_RE = re.compile(
    r"\.(?:execute|query)\(\s*[rbuf]?[\"'`]\s*(?:PRAGMA|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT"
    r"|SET\s|VACUUM|ANALYZE)\b", re.I)
_TXN_RE = re.compile(r"transaction\.atomic|\.transaction\(|\$transaction|\bBEGIN\b")

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


def _executes_statement_list(call: str, stmt_loop: bool) -> bool:
    """The loop runs a list of statements (DDL scripts, schema editors), not one query per item:
    in a loop over statements the executed argument is a plain variable; anywhere, the argument is
    a *_sql(...) builder or a DDL/PRAGMA literal. `call` is the call line plus continuation lines."""
    flat = re.sub(r"\(\s+", "(", call)
    if stmt_loop and _EXEC_IDENT_ARG_RE.search(flat):
        return True
    return bool(_SQL_BUILDER_ARG_RE.search(flat) or _DDL_ARG_RE.search(flat))


def rule_query_in_loop(fi: FileInfo, text: str) -> List[Signal]:
    """DB-looking call inside a loop body (N+1 queries).

    Explicit query verbs (.query, .execute, .findOne, .findMany, session.query, objects.get|filter,
    db.Query) always count; generic verbs (.get, .filter, .find, .findById) count only with a DB-like
    receiver (db, conn, session, cursor, repo, prisma.x, knex, collection, objects, *Repository/*Repo/
    *Model, or a CamelCase class such as User.find; ALL_CAPS constants and dotted attributes such as
    request.META do not count). Loops that execute a list of statements (a loop over sql/statements/
    commands executing a plain variable, a *_sql(...) builder or a DDL/PRAGMA literal) and migration
    files are skipped; a loop body that opens a transaction per item (transaction.atomic, BEGIN)
    emits low confidence.
    False-positive risks: a CamelCase receiver can be a non-DB class (Cache.get), a differently named
    DB handle (store.get) is missed, a loop over a handful of items is harmless, and batching built
    from the loop is not distinguished.
    """
    kind = _kind(fi)
    if kind not in _CORE_KINDS or _is_migration(fi):
        return []
    lines = text.split("\n")
    out: List[Signal] = []
    seen = set()
    for header, body in _loops(text, kind):
        head = lines[header - 1] if header <= len(lines) else ""
        stmt_loop = _statement_loop(head)
        conf = "low" if any(_TXN_RE.search(b) for _, b in body) else "medium"
        for k, (lineno, ln) in enumerate(body):
            if lineno in seen or not _DB_CALL_RE.search(ln):
                continue
            call = "\n".join(b for _, b in body[k:k + 3])
            if _executes_statement_list(call, stmt_loop):
                continue
            seen.add(lineno)
            out.append(Signal("query-in-loop", "Scalability", "High", fi.rel, lineno, ln, conf))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- unbounded-query
# FROM must name a table: a derived table `FROM (` is a fragment of a larger, bounded query.
_SELECT_RE = re.compile(r"(?:SELECT\b.*?\bFROM|select\b.*?\bfrom)\s+[\w\"`\[]")
# Linear-time equivalent of _SELECT_RE.finditer (see _select_starts): the lazy `.*?` above rescans
# the rest of the line from every SELECT, which is quadratic on a long line of SELECTs.
_SELECT_WORD_RE = re.compile(r"SELECT\b|select\b")
_FROM_AT_RE = {"S": re.compile(r"(?=\bFROM\s+[\w\"`\[])"), "s": re.compile(r"(?=\bfrom\s+[\w\"`\[])")}
_FROM_TAIL_RE = {"S": re.compile(r"FROM\s+[\w\"`\[]"), "s": re.compile(r"from\s+[\w\"`\[]")}


def _select_starts(text: str) -> List[int]:
    """Start offsets of the matches _SELECT_RE.finditer(text) would return, in linear time."""
    froms = {k: [m.start() for m in rx.finditer(text)] for k, rx in _FROM_AT_RE.items()}
    out: List[int] = []
    pos = 0
    while True:
        m = _SELECT_WORD_RE.search(text, pos)
        if m is None:
            return out
        key = m.group(0)[0]
        cands = froms[key]
        k = bisect.bisect_left(cands, m.end())
        eol = text.find("\n", m.end())
        eol = len(text) if eol < 0 else eol
        if k < len(cands) and cands[k] < eol:
            out.append(m.start())
            pos = _FROM_TAIL_RE[key].match(text, cands[k]).end()
        else:
            pos = m.start() + 1
_LIMIT_RE = re.compile(r"\bLIMIT\b|\bTOP\s*\(?\s*\d|FETCH\s+FIRST|COUNT\s*\(", re.I)
_AGGREGATE_RE = re.compile(r"\b(?:MAX|MIN|SUM|AVG)\s*\(", re.I)
_GROUP_BY_RE = re.compile(r"\bGROUP\s+BY\b", re.I)
# System catalogs / data dictionaries (schema introspection, one row per object).
_CATALOG_RE = re.compile(
    r"\bFROM\s+[\"`\[]?(?:pg_\w+|information_schema\b|sqlite_(?:master|schema|temp_master)\b|dual\b"
    r"|(?:user|all|dba)_(?:tab\w*|views|mviews|ind\w*|cons\w*|sequences|triggers|objects|sdo_\w+"
    r"|col\w*|synonyms|lobs|part\w*)\b|sys\.|mysql\.)", re.I)
# Equality on a primary-key-like column: a single-row lookup.
_KEY_LOOKUP_RE = re.compile(r"\bWHERE\s+(?:[\w\"`]+\.)?[\"`]?(?:id|pk|uuid|oid)[\"`]?\s*=", re.I)
_SINGLE_ROW_RE = re.compile(r"fetchone\(|QueryRow(?:Context)?\(|\.scalar(?:_one)?\(|\.one\(\)|\.first\(\)")
_OBJECTS_ALL_CHAINED_RE = re.compile(
    r"\s*(?:\[|\.(?:delete|update|count|exists|first|last|aggregate|earliest|latest|get|filter"
    r"|exclude|none)\()")
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


def _bounded_select(text: str, pos: int) -> bool:
    """True when the SELECT at pos returns a bounded result or is part of a larger statement."""
    stmt = _statement(text, pos)
    if _LIMIT_RE.search(stmt) or _CATALOG_RE.search(stmt) or _KEY_LOOKUP_RE.search(stmt):
        return True
    if _AGGREGATE_RE.search(stmt) and not _GROUP_BY_RE.search(stmt):
        return True
    prefix = text[text.rfind("\n", 0, pos) + 1:pos]
    if re.search(r"\b(?:UPDATE|INSERT|DELETE)\b", prefix) or prefix.rstrip().endswith("("):
        return True  # subquery inside UPDATE/INSERT/DELETE or a parenthesised expression
    end = pos
    for _ in range(4):  # the statement line plus three more: .fetchone(), QueryRow(...)
        nxt = text.find("\n", end + 1)
        end = len(text) if nxt < 0 else nxt
    return bool(_SINGLE_ROW_RE.search(text[pos:end]))


def rule_unbounded_query(fi: FileInfo, text: str) -> List[Signal]:
    """SELECT without LIMIT/TOP/FETCH FIRST/COUNT, .find({}), findMany without take, objects.all().

    Skipped as bounded: primary-key lookups (WHERE id = ...), single-row fetches (fetchone,
    QueryRow, .first()), aggregates without GROUP BY, system-catalog introspection, subqueries inside
    UPDATE/INSERT/DELETE, derived-table fragments (FROM (...)), findMany with a ...spread argument,
    objects.all() chained into delete/update/count/exists/first/filter/slice, and migration files.
    False-positive risks: small lookup tables, querysets returned lazily for the caller to
    paginate, limits applied by a wrapper, and English prose containing "select ... from".
    """
    kind = _kind(fi)
    if kind not in _CORE_KINDS or _is_migration(fi):
        return []
    out: List[Signal] = []
    for start in _select_starts(text):
        if not _bounded_select(text, start):
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, start, "low"))
    for m in _FIND_ALL_RE.finditer(text):
        out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "medium"))
    for m in _FIND_MANY_RE.finditer(text):
        open_idx = m.end() - 1
        call = text[open_idx:_match_end(text, open_idx, CALL_MAX_LINES) + 1]
        if not re.search(r"\b(?:take|limit)\b|\.\.\.", call):
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "medium"))
    for m in _OBJECTS_ALL_RE.finditer(text):
        if not _OBJECTS_ALL_CHAINED_RE.match(text, m.end()):
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "low"))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- hardcoded-secret
# No `[\w.-]*` prefix before the key word: any prefix was allowed anyway (DB_PASSWORD), and an
# unanchored prefix made the engine rescan every word run from each position (quadratic; a long hex
# blob took minutes). The match starts at the key word, on the same line as the full key.
_SECRET_RE = re.compile(
    SECRET_KEY + r"[\"']?\s*(?:=>|[:=])\s*"
    r"[\"']([^\"'\n]{8,})[\"']",
    re.I)
_SECRET_IGNORE_RE = re.compile(
    r"example|change(?:me|this|it)|placeholder|dummy|xxx|your_|<|\$\{|process\.env|os\.environ", re.I)
# Quoted values that are not credentials: password hashes (bcrypt, argon2, pbkdf2, crypt),
# regex/template text, and dummy values (all digits, one repeated character).
_HASH_VALUE_RE = re.compile(
    r"^\$(?:2[abxy]?|argon2(?:id|i|d)?|pbkdf2[\w-]*|scrypt|[156y])\$|^(?:pbkdf2_\w+|bcrypt\w*|argon2\w*|sha1|md5)\$")
_PATTERN_VALUE_RE = re.compile(r"\[[^\]]*-[^\]]*\]|\{\d+(?:,\d*)?\}|\\[dwsSbB]|\(\?")
_DUMMY_VALUE_RE = re.compile(r"^(?:\d+|(.)\1+)$")
# Config files only: unquoted dotenv `KEY=value` and YAML/INI `key: value` lines.
_UNQUOTED_SECRET_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?[\w.-]*" + SECRET_KEY
    + r"[ \t]*[:=][ \t]*([^\s\"'#]\S{7,})[ \t]*$",  # `#` inside a value is not a comment
    re.I | re.M)
_NON_SECRET_VALUE_RE = re.compile(r"^(?:\d+|true|false|null|none)$|^[$%]", re.I)
# Values that are locations, not credentials: URLs and absolute file paths.
_LOCATION_VALUE_RE = re.compile(r"^(?:https?://|/|[A-Za-z]:\\)", re.I)
_AWS_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")


PRIVATE_KEY_FILE_SNIPPET = "<private key file>"
_PEM_FILE_HEADER_RE = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def rule_private_key_file(fi: FileInfo) -> List[Signal]:
    """A committed private key file (*.pem, *.key, id_rsa*, *.p12): signal "private key committed".

    Only the first 4 KB are inspected for a PRIVATE KEY header (`.p12` bundles are binary and always
    reported); nothing else is read and the snippet never contains key material. False-positive
    risks: test or sample keys, keys that are intentionally public-test-only, `.key` files that are
    not cryptographic keys (those lack the header and are skipped), and `.p12` files that hold only
    certificates.
    """
    try:
        with open(fi.abs, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return []
    if fi.ext != ".p12" and not _PEM_FILE_HEADER_RE.search(head):
        return []
    conf = "low" if fi.is_test else "high"
    return [Signal("hardcoded-secret", "Security", "Critical", fi.rel, 1,
                   PRIVATE_KEY_FILE_SNIPPET, conf)]


def rule_hardcoded_secret(fi: FileInfo, text: str) -> List[Signal]:
    """Credential-looking assignment, AWS access key id or PEM private key header.

    Runs on code, test and config files (.env, YAML, JSON, TOML, INI, properties). In test files only
    the provider formats (AWS key id, PEM header) are reported, with confidence "low": key = value
    matches there are fixtures. Config files also match unquoted `KEY=value` / `key: value` lines.
    Placeholders (example, changeme, changethis, dummy, ...), values containing whitespace (labels
    such as "Password must be ..."), `${...}` references, env lookups, password hashes, regex or
    template text and dummy values (all digits, one repeated character) are skipped. The snippet
    masks the value so the report never repeats the secret.
    Key words: password, passwd, secret, token, api key, access key, client secret, secret access
    key, private key, with `_` or `-` between words (api-key, X-API-Key, aws_secret_access_key).
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

    fixture = fi.is_test and fi.lang is not None  # test code; config files in test folders stay
    for m in ([] if fixture else _SECRET_RE.finditer(text)):
        value = m.group(1)
        if (_SECRET_IGNORE_RE.search(value) or re.search(r"\s", value)
                or _LOCATION_VALUE_RE.search(value) or _HASH_VALUE_RE.search(value)
                or _PATTERN_VALUE_RE.search(value) or _DUMMY_VALUE_RE.match(value)):
            continue
        emit(m.start(), value)
    if fi.is_config:
        for m in _UNQUOTED_SECRET_RE.finditer(text):
            value = m.group(1)
            if (_SECRET_IGNORE_RE.search(value) or _NON_SECRET_VALUE_RE.search(value)
                    or _LOCATION_VALUE_RE.search(value) or _HASH_VALUE_RE.search(value)
                    or _DUMMY_VALUE_RE.match(value)):
                continue
            emit(m.start(1), value)
    for rx, label in ((_AWS_RE, "AWS access key id <redacted>"), (_PEM_RE, "private key header")):
        for m in rx.finditer(text):
            out.append(_sig("hardcoded-secret", "Security", "Critical", fi, text, m.start(), conf, label))
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- sql-string-concat
_SQL_WORDS_RE = re.compile(
    r"\bSELECT\b.*\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\b.*\bSET\b|\bDELETE\s+FROM\b")
_SQL_FIXED_RE = re.compile(r"\bINSERT\s+INTO\b|\bDELETE\s+FROM\b")
_SQL_PAIRS = ((re.compile(r"\bSELECT\b"), re.compile(r"\bFROM\b")),
              (re.compile(r"\bUPDATE\b"), re.compile(r"\bSET\b")))


def _sql_words(line: str) -> bool:
    """Same answer as _SQL_WORDS_RE.search(line) in linear time: a SELECT is followed by a FROM
    iff the first SELECT is (the greedy `.*` form rescans the line from every SELECT)."""
    if _SQL_FIXED_RE.search(line):
        return True
    for head, tail in _SQL_PAIRS:
        m = head.search(line)
        if m is not None and tail.search(line, m.end()):
            return True
    return False
_LOG_LINE_RE = re.compile(
    r"^\s*(?:console\.\w+|print|(?:\w+\.)?(?:log|logger|logging)\w*\.\w+|fmt\.Print\w*"
    r"|System\.(?:out|err)\.print\w*|Console\.Write\w*|error_log)\s*\(|^\s*(?:puts|echo)\b", re.I)
_CONCAT_RE = re.compile(r"[\"'`]\s*\+|\+\s*[\"'`]|(?<!\w)[fF][rR]?[\"'].*\{|\$\{")
# Per-language interpolation/concatenation forms beyond `+` and `${`.
_CONCAT_BY_EXT = {
    ".java": re.compile(r"\bString\.format\s*\(|\.formatted\s*\("),
    ".kt": re.compile(r"\"[^\"\n]*\$\w|\bString\.format\s*\(|\.format\s*\("),
    ".cs": re.compile(r"\$@?\"[^\"\n]*\{|@\$\"[^\"\n]*\{|\bString\.Format\s*\(|\bstring\.Format\s*\("),
    ".php": re.compile(r"[\"']\s*\.\s*\$|\$\w+(?:\[[^\]\n]*\])?\s*\.\s*[\"']"),
    ".rb": re.compile(r"#\{"),
}


def _php_interpolates(line: str) -> bool:
    """A double-quoted PHP string on the line interpolates a variable ("... $id", "{$id}").
    Literals are paired in order, so `"...?", [$id]` (a variable after a string) does not count."""
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in "\"'":
            j = i + 1
            while j < n and line[j] != c:
                j += 2 if line[j] == "\\" else 1
            if c == '"' and re.search(r"\{?\$[A-Za-z_]", line[i + 1:j]):
                return True
            i = j + 1
        else:
            i += 1
    return False
_QUOTE_IDENT_RE = re.compile(r"quote_(?:name|ident\w*)\(|escape_identifier\(|\bqn\(")
_INTERP_NAME_RE = re.compile(r"(?:\$)?\{(\w+)\}")


def _identifiers_only(lines: List[str], idx: int) -> bool:
    """Every value interpolated on lines[idx] is a quoted identifier: the line calls quote_name()
    (or similar), or each {name} was assigned from such a call in the ten lines above."""
    ln = lines[idx]
    if _QUOTE_IDENT_RE.search(ln):
        return True
    names = set(_INTERP_NAME_RE.findall(ln))
    if not names:
        return False
    above = lines[max(0, idx - 10):idx]
    return all(any(re.search(r"\b%s\s*=[^=\n]*(?:" % re.escape(n) + _QUOTE_IDENT_RE.pattern + ")", a)
                   for a in above) for n in names)


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
    Also: Java String.format/.formatted, Kotlin "$x" templates, C# $"{x}" and String.Format,
    PHP `.` concatenation and "$x" interpolation, Ruby "#{x}" interpolation.
    Interpolating only identifiers quoted by quote_name()/quote_ident() emits low confidence.
    False-positive risks: interpolating a constant table name or a placeholder list is flagged.
    Lowercase SQL and statements assembled over several lines can be missed.
    """
    kind = _kind(fi)
    if kind is None:
        return []
    if kind == "js":
        text = _blank_tagged_templates(text)
    extra = _CONCAT_BY_EXT.get(fi.ext)
    out: List[Signal] = []
    lines = text.split("\n")
    for idx, ln in enumerate(lines):
        if (_sql_words(ln)
                and (_CONCAT_RE.search(ln) or (extra is not None and extra.search(ln))
                     or (fi.ext == ".php" and _php_interpolates(ln)))
                and not _LOG_LINE_RE.match(ln)):
            conf = "low" if _identifiers_only(lines, idx) else "medium"
            out.append(Signal("sql-string-concat", "Security", "Critical", fi.rel, idx + 1, ln, conf))
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
    if kind not in _CORE_KINDS:
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
_EXCEPT_RE = re.compile(r"except\b([^:\n]*):\s*pass\b")
_GO_ERR_RE = re.compile(r"(?<![\w.])_\s*=\s*err\b|if\s+err\s*!=\s*nil\s*\{\s*\}")
# Ruby: a rescue clause with an empty body (`rescue => e` then `end`/`ensure`, or `rescue; end`).
_RB_RESCUE_RE = re.compile(r"(?m)^[ \t]*rescue\b([^\n;]*)(?:;|\n)(?:[ \t]*\n)*[ \t]*(?:end|ensure)\b")
_RB_BROAD_EXC = {"StandardError", "Exception"}
_BROAD_EXC = {"Exception", "BaseException"}
# Exceptions that signal an expected condition (lookup miss, missing optional module, end of
# iteration): `except KeyError: pass` is control flow, not a swallowed failure.
_EXPECTED_EXC = {
    "ImportError", "ModuleNotFoundError", "KeyError", "LookupError", "StopIteration",
    "StopAsyncIteration", "DoesNotExist", "ObjectDoesNotExist", "NoReverseMatch", "Resolver404",
}
# Tool directives are not an explanation of intent.
_DIRECTIVE_RE = re.compile(
    r"noqa\b(?::\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)?"
    r"|pylint:\s*disable=[\w-]+(?:\s*,\s*[\w-]+)*"
    r"|type:\s*ignore(?:\[[\w, -]*\])?|nosec\b(?:\s+B\d+(?:\s*,\s*B\d+)*)?"
    r"|pragma:?\s*no\s*cover|eslint-disable(?:-next-line|-line)?(?:\s+[\w/@-]+(?:\s*,\s*[\w/@-]+)*)?"
    r"|@ts-(?:ignore|expect-error)|nolint\b(?::[\w,]+)?",
    re.I)


def _intent_comment(raw: str, text: str) -> bool:
    """True when raw (with comments) differs from text (comments blanked) by a comment that is
    more than a tool directive (# noqa, # type: ignore, eslint-disable, ...)."""
    comment = "".join(r if r != t else " " for r, t in zip(raw, text))
    comment = _DIRECTIVE_RE.sub(" ", comment)
    return bool(re.search(r"[A-Za-z]{2,}", re.sub(r"[#/*]", " ", comment)))


def _except_class(clause: str) -> str:
    """'broad' (bare / Exception), 'expected' (lookup-miss family) or 'narrow' (other named)."""
    clause = re.sub(r"\bas\s+\w+\s*$", "", clause.strip()).strip().strip("()")
    names = [n.strip().split(".")[-1] for n in clause.split(",") if n.strip()]
    if not names or any(n in _BROAD_EXC for n in names):
        return "broad"
    if all(n in _EXPECTED_EXC or n.endswith("DoesNotExist") for n in names):
        return "expected"
    return "narrow"


def rule_swallowed_error(fi: FileInfo, text: str) -> List[Signal]:
    """Empty catch block, `except: pass`, Go `_ = err`, an empty `if err != nil {}` or an empty
    Ruby `rescue` body (Java/Kotlin/C#/PHP `catch (...) {}` and C# `catch {}` use the catch form).
    Ruby: a rescue naming only specific classes (not StandardError/Exception) emits low.

    Python: lookup-miss exceptions (KeyError, AttributeError, IndexError, StopIteration,
    *DoesNotExist, FileNotFoundError, ImportError, ...) are exempt; other named exceptions
    (ValueError, OSError, ...) and a try/except/else emit low confidence and severity Low; only a
    bare `except:` / `except Exception:` stays high. Any language: a comment inside the block or on
    the closing line (documented intent) gives low confidence and severity Low.
    False-positive risks: deliberate best-effort cleanup without a comment under a broad except, or
    an undocumented empty JS/Go catch, is still reported.
    """
    kind = _kind(fi)
    rx = {"js": _CATCH_RE, "c": _CATCH_RE, "py": _EXCEPT_RE, "go": _GO_ERR_RE,
          "rb": _RB_RESCUE_RE}.get(kind or "")
    if rx is None:
        return []
    raw: Optional[str] = None
    lines: Optional[List[str]] = None  # split once per file, not per match
    out: List[Signal] = []
    for m in rx.finditer(text):
        downgrade = False
        if kind == "py":
            cls = _except_class(m.group(1))
            if cls == "expected":
                continue
            if lines is None:
                lines = text.split("\n")
            downgrade = (cls == "narrow" or _has_else(text, m, lines)
                         or _fallback_assignment(text, m, lines))
        elif kind == "rb":
            names = re.findall(r"[A-Z][\w:]*", m.group(1).split("=>")[0])
            downgrade = bool(names) and not any(n.split("::")[-1] in _RB_BROAD_EXC for n in names)
        if raw is None:
            raw = _read(fi)
        eol = text.find("\n", m.end())
        eol = len(text) if eol < 0 else eol
        # Comment stripping keeps offsets, so the raw slice lines up with the match.
        if _intent_comment(raw[m.start():eol], text[m.start():eol]):
            downgrade = True
        sev, conf = ("Low", "low") if downgrade else ("Medium", "high")
        out.append(_sig("swallowed-error", "Reliability", sev, fi, text, m.start(), conf))
    return out


_ASSIGN_RE = re.compile(r"^[ \t]*(\w+)[ \t]*(?::[^=\n]*)?=(?!=)")


def _fallback_assignment(text: str, m: "re.Match[str]", lines: Optional[List[str]] = None) -> bool:
    """`x = None; try: x = f() except ...: pass; if x is None` — the try body is one assignment
    whose fallback value is set before the try or checked after it (an optional value).
    `lines` is the text split on LF, passed in so a file with many excepts is split once."""
    if lines is None:
        lines = text.split("\n")
    idx = _line_of(text, m.start()) - 1
    indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
    t = idx - 1
    while t >= 0 and not re.match(re.escape(indent) + r"try\s*:\s*$", lines[t]):
        if lines[t].strip() and not lines[t].startswith(indent + " ") and \
                not lines[t].startswith(indent + "\t"):
            return False
        t -= 1
    body = [ln for ln in lines[t + 1:idx] if ln.strip()] if t >= 0 else []
    am = _ASSIGN_RE.match(body[0]) if len(body) == 1 else None
    if am is None:
        return False
    name = re.escape(am.group(1))
    before = "\n".join(lines[max(0, t - 15):t])
    after = "\n".join(lines[idx + 1:idx + 16])
    return bool(re.search(r"(?m)^[ \t]*%s[ \t]*(?::[^=\n]*)?=[ \t]*None\b" % name, before)
                or re.search(r"\b%s\s+is\s+(?:not\s+)?None\b" % name, after))


def _has_else(text: str, m: "re.Match[str]", lines: Optional[List[str]] = None) -> bool:
    """The except clause is followed by `else:` at its own indentation (try/except/else)."""
    line_start = text.rfind("\n", 0, m.start()) + 1
    indent = text[line_start:m.start()]
    if indent.strip():
        return False
    if lines is None:
        lines = text.split("\n")
    for ln in lines[_line_of(text, m.end()):]:  # the lines after the one holding the match end
        if ln.strip():
            return re.match(re.escape(indent) + r"else\s*:", ln) is not None
    return False


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
    """Code files under folder, excluding example/demo programs."""
    if folder == ".":
        return [f for f in files if f.lang is not None and not f.is_example]
    return [f for f in files if f.rel.startswith(folder + "/") and f.lang is not None
            and not f.is_example]


def rule_console_logging_only(files: List[FileInfo], units: List[str],
                              texts: Optional[Dict[str, str]] = None) -> List[Signal]:
    """Folder unit (routes or >= 100 LOC) that logs only via console.log/print/fmt.Println.

    False-positive risks: a logger configured in another folder or injected by the platform,
    CLIs/scripts where printing is the interface, and logger imports written in unusual
    forms (dynamic require, vendored wrapper) go undetected.
    """
    out: List[Signal] = []
    for folder in units:
        code = [f for f in _folder_files(files, folder) if not f.is_test and _kind(f) in _CORE_KINDS]
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


_NO_TEST_UNIT_NAMES = {"config", "configs", "conf", "docs", "doc", "migrations",
                       "alembic"}
_CONFIG_FILE_RE = re.compile(
    r"(?:^|[._-])(?:config|conf|settings)\.(?:[cm]?[jt]sx?|py)$|^\.?\w*rc\.[cm]?js$")
_PY_IMPORT_LINE_RE = re.compile(r"^[ \t]*(?:from|import)[ \t]+([\w.]+)", re.M)


_DJANGO_SETTINGS_RE = re.compile(r"^(?:INSTALLED_APPS|DATABASES|MIDDLEWARE|ROOT_URLCONF)\s*=", re.M)


def _django_settings(inside: List[FileInfo]) -> bool:
    """A settings/ folder of Django-style settings modules (not a feature folder named settings)."""
    py = [f for f in inside if f.ext == ".py"]
    return len(py) == len(inside) and any(_DJANGO_SETTINGS_RE.search(_read(f)) for f in py)


def _untestable_unit(folder: str, inside: List[FileInfo]) -> bool:
    """Config, settings, docs and migration folders, folders of only config-named files, and
    folders with only SQL/GraphQL/proto files have no unit tests by design."""
    parts = folder.split("/")
    if any(p in _NO_TEST_UNIT_NAMES for p in parts):
        return True
    if parts[-1] == "settings" and _django_settings(inside):
        return True
    code = [f for f in inside if f.lang not in _SKIP_LANGS]
    return not code or all(_CONFIG_FILE_RE.search(f.rel.rsplit("/", 1)[-1]) for f in code)


class _TestIndex:
    """Test files of the whole scan root, for finding tests that live outside a unit."""

    def __init__(self, files: List[FileInfo]) -> None:
        self.tests = [f for f in files if f.is_test and f.lang is not None]
        self.top = [f for f in self.tests if f.rel.split("/")[0] in _TOP_TEST_DIRS]
        self._imports: Optional[List[Tuple[FileInfo, str]]] = None

    def in_sibling_dirs(self, folder: str) -> bool:
        """Test files in tests/, test/ or __tests__/ next to the unit or next to any ancestor
        below the scan root (backend/app -> backend/tests)."""
        parts = folder.split("/")
        prefixes = ["/".join(parts[:i]) + "/" + d + "/"
                    for i in range(1, len(parts)) for d in _TOP_TEST_DIRS]
        return any(f.rel.startswith(p) for f in self.tests for p in prefixes)

    def top_names_unit(self, folder: str) -> bool:
        """A top-level test names or mirrors the unit (tests/<name>/, tests/test_<name>*,
        <name>.test.*) or imports it (Python `import pkg` / `from pkg.sub`, JS '../<name>/...')."""
        name = folder.rsplit("/", 1)[-1]
        for f in self.top:
            parts = f.rel.split("/")
            base = parts[-1]
            if name in parts[1:-1] or base.startswith(("test_" + name, name + "_test", name + ".")):
                return True
        mod = folder[4:] if folder.startswith("src/") else folder
        dotted = mod.replace("/", ".")
        js_ref = re.compile(r"[\"'][^\"'\n]*/" + re.escape(name) + r"(?:/|[\"'])")
        if self._imports is None:
            self._imports = [(f, _read(f)) for f in self.top]
        for f, text in self._imports:
            if f.ext == ".py":
                if any(m == dotted or m.startswith(dotted + ".")
                       for m in _PY_IMPORT_LINE_RE.findall(text)):
                    return True
            elif js_ref.search(text):
                return True
        return False


def rule_no_tests(files: List[FileInfo], units: List[str],
                  all_files: Optional[List[FileInfo]] = None, deadline: Optional[float] = None,
              timings: Optional[Dict[str, float]] = None) -> List[Signal]:
    """Folder unit with non-test code and no test file for it (spec R2-fp1).

    Tests count when they are inside the unit, or in a tests/, test/ or __tests__/ folder next to the
    unit or next to any of its ancestors below the scan root (looked up in the whole scan root, so
    tests outside --scope count). A top-level tests folder covers a depth-1 unit; for deeper units it
    counts only when a test there names, mirrors or imports the unit, otherwise the signal is
    emitted with confidence low. Config, settings, docs and migration folders, folders of only
    config-named files and example/demo folders are skipped.
    False-positive risks: tests in a sibling package with an unrelated name, generated code.
    """
    index = _TestIndex(all_files if all_files is not None else files)
    out: List[Signal] = []
    for folder in units:
        inside = _folder_files(files, folder)
        if not inside or any(f.is_test for f in inside) or _untestable_unit(folder, inside):
            continue
        if folder != "." and index.in_sibling_dirs(folder):
            continue
        conf = "medium"
        if index.top:
            if "/" not in folder or index.top_names_unit(folder):
                continue
            conf = "low"
        out.append(Signal("no-tests", "Maintainability", "Medium", folder, 0,
                          "no test files under " + folder, conf))
    return out


# ---------------------------------------------------------------- driver
FILE_RULES: List[Callable[[FileInfo, str], List[Signal]]] = [
    rule_http_no_timeout, rule_query_in_loop, rule_unbounded_query, rule_sql_string_concat,
    rule_retry_no_backoff, rule_swallowed_error,
]


def run_rules(files: List[FileInfo], folders: List[dict], scopes: Optional[List[str]] = None,
              all_files: Optional[List[FileInfo]] = None, deadline: Optional[float] = None,
              timings: Optional[Dict[str, float]] = None) -> List[Signal]:
    """Run every rule. `folders` are candidate folders of `files`; `scopes` are the scan scope
    roots; `all_files` is the unscoped walk (used to find a top-level tests folder). Stops early
    when `deadline` (time.perf_counter) passes; `timings` collects seconds per rule."""
    signals: List[Signal] = []
    texts: Dict[str, str] = {}
    for fi in files:
        if deadline is not None and time.perf_counter() > deadline:
            break
        if fi.is_secret_file:
            signals.extend(rule_private_key_file(fi))
            continue
        if fi.is_config:
            raw = _read(fi)
            text = raw if fi.ext == ".json" else strip_config_comments(raw)
            signals.extend(rule_hardcoded_secret(fi, text))
            continue
        if fi.lang is None or fi.lang in _SKIP_LANGS:
            continue
        text = strip_comments(_read(fi), fi.lang)
        texts[fi.rel] = text
        if not fi.is_test:
            for rule in FILE_RULES:
                t0 = time.perf_counter()
                signals.extend(rule(fi, text))
                if timings is not None:
                    timings[rule.__name__] = timings.get(rule.__name__, 0.0) + time.perf_counter() - t0
        t0 = time.perf_counter()
        signals.extend(rule_hardcoded_secret(fi, text))
        if timings is not None:
            timings["rule_hardcoded_secret"] = (timings.get("rule_hardcoded_secret", 0.0)
                                                + time.perf_counter() - t0)
    units = folder_units(folders, scopes)
    signals.extend(rule_console_logging_only(files, units, texts))
    signals.extend(rule_no_tests(files, units, all_files))
    return signals

# ---------------------------------------------------------------- registry
# confidence = what the rule emits on its main path (non-test files, typical match);
# `limitations` names the cases that emit lower confidence or are known false positives/negatives.
_BRACE = ["Python", "JavaScript", "TypeScript", "Go", "Java", "Kotlin", "C#", "PHP"]
_PJTG = ["Python", "JavaScript", "TypeScript", "Go"]
_ALL = _BRACE + ["Ruby"]
for _m in [
    RuleMeta("http-no-timeout", "2", "Reliability", "High", "medium",
             "Outbound HTTP call (fetch, axios, requests/httpx, Go net/http, Java HttpClient/"
             "RestTemplate/OkHttp, C# HttpClient, Ruby Net::HTTP, PHP curl/file_get_contents) "
             "without a timeout or abort signal.",
             "A timeout set on a shared client, axios.create defaults, session, wrapper or context "
             "deadline is not seen; timeout forwarded through **kwargs is flagged. Only the listed "
             "call forms are recognised. Java/C#/Ruby/PHP checks are file- or statement-level (a "
             "timeout set anywhere in the file, e.g. read_timeout, CURLOPT_TIMEOUT, .Timeout =, "
             "counts for every call there); library default timeouts (OkHttp, .NET HttpClient, "
             "Net::HTTP) are not credited and RestTemplateBuilder/IHttpClientFactory clients are "
             "not seen.",
             "Set an explicit timeout on every outbound call or on the shared client.",
             _PJTG + ["Java", "Kotlin", "C#", "Ruby", "PHP"]),
    RuleMeta("query-in-loop", "4", "Scalability", "High", "low",
             "Database-looking call inside a loop body (N+1 queries).",
             "CamelCase receivers can be non-DB classes (Cache.get); ALL_CAPS constants and dotted "
             "attributes (request.META) are not receivers. Differently named DB handles are missed, "
             "loops over a handful of items are harmless, and loop bodies are limited to 30 lines. "
             "Loops executing a list of statements (loop variable or iterable named sql, statements, "
             "commands, *_sql ..., *_sql() builders, DDL/PRAGMA literals) and migration files are "
             "skipped; a deliberate per-item transaction "
             "(transaction.atomic or BEGIN in the loop) emits low confidence.",
             "Batch the lookups (IN query, join, prefetch/eager load) outside the loop.", _BRACE),
    RuleMeta("unbounded-query", "3", "Scalability", "Medium", "low",
             "Query that reads every row: SELECT without LIMIT/TOP/FETCH FIRST/COUNT, find({}), "
             "findMany without take, objects.all().",
             "Plain SELECT and objects.all() emit low confidence. Skipped as bounded: WHERE id/pk = "
             "lookups, fetchone/QueryRow/.first() reads, aggregates without GROUP BY, system-catalog "
             "introspection, subqueries in UPDATE/INSERT/DELETE, FROM (...) fragments, findMany with "
             "a ...spread argument, chained objects.all().delete()/count()/filter() and migration "
             "files. Small lookup tables, querysets returned for the caller to paginate, limits "
             "applied by a wrapper, single-row lookups on other unique columns and prose containing "
             "select ... from are still reported.",
             "Add LIMIT/pagination or stream results.", _BRACE),
    RuleMeta("hardcoded-secret", "7", "Security", "Critical", "low",
             "Credential-like quoted assignment, AWS access key id or PEM private key header in code or config.",
             "Skips values shorter than 8 characters, values with whitespace, URLs and absolute paths, "
             "placeholders (example, changeme, changethis, dummy, ${...}, env lookups), password "
             "hashes (bcrypt, argon2, pbkdf2), regex/template text and dummy values (all digits, one "
             "repeated character). Comments are stripped, so secrets in comments are not seen. "
             "Unquoted values are matched only in config files. In test code files only AWS key ids "
             "and PEM headers are reported (low confidence), so a real credential written as a "
             "test fixture is missed; config files in test folders are still reported (low). Public identifiers and constant names under secret-sounding keys "
             "can still match. PHP/Ruby `'password' => 'value'` pairs count as assignments. "
             "Key words: password, passwd, secret, token, api key, access key, client secret, "
             "secret access key, private key (`_` or `-` between words, any prefix); other key "
             "names are missed. In config files `#` starts a comment only at line start or after "
             "whitespace (dotenv/YAML). Quoted values spanning lines and UTF-16 files are not "
             "matched.",
             "Move the secret to a secret manager or environment, and rotate it.",
             _ALL + ["Config"]),
    RuleMeta("sql-string-concat", "4", "Security", "Critical", "low",
             "SQL built with +, f-strings or untagged template literals on one line (injection lead).",
             "Keywords must be UPPERCASE and on a single line; lowercase SQL, multi-line assembly and "
             "Go fmt.Sprintf are missed. Interpolating identifiers quoted by quote_name()/"
             "quote_ident() emits low confidence; other constant table names or placeholder lists "
             "are flagged. Tagged templates and log lines (incl. System.out, Console.Write, "
             "puts, echo) are skipped. Also read: Java String.format/.formatted, Kotlin \"$x\" "
             "templates, C# $\"{x}\" and String.Format, PHP `.` concatenation and \"$x\" "
             "interpolation, Ruby \"#{x}\" interpolation.",
             "Use parameterised queries or the ORM query builder.", _ALL),
    RuleMeta("retry-no-backoff", "1", "Reliability", "Medium", "low",
             "Loop mentioning retry/retries/attempt around a network call with no sleep or backoff.",
             "Needs a retry, retries or attempt word plus a recognised network call (fetch, axios, "
             "requests/httpx, net/http, urlopen) in the loop; backoff in a helper or HTTP client "
             "library (urllib3 Retry, axios-retry) is not seen; login-attempt loops can match.",
             "Add exponential backoff with jitter and a retry cap.", _PJTG),
    RuleMeta("swallowed-error", "5", "Reliability", "Medium", "low",
             "Empty catch block, except: pass, Go _ = err or empty if err != nil {}.",
             "Python lookup-miss exceptions (KeyError, LookupError, StopIteration, *DoesNotExist, "
             "ImportError) are exempt. Low confidence and severity Low for: other named exceptions "
             "(AttributeError, IndexError, FileNotFoundError, ValueError, OSError, ...), try/except/else, a "
             "one-assignment try whose fallback is set before or checked after, and any block with a "
             "comment (documented intent; tool directives such as # noqa, # type: ignore or "
             "eslint-disable alone do not count). Undocumented broad excepts and empty JS/Go catches used "
             "for best-effort cleanup are still reported, capped at low confidence by the rule registry. Ruby: an empty "
             "rescue body (rescue => e; end) is reported; one naming only specific classes (not "
             "StandardError/Exception) is low.",
             "Log, handle or rethrow the error.", _ALL),
    RuleMeta("console-logging-only", "3", "Observability", "Low", "low",
             "Folder (with routes or at least 100 LOC) that logs only via console.log, print or fmt.Println.",
             "A logger configured in another folder or injected by the platform, CLIs where printing "
             "is the interface, and logger imports in unusual forms are not seen. Example/demo "
             "folders are skipped. Always low confidence.",
             "Adopt a structured logger with levels and request correlation.", _PJTG),
    RuleMeta("no-tests", "4", "Maintainability", "Medium", "low",
             "Folder unit with non-test code and no test file for it.",
             "Tests count inside the unit and in tests/, test/ or __tests__/ next to the unit or any "
             "ancestor below the scan root (also outside --scope). A top-level tests folder covers "
             "depth-1 units; for deeper units it counts only when a test names, mirrors or imports "
             "the unit, otherwise the signal has low confidence. Config, docs, migration, "
             "config-file-only and example folders, and settings/ folders of Django-style settings "
             "modules, are skipped. Tests in an unrelated sibling "
             "package and generated code are not recognised.",
             "Add tests for the folder's critical paths.", _BRACE),
]:
    register(_m)
