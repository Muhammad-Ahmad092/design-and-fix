from __future__ import annotations

import bisect
import json
import math
import os
import re
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from .filecache import FileCache, text_of
from .imports import _read
from .masking import PROVIDER_FORMATS, SECRET_KEY, SECRET_WORDS, mask_secrets  # noqa: F401  (re-export)
from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .routes import find_routes, has_routes as _has_routes
from .taint import find_flows
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
_BRACE_LOOP_RE = re.compile(r"(?m)^[ \t]*(?:\}[ \t]*)?(for|while)\b")
# query-in-loop also reads C#/PHP `foreach` loops (retry-no-backoff keeps the for/while set).
_BRACE_EACH_LOOP_RE = re.compile(r"(?m)^[ \t]*(?:\}[ \t]*)?(for|foreach|while)\b")
_ITER_CALL_RE = re.compile(r"\.(forEach|map)\(")
_PY_LOOP_RE = re.compile(r"^(\s*)(?:async\s+)?(for|while)\b.*:\s*$")


class _LoopSpan(NamedTuple):
    header: int                   # line of the loop keyword (or of the .map/.forEach call)
    body: List[Tuple[int, str]]   # (line number, text) of the body, at most 30 lines
    kind: str                     # for, while, map, forEach, comprehension
    head: str                     # py header line, brace loop (...) text, text before .map(


Loop = Tuple[int, List[Tuple[int, str]]]  # (header line, [(line number, line text)])


def _body_lines(text: str, start: int, end: int) -> List[Tuple[int, str]]:
    first = _line_of(text, start)
    return [(first + k, ln) for k, ln in enumerate(text[start:end].split("\n"))]


def _loop_spans(text: str, kind: str, foreach: bool = False) -> List[_LoopSpan]:
    """Loop bodies (<= 30 lines). Brace-scoped for js/go/c, indentation-scoped for py.
    `foreach` adds C#/PHP `foreach` loops."""
    loops: List[_LoopSpan] = []
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
            loops.append(_LoopSpan(idx + 1, body, m.group(2), ln))
        return loops
    for m in (_BRACE_EACH_LOOP_RE if foreach else _BRACE_LOOP_RE).finditer(text):
        kw_end = m.end()
        eol = text.find("\n", kw_end)
        eol = len(text) if eol < 0 else eol
        if kind == "go":
            brace = text.rfind("{", kw_end, eol)
            head = text[kw_end:brace] if brace >= 0 else ""
        else:
            paren = text.find("(", kw_end, eol)
            if paren < 0:
                continue
            close = _match_end(text, paren, 3)
            head = text[paren + 1:close]
            brace = close + 1
            while brace < len(text) and text[brace] in " \t\r\n":
                brace += 1
            if brace >= len(text) or text[brace] != "{":
                brace = -1
        if brace < 0:
            continue
        end = _match_end(text, brace, LOOP_BODY_MAX_LINES)
        loop_kind = "while" if m.group(1) == "while" else "for"
        loops.append(_LoopSpan(_line_of(text, m.start()), _body_lines(text, brace + 1, end),
                               loop_kind, head))
    if kind != "go":
        for m in _ITER_CALL_RE.finditer(text):
            open_idx = m.end() - 1
            end = _match_end(text, open_idx, LOOP_BODY_MAX_LINES)
            line_start = text.rfind("\n", 0, m.start()) + 1
            loops.append(_LoopSpan(_line_of(text, m.start()), _body_lines(text, open_idx + 1, end),
                                   m.group(1), text[line_start:m.start()]))
    return loops


def _loops(text: str, kind: str) -> List[Loop]:
    """(header line, body) of every for/while loop and .forEach/.map callback (retry-no-backoff)."""
    return [(s.header, s.body) for s in _loop_spans(text, kind)]


# ---------------------------------------------------------------- python docstrings
_PY_QUOTE_RE = re.compile(r"[\"']")
# A line that ends a block header: def/class (or another compound statement whose first statement
# is the string), or the closing line `) -> T:` of a multi-line def signature.
_BLOCK_HEADER_RE = re.compile(
    r"[ \t]*(?:(?:async[ \t]+)?(?:def|class|if|elif|else|for|while|try|except|finally|with)\b|[)\]])")


_NON_NL_RE = re.compile(r"[^\n]")  # blanking keeps newlines and offsets
_DOCSTRINGS_LAST = [None, None]  # type: List[object]  # [text, result]: one entry, held strongly


def _blank_py_docstrings(text: str) -> str:
    """_blank_py_docstrings_once, memoised for the most recent text object (the file rules of
    one file share its stripped text)."""
    if _DOCSTRINGS_LAST[0] is not text:
        _DOCSTRINGS_LAST[0], _DOCSTRINGS_LAST[1] = text, _blank_py_docstrings_once(text)
    return _DOCSTRINGS_LAST[1]  # type: ignore[return-value]


def _blank_py_docstrings_once(text: str) -> str:
    """Blank the contents of Python docstrings (a triple-quoted string that opens the file, or
    starts the line after a block header ending in `:`), keeping quotes, newlines and offsets.
    Linear scan; strings passed to execute(), assigned to a name or used as a dict value
    (`{"q": \"\"\"SELECT ...\"\"\"}`) are kept. Input is comment-stripped text."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        m = _PY_QUOTE_RE.search(text, i)
        if m is None:
            break
        q = m.start()
        if text[q:q + 3] in ('"""', "'''"):
            close = text.find(text[q:q + 3], q + 3)
            close = n if close < 0 else close
            k = q - 1
            while k >= 0 and text[k] in "rRbBuUfF" and q - k <= 2:
                k -= 1
            while k >= 0 and text[k] in " \t\r\n":
                k -= 1
            if k < 0 or (text[k] == ":" and "\n" in text[k:q] and _BLOCK_HEADER_RE.match(
                    text, text.rfind("\n", 0, k) + 1)):
                out.append(text[i:q + 3])
                out.append(_NON_NL_RE.sub(" ", text[q + 3:close]))
                out.append(text[close:close + 3])
                i = min(close + 3, n)
            else:
                out.append(text[i:min(close + 3, n)])
                i = min(close + 3, n)
            continue
        j = q + 1
        while j < n and text[j] != text[q] and text[j] != "\n":
            j += 2 if text[j] == "\\" else 1
        j = min(j + 1, n)
        out.append(text[i:j])
        i = j
    out.append(text[i:])
    return "".join(out)


# ---------------------------------------------------------------- query-in-loop
# DB/ORM calls, recognised by receiver and method. Every pattern is a word-anchored receiver, a
# literal dot and a method alternation: no nested quantifiers, linear time.
_DB_HANDLE = (r"\b(?:db|database|conn|connection|cursor|cur|session|pool|knex|stmt|pstmt|statement"
              r"|tx|txn|repo|repository|dao|collection|em|entityManager|jdbcTemplate"
              r"|\w*(?:Repository|Repo|Dao|DAO|Cursor|Conn|Connection|Collection|Pool|Statement|Stmt"
              r"|JdbcTemplate|Db|DB))")
_REPO_HANDLE = r"\b(?:db|repo|repository|dao|em|entityManager|\w*(?:Repository|Repo|Dao|DAO))"
_CAMEL = r"(?<![\w.$])[A-Z][A-Z0-9_]*[a-z]\w*"  # User, OrderModel; not CONSTANTS or a.b.Attr
_DB_CALL_RES = [
    # cursor.execute, db.query, session.query, pool.query, Go db.Query/Exec, JDBC stmt.execute,
    # repository/collection find*, jdbcTemplate.query*, entityManager.createQuery
    re.compile(_DB_HANDLE + r"\.(?:query|queryRow|execute|executeQuery|executeUpdate|exec|raw|find"
               r"|findOne|findOneBy|findBy|findAll|findById|findByPk|findMany|findUnique|findFirst"
               r"|findAndCount|findOneAndUpdate|insertOne|updateOne|deleteOne|replaceOne"
               r"|countDocuments|aggregate|Query|QueryRow|QueryContext|QueryRowContext|Exec"
               r"|ExecContext|First|Find|Take|Raw|queryForObject|queryForList"
               r"|queryForMap|createQuery|createNativeQuery|scalar|scalars)\("),
    # repository verbs that are generic elsewhere (get, save, update, delete, count)
    re.compile(_REPO_HANDLE + r"\.(?:get|getOne|getById|getReference|save|saveAndFlush|update"
               r"|delete|deleteById|remove|count|exists|existsById|insert|upsert|filter|persist"
               r"|merge)\("),
    # Django managers: Model.objects.get/filter/...
    re.compile(r"\b(?:[A-Za-z_]\w*\.)?objects\.(?:get|filter|exclude|get_or_create|update_or_create"
               r"|create|count|exists|first|last|values|values_list|aggregate|annotate|update|delete"
               r"|raw|all|latest|earliest|select_related|prefetch_related|only|defer|order_by)\("),
    # SQLAlchemy session.get(Model, id); Flask-SQLAlchemy Model.query.get/filter
    re.compile(r"\bsession\.get\((?=\s*[A-Z])"),
    re.compile(_CAMEL + r"\.query\.(?:get|filter|filter_by|all|first|one|get_or_404|first_or_404"
               r"|order_by)\("),
    # Prisma: prisma.user.findUnique, ctx.db.post.findMany, tx.order.update
    re.compile(r"\b(?:prisma|db|tx)\.\w+\.(?:findUnique|findUniqueOrThrow|findFirst|findFirstOrThrow"
               r"|findMany|create|update|upsert|delete|count|aggregate|groupBy)\("),
    # Sequelize / Mongoose / TypeORM entity statics: User.findByPk, Order.findOne
    re.compile(_CAMEL + r"\.(?:find|findOne|findOneBy|findBy|findAll|findById|findByPk"
               r"|findOneAndUpdate|findOrFail|findUnique|countDocuments)\("),
    # EF Core: _context.Orders.Find(id), db.Users.FirstOrDefault(...)
    re.compile(r"\b\w*(?:[Cc]ontext|[Dd]b)\.[A-Z]\w*\.(?:Find|FindAsync|First|FirstAsync|FirstOrDefault"
               r"|FirstOrDefaultAsync|Single|SingleAsync|SingleOrDefault|SingleOrDefaultAsync|Where"
               r"|Any|AnyAsync|Count|CountAsync|ToList|ToListAsync|Include)\("),
    re.compile(r"\bknex\("),
    # Laravel: DB::table, User::find/where; PDO/mysqli: $pdo->query, $stmt->execute
    re.compile(r"(?<![\w\\$])DB::(?:table|select|insert|update|delete|statement)\("),
    re.compile(r"(?<![\w\\$])[A-Z]\w*::(?:find|findOrFail|findMany|where|firstWhere|firstOrCreate"
               r"|updateOrCreate)\("),
    # Go sqlx: db.Get / db.Select (sync.Pool.Get and friends are not DB calls)
    re.compile(r"\b(?:db|tx|\w*(?:DB|Db|Tx))\.(?:Get|Select|GetContext|SelectContext)\("),
]
# PHP: $pdo->query, $this->db->query, $stmt->execute; the receiver must name a DB handle.
_PHP_CALL_RE = re.compile(r"(\$\w+(?:->\w+)?)->(?:query|execute|exec)\(")
_PHP_DB_WORDS = ("db", "pdo", "conn", "stmt", "mysqli")
# Python only: peewee-style User.get(...) / User.select().
_PY_CAMEL_CALL_RE = re.compile(_CAMEL + r"\.(?:get|get_by_id|get_or_none|select)\(")
# Django related manager on the loop variable: `for o in orders: o.items.all()`.
# QuerySet reads only (all()/count()/exists() take no arguments): dict .get/.values and
# list .count(x) are not matched; private attributes (__dict__, _cache) are not relations.
_RELATED_METHODS = r"(?:(all|count|exists)\(\)|(filter|exclude)\()"
_PREFETCH_SKIPPED = ("all", "count", "exists")
_EAGER_RE = re.compile(r"prefetch_related|select_related|joinedload|selectinload|subqueryload"
                       r"|contains_eager")
# Batched forms: an IN list / ANY array / $in / in: [...] / .in_() fetches a set per call.
_BATCH_ARG_RE = re.compile(r"__in\s*=|\bIN\s*\(|\bANY\s*\(|\$in\b|\bIn\(|\bin:\s|\bwhereIn\(|\.in_\(")
# Loops over schema objects (fields, columns, tables, constraints ...) are bounded by the schema.
_SCHEMA_IDENT_RE = re.compile(
    r"^(?:_meta|metadata|schema|schemas|fields|local_fields|concrete_fields|local_many_to_many"
    r"|columns|indexes|indices|constraints|tables|table_names|relations|get_models|get_fields"
    r"|sequences|triggers|foreign_keys|table_info|index_info)$"
    r"|_(?:fields|columns|tables|indexes|indices|constraints|relations|checks)$")
# Loops over batches/chunks/pages already batch the work.
_BATCH_IDENT_RE = re.compile(r"^(?:chunks?|chunked|batch(?:es)?|batched|pages)$|_(?:chunks|batch(?:es)?|pages)$")
SMALL_LOOP_MAX = 20  # a literal bound or literal collection of at most this many items
# A loop over statements: `for sql in deferred_sql`, `for sql, params in post_actions`.
# Identifiers naming statements: sql, stmts, statements, ddl, scripts, queries, commands,
# migrations, or a _sql/_stmts/_statements/_commands/_queries suffix or sql_ prefix.
_STMT_IDENT_RE = re.compile(
    r"^(?:sql|stmts?|statements?|ddl|scripts?|queries|commands?|migrations?)$"
    r"|_(?:sql|stmts?|statements?|commands?|queries|scripts?)$|^sql_", re.I)
_STMT_LOOP_RE = re.compile(r"\bfor\b(.*)\b(?:in|of)\b(.*)", re.I)
_EXEC_IDENT_ARG_RE = re.compile(r"\.(?:execute|query)\(\s*\w+\s*[,)]")
_SQL_BUILDER_ARG_RE = re.compile(r"\.(?:execute|query)\(\s*\*?(?:self\.|this\.)?\w*_sql\(")
_DDL_ARG_RE = re.compile(
    r"\.(?:execute|query)\(\s*[rbuf]?(?:\"\"\"|'''|[\"'`])\s*(?:PRAGMA|CREATE|ALTER|DROP|TRUNCATE"
    r"|GRANT|REVOKE|COMMENT|SET\s|VACUUM|ANALYZE)\b", re.I)
_TXN_RE = re.compile(r"transaction\.atomic|\.transaction\(|\$transaction|\bBEGIN\b")
# The labelled deliberate case: `with transaction.atomic():` around one write keyed by id/pk.
_KEYED_WRITE_RE = re.compile(r"\b(?:id|pk)\s*=.*\.(?:update|delete|save)\(")
# A cache consulted in the loop body before the DB call, or a memoised enclosing function.
_CACHE_CHECK_RE = re.compile(r"\bcache\.get(?:_or_set)?\(|\bredis\.get\(|\bmemo\w*\[|\bin\s+(?:self\.)?\w*cache\b",
                             re.I)
_MEMO_DECORATOR_RE = re.compile(r"^\s*@(?:functools\.)?(?:lru_cache|cache|cached|memoize)\b")
_COUNTED_BOUND_RE = re.compile(r"<=?\s*(\d+)\s*;")
_RANGE_RE = re.compile(r"range\(\s*(?:-?\d+\s*,\s*)?(\d+)\s*\)")
_LOOP_TARGETS_RE = re.compile(r"\w+(?:\s*,\s*\w+)*")
_SINGLE_COLON_RE = re.compile(r"(?<!:):(?!:)")
_LOOP_LABELS = {"for": "for loop", "while": "while loop", "map": "map callback",
                "forEach": "forEach callback", "comprehension": "comprehension"}


def _statement_loop(head: str) -> bool:
    """`for sql in deferred_sql`, `for sql, params in post_actions`, `for t in statements`:
    the loop variable or the iterable is named like a list of statements."""
    m = _STMT_LOOP_RE.search(head)
    if not m:
        return False
    return any(_STMT_IDENT_RE.search(w) for w in re.findall(r"\w+", m.group(1) + " " + m.group(2)))


def _executes_statement_list(call: str, stmt_loop: bool) -> bool:
    """The loop runs a list of statements (DDL scripts, schema editors), not one query per item:
    in a loop over statements the executed argument is a plain variable; anywhere, the argument is
    a *_sql(...) builder or a DDL/PRAGMA literal. `call` is the call text up to its closing paren."""
    flat = re.sub(r"\(\s+", "(", call)
    if stmt_loop and _EXEC_IDENT_ARG_RE.search(flat):
        return True
    return bool(_SQL_BUILDER_ARG_RE.search(flat) or _DDL_ARG_RE.search(flat))


def _comprehension_spans(lines: List[str]) -> List[_LoopSpan]:
    """Single-line Python comprehensions: the part of the line before ` for x in ` is the body."""
    spans: List[_LoopSpan] = []
    for idx, ln in enumerate(lines):
        if _PY_LOOP_RE.match(ln):
            continue
        pos = ln.find(" for ")
        while pos >= 0:
            j = ln.find(" in ", pos + 5, pos + 5 + 80)  # bounded: linear on long lines
            if j > pos + 5 and _LOOP_TARGETS_RE.fullmatch(ln[pos + 5:j].strip()):
                spans.append(_LoopSpan(idx + 1, [(idx + 1, ln[:pos])], "comprehension",
                                       "for " + ln[pos + 5:]))
                break
            pos = ln.find(" for ", pos + 5)
    return spans


def _loop_targets_and_iterable(sp: _LoopSpan) -> Tuple[List[str], str]:
    """Loop variable names and the iterable expression ("" for while and counted loops)."""
    if sp.kind in ("while", "map", "forEach"):
        return [], ""
    h = sp.head.strip()
    if sp.kind == "comprehension" or h.startswith(("for ", "async for ")):
        h = h[h.find("for ") + 4:]
        if h.endswith(":"):
            h = h[:-1]
        j = h.find(" in ")
        if j < 0:
            return [], ""
        it = h[j + 4:]
        if sp.kind == "comprehension":
            it = re.split(r"[\]\)\}]| if ", it, maxsplit=1)[0]
        return re.findall(r"\w+", h[:j]), it.strip()
    if ";" in h:
        return [], ""
    j = h.find(" as ")
    if h.startswith("$") and j > 0:  # PHP foreach ($items as $item)
        return re.findall(r"\w+", h[j + 4:]), h[:j].strip()
    for sep in (" of ", " in ", "range "):
        j = h.find(sep)
        if j >= 0:
            return re.findall(r"\w+", h[:j]), h[j + len(sep):].strip()
    m = _SINGLE_COLON_RE.search(h)  # Java/Kotlin for (T x : xs)
    if m:
        return re.findall(r"\w+", h[:m.start()])[-1:], h[m.end():].strip()
    return [], ""


def _small_literal(expr: str) -> bool:
    """A literal tuple/list/set/array of at most SMALL_LOOP_MAX flat items."""
    expr = expr.strip()
    pairs = {"(": ")", "[": "]", "{": "}"}
    if len(expr) < 2 or pairs.get(expr[0]) != expr[-1]:
        return False
    inner = expr[1:-1]
    if any(c in inner for c in "()[]{}"):
        return False
    return len([x for x in inner.split(",") if x.strip()]) <= SMALL_LOOP_MAX


def _fixed_loop(sp: _LoopSpan, iterable: str) -> bool:
    """A small loop with a fixed iteration count: `range(3)`, `i < 5;`, `("a", "b")`, `[x, y].map`."""
    if sp.kind == "while":
        return False
    if sp.kind in ("map", "forEach"):
        head = sp.head.rstrip()
        if not head.endswith("]"):
            return False
        k = head.rfind("[")
        return k >= 0 and (k == 0 or not (head[k - 1].isalnum() or head[k - 1] in "_)]$")) \
            and _small_literal(head[k:])
    if ";" in sp.head:
        m = _COUNTED_BOUND_RE.search(sp.head)
        return bool(m) and int(m.group(1)) <= SMALL_LOOP_MAX
    m = _RANGE_RE.fullmatch(iterable)
    if m:
        return int(m.group(1)) <= SMALL_LOOP_MAX
    return _small_literal(iterable)


def _literal_in_code(iterable: str, header: int, lines: List[str]) -> bool:
    """The iterable is a name bound to a collection literal in the 30 lines above the loop and
    neither grown nor rebound before it (`tasks = [...]` / `const roles = {...}`): its size is
    fixed in code."""
    if not re.fullmatch(r"\w+", iterable):
        return False
    lo = max(0, header - 1 - LOOP_BODY_MAX_LINES)
    assign = re.compile(r"^\s*(?:const\s+|let\s+|var\s+)?" + iterable + r"\s*=\s*[\[{]")
    grow = re.compile(r"\b" + iterable + r"(?:\.(?:append|extend|add|push|insert|update|unshift)\(|"
                      r"\s*\+=|\[[^\]\n]*\]\s*=[^=])|\bfor\b")
    rebind = re.compile(r"(?<![\w.])" + iterable + r"\s*(?::[^=\n]*)?=(?!=)")
    for k in range(header - 2, lo - 1, -1):
        if assign.match(lines[k]):
            return not (any(grow.search(ln) for ln in lines[k:header - 1])
                        or any(rebind.search(ln) for ln in lines[k + 1:header - 1]))
    return False


def _not_a_data_loop(sp: _LoopSpan, iterable: str, lines: List[str], kind: str) -> bool:
    """Fixed-size loops, collection literals defined in code, and JVM `.map(` calls outside a
    stream (Optional.map runs once)."""
    if _fixed_loop(sp, iterable) or _literal_in_code(iterable, sp.header, lines):
        return True
    if kind == "c" and sp.kind == "map":
        ctx = "\n".join(lines[max(0, sp.header - 4):sp.header])
        return "stream(" not in ctx and "Stream" not in ctx
    return False


def _bounded_loop(iterable: str) -> bool:
    """Loops that are not data-driven N+1: over schema objects or batches."""
    words = re.findall(r"\w+", iterable)
    return any(_SCHEMA_IDENT_RE.search(w) or _BATCH_IDENT_RE.search(w) for w in words)


def _cached_path(sp: _LoopSpan, lineno: int, lines: List[str], kind: str) -> bool:
    """The loop body checks a cache before the call on `lineno`, or (Python) the enclosing def is
    memoised with @lru_cache/@cache/@cached/@memoize."""
    if any(_CACHE_CHECK_RE.search(b) for n, b in sp.body if n <= lineno):
        return True
    if kind != "py" or sp.header > len(lines):
        return False
    head = lines[sp.header - 1]
    indent = len(head) - len(head.lstrip())
    for k in range(sp.header - 2, max(-1, sp.header - 2 - 300), -1):  # bounded look-back
        m = _PY_DEF_RE.match(lines[k])
        if m and len(m.group(1).expandtabs()) < indent:
            j = k - 1
            while j >= 0 and lines[j].lstrip().startswith("@"):
                if _MEMO_DECORATOR_RE.match(lines[j]):
                    return True
                j -= 1
            return False
    return False


def _db_calls(part: str, kind: str, loop_vars: List[str]) -> List[Tuple[int, str, str]]:
    """(offset of the call's "(", call name, related-manager method or "") for DB calls in `part`."""
    found: Dict[int, Tuple[int, str, str]] = {}
    rxs = list(_DB_CALL_RES)
    if kind == "py":
        rxs.append(_PY_CAMEL_CALL_RE)
    for rx in rxs:
        for m in rx.finditer(part):
            found.setdefault(m.end() - 1, (m.end() - 1, part[m.start():m.end() - 1].strip(), ""))
    if kind == "py":
        for v in loop_vars:
            rel = re.compile(r"\b" + re.escape(v) + r"\.(?!_)\w+\." + _RELATED_METHODS)
            for m in rel.finditer(part):
                paren = part.index("(", m.start())
                found.setdefault(paren, (paren, part[m.start():paren], m.group(1) or m.group(2)))
    if kind == "c":
        for m in _PHP_CALL_RE.finditer(part):
            if any(w in m.group(1).lower() for w in _PHP_DB_WORDS):
                found.setdefault(m.end() - 1, (m.end() - 1, part[m.start():m.end() - 1], ""))
    return [found[k] for k in sorted(found)]


def rule_query_in_loop(fi: FileInfo, text: str) -> List[Signal]:
    """DB/ORM call repeated inside a loop body (N+1 queries). Snippet: "<call> inside <loop> at line n".

    Calls are recognised by receiver and method: Django `X.objects.get/filter/...` and related
    managers on the loop variable (`o.items.all()`), SQLAlchemy `session.query/execute/get(Model,`,
    Flask-SQLAlchemy `Model.query.*`, Prisma `prisma.<model>.find*`, TypeORM/Sequelize/Mongoose
    repository and entity methods, `cursor/conn/db/pool.execute|query`, JDBC statements, Go
    `db.Query/Exec`, EF Core `context.Set.Find`, Laravel `DB::`/`Model::find`, PDO. A plain `.get`
    is not a DB call (dicts, requests/HTTP sessions, os.environ, Paths.get); `self.execute` is not
    recognised. The call is attributed to the innermost enclosing loop that is not a small fixed
    loop (python for/while and single-line comprehensions, brace for/foreach/while, .map/.forEach).
    Skipped: loops over schema objects (fields, columns, tables, relations, _meta ...), over
    batches/chunks/pages, Django `transaction.atomic` around a single write keyed by id/pk (the
    documented per-item pattern), small fixed loops (range(<=20), literal collections, `i < 5;`,
    a collection literal bound in code and not grown or rebound), batched calls
    (IN/ANY/__in=/in:), related-manager reads with prefetch_related/select_related/joinedload in
    the 30 lines above the loop, statement lists (sql/statements loops, *_sql() builders,
    DDL/PRAGMA literals), catalog queries (sqlite_master, information_schema, pg_*), Python
    docstrings and migration files.
    The code emits low (instead of medium) for any other transaction in the loop body (BEGIN,
    .transaction(, $transaction) and for cached paths, i.e. a cache checked in the body before the
    call (cache.get, get_or_set, redis.get, memo[...], `in cache`) or a Python def decorated with
    @lru_cache/@cache/@cached/@memoize; cached paths add " (cached path)" to the snippet. The
    registry ceiling (low) caps every query-in-loop signal at low, so only the suffix tells them
    apart.
    False-positive risks: a loop over a handful of items held in a variable and non-DB classes
    with ORM-like static methods. Missed: differently named DB handles, data collections named
    like schema or batches (pages, tables, fields), and lazy FK attribute access (o.customer).
    """
    kind = _kind(fi)
    if kind not in _CORE_KINDS or _is_migration(fi):
        return []
    if kind == "py":
        text = _blank_py_docstrings(text)
    lines = text.split("\n")
    starts = line_starts(text)
    spans = _loop_spans(text, kind, foreach=True)
    if kind == "py":
        spans += _comprehension_spans(lines)
    info = [_loop_targets_and_iterable(sp) for sp in spans]
    # call "(" offset -> (line, name, related method, [enclosing span indexes])
    calls: Dict[int, Tuple[int, str, str, List[int]]] = {}
    # Nested loops share body lines: each (line text, loop variables) is searched once. Only
    # Python's related-manager check reads the loop variables.
    found: Dict[Tuple[str, Tuple[str, ...]], List[Tuple[int, str, str]]] = {}
    for idx, sp in enumerate(spans):
        loop_vars = tuple(info[idx][0]) if kind == "py" else ()
        for lineno, part in sp.body:
            full = lines[lineno - 1] if lineno <= len(lines) else part
            off = full.find(part) if part else 0
            base = starts[lineno - 1] + max(off, 0)
            key = (part, loop_vars)
            hits = found.get(key)
            if hits is None:
                hits = found[key] = _db_calls(part, kind, list(loop_vars))
            for col, name, related in hits:
                entry = calls.setdefault(base + col, (lineno, name, related, []))
                entry[3].append(idx)
    out: List[Signal] = []
    for open_idx in sorted(calls):
        lineno, name, related, idxs = calls[open_idx]
        chosen = -1
        for idx in sorted(idxs, key=lambda i: spans[i].header, reverse=True):
            if not _not_a_data_loop(spans[idx], info[idx][1], lines, kind):
                chosen = idx
                break
        if chosen < 0:
            continue
        sp = spans[chosen]
        if _bounded_loop(info[chosen][1]):
            continue
        conf, suffix = "medium", ""
        if any(_TXN_RE.search(b) for _, b in sp.body):
            own = [c for c in calls.values() if chosen in c[3]]
            if (len(own) == 1 and any("transaction.atomic" in b for _, b in sp.body)
                    and _KEYED_WRITE_RE.search(lines[lineno - 1])):
                continue  # deliberate per-item atomic write (documented pattern)
            conf = "low"
        if _cached_path(sp, lineno, lines, kind):
            conf, suffix = "low", " (cached path)"
        call = text[open_idx - len(name):_match_end(text, open_idx, CALL_MAX_LINES) + 1]
        head_line = lines[sp.header - 1] if sp.header <= len(lines) else ""
        if (_BATCH_ARG_RE.search(call) or _CATALOG_RE.search(call)
                or _executes_statement_list(call, _statement_loop(head_line))):
            continue
        if related in _PREFETCH_SKIPPED and _EAGER_RE.search(
                "\n".join(lines[max(0, sp.header - 1 - LOOP_BODY_MAX_LINES):sp.header])):
            continue
        snippet = "%s inside %s at line %d%s" % (name, _LOOP_LABELS[sp.kind], sp.header, suffix)
        out.append(Signal("query-in-loop", "Scalability", "High", fi.rel, lineno, snippet, conf))
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
# System catalogs / data dictionaries (schema introspection, one row per object), including the
# PostGIS metadata views.
_CATALOG_RE = re.compile(
    r"\bFROM\s+[\"`\[]?(?:pg_\w+|information_schema\b|sqlite_(?:master|schema|temp_master)\b|dual\b"
    r"|(?:user|all|dba)_(?:tab\w*|views|mviews|ind\w*|cons\w*|sequences|triggers|objects|sdo_\w+"
    r"|col\w*|synonyms|lobs|part\w*)\b|sys\.|mysql\.|geometry_columns\b|geography_columns\b"
    r"|raster_columns\b|spatial_ref_sys\b)", re.I)
# Equality on a primary-key-like column: a single-row lookup.
_KEY_LOOKUP_RE = re.compile(r"\bWHERE\s+(?:[\w\"`]+\.)?[\"`]?(?:id|pk|uuid|oid)[\"`]?\s*=", re.I)
_SINGLE_ROW_RE = re.compile(r"fetchone\(|QueryRow(?:Context)?\(|\.scalar(?:_one)?\(|\.one\(\)|\.first\(\)")
_OBJECTS_ALL_CHAINED_RE = re.compile(
    r"\s*(?:\[|\.(?:delete|update|count|exists|first|last|aggregate|earliest|latest|get|filter"
    r"|exclude|none)\()")
_FIND_ALL_RE = re.compile(r"\.find\(\s*\{\s*\}\s*\)")
_FIND_MANY_RE = re.compile(r"\.findMany\(")
_OBJECTS_ALL_RE = re.compile(r"\bobjects\.all\(\)")
_FROM_WORD_RE = re.compile(r"\bFROM[ \t\r\n]+", re.I)
_PY_DEF_RE = re.compile(r"^([ \t]*)(?:async[ \t]+)?def[ \t]")
_REQUEST_PARAM_RE = re.compile(r"\b(?:request|req)\b")
STATEMENT_MAX_CHARS = 4096  # SQL text read around one SELECT (keeps long lines linear)
_QUERY_ROW_RE = re.compile(r"\bQueryRow(?:Context)?\(")
# Keywords each bounding regex needs (upper-cased statement), checked before the regex runs.
_LIMIT_WORDS = ("LIMIT", "TOP", "FETCH", "COUNT")
_AGGREGATE_WORDS = ("MAX", "MIN", "SUM", "AVG")
_CATALOG_WORDS = ("PG_", "INFORMATION_SCHEMA", "SQLITE_", "DUAL", "USER_", "ALL_", "DBA_", "SYS.",
                  "MYSQL.", "GEOMETRY_COLUMNS", "GEOGRAPHY_COLUMNS", "RASTER_COLUMNS", "SPATIAL_REF_SYS")
_SINGLE_ROW_WORDS = ("fetchone(", "QueryRow", ".scalar", ".one()", ".first()")
# DRF / Django class-based list views: `queryset = X.objects.all()` in the class body.
_VIEW_CLASS_RE = re.compile(
    r"^([ \t]*)class[ \t]+\w+[ \t]*\(([^)]*)\)")
_VIEW_BASES_RE = re.compile(
    r"\b(?:ViewSet|ModelViewSet|ReadOnlyModelViewSet|APIView|GenericAPIView|ListAPIView"
    r"|ListCreateAPIView|ListView)\b")
# `pagination_class = None` / `paginate_by = None` (or 0) opts a view out of pagination.
_PAGINATION_RE = re.compile(r"\b(?:pagination_class|paginate_by)\s*=\s*(?!None\b|0\b)\S")
_DEFAULT_PAGINATION_RE = re.compile(r"[\"']DEFAULT_PAGINATION_CLASS[\"']\s*:\s*(?!None\b)\S")


def _statement(text: str, pos: int) -> str:
    """SQL statement text starting at pos: rest of the line, extended over a multi-line string."""
    lines = text[pos:pos + STATEMENT_MAX_CHARS].split("\n", 6)
    stmt = [lines[0]]
    if not re.search(r"[\"'`]", lines[0]):
        for ln in lines[1:6]:
            stmt.append(ln)
            if re.search(r"[\"'`]", ln):
                break
    return "\n".join(stmt).split(";")[0]


def _runtime_table(stmt: str) -> bool:
    """FROM names no table in this string: a placeholder (`%s`, `{t}`, `${t}`, `?`) or the end of
    the string literal ("SELECT * FROM " + name). The query is assembled by generic code."""
    m = _FROM_WORD_RE.search(stmt)
    if m is None:
        return False
    t = stmt[m.end():m.end() + 2]
    c0, c1 = t[:1], t[1:2]
    if c0 and c0 in "%{$?:":
        return True
    if c0 and c0 in "\"'`[":
        if c1 and c1 in "%{$?":
            return True
        return c0 in "\"'" and not (c1.isalnum() or c1 == "_")
    return False


def _bounded_select(text: str, pos: int) -> bool:
    """True when the SELECT at pos returns a bounded result or is part of a larger statement."""
    stmt = _statement(text, pos)
    if _runtime_table(stmt):
        return True
    # Substring pre-checks keep each SELECT cheap on long lines (the regexes run only when their
    # keyword is present in the statement).
    up = stmt.upper()
    if ((any(w in up for w in _LIMIT_WORDS) and _LIMIT_RE.search(stmt))
            or (any(w in up for w in _CATALOG_WORDS) and _CATALOG_RE.search(stmt))
            or ("WHERE" in up and _KEY_LOOKUP_RE.search(stmt))):
        return True
    if (any(w in up for w in _AGGREGATE_WORDS) and _AGGREGATE_RE.search(stmt)
            and not ("GROUP" in up and _GROUP_BY_RE.search(stmt))):
        return True
    line_start = text.rfind("\n", max(0, pos - STATEMENT_MAX_CHARS), pos) + 1
    prefix = text[max(line_start, pos - STATEMENT_MAX_CHARS):pos]
    if "QueryRow" in prefix and _QUERY_ROW_RE.search(prefix):
        return True  # db.QueryRow("SELECT ...") on the same line: one row
    if prefix.rstrip().endswith("(") or (any(w in prefix for w in ("UPDATE", "INSERT", "DELETE"))
                                         and re.search(r"\b(?:UPDATE|INSERT|DELETE)\b", prefix)):
        return True  # subquery inside UPDATE/INSERT/DELETE or a parenthesised expression
    if prefix.strip().startswith(")"):  # `) SELECT ... FROM cte`: the tail of a WITH clause
        lo = line_start
        for _ in range(12):
            if lo == 0:
                break
            lo = text.rfind("\n", 0, lo - 1) + 1
        if _CATALOG_RE.search(text[lo:pos]):
            return True
    end = pos
    for _ in range(4):  # the statement line plus three more: .fetchone(), QueryRow(...)
        nxt = text.find("\n", end + 1)
        end = len(text) if nxt < 0 else nxt
    window = text[pos:min(end, pos + STATEMENT_MAX_CHARS)]
    return any(w in window for w in _SINGLE_ROW_WORDS) and bool(_SINGLE_ROW_RE.search(window))


def _py_request_scopes(lines: List[str]) -> List[bool]:
    """Per line (index i is line i + 1): the innermost enclosing Python def takes a `request`/`req`
    parameter. One forward pass with an indentation stack."""
    flags = [False] * len(lines)
    stack: List[Tuple[int, bool]] = []  # (def indent, takes request)
    k = 0
    while k < len(lines):
        ln = lines[k].expandtabs()
        if not ln.strip():
            flags[k] = stack[-1][1] if stack else False
            k += 1
            continue
        indent = len(ln) - len(ln.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        flags[k] = stack[-1][1] if stack else False
        if _PY_DEF_RE.match(ln):
            end = k
            while end < min(len(lines), k + CALL_MAX_LINES) - 1 and not lines[end].rstrip().endswith(":"):
                end += 1
            for j in range(k + 1, end + 1):  # signature continuation lines
                flags[j] = flags[k]
            stack.append((indent, bool(_REQUEST_PARAM_RE.search("\n".join(lines[k:end + 1])))))
            k = end + 1
            continue
        k += 1
    return flags


def _list_view_class(lines: List[str], lineno: int) -> int:
    """Index of the DRF/Django list-view class whose body (or get_queryset/list method) holds
    line `lineno` (1-based), or -1. Bases must name ViewSet, APIView, ListView ..."""
    cur = lines[lineno - 1].expandtabs()
    indent = len(cur) - len(cur.lstrip())
    for k in range(lineno - 2, max(-1, lineno - 2 - 500), -1):  # bounded look-back
        ln = lines[k].expandtabs()
        if not ln.strip() or len(ln) - len(ln.lstrip()) >= indent:
            continue
        m = _VIEW_CLASS_RE.match(ln)
        if m:
            return k if _VIEW_BASES_RE.search(m.group(2)) else -1
        if re.match(r"[ \t]*(?:async[ \t]+)?def[ \t]+(?:get_queryset|list)\b", ln):
            indent = len(ln) - len(ln.lstrip())
            continue
        return -1
    return -1


def _class_paginated(lines: List[str], k: int) -> bool:
    """The class starting at lines[k] sets pagination_class / paginate_by in its body."""
    head = lines[k].expandtabs()
    indent = len(head) - len(head.lstrip())
    for ln in lines[k + 1:k + 1 + 500]:
        e = ln.expandtabs()
        if e.strip() and len(e) - len(e.lstrip()) <= indent:
            return False
        if _PAGINATION_RE.search(ln):
            return True
    return False


def _settings_paginate(fi: FileInfo, cache: Optional[FileCache] = None) -> bool:
    """A Django settings module (settings.py or settings/*.py) in the file's folder, an ancestor
    up to the scan root, or a direct child of one, sets REST_FRAMEWORK DEFAULT_PAGINATION_CLASS.
    With the scan's `cache` the answer is kept per folder and per settings file."""
    if cache is not None:
        return cache.memo(("drf_paginate", os.path.dirname(fi.abs), fi.rel.count("/")),  # type: ignore
                          lambda: _settings_paginate_uncached(fi, cache))
    return _settings_paginate_uncached(fi, None)


def _settings_file_paginates(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            body = fh.read(1 << 20)
    except OSError:
        return False
    return bool(_DEFAULT_PAGINATION_RE.search(strip_comments(body, "Python")))


def _settings_paginate_uncached(fi: FileInfo, cache: Optional[FileCache]) -> bool:
    folder = os.path.dirname(fi.abs)
    dirs = []
    for _ in range(fi.rel.count("/") + 1):
        dirs.append(folder)
        folder = os.path.dirname(folder)
    candidates: List[str] = []
    for d in dirs:
        try:
            children = [os.path.join(d, c) for c in os.listdir(d)]
        except OSError:
            continue
        for c in [d] + [x for x in children if os.path.isdir(x)]:
            candidates.append(os.path.join(c, "settings.py"))
            sub = os.path.join(c, "settings")
            if os.path.isdir(sub):
                try:
                    candidates.extend(os.path.join(sub, x) for x in os.listdir(sub) if x.endswith(".py"))
                except OSError:
                    pass
    for path in candidates:
        if (cache.memo(("drf_settings_file", path), lambda: _settings_file_paginates(path))
                if cache is not None else _settings_file_paginates(path)):
            return True
    return False


def rule_unbounded_query(fi: FileInfo, text: str, cache: Optional[FileCache] = None) -> List[Signal]:
    """SELECT without LIMIT/TOP/FETCH FIRST/COUNT, .find({}), findMany without take, objects.all().

    Skipped as bounded: primary-key lookups (WHERE id = ...), single-row fetches (fetchone,
    QueryRow, .first()), aggregates without GROUP BY, system-catalog and PostGIS metadata
    introspection (also as the tail of a WITH over a catalog), subqueries inside UPDATE/INSERT/
    DELETE, derived-table fragments (FROM (...)), tables supplied at runtime (FROM `%s`, FROM " +
    name), findMany with a ...spread argument, objects.all() chained into delete/update/count/
    exists/first/filter/slice, SQL inside Python docstrings, Go QueryRow("SELECT ...") on the same
    line, and migration files. objects.all() counts only in a request handler (a def with a
    request/req parameter, or a file that defines routes) or in the body / get_queryset of a
    DRF or class-based list view (ViewSet, APIView, ListAPIView, ListView ...) that sets no
    pagination_class/paginate_by while neither the file nor a nearby settings module sets
    DEFAULT_PAGINATION_CLASS; elsewhere it is a lazy QuerySet the caller filters or paginates.
    False-positive risks: small lookup tables, limits applied by a wrapper, single-row lookups on
    other unique columns, and prose containing "select ... from" in non-docstring strings.
    """
    kind = _kind(fi)
    if kind not in _CORE_KINDS or _is_migration(fi):
        return []
    if kind == "py":
        text = _blank_py_docstrings(text)
    out: List[Signal] = []
    flagged_lines = set()
    for start in _select_starts(text):
        line = _line_of(text, start)
        if line in flagged_lines:  # one signal per line: same snippet, same finding
            continue
        if not _bounded_select(text, start):
            flagged_lines.add(line)
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, start, "low"))
    for m in _FIND_ALL_RE.finditer(text):
        out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "medium"))
    for m in _FIND_MANY_RE.finditer(text):
        open_idx = m.end() - 1
        call = text[open_idx:_match_end(text, open_idx, CALL_MAX_LINES) + 1]
        if not re.search(r"\b(?:take|limit)\b|\.\.\.", call):
            out.append(_sig("unbounded-query", "Scalability", "Medium", fi, text, m.start(), "medium"))
    has_routes: Optional[bool] = None
    paginated: Optional[bool] = None
    in_handler: List[bool] = []
    lines: List[str] = []
    for m in _OBJECTS_ALL_RE.finditer(text):
        if _OBJECTS_ALL_CHAINED_RE.match(text, m.end()):
            continue
        if not in_handler:
            lines = text.split("\n")
            in_handler = _py_request_scopes(lines)
        lineno = _line_of(text, m.start())
        view = _list_view_class(lines, lineno)
        if view >= 0:  # DRF / class-based list view: unbounded unless pagination is configured
            if _class_paginated(lines, view):
                continue
            if paginated is None:
                paginated = (bool(_DEFAULT_PAGINATION_RE.search(text))
                             or _settings_paginate(fi, cache))
            if paginated:
                continue
        elif not in_handler[lineno - 1]:
            if has_routes is None:
                has_routes = _has_routes([fi], cache) if cache is not None else bool(find_routes([fi]))
            if not has_routes:
                continue
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
# Placeholder words: a value containing one is a template, not a credential.
_SECRET_IGNORE_RE = re.compile(
    r"example|sample|change(?:me|this|it)|placeholder|dummy|fake|xxx|your[_-]|replace[_-]?me"
    r"|redacted|<|\$\{|\{\{|process\.env|os\.environ", re.I)
# Quoted values that are not credentials: password hashes (bcrypt, argon2, pbkdf2, crypt),
# regex/template text, and dummy values (all digits, one repeated character).
_HASH_VALUE_RE = re.compile(
    r"^\$(?:2[abxy]?|argon2(?:id|i|d)?|pbkdf2[\w-]*|scrypt|[156y])\$|^(?:pbkdf2_\w+|bcrypt\w*|argon2\w*|sha1|md5)\$")
# `[` is excluded inside the class so a run of `[` cannot make each start rescan the value.
_PATTERN_VALUE_RE = re.compile(r"\[[^\]\[]*-[^\]\[]*\]|\{\d+(?:,\d*)?\}|\\[dwsSbB]|\(\?")
_DUMMY_VALUE_RE = re.compile(r"^(?:\d+|(.)\1+)$")
# Config files only: unquoted dotenv `KEY=value` and YAML/INI `key: value` lines.
_UNQUOTED_SECRET_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?[\w.-]*" + SECRET_KEY
    + r"[ \t]*[:=][ \t]*([^\s\"'#]\S{7,})[ \t]*$",  # `#` inside a value is not a comment
    re.I | re.M)
_NON_SECRET_VALUE_RE = re.compile(r"^(?:\d+|true|false|null|none)$|^[$%]", re.I)
# Values that are locations, not credentials: URLs and absolute file paths.
_LOCATION_VALUE_RE = re.compile(r"^(?:https?://|/|[A-Za-z]:\\)", re.I)
# Identifier-shaped values (names, not credentials): words of one case joined by `_`, `-` or `.`
# (RESET_PASSWORD, _password_reset_token, set-password, auth.session.key) or camelCase words
# (resetPassword; 2+ lowercase letters per word). Used with fullmatch; every branch is
# deterministic, so matching is linear.
_IDENTIFIER_VALUE_RE = re.compile(
    r"_?(?:[a-z]+|[A-Z]+)(?:[._-](?:[a-z]+|[A-Z]+))+|[a-z]{2,}(?:[A-Z][a-z]{2,})+")
# Pipe-separated rule lists: Laravel validation (required|string|min:8) and template filters
# (value|default:). Used with fullmatch; `|` ends each segment, so matching is linear.
_RULE_LIST_VALUE_RE = re.compile(r"[A-Za-z_]+(?::[^|\s]*)?(?:\|[A-Za-z_]+(?::[^|\s]*)?)+\|?")
# Generic key = "value" strength: a value fires when it has GENERIC_LONG_LEN+ characters and
# GENERIC_MIN_ENTROPY+ bits per character, or GENERIC_SHORT_LEN+ characters, 3 of the 4 character
# classes (lower, upper, digit, symbol) and GENERIC_SHORT_ENTROPY+ bits. Letters-only never fires.
GENERIC_MIN_ENTROPY = 3.5
GENERIC_LONG_LEN = 12
GENERIC_SHORT_LEN = 8
GENERIC_SHORT_ENTROPY = 2.8
_CHAR_CLASS_RES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"[0-9]"),
                   re.compile(r"[^A-Za-z0-9]"))
# Known provider formats (Critical) and PEM private key headers.
# One regex per format: each starts with a literal, which the engine scans for quickly (one
# combined alternation was tried at every position and took seconds on large files).
_PROVIDER_RES = [(label, re.compile(rx)) for label, rx in PROVIDER_FORMATS]


def _provider_matches(text: str) -> List[Tuple[int, str, str]]:
    """(offset, label, token) of every known provider token not preceded by a word character."""
    found = []
    for label, rx in _PROVIDER_RES:
        for m in rx.finditer(text):
            i = m.start()
            if i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"):
                found.append((i, label, m.group(0)))
    return found
# Same header shapes as key files (RSA, EC, DSA, OPENSSH, ENCRYPTED ...).
_PEM_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
# CI workflow files, and the database-service password keys that only configure a throwaway
# service container there (under a `services:` key: services.postgres.env.POSTGRES_PASSWORD).
_CI_FILE_RE = re.compile(r"(?:^|/)(?:\.github/workflows/[^/]+|\.gitlab-ci\.ya?ml|\.circleci/[^/]+)$")
_CI_SERVICE_WORDS = ("POSTGRES", "MYSQL", "MARIADB", "MONGO", "REDIS", "RABBITMQ", "PG", "DB",
                     "DATABASE")
_SERVICES_KEY_RE = re.compile(r"[ \t]*(?:-[ \t]*)?[\"']?services[\"']?[ \t]*:")
# `(?:-[ \t]*)?` rather than `-?[ \t]*`: two adjacent `[ \t]*` made a long indent quadratic.
_LINE_KEY_RE = re.compile(r"[ \t]*(?:-[ \t]*)?(?:export[ \t]+)?[\"']?([\w.-]+)")
# OpenAPI / Swagger documents and `example:` blocks hold sample values.
_OPENAPI_DOC_RE = re.compile(
    r"^[ \t]*[\"']?(?:openapi|swagger)[\"']?[ \t]*:|[{,][ \t\r\n]*\"(?:openapi|swagger)\"[ \t]*:", re.M)
_EXAMPLE_KEY_RE = re.compile(r"[ \t]*(?:-[ \t]*)?[\"']?(?:x-)?examples?[\"']?[ \t]*[:=]", re.I)
_EXAMPLE_INLINE_RE = re.compile(r"[\"']?\b(?:x-)?examples?[\"']?[ \t]*[:=]", re.I)
_INDENT_RE = re.compile(r"[ \t]*")
EXAMPLE_WALK_MAX_LINES = 200
# Folders that hold test fixtures: a private key file there is likely a test key (low).
_FIXTURE_PATH_RE = re.compile(
    r"(?:^|/)(?:testdata|test[_-]data|fixtures?|__fixtures__|tests?|__tests__|specs?)/")


_PY_TRIPLE_RE = re.compile(r"\"\"\"|'''|[\"']")
# A triple-quoted string after one of these characters is part of an expression (an argument,
# assignment, dict value or concatenation) and is kept.
_PY_EXPR_CONTINUES = "([{,=+%\\:*/|&<>-"


def _blank_py_doc_blocks(text: str) -> str:
    """Docstrings (_blank_py_docstrings) plus bare triple-quoted string statements (a string that
    starts its line after a complete statement, used as an inline doc block) blanked, keeping
    newlines and offsets. Linear scan; strings that continue an expression are kept."""
    text = _blank_py_docstrings(text)
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        m = _PY_TRIPLE_RE.search(text, i)
        if m is None:
            break
        q = m.start()
        if len(m.group(0)) == 3:
            close = text.find(m.group(0), q + 3)
            close = n if close < 0 else close
            k = q - 1
            while k >= 0 and text[k] in "rRbBuUfF" and q - k <= 2:
                k -= 1
            line_start = text.rfind("\n", 0, q) + 1
            bare = not text[line_start:k + 1].strip()
            while k >= 0 and text[k] in " \t\r\n":
                k -= 1
            out.append(text[i:q + 3])
            if bare and (k < 0 or text[k] not in _PY_EXPR_CONTINUES):
                out.append(_NON_NL_RE.sub(" ", text[q + 3:close]))
            else:
                out.append(text[q + 3:close])
            out.append(text[close:close + 3])
            i = min(close + 3, n)
            continue
        j = q + 1
        while j < n and text[j] != text[q] and text[j] != "\n":
            j += 2 if text[j] == "\\" else 1
        j = min(j + 1, n)
        out.append(text[i:j])
        i = j
    out.append(text[i:])
    return "".join(out)


def _entropy(value: str) -> float:
    """Shannon entropy of `value` in bits per character."""
    n = len(value)
    counts: Dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / n * math.log2(c / n) for c in counts.values()) if n else 0.0


def _generic_value_skipped(value: str, quoted: bool = True) -> bool:
    """True when a value under a secret-sounding key is not a credential: a placeholder, label or
    sentence (whitespace), location, hash, regex/template, dummy, identifier name, rule list, or
    too weak (_generic_strong). Unquoted config values also skip booleans, null and `$`/`%`
    references."""
    return bool(_SECRET_IGNORE_RE.search(value) or re.search(r"\s", value)
                or (not quoted and _NON_SECRET_VALUE_RE.search(value))
                or _LOCATION_VALUE_RE.search(value)
                or _HASH_VALUE_RE.search(value) or _PATTERN_VALUE_RE.search(value)
                or _DUMMY_VALUE_RE.match(value) or _IDENTIFIER_VALUE_RE.fullmatch(value)
                or _RULE_LIST_VALUE_RE.fullmatch(value)
                or not _generic_strong(value))


def _generic_strong(value: str) -> bool:
    """(length >= 12 and entropy >= 3.5) or (length >= 8, 3 of 4 character classes and entropy
    >= 2.8); a letters-only value is never strong."""
    if all("a" <= ch.lower() <= "z" for ch in value):
        return False
    h = _entropy(value)
    if len(value) >= GENERIC_LONG_LEN and h >= GENERIC_MIN_ENTROPY:
        return True
    classes = sum(1 for rx in _CHAR_CLASS_RES if rx.search(value))
    return len(value) >= GENERIC_SHORT_LEN and classes >= 3 and h >= GENERIC_SHORT_ENTROPY


def _under_example(text: str, pos: int) -> bool:
    """True when `pos` sits under an `example:` / `examples:` key: earlier on the same line, or as
    an ancestor in the indentation tree (YAML, pretty JSON, indented dict literals)."""
    starts = line_starts(text)
    start = starts[bisect.bisect_right(starts, pos) - 1]
    return bool(_EXAMPLE_INLINE_RE.search(text, start, pos)) or _has_ancestor_key(
        text, pos, _EXAMPLE_KEY_RE)


def _has_ancestor_key(text: str, pos: int, key_re: "re.Pattern[str]") -> bool:
    """True when a line with less indentation above `pos` (an ancestor in the indentation tree)
    matches `key_re`. The upward walk reads only line indents and stops after
    EXAMPLE_WALK_MAX_LINES lines."""
    starts = line_starts(text)
    idx = bisect.bisect_right(starts, pos) - 1
    start = starts[idx]
    indent = _INDENT_RE.match(text, start).end() - start
    for j in range(idx - 1, max(-1, idx - 1 - EXAMPLE_WALK_MAX_LINES), -1):
        if indent == 0:
            break
        s = starts[j]
        e = _INDENT_RE.match(text, s).end()
        if e >= len(text) or text[e] in "\r\n":
            continue  # blank line
        if e - s < indent:
            if key_re.match(text, s):
                return True
            indent = e - s
    return False


def _generic_context_skipped(fi: FileInfo, text: str, pos: int, openapi: bool) -> bool:
    """True when a generic match is documentation or throwaway CI config: an OpenAPI/Swagger
    document, a value under an `example:` key, a doctest line (`>>>`), or a database-service
    password under a `services:` key in a CI workflow file."""
    if openapi:
        return True
    line = _line_text(text, pos)
    head = line.lstrip()
    if head.startswith(">>>") or head.startswith("... "):
        return True
    if _CI_FILE_RE.search(fi.rel):
        m = _LINE_KEY_RE.match(line)
        key = m.group(1).upper() if m else ""
        if ("PASS" in key and any(w in key for w in _CI_SERVICE_WORDS)
                and _has_ancestor_key(text, pos, _SERVICES_KEY_RE)):
            return True
    return _under_example(text, pos)


PRIVATE_KEY_FILE_SNIPPET = "<private key file>"
_PEM_FILE_HEADER_RE = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def rule_private_key_file(fi: FileInfo) -> List[Signal]:
    """A committed private key file (*.pem, *.key, id_rsa*, *.p12): signal "private key committed".

    Only the first 4 KB are inspected for a PRIVATE KEY header (`.p12` bundles are binary and always
    reported); nothing else is read and the snippet never contains key material. Key files in test
    or fixture folders (testdata/, fixtures/, __fixtures__/, test/, tests/, spec/) are reported with
    confidence "low". False-positive risks: test or sample keys, `.key` files that are not
    cryptographic keys (those lack the header and are skipped), and `.p12` files that hold only
    certificates.
    """
    conf = "low" if fi.is_test or _FIXTURE_PATH_RE.search(fi.rel) else "high"
    try:
        with open(fi.abs, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return []
    if fi.ext != ".p12" and not _PEM_FILE_HEADER_RE.search(head):
        return []
    return [Signal("hardcoded-secret", "Security", "Critical", fi.rel, 1,
                   PRIVATE_KEY_FILE_SNIPPET, conf)]


def rule_hardcoded_secret(fi: FileInfo, text: str) -> List[Signal]:
    """Known provider token, PEM private key header, or a credential-looking assignment.

    Runs on code, test and config files (.env, YAML, JSON, TOML, INI, properties). Known formats
    come first and are reported wherever they appear with severity Critical: AWS access key ids,
    Stripe live secret keys, GitHub, GitLab, Slack, Google, OpenAI and Anthropic tokens, and PEM
    private key headers (RSA, EC, DSA, OPENSSH, ENCRYPTED ...); a token containing "example" is
    skipped, and one inside a Python docstring or bare triple-quoted doc block is still reported.
    Generic `key = "value"` matches (and, in config files, unquoted `KEY=value` /
    `key: value` lines) are reported with severity Medium, and only when the value is strong
    (length >= 12 and entropy >= 3.5 bits per character, or length >= 8 with 3 of the 4 character
    classes lower/upper/digit/symbol and entropy >= 2.8; letters-only never) and is not
    a placeholder (example, sample, changeme, dummy, fake, xxx, your_, <...>, ${...}, {{...}}, env
    lookups), a label or sentence (whitespace), a URL or absolute path, a password hash, regex or
    template text, a dummy value, an identifier name (RESET_PASSWORD, resetPassword,
    set-password) or a pipe rule list (required|min:8). Generic matches are also skipped in
    OpenAPI/Swagger documents, under `example:` keys, on doctest lines, inside Python docstrings
    and bare triple-quoted doc blocks, for database-service passwords under a `services:` key in
    CI workflow files, and on a line that already holds a known format. In test code files only the known formats are
    reported. Confidence: the code emits "high" for a known format outside tests and docstrings,
    but report._stamp caps it at the registry ceiling, low, so every signal of this rule is low
    (known formats included; see the ROADMAP follow-up on a separate known-format rule).
    Key words: password, passwd, secret, token, api key, access key, client secret, secret access
    key, private key, with `_` or `-` between words (api-key, X-API-Key, aws_secret_access_key).
    The key may have any prefix (DB_PASSWORD, stripe_secret) but after the secret word only an optional
    `key` (`_key`, `-key`, `Key`) may follow, so tokenUrl, password_file, password_hash, token_expiry,
    tokenizer and tokens never match. The snippet masks the value so the report never repeats it.
    False-positive risks: random-looking non-secret values under a secret-sounding key, sample
    tokens outside example blocks, public identifiers. Secrets inside comments are not seen because
    comments are stripped.
    """
    conf = "low" if fi.is_test else "high"
    out: List[Signal] = []
    scan_text = _blank_py_doc_blocks(text) if fi.ext == ".py" else text
    known_lines = set()

    def known(pos: int, label: str, confidence: str) -> None:
        sig = _sig("hardcoded-secret", "Security", "Critical", fi, text, pos, confidence, label)
        known_lines.add(sig.line)
        out.append(sig)

    def in_doc(pos: int) -> bool:  # blanked by _blank_py_doc_blocks
        return scan_text[pos] != text[pos]

    for pos, label, token in _provider_matches(text):
        if "example" not in token.lower():
            known(pos, label + " <redacted>", "low" if in_doc(pos) else conf)
    for m in _PEM_RE.finditer(text):
        known(m.start(), "private key header", "low" if in_doc(m.start()) else conf)

    openapi = fi.is_config and bool(_OPENAPI_DOC_RE.search(text, 0, 4096))

    def generic(pos: int, value: str, quoted: bool) -> None:
        if _generic_value_skipped(value, quoted) or _line_of(text, pos) in known_lines:
            return
        if _generic_context_skipped(fi, text, pos, openapi):
            return
        masked = _line_text(text, pos).replace(value, "<redacted>")
        out.append(_sig("hardcoded-secret", "Security", "Medium", fi, text, pos, conf, masked))

    fixture = fi.is_test and fi.lang is not None  # test code; config files in test folders stay
    for m in ([] if fixture else _SECRET_RE.finditer(scan_text)):
        generic(m.start(), m.group(1), True)
    if fi.is_config:
        for m in _UNQUOTED_SECRET_RE.finditer(scan_text):
            generic(m.start(1), m.group(1), False)
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- sql-string-concat
_SQL_IDENT_REASON = ("%s reaches an SQL identifier position (table, column or ORDER BY); "
                     "identifiers cannot be bound as parameters, so check it against an allow-list")


def rule_sql_string_concat(fi: FileInfo, text: str) -> List[Signal]:
    """Untrusted input that reaches the SQL text of a database call in the same function.

    Evidence comes from taint.find_flows: a request parameter, query, body, header, route
    parameter, input() or CLI argument, directly or through local assignments, ends up in the
    SQL argument of execute/raw/query/Exec/QueryRow/$wpdb->query/DB::select/createQuery/...
    concatenated or interpolated (never as a bound parameter). Concatenation alone, with no
    traced source, emits nothing. The snippet reads "<var> from line <src> reaches SQL at line
    <sink>"; a value used as an identifier (FROM {table}, ORDER BY + sort) carries an
    uncertain_reason, since it may be checked against an allow-list elsewhere. Confidence:
    report._stamp caps it at the registry ceiling, low, so every signal of this rule is low.
    False-positive risks: a value validated by a helper the tracker does not know (custom
    allow-list, regex check, typed DTO) is still reported. Flow through other functions or
    files, object fields and return values is not followed (missed findings).
    """
    if _kind(fi) is None:
        return []
    out: List[Signal] = []
    for f in find_flows(text, fi.lang or ""):
        snippet = "%s from line %d reaches SQL at line %d" % (f.variable, f.source_line, f.sink_line)
        if f.identifier:
            out.append(Signal("sql-string-concat", "Security", "Critical", fi.rel, f.sink_line,
                              snippet, "low", uncertain_reason=_SQL_IDENT_REASON % f.variable))
        else:
            out.append(Signal("sql-string-concat", "Security", "Critical", fi.rel, f.sink_line,
                              snippet, "medium"))
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
_CATCH_RE = re.compile(r"catch\s*(?:\([^()]{0,300}\))?\s*\{\s*\}")
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


INTENT_TAIL_CHARS = 300  # text read after a handler on its closing line, for a trailing comment
# Words that do not explain a handler: they restate it (ignore, pass, swallow) or mark it as
# unfinished (todo, fixme), plus short filler words.
_VAGUE_WORDS = {
    "ignore", "ignored", "ignores", "ignoring", "ok", "okay", "pass", "passes", "noop", "nothing",
    "silent", "silently", "swallow", "swallowed", "swallows", "log", "logged", "logging", "todo",
    "fixme", "xxx", "hack",
    "it", "is", "be", "to", "do", "we", "the", "an", "and", "or", "of", "in", "on", "at", "here",
    "this", "that", "just", "error", "errors", "exception", "exceptions", "err", "ex",
}
_COMMENT_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']+")


def _comment_intent(raw: str, text: str) -> str:
    """"explained" when raw (with comments) differs from text (comments blanked) by a comment
    holding at least two words outside _VAGUE_WORDS (tool directives such as # noqa or
    eslint-disable removed first), "vague" for any other comment, "" when there is none."""
    comment = "".join(r if r != t or r == "\n" else " " for r, t in zip(raw, text))
    if not comment.strip():
        return ""
    words = _COMMENT_WORD_RE.findall(_DIRECTIVE_RE.sub(" ", comment))
    if not words:
        return ""  # only a tool directive or comment markers
    explained = [w for w in words if w.lower() not in _VAGUE_WORDS]
    return "explained" if len(explained) >= 2 else "vague"


def _exc_names(clause: str) -> List[str]:
    """Class names of an except clause (`(a.B, C) as e` -> ["B", "C"])."""
    clause = re.sub(r"\bas\s+\w+\s*$", "", clause.strip()).strip().strip("()")
    return [n.strip().split(".")[-1] for n in clause.split(",") if n.strip()]


def _except_class(clause: str) -> str:
    """'broad' (bare / Exception), 'expected' (lookup-miss family) or 'narrow' (other named)."""
    names = _exc_names(clause)
    if not names or any(n in _BROAD_EXC for n in names):
        return "broad"
    if all(n in _EXPECTED_EXC or n.endswith("DoesNotExist") for n in names):
        return "expected"
    return "narrow"


TRY_BODY_MAX_LINES = 50  # a try body longer than this is never treated as an idiom
TRY_BODY_MAX_CHARS = 4000  # brace languages: longest try block read before a catch
# Classes raised when an optional dependency is missing or unusable.
_OPTIONAL_IMPORT_EXC = ("ImportError", "ModuleNotFoundError", "ImproperlyConfigured", "LoadError")
_PY_TRY_RE = re.compile(r"try[ \t]*:[ \t]*\r?$")
_PY_IMPORT_STMT_RE = re.compile(r"(?:import|from)[ \t]+[\w.]")
_PY_IMPORT_NAMES_RE = re.compile(r"^[ \t]*(?:from[ \t]+[\w.]+[ \t]+)?import[ \t]+\(?([^)\n]*)")
_JS_LITERAL_REQUIRE_RE = re.compile(r"\b(?:require|import)\s*\(\s*['\"`]")
_RB_LITERAL_REQUIRE_RE = re.compile(r"^[ \t]*require(?:_relative)?[ \t(]+['\"]")
_STMT_ASSIGN_RE = re.compile(r"^[ \t]*(?:(?:const|let|var)[ \t]+)?[\w.$\[\]'\"]+[ \t]*[-+*/|]?=(?!=)")
_WORD_RE = re.compile(r"[\w$]+")
# One best-effort cleanup call: closing, deleting a temp file, chown/chmod, stopping a process.
_CLEANUP_CALL_RE = re.compile(
    r"\.(?:close|unlink(?:Sync)?|rmdir(?:Sync)?|rmSync|rmtree|chown|chmod|kill|terminate|shutdown"
    r"|disconnect|dispose|cleanup|cleanUp)\s*\(|\bos\.(?:remove|unlink|rmdir)\s*\(|\bshutil\.rmtree\s*\(")
# A read or pop on a cache/memo-named receiver (`self._cache[k]` not followed by `=`,
# `cache.get(`, `x._prefetched_objects_cache.pop(`); a write to the cache is not a cache miss.
_CACHE_READ_RE = re.compile(
    r"(?:cache|memo)\w*(?:\[[^\]\n]*\](?![ \t]*[-+*/|&]?=(?!=))|\.(?:get|pop|popitem)[ \t]*\()",
    re.I)
_ASSIGN_RE = re.compile(r"^[ \t]*(\w+)[ \t]*(?::[^=\n]*)?=(?!=)")
_TRY_WORD_END_RE = re.compile(r"(?<![\w$.])try$")
_BRACE_CHAR_RE = re.compile(r"[{}]")


def _indent_of(ln: str) -> int:
    return len(ln) - len(ln.lstrip(" \t"))


def _import_like(names: List[str]) -> bool:
    return any(n.endswith(_OPTIONAL_IMPORT_EXC) for n in names)


def _py_try_body(lines: List[str], idx: int) -> Optional[Tuple[int, List[str]]]:
    """(index of the `try:` line, its non-blank body lines) for the except clause on lines[idx],
    or None when the try is not found within TRY_BODY_MAX_LINES. Earlier except clauses of the
    same try are skipped, so the second handler of a chain sees the same body."""
    indent = _indent_of(lines[idx])
    body: List[str] = []
    for t in range(idx - 1, max(-1, idx - 1 - TRY_BODY_MAX_LINES), -1):
        cur = lines[t]
        if not cur.strip():
            continue
        ind = _indent_of(cur)
        if ind > indent:
            body.append(cur)
            continue
        if ind < indent:
            return None
        stmt = cur.lstrip()
        if _PY_TRY_RE.match(stmt):
            body.reverse()
            return t, body
        if not stmt.startswith("except"):
            return None
        body = []  # an earlier handler of the same try
    return None


def _py_next_statement(lines: List[str], idx: int, indent: int) -> str:
    """First statement at `indent` after the handler on lines[idx] (skipping its body and later
    except clauses), "" when none follows within TRY_BODY_MAX_LINES."""
    for ln in lines[idx + 1:idx + 1 + TRY_BODY_MAX_LINES]:
        if not ln.strip():
            continue
        ind = _indent_of(ln)
        if ind > indent:
            continue
        stmt = ln.strip()
        if ind == indent and stmt.startswith("except"):
            continue
        return stmt  # at the try's indentation, or after the enclosing block ends
    return ""


def _fallback_assignment(lines: List[str], t: int, body: List[str], idx: int) -> bool:
    """`x = None; try: x = f() except ...: pass; if x is None` — the try body is one assignment
    whose fallback value is set before the try or checked after it (an optional value)."""
    am = _ASSIGN_RE.match(body[0]) if len(body) == 1 else None
    if am is None:
        return False
    name = re.escape(am.group(1))
    before = "\n".join(lines[max(0, t - 15):t])
    after = "\n".join(lines[idx + 1:idx + 16])
    return bool(re.search(r"(?m)^[ \t]*%s[ \t]*(?::[^=\n]*)?=[ \t]*None\b" % name, before)
                or re.search(r"\b%s\s+is\s+(?:not\s+)?None\b" % name, after))


def _has_else(lines: List[str], idx: int, indent: int) -> bool:
    """The handler on lines[idx] is followed by `else:` at its own indentation (try/except/else);
    its body and later except clauses are skipped."""
    for ln in lines[idx + 1:idx + 1 + TRY_BODY_MAX_LINES]:
        if not ln.strip() or _indent_of(ln) > indent:
            continue
        stmt = ln.strip()
        if _indent_of(ln) == indent and stmt.startswith("except"):
            continue
        return _indent_of(ln) == indent and re.match(r"else\s*:", stmt) is not None
    return False


def _only_imports(stmts: List[str], is_import: Callable[[str], bool],
                  bound: Callable[[str], List[str]]) -> bool:
    """Every statement is an import/require, or an assignment that uses a name an earlier
    import bound (`import sqlite3; value = sqlite3.adapt(value)`)."""
    names: set = set()
    for s in stmts:
        if is_import(s):
            names.update(bound(s))
            continue
        if not (_STMT_ASSIGN_RE.match(s) and names.intersection(_WORD_RE.findall(s))):
            return False
    return True


def _py_bound(stmt: str) -> List[str]:
    m = _PY_IMPORT_NAMES_RE.match(stmt)
    if m is None:
        return []
    out = []
    for part in m.group(1).split(","):
        bits = part.split()
        if bits:
            out.append(bits[-1] if len(bits) >= 3 and bits[-2] == "as" else bits[0].split(".")[0])
    return out


def _js_bound(stmt: str) -> List[str]:
    head = stmt.split("=", 1)[0] if "=" in stmt else ""
    return _WORD_RE.findall(head)


def _optional_import(stmts: List[str], names: List[str], is_import: Callable[[str], bool],
                     bound: Callable[[str], List[str]]) -> bool:
    """The optional-dependency idiom: the try body imports a literal module and either the
    handler names an import-failure class (ImportError, ModuleNotFoundError, LoadError,
    ImproperlyConfigured) or the body is only imports plus assignments that use them. A lazy
    import next to real work under a broad handler is not the idiom."""
    if not any(is_import(s) for s in stmts):
        return False
    return _import_like(names) or _only_imports(stmts, is_import, bound)


def _py_idiom(lines: List[str], idx: int, cls: str, names: List[str]) -> bool:
    """Python handler on lines[idx] that is an intentional idiom: try/except/else, an optional
    value (fallback set before / checked after), an optional import, one best-effort cleanup
    call, and for a named (non-broad) class a cache read or a fall-through fallback (the try
    body returns, the statement after the handler returns or raises)."""
    indent = _indent_of(lines[idx])
    if _has_else(lines, idx, indent):
        return True
    found = _py_try_body(lines, idx)
    if found is None:
        return False
    t, body = found
    if not body:
        return False
    if _optional_import(body, names, lambda s: _PY_IMPORT_STMT_RE.match(s.lstrip()) is not None,
                        _py_bound):
        return True
    if len(body) == 1 and _CLEANUP_CALL_RE.search(body[0]):
        return True
    if _fallback_assignment(lines, t, body, idx):
        return True
    if cls == "broad":
        return False
    if len(body) == 1 and _CACHE_READ_RE.search(body[0]):
        return True
    return (body[-1].lstrip().startswith("return")
            and _py_next_statement(lines, idx, indent).startswith(("return", "raise")))


def _brace_pairs(text: str) -> Dict[int, int]:
    """Offset of each `}` -> offset of the `{` it closes, in one forward pass (braces inside
    strings are not tracked; unmatched braces are dropped)."""
    pairs: Dict[int, int] = {}
    stack: List[int] = []
    for b in _BRACE_CHAR_RE.finditer(text):
        k = b.start()
        if text[k] == "{":
            stack.append(k)
        elif stack:
            pairs[k] = stack.pop()
    return pairs


def _brace_try_body(text: str, pos: int, pairs: Dict[int, int]) -> Optional[str]:
    """Text of the `try { ... }` block whose closing brace precedes the catch at `pos` (at most
    TRY_BODY_MAX_CHARS long), found through the precomputed brace pairs."""
    j = pos - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j < 0 or text[j] != "}" or j not in pairs:
        return None
    k = pairs[j]
    if j - k > TRY_BODY_MAX_CHARS:
        return None
    head = text[max(0, k - 20):k].rstrip()
    return text[k + 1:j] if _TRY_WORD_END_RE.search(head) else None


def _catch_names(match_text: str) -> List[str]:
    m = re.search(r"\(([^()]*)\)", match_text)
    return re.findall(r"[A-Z][\w.\\]*", m.group(1)) if m else []


def _brace_idiom(text: str, m: "re.Match[str]", pairs: Dict[int, int]) -> bool:
    """An optional require/import (see _optional_import) or one best-effort cleanup call in the
    try block of the catch matched by `m`."""
    body = _brace_try_body(text, m.start(), pairs)
    if body is None:
        return False
    stmts = [s.strip() for s in re.split(r"[;\n]", body) if s.strip()]
    names = [n.split(".")[-1].split("\\")[-1] for n in _catch_names(m.group(0))]
    if _optional_import(stmts, names, lambda s: _JS_LITERAL_REQUIRE_RE.search(s) is not None,
                        _js_bound):
        return True
    return len(stmts) == 1 and _CLEANUP_CALL_RE.search(stmts[0]) is not None


def _rb_idiom(lines: List[str], idx: int, names: List[str]) -> bool:
    """An optional `require 'gem'` (see _optional_import) or one best-effort cleanup call in the
    body the rescue on lines[idx] protects (the lines above it, indented deeper)."""
    indent = _indent_of(lines[idx])
    body: List[str] = []
    for ln in reversed(lines[max(0, idx - TRY_BODY_MAX_LINES):idx]):
        if not ln.strip():
            continue
        if _indent_of(ln) <= indent:
            break
        body.append(ln)
    body.reverse()
    if _optional_import(body, names, lambda s: _RB_LITERAL_REQUIRE_RE.match(s) is not None,
                        lambda s: []):
        return True
    return len(body) == 1 and _CLEANUP_CALL_RE.search(body[0]) is not None


def rule_swallowed_error(fi: FileInfo, text: str, cache: Optional[FileCache] = None) -> List[Signal]:
    """Empty catch block, `except: pass`, Go `_ = err`, an empty `if err != nil {}` or an empty
    Ruby `rescue` body (Java/Kotlin/C#/PHP `catch (...) {}` and C# `catch {}` use the catch form).

    Intentional handling is not reported: a comment in the handler or on its closing line that
    explains it (at least two words beyond ignore/ok/pass/noop/nothing/silent/swallow/log/todo/
    fixme and filler; tool directives do not count), the optional-dependency idiom (a literal
    import/require in the try body, and an import-failure class or a body of only imports and
    assignments using them), one best-effort cleanup call (close, unlink, chown, kill, ...), and
    in Python try/except/else, an optional value (fallback set before the try or checked after
    it), lookup-miss exceptions (KeyError, StopIteration, *DoesNotExist, ImportError, ...), and
    for a named (non-broad) class a cache read or a fall-through fallback (the try body returns
    and the next statement returns or raises). Other named exceptions (Python, or a Ruby rescue
    not naming StandardError/Exception) get severity Low. Confidence: the code emits low for those
    and for a broad handler with only a vague comment, and high for a bare `except:` /
    `except Exception:`, but report._stamp caps it at the registry ceiling, low, so every signal of this rule is low.
    False-positive risks: deliberate best-effort code under a broad except with no comment and
    no recognised idiom, or an undocumented empty JS/Go catch, is still reported.
    """
    kind = _kind(fi)
    rx = {"js": _CATCH_RE, "c": _CATCH_RE, "py": _EXCEPT_RE, "go": _GO_ERR_RE,
          "rb": _RB_RESCUE_RE}.get(kind or "")
    if rx is None:
        return []
    raw: Optional[str] = None
    lines: Optional[List[str]] = None  # split once per file, not per match
    pairs: Optional[Dict[int, int]] = None  # brace pairs, computed once per file
    out: List[Signal] = []
    for m in rx.finditer(text):
        downgrade = False
        if kind == "py":
            cls = _except_class(m.group(1))
            if cls == "expected":
                continue
            if lines is None:
                lines = text.split("\n")
            if _py_idiom(lines, _line_of(text, m.start()) - 1, cls, _exc_names(m.group(1))):
                continue
            downgrade = cls == "narrow"
        elif kind == "rb":
            names = [n.split("::")[-1] for n in re.findall(r"[A-Z][\w:]*", m.group(1).split("=>")[0])]
            downgrade = bool(names) and not any(n in _RB_BROAD_EXC for n in names)
            if lines is None:
                lines = text.split("\n")
            if _rb_idiom(lines, _line_of(text, m.start()) - 1, names):
                continue
        elif kind in ("js", "c"):
            if pairs is None:
                pairs = _brace_pairs(text)
            if _brace_idiom(text, m, pairs):
                continue
        if raw is None:
            raw = text_of(fi, cache)
        eol = text.find("\n", m.end(), m.end() + INTENT_TAIL_CHARS)
        eol = min(len(text), m.end() + INTENT_TAIL_CHARS) if eol < 0 else eol
        # Comment stripping keeps offsets, so the raw slice lines up with the match.
        intent = _comment_intent(raw[m.start():eol], text[m.start():eol])
        if intent == "explained":
            continue
        if downgrade:
            sev, conf = "Low", "low"
        else:
            sev, conf = "Medium", "low" if intent == "vague" else "high"
        out.append(_sig("swallowed-error", "Reliability", sev, fi, text, m.start(), conf))
    return out


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
                              texts: Optional[Dict[str, str]] = None,
                              cache: Optional[FileCache] = None) -> List[Signal]:
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
        if cache is not None:
            # Same answers, without holding every stripped text of the unit at once.
            if not (sum(f.loc for f in code) >= 100 or _has_routes(code, cache)):
                continue
            if any(_LOG_LIB_RE.search(cache.code(f)) for f in code):
                continue
            bare = any(_BARE_LOG_RE.search(cache.code(f)) for f in code)
        else:
            has_routes = bool(find_routes(code))
            if not (has_routes or sum(f.loc for f in code) >= 100):
                continue
            stripped = {f.rel: (texts[f.rel] if texts and f.rel in texts
                                else strip_comments(_read(f), f.lang or "")) for f in code}
            if any(_LOG_LIB_RE.search(t) for t in stripped.values()):
                continue
            bare = any(_BARE_LOG_RE.search(t) for t in stripped.values())
        if bare:
            out.append(Signal("console-logging-only", "Observability", "Low", folder, 0,
                              "only console/print logging in " + folder, "low"))
    return out


_NO_TEST_UNIT_NAMES = {"config", "configs", "conf", "docs", "doc", "migrations",
                       "alembic",
                       # 1.1.0: database migrations/seed data, translations, generator templates
                       # and tooling packages (lint/format presets) have no unit tests by design.
                       "migrate", "seeds", "seeders", "factories", "lang", "locale", "locales",
                       "i18n", "translations", "templates", "tooling"}
_TEMPLATE_FILE_RE = re.compile(r"\.blade\.php$|\.(?:erb|haml|slim|twig|hbs|mustache|njk|jinja2?)$")
_CONFIG_FILE_RE = re.compile(
    r"(?:^|[._-])(?:config|conf|settings)\.(?:[cm]?[jt]sx?|py)$|^\.?\w*rc\.[cm]?js$")
_PY_IMPORT_LINE_RE = re.compile(r"^[ \t]*(?:from|import)[ \t]+([\w.]+)", re.M)


_DJANGO_SETTINGS_RE = re.compile(r"^(?:INSTALLED_APPS|DATABASES|MIDDLEWARE|ROOT_URLCONF)\s*=", re.M)


def _django_settings(inside: List[FileInfo], cache: Optional[FileCache] = None) -> bool:
    """A settings/ folder of Django-style settings modules (not a feature folder named settings)."""
    py = [f for f in inside if f.ext == ".py"]
    return len(py) == len(inside) and any(_DJANGO_SETTINGS_RE.search(text_of(f, cache)) for f in py)


def _untestable_unit(folder: str, inside: List[FileInfo],
                     cache: Optional[FileCache] = None) -> bool:
    """Config, settings, docs, migration/seed, translation, template and tooling folders, folders
    of only config-named files or view templates (Blade, ERB, Twig, ...), and folders with only
    SQL/GraphQL/proto files have no unit tests by design."""
    parts = folder.split("/")
    if any(p.lower() in _NO_TEST_UNIT_NAMES for p in parts):
        return True
    if parts[-1] == "settings" and _django_settings(inside, cache):
        return True
    code = [f for f in inside if f.lang not in _SKIP_LANGS]
    return not code or all(_CONFIG_FILE_RE.search(f.rel.rsplit("/", 1)[-1])
                           or _TEMPLATE_FILE_RE.search(f.rel) for f in code)


# Root test trees: tests/, test/, __tests__/ (any test-named or test-folder file) and spec/ (every
# code file counts: RSpec names files *_spec.rb), plus pytest `testpaths` folders.
_ROOT_SPEC_DIRS = ("spec",)
# A root test tree with one of these folders exercises the app through its entry points, so it
# covers layer folders (routes, controllers, validations, providers) without naming them.
_INTEGRATION_DIRS = {"integration", "integrations", "e2e", "end2end", "end-to-end", "feature",
                     "features", "functional", "acceptance", "system", "api", "requests"}
_RUNNER_FILES = ("pytest.ini", "conftest.py", "phpunit.xml", "phpunit.xml.dist", ".rspec",
                 "karma.conf.js", "jest.config.js", "jest.config.ts", "jest.config.mjs",
                 "jest.config.cjs", "jest.config.json", "vitest.config.js", "vitest.config.ts",
                 "vitest.config.mjs", "vitest.config.mts", "playwright.config.ts",
                 "playwright.config.js", "cypress.config.ts", "cypress.config.js")
_PYTEST_SECTION_RE = re.compile(r"(?m)^\[(?:pytest|tool:pytest|tool\.pytest\.ini_options)\]")
_TESTPATHS_RE = re.compile(r"(?m)^[ \t]*testpaths[ \t]*=[ \t]*([^\n]*)")
# Test commands, matched only inside run:/script:/command: values. A command word must not be
# glued to a path or name character (requirements-unittest.txt, jest-coverage-badge).
_CI_TEST_CMD_RE = re.compile(
    r"(?<![\w.-])(?:go\s+test|pytest|py\.test|tox|nox|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test"
    r"|jest|vitest|mocha|karma|phpunit|pest|rspec|rails\s+test|rake\s+(?:test|spec)|dotnet\s+test"
    r"|cargo\s+test|manage\.py\s+test|unittest|ctest)(?![\w.-])")
_CI_BUILD_TOOL_RE = re.compile(r"(?<![\w.-])(?:mvnw?|gradlew?)(?![\w.-])")
_CI_BUILD_GOAL_RE = re.compile(r"(?<![\w.:-])(?:test|verify|check)(?![\w.-])")
_CI_INSTALL_RE = re.compile(
    r"\b(?:pip3?|uv\s+pip|pipx|poetry|npm|pnpm|yarn|bun|gem|bundle|go|apt(?:-get)?|apk|brew"
    r"|composer|dotnet\s+tool|cargo)\s+(?:install|add|get|require|ci|i)\b")
_CI_KEY_RE = re.compile(r"^([ \t]*)(?:-[ \t]+)?(run|script|command|commands)[ \t]*:[ \t]?(.*)$")
_CI_COMMENT_RE = re.compile(r"(?:^|[ \t])#[^\n]*")
_CI_MAPPING_ITEM_RE = re.compile(r"^[\w.-]+[ \t]*:(?:[ \t]|$)")
_CI_PATH_RE = re.compile(r"(?:^|/)(?:\.github/workflows/[^/]+\.ya?ml|\.gitlab-ci\.ya?ml"
                         r"|\.circleci/config\.ya?ml)$")


def _ci_command_runs_tests(cmd: str) -> bool:
    """One shell command line (comment stripped) runs tests and is not an install command."""
    for part in re.split(r"&&|\|\||;", cmd):
        if _CI_INSTALL_RE.search(part):
            continue
        if _CI_TEST_CMD_RE.search(part) or (_CI_BUILD_TOOL_RE.search(part)
                                            and _CI_BUILD_GOAL_RE.search(part)):
            return True
    return False


def _ci_test_step(text: str) -> bool:
    """A CI config with a run:/script:/command: value (inline, block scalar or list items) that
    runs a test command (go test, pytest, npm test, jest, phpunit, rspec, rails test, dotnet
    test, cargo test, mvn/gradle test|verify|check, ...). Comments, job names, other keys and
    install commands (pip install pytest) do not count. One pass, line by line."""
    key_indent = -1  # indentation of the open run/script/command key, -1 when none is open
    for raw in text.split("\n"):
        ln = _CI_COMMENT_RE.sub("", raw).rstrip()
        if not ln.strip():
            continue
        km = _CI_KEY_RE.match(ln)
        if km:
            key_indent = len(km.group(1))
            value = km.group(3).strip()
            if value and value[0] not in "|>" and _ci_command_runs_tests(value):
                return True
            continue
        if key_indent < 0:
            continue
        if _indent_of(ln) <= key_indent:
            key_indent = -1
            continue
        item = ln.strip()
        if item.startswith("- "):
            item = item[2:].strip()
        elif _CI_MAPPING_ITEM_RE.match(item):
            continue  # a sibling key of a CircleCI `run:` mapping (name:, working_directory:)
        if not _CI_MAPPING_ITEM_RE.match(item) and _ci_command_runs_tests(item):
            return True
    return False


def _read_path(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(200000)
    except OSError:
        return ""


def _scan_root(files: List[FileInfo]) -> Optional[str]:
    """Absolute scan root, derived from any walked file (abs path minus its rel parts)."""
    for f in files:
        root = f.abs
        for _ in range(f.rel.count("/") + 1):
            root = os.path.dirname(root)
        return root
    return None


def _runner_config(folder_abs: str) -> bool:
    """A test runner is configured in this folder: pytest/jest/vitest/karma/phpunit/rspec/
    playwright/cypress config file, a pytest section in setup.cfg/tox.ini/pyproject.toml, or a
    package.json with a jest key or a real "test" script."""
    if any(os.path.isfile(os.path.join(folder_abs, n)) for n in _RUNNER_FILES):
        return True
    for n in ("setup.cfg", "tox.ini", "pyproject.toml"):
        if _PYTEST_SECTION_RE.search(_read_path(os.path.join(folder_abs, n))):
            return True
    pkg = _read_path(os.path.join(folder_abs, "package.json"))
    if pkg:
        try:
            data = json.loads(pkg)
        except (ValueError, RecursionError):
            return False
        if isinstance(data, dict):
            scripts = data.get("scripts")
            test = scripts.get("test") if isinstance(scripts, dict) else None
            if "jest" in data or (isinstance(test, str) and _real_test_script(test)):
                return True
    return False


def _real_test_script(script: str) -> bool:
    """A package.json "test" script that runs something: not only echo / exit / true (the npm
    init placeholder `echo "Error: no test specified" && exit 1`, or `exit 0`)."""
    for part in re.split(r"&&|\|\||;", script):
        cmd = part.strip()
        if cmd and not (cmd == "true" or cmd.startswith("echo ") or cmd == "echo"
                        or re.match(r"exit(?:\s+\d+)?$", cmd)):
            return True
    return False


def _testpaths(root: Optional[str]) -> List[str]:
    """Folders named by pytest `testpaths` in the root pytest.ini / setup.cfg / tox.ini /
    pyproject.toml (first path segment, POSIX)."""
    if root is None:
        return []
    out: List[str] = []
    for n in ("pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"):
        text = _read_path(os.path.join(root, n))
        if not text or (n != "pytest.ini" and not _PYTEST_SECTION_RE.search(text)):
            continue
        for m in _TESTPATHS_RE.finditer(text):
            for tok in re.findall(r"[\w.-]+(?:/[\w.-]+)*", m.group(1)):
                seg = tok.strip("./").split("/")[0]
                if seg and seg not in out:
                    out.append(seg)
    return out


class _TestIndex:
    """Test files of the whole scan root, for finding tests that live outside a unit."""

    def __init__(self, files: List[FileInfo], cache: Optional[FileCache] = None) -> None:
        self.files = files
        self.cache = cache
        self.root = _scan_root(files)
        roots = set(_TOP_TEST_DIRS) | set(_ROOT_SPEC_DIRS) | set(_testpaths(self.root))
        # Every code file under spec/ is a test (RSpec names files *_spec.rb); under a pytest
        # testpaths folder (`testpaths = src tests`) only test-named files are.
        self.tests = [f for f in files if f.lang is not None
                      and (f.is_test or f.rel.split("/")[0] in _ROOT_SPEC_DIRS)]
        self.top = [f for f in self.tests if "/" in f.rel and f.rel.split("/")[0] in roots]
        self._top_set = {f.rel for f in self.top}
        self.test_set = {f.rel for f in self.tests}
        self._imports: Optional[List[Tuple[FileInfo, str]]] = None
        self._runner: Dict[str, bool] = {}
        self._ci: Optional[bool] = None
        self._integration: Optional[bool] = None

    def in_sibling_dirs(self, folder: str) -> bool:
        """Test files in tests/, test/ or __tests__/ next to the unit or next to any ancestor
        below the scan root (backend/app -> backend/tests)."""
        parts = folder.split("/")
        prefixes = ["/".join(parts[:i]) + "/" + d + "/"
                    for i in range(1, len(parts)) for d in _TOP_TEST_DIRS]
        return any(f.rel.startswith(p) for f in self.tests for p in prefixes)

    def top_names_unit(self, folder: str) -> bool:
        """A top-level test names or mirrors the unit (tests/<name>/, tests/<name>.Tests/,
        tests/test_<name>*, <name>.test.*) or imports it (Python `import pkg` / `from pkg.sub`,
        JS '../<name>/...')."""
        name = folder.rsplit("/", 1)[-1]
        for f in self.top:
            parts = f.rel.split("/")
            base = parts[-1]
            if any(p == name or p.split(".")[0] == name for p in parts[1:-1]) \
                    or base.startswith(("test_" + name, name + "_test", name + ".")):
                return True
        mod = folder[4:] if folder.startswith("src/") else folder
        dotted = mod.replace("/", ".")
        js_ref = re.compile(r"[\"'][^\"'\n]*/" + re.escape(name) + r"(?:/|[\"'])")
        if self.cache is not None:  # texts stay in the bounded cache, not in the index
            cache = self.cache
            pairs = ((f, cache.text(f)) for f in self.top)  # type: object
        else:
            if self._imports is None:
                self._imports = [(f, _read(f)) for f in self.top]
            pairs = self._imports
        for f, text in pairs:  # type: ignore[attr-defined]
            if f.ext == ".py":
                if any(m == dotted or m.startswith(dotted + ".")
                       for m in _PY_IMPORT_LINE_RE.findall(text)):
                    return True
            elif js_ref.search(text):
                return True
        return False

    def _colocated(self, folder: str) -> bool:
        """Tests live next to the code under the unit's parent folder (src/auth/x.spec.ts beside
        src/posts): the project tests per folder, so a folder without tests is untested."""
        parent = folder.rsplit("/", 1)[0] + "/"
        return any(f.rel.startswith(parent) and f.rel not in self._top_set for f in self.tests)

    def _integration_tree(self) -> bool:
        if self._integration is None:
            self._integration = any(p.lower() in _INTEGRATION_DIRS or "integration" in p.lower()
                                    for f in self.top for p in f.rel.split("/")[1:-1])
        return self._integration

    def _runner_evidence(self, folder: str) -> bool:
        """A test runner configured at the scan root or in an ancestor of the unit, or a CI
        config (GitHub Actions, GitLab CI, CircleCI) that runs a test command."""
        if self.root is not None:
            parts = folder.split("/")
            for i in range(len(parts)):
                rel = "/".join(parts[:i])
                if rel not in self._runner:
                    self._runner[rel] = _runner_config(os.path.join(self.root, *rel.split("/"))
                                                       if rel else self.root)
                if self._runner[rel]:
                    return True
        if self._ci is None:
            self._ci = any(_ci_test_step(text_of(f, self.cache)) for f in self.files
                           if f.is_infra and _CI_PATH_RE.search(f.rel))
        return self._ci

    def root_tree_covers(self, folder: str) -> bool:
        """A root test tree covers a deeper unit when the project keeps its tests there (no
        tests beside the unit's siblings), the tree holds integration/e2e/feature tests that
        exercise the app through its entry points, and it is run as the suite (a configured test
        runner or a CI test step). A runner alone with unrelated root tests proves nothing about
        the unit (it stays reported)."""
        return (bool(self.top) and not self._colocated(folder)
                and self._integration_tree() and self._runner_evidence(folder))


def rule_no_tests(files: List[FileInfo], units: List[str],
                  all_files: Optional[List[FileInfo]] = None, deadline: Optional[float] = None,
                  timings: Optional[Dict[str, float]] = None,
                  cache: Optional[FileCache] = None) -> List[Signal]:
    """Folder unit with non-test code and no test file for it.

    Tests count when they are inside the unit, or in a tests/, test/ or __tests__/ folder next to the
    unit or next to any of its ancestors below the scan root (looked up in the whole scan root, so
    tests outside --scope count). A root test tree (tests/, test/, __tests__/, spec/ or a pytest
    testpaths folder) covers a depth-1 unit; a deeper unit is covered when a test there names,
    mirrors or imports it, or when the project keeps its tests in that tree (no tests beside the
    unit's sibling folders), the tree has an integration/e2e/feature folder, and it is run as the
    suite (a runner config such as pytest.ini, jest.config.*, phpunit.xml, or a CI test step);
    otherwise the unit is reported (a runner config with only unrelated root tests is not proof
    of coverage). Every signal is low confidence (the registry ceiling). Config, settings, docs, migration/seed,
    translation, template and tooling folders, folders of only config-named files or view
    templates, and example/demo folders are skipped.
    False-positive risks: tests in a sibling package with an unrelated name, generated code,
    presentational or boilerplate-only folders.
    """
    index = _TestIndex(all_files if all_files is not None else files, cache)
    out: List[Signal] = []
    for folder in units:
        inside = _folder_files(files, folder)
        if not inside or any(f.is_test or f.rel in index.test_set for f in inside) \
                or _untestable_unit(folder, inside, cache):
            continue
        if folder != "." and index.in_sibling_dirs(folder):
            continue
        conf = "medium"
        if index.top:
            if "/" not in folder or index.top_names_unit(folder) or index.root_tree_covers(folder):
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


# File rules that also take the scan's file cache (they read the raw text or other files).
_CACHE_RULES = (rule_unbounded_query, rule_swallowed_error)


def run_rules(files: List[FileInfo], folders: List[dict], scopes: Optional[List[str]] = None,
              all_files: Optional[List[FileInfo]] = None, deadline: Optional[float] = None,
              timings: Optional[Dict[str, float]] = None,
              cache: Optional[FileCache] = None,
              notes: Optional[List[str]] = None) -> List[Signal]:
    """Run every rule. `folders` are candidate folders of `files`; `scopes` are the scan scope
    roots; `all_files` is the unscoped walk (used to find a top-level tests folder). Stops early
    when `deadline` (time.perf_counter) passes; `timings` collects seconds per rule. `cache` is
    the scan's file cache (files are read directly without one).

    An exception in one rule on one file drops only that rule's signals for that file: the other
    rules and files still run, and `notes` (optional) gets one line per failing rule ("rule X
    failed on <file> (Type)", plus how many more files failed)."""
    signals: List[Signal] = []
    texts: Dict[str, str] = {}  # only without a cache (the cache holds them, bounded)
    failed: Dict[str, Tuple[str, str]] = {}  # rule id -> (first failing file, exception type)
    failures: Dict[str, int] = {}  # rule id -> number of failing files

    def guarded(rule: Callable[..., List[Signal]], where: str, *args: object) -> List[Signal]:
        try:
            return rule(*args)
        except Exception as exc:  # one rule failing on one file must not drop the family
            rid = rule.__name__[len("rule_"):].replace("_", "-")
            failed.setdefault(rid, (where, type(exc).__name__))
            failures[rid] = failures.get(rid, 0) + 1
            return []

    for fi in files:
        if deadline is not None and time.perf_counter() > deadline:
            break
        if fi.is_secret_file:
            signals.extend(guarded(rule_private_key_file, fi.rel, fi))
            continue
        if fi.is_config:
            raw = text_of(fi, cache)
            text = raw if fi.ext == ".json" else strip_config_comments(raw)
            signals.extend(guarded(rule_hardcoded_secret, fi.rel, fi, text))
            continue
        if fi.lang is None or fi.lang in _SKIP_LANGS:
            continue
        if cache is not None:
            text = cache.code(fi)
        else:
            text = strip_comments(_read(fi), fi.lang)
            texts[fi.rel] = text
        if not fi.is_test:
            for rule in FILE_RULES:
                t0 = time.perf_counter()
                signals.extend(guarded(rule, fi.rel, fi, text, cache) if rule in _CACHE_RULES
                               else guarded(rule, fi.rel, fi, text))
                if timings is not None:
                    timings[rule.__name__] = timings.get(rule.__name__, 0.0) + time.perf_counter() - t0
        t0 = time.perf_counter()
        signals.extend(guarded(rule_hardcoded_secret, fi.rel, fi, text))
        if timings is not None:
            timings["rule_hardcoded_secret"] = (timings.get("rule_hardcoded_secret", 0.0)
                                                + time.perf_counter() - t0)
    units = folder_units(folders, scopes)
    signals.extend(guarded(rule_console_logging_only, "folder units", files, units, texts, cache))
    signals.extend(guarded(rule_no_tests, "folder units", files, units, all_files, None, None,
                           cache))
    if notes is not None:
        for rid, (where, kind) in sorted(failed.items()):
            more = failures[rid] - 1
            notes.append("rule %s failed on %s (%s)%s: its signals there are missing" % (
                rid, where, kind, " and %d more file(s)" % more if more else ""))
    return signals

# ---------------------------------------------------------------- registry
# confidence = the ceiling: report._stamp caps every signal at it, and measured.json can only lower
# it. `limitations` states the confidence the scan actually reports (a rule capped at low says
# "Always low confidence"; tests/test_registry.py checks no text promises a tier above the
# ceiling) and names the known false positives/negatives.
_BRACE = ["Python", "JavaScript", "TypeScript", "Go", "Java", "Kotlin", "C#", "PHP"]
_PJTG = ["Python", "JavaScript", "TypeScript", "Go"]
_ALL = _BRACE + ["Ruby"]
for _m in [
    RuleMeta("http-no-timeout", "3", "Reliability", "High", "medium",
             "Outbound HTTP call (fetch, axios, requests/httpx, Go net/http, Java HttpClient/"
             "RestTemplate/OkHttp, C# HttpClient, Ruby Net::HTTP, PHP curl/file_get_contents) "
             "without a timeout or abort signal.",
             "A timeout set on a shared client, axios.create defaults, session, wrapper or context "
             "deadline is not seen; timeout forwarded through **kwargs is flagged. Only the listed "
             "call forms are recognised. Java/C#/Ruby/PHP checks are file- or statement-level (a "
             "timeout set anywhere in the file, e.g. read_timeout, CURLOPT_TIMEOUT, .Timeout =, "
             "counts for every call there); library default timeouts (OkHttp, .NET HttpClient, "
             "Net::HTTP) are not credited and RestTemplateBuilder/IHttpClientFactory clients are "
             "not seen. Test files are skipped; RSpec files (*_spec.rb, *.spec.rb) count as test files (1.1.0).",
             "Set an explicit timeout on every outbound call or on the shared client.",
             _PJTG + ["Java", "Kotlin", "C#", "Ruby", "PHP"]),
    RuleMeta("query-in-loop", "5", "Scalability", "High", "low",
             "DB/ORM call repeated inside a loop body (N+1 queries); the snippet names the call and "
             "the loop (\"Order.objects.get inside for loop at line 12\").",
             "Calls are recognised by receiver and method (Django managers and related managers, "
             "SQLAlchemy session, Prisma, TypeORM/Sequelize/Mongoose, cursor/connection/db/pool "
             "execute|query, JDBC statements, Go database/sql, EF Core, Laravel, PDO); a plain .get "
             "(dicts, HTTP sessions, Paths.get) and self.execute are not DB calls, and differently "
             "named DB handles are missed. Loop bodies are limited to 30 lines; Python comprehensions "
             "count only on one line. Skipped: loops over schema objects (fields, columns, tables, "
             "relations, _meta), batches/chunks/pages, Django transaction.atomic around one write "
             "keyed by id/pk, small fixed loops (range(<=20), literal collections, i < 5, a "
             "collection literal bound in code and not grown or rebound), batched calls (IN, ANY, "
             "__in=, in:), related-manager reads after prefetch_related/select_related/joinedload, "
             "statement lists (sql/statements loops, *_sql() builders, DDL/PRAGMA literals), "
             "catalog queries, Python docstrings and migration files. Always low confidence (the rule ceiling), also for "
             "other transactions in the loop (BEGIN, .transaction(, $transaction) and cached paths (a "
             "cache.get/get_or_set/redis.get/memo[...]/in cache check before the call, or a Python "
             "def decorated with @lru_cache/@cache/@cached/@memoize; snippet suffix \" (cached "
             "path)\"). A data collection named like schema objects or batches (pages, tables, "
             "fields) is missed, as is lazy FK attribute access (o.customer). A loop over a few "
             "items held in a variable and non-DB classes with ORM-like static methods "
             "(Foo.findById) are still reported.",
             "Batch the lookups (IN query, join, prefetch/eager load) outside the loop.", _BRACE),
    RuleMeta("unbounded-query", "4", "Scalability", "Medium", "low",
             "Query that reads every row: SELECT without LIMIT/TOP/FETCH FIRST/COUNT, find({}), "
             "findMany without take, objects.all() in a request handler or list view.",
             "Always low confidence (the rule ceiling). Skipped as bounded: WHERE id/pk = "
             "lookups, fetchone/QueryRow/.first() reads (also QueryRow(\"SELECT ...\") on the same "
             "line), aggregates without GROUP BY, system-catalog "
             "and PostGIS metadata introspection (also a WITH over a catalog), subqueries in "
             "UPDATE/INSERT/DELETE, FROM (...) fragments, tables supplied at runtime (FROM `%s`, "
             "FROM \" + name), findMany with a ...spread argument, chained "
             "objects.all().delete()/count()/filter(), SQL in Python docstrings and migration files. "
             "objects.all() is reported only inside a def with a request/req parameter, in a file "
             "that defines routes, or in the body/get_queryset of a DRF or class-based list view "
             "(ViewSet, APIView, ListAPIView, ListView ...; bases on one line) without "
             "pagination_class/paginate_by and without DEFAULT_PAGINATION_CLASS in the file or a "
             "settings module in an ancestor folder or its direct children. Small lookup tables, limits applied by a wrapper, single-row lookups on other "
             "unique columns and prose containing select ... from in non-docstring strings are still "
             "reported.",
             "Add LIMIT/pagination or stream results.", _BRACE),
    RuleMeta("hardcoded-secret", "9", "Security", "Critical", "low",
             "Known provider token (AWS key id, Stripe live key, GitHub, GitLab, Slack, Google, "
             "OpenAI, Anthropic), PEM private key header or committed key file (Critical), or a "
             "strong credential-like assignment in code or config (Medium).",
             "Only known formats and private keys are Critical; generic `key = value` matches are "
             "at most Medium. Always low confidence (the rule ceiling), known formats included: confidence does not tell "
             "a provider token from a heuristic match (severity does). A generic value fires only when it has (12+ characters and at least "
             "3.5 bits of entropy per character) or (8+ characters, 3 of the 4 character classes "
             "lower/upper/digit/symbol and at least 2.8 bits per character); letters-only values "
             "never fire. So passphrases and word-like passwords (correct-horse-battery-staple, "
             "letters-only words) are missed, and a random-looking non-secret value can still "
             "match. Generic values are also skipped when containing whitespace (labels, "
             "validation messages), URLs and absolute paths, placeholders (example, sample, "
             "changeme, dummy, fake, xxx, your_, <...>, ${...}, {{...}}, env lookups), password "
             "hashes, regex/template text, dummy values, identifier names (RESET_PASSWORD, "
             "resetPassword, set-password, auth.session.key) and pipe rule lists "
             "(required|min:8); also in OpenAPI/Swagger documents, under `example:` keys, on "
             "doctest lines, inside Python docstrings and bare triple-quoted doc blocks, and for "
             "database-service passwords (POSTGRES_PASSWORD, MYSQL_ROOT_PASSWORD ...) under a "
             "`services:` key in CI workflow files. Known formats inside Python docstrings and doc "
             "blocks are still reported. A known-format token containing "
             "\"example\" is skipped. Comments are stripped, so secrets in comments are not seen. "
             "Unquoted values are matched only in config files. In test code files only known "
             "formats are reported, so a real credential written as a test "
             "fixture is missed; config files in test folders and key files in test or fixture "
             "folders (testdata/, fixtures/, test/, spec/) are still reported. "
             "PHP/Ruby `'password' => 'value'` pairs count as assignments. "
             "Key words: password, passwd, secret, token, api key, access key, client secret, "
             "secret access key, private key (`_` or `-` between words, any prefix); other key "
             "names are missed. In config files `#` starts a comment only at line start or after "
             "whitespace (dotenv/YAML). Quoted values spanning lines and UTF-16 files are not "
             "matched. RSpec files (*_spec.rb, *.spec.rb) count as test files (1.1.0).",
             "Move the secret to a secret manager or environment, and rotate it.",
             _ALL + ["Config"]),
    RuleMeta("sql-string-concat", "6", "Security", "Critical", "low",
             "Untrusted input (request params, query, body, headers, route params, input(), CLI "
             "args) reaching the SQL text of a database call in the same function by "
             "concatenation or interpolation; the snippet reads \"<var> from line <src> reaches "
             "SQL at line <sink>\" (injection lead).",
             "Intra-function only: flow through other functions or files (2.0.0), object fields, "
             "return values and containers other than append/push lists is not followed, and SQL "
             "built from a source with no traced flow emits nothing. Sources: Flask/Django/DRF/"
             "FastAPI request objects and route-handler parameters, Express/Koa req.*, Go "
             "net/http, gorilla/chi, gin/echo context, Spring @RequestParam/@PathVariable/"
             "@RequestBody parameters, servlet getParameter, ASP.NET [From*] and controller-action "
             "string parameters, Request.Query/Form, Rails params/request, Laravel $request->/"
             "request()/controller route parameters, PHP superglobals, sys.argv/input/"
             "process.argv/os.Args/ARGV. Numeric-typed parameters and casts/escapes (int(), "
             "Number(), strconv.Atoi, Integer.parseInt, int.Parse, intval, .to_i, quote_name, "
             "escape) clear the taint; custom validators and allow-lists are not seen. Sinks "
             "need SQL keywords in the argument (SELECT..FROM, INSERT INTO, UPDATE..SET, DELETE "
             "FROM, any case) or a variable built from them; raw fragments (ActiveRecord where/"
             "order, whereRaw/orderByRaw, gorm Where/Order, knex *Raw) need only a string literal. "
             "Bound parameters, tagged templates (sql`...`, $queryRaw`...`) and ORM filters are not "
             "sinks. A value placed as an identifier (FROM/JOIN/INTO/ORDER BY + value) carries "
             "uncertain_reason, as does a format placeholder there (FROM %s, FROM "
             "{}, String.format/Sprintf). Values inside hash/map literals (\"k\" => v, {\"k\": v}, "
             "Go map[string]interface{}{...}) are ORM conditions and not reported. SQL-text "
             "constants at module or class level are followed into functions. A re-assignment "
             "inside a branch or loop does not clear taint. Ruby calls without parentheses are not "
             "seen, and a stray quote in a Ruby/PHP/JS regex literal can flip string tracking for "
             "the rest of the literal (Ruby/PHP strings may span lines). Test files are skipped;"
             " RSpec files (*_spec.rb, *.spec.rb) count as test files (1.1.0). Always low confidence (the rule ceiling).",
             "Use parameterised queries or the ORM query builder; allow-list identifiers.", _ALL),
    RuleMeta("retry-no-backoff", "1", "Reliability", "Medium", "low",
             "Loop mentioning retry/retries/attempt around a network call with no sleep or backoff.",
             "Needs a retry, retries or attempt word plus a recognised network call (fetch, axios, "
             "requests/httpx, net/http, urlopen) in the loop; backoff in a helper or HTTP client "
             "library (urllib3 Retry, axios-retry) is not seen; login-attempt loops can match.",
             "Add exponential backoff with jitter and a retry cap.", _PJTG),
    RuleMeta("swallowed-error", "7", "Reliability", "Medium", "low",
             "Empty catch block, except: pass, Go _ = err, empty if err != nil {} or empty Ruby "
             "rescue that hides a failure.",
             "Intentional handling is not reported: a comment in the handler or on its closing line "
             "that explains it (at least two words beyond ignore/ok/pass/noop/nothing/silent/"
             "swallow/log/todo/fixme and filler words; tool directives such as # noqa do not count), "
             "the optional-dependency idiom (a literal import/require in the try body, and either "
             "an ImportError/ModuleNotFoundError/LoadError/ImproperlyConfigured handler or a body of "
             "only imports plus assignments that use them), a try body that is one best-effort "
             "cleanup call (close, unlink, rmtree, chown, chmod, kill, shutdown, ...), and in Python "
             "try/except/else, a one-assignment try whose fallback is set before or checked after, "
             "lookup-miss exceptions (KeyError, LookupError, StopIteration, *DoesNotExist, "
             "ImportError) and, for a named (non-broad) class, a read or pop on a cache-named "
             "receiver or a fall-through fallback (the try body returns and the next statement "
             "returns or raises). Other named Python exceptions and a Ruby rescue not naming "
             "StandardError/Exception get severity Low. A broad handler with only a vague "
             "comment, undocumented best-effort code under a broad except with no recognised "
             "idiom, and empty JS/Go catches are still reported. Always low confidence (the rule ceiling). Try bodies are read at most 50 lines back (Python/Ruby) "
             "or up to 4000 characters (brace languages); braces inside strings are not tracked. "
             "Test files are skipped; RSpec files (*_spec.rb, *.spec.rb) count as test files (1.1.0).",
             "Log, handle or rethrow the error.", _ALL),
    RuleMeta("console-logging-only", "3", "Observability", "Low", "low",
             "Folder (with routes or at least 100 LOC) that logs only via console.log, print or fmt.Println.",
             "A logger configured in another folder or injected by the platform, CLIs where printing "
             "is the interface, and logger imports in unusual forms are not seen. Example/demo "
             "folders are skipped. Always low confidence.",
             "Adopt a structured logger with levels and request correlation.", _PJTG),
    RuleMeta("no-tests", "6", "Maintainability", "Medium", "low",
             "Folder unit with non-test code and no test file for it.",
             "Tests count inside the unit and in tests/, test/ or __tests__/ next to the unit or any "
             "ancestor below the scan root (also outside --scope). A root test tree (tests/, test/, "
             "__tests__/, spec/, or test-named files under a pytest testpaths folder) covers depth-1 "
             "units; a deeper unit is covered when a test there names, mirrors or imports it "
             "(tests/<name>/, tests/<name>.IntegrationTests/), or when the project keeps its tests in "
             "that tree (no tests beside the unit's sibling folders), the tree has an integration, "
             "e2e, feature, functional, api or system folder, and it is run as the suite: a runner "
             "config at the root or in an ancestor (pytest.ini, conftest.py, pytest section in "
             "setup.cfg/tox.ini/pyproject, jest/vitest/karma/playwright/cypress config, phpunit.xml, "
             ".rspec, package.json jest key or a test script that is more than echo/exit), or a "
             "GitHub/GitLab/CircleCI run:/script:/command: value running a test command (install "
             "commands, job names and comments do not count). Otherwise a deeper unit is reported, "
             "also when only a runner config and unrelated root tests exist. Always low confidence (the rule ceiling). "
             "Config, "
             "docs, migration/seed (migrate, seeds, factories), translation (lang, locales, i18n), "
             "templates, tooling, config-file-only, view-template-only (Blade, ERB, Twig, ...) and "
             "example folders, and settings/ folders of Django-style settings modules, are skipped. "
             "Tests in an unrelated sibling package, generated code, and presentational or "
             "boilerplate-only folders are not recognised. RSpec files (*_spec.rb, *.spec.rb) count as test files (1.1.0).",
             "Add tests for the folder's critical paths.", _BRACE),
]:
    register(_m)
