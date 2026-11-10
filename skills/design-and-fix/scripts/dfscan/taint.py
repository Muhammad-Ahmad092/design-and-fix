"""Intra-function source-to-sink tracking for SQL injection leads (sql-string-concat).

`flows(text, lang)` reports untrusted input (request parameters, query, body, headers, route
parameters, `input()`, CLI arguments) that reaches the SQL text argument of a database call
(`execute`, `raw`, `query`, `Exec`, `QueryRow`, `$wpdb->query`, `DB::select`, `createQuery`, ...)
inside one function, directly or through a chain of local assignments.

How it works, in one linear pass per file:
- a "code view" of the text blanks string-literal contents but keeps interpolated code
  (f"{x}", `${x}`, "#{x}", "$x", $"{x}") and every newline, so offsets and lines match the text;
- lines are grouped into statements (open brackets, multi-line strings, heredocs, trailing `+`);
- a function header (def, func, a line ending in `{` that is not a control statement, ...)
  at the same or a lower indentation than the current one starts a new scope; nested closures
  share their parent's scope;
- an assignment whose right side holds a source or a tainted variable taints its target,
  and a plain re-assignment clears it; casts and escape helpers (int(), Number(), strconv.Atoi,
  intval, .to_i, quote_name ...) remove the taint;
- a sink is reported when its SQL argument holds a tainted value outside a plain string
  literal and the argument is SQL text (SELECT..FROM, INSERT INTO, UPDATE..SET, DELETE FROM,
  case-insensitive) or a variable built from SQL text. Raw-fragment APIs (where("..."),
  whereRaw, orderByRaw, gorm Where) need only a string literal in the argument.

Out of scope (2.0.0): flow across functions and files, fields, containers other than lists
built with append/push, and taint through return values of other functions.
"""
from __future__ import annotations

import bisect
import re
from typing import Dict, List, NamedTuple, Optional, Pattern, Set, Tuple


class Flow(NamedTuple):
    source_line: int
    sink_line: int
    variable: str
    identifier: bool  # the value lands where SQL expects an identifier (table, column, ORDER BY)


_LANG_KEYS = {"Python": "py", "JavaScript": "js", "TypeScript": "js", "Go": "go", "Java": "java",
              "Kotlin": "kt", "C#": "cs", "PHP": "php", "Ruby": "rb"}
MAX_STMT_LINES = 30
MAX_SINKS_PER_STMT = 8  # bounds the work on a line of many nested sink calls (linear time)
_NESTING_MAX = 20  # interpolation nesting deeper than this is treated as plain string text


def _lang_key(lang: str) -> Optional[str]:
    if lang in _LANG_KEYS.values():
        return lang
    return _LANG_KEYS.get((lang or "").split("/")[0])


# ---------------------------------------------------------------- SQL words (linear time)
_SQL_FIXED_RE = re.compile(r"\b(?:INSERT|MERGE)\s+INTO\b|\bDELETE\s+FROM\b", re.I)
_SQL_PAIRS = ((re.compile(r"\bSELECT\b", re.I), re.compile(r"\bFROM\b", re.I)),
              (re.compile(r"\bUPDATE\b", re.I), re.compile(r"\bSET\b", re.I)))


def sql_words(s: str) -> bool:
    """SELECT..FROM, INSERT INTO, MERGE INTO, UPDATE..SET or DELETE FROM in `s`, any case.
    Linear: a later keyword follows some SELECT iff it follows the first one."""
    if _SQL_FIXED_RE.search(s):
        return True
    for head, tail in _SQL_PAIRS:
        m = head.search(s)
        if m is not None and tail.search(s, m.end()):
            return True
    return False


# ---------------------------------------------------------------- code view
class _View:
    """Text with string-literal contents blanked (interpolated code kept) and the
    character spans of literals that cross a newline."""

    _STARTS = {
        "py": re.compile(r"[\"'{}]"),
        "js": re.compile(r"[\"'`{}]"),
        "go": re.compile(r"[\"'`{}]"),
        "java": re.compile(r"[\"'{}]"),
        "kt": re.compile(r"[\"'{}]"),
        "cs": re.compile(r"[\"'{}]"),
        "php": re.compile(r"[\"'{}]|<<<"),
        "rb": re.compile(r"[\"'`{}]|<<[~-]?(?=['\"]?[A-Za-z_])"),
    }
    _PHP_HEREDOC_RE = re.compile(r"<<<[ \t]*(['\"]?)([A-Za-z_]\w*)\1")
    _RB_HEREDOC_RE = re.compile(r"<<([~-]?)(['\"]?)([A-Za-z_]\w*)\2")

    def __init__(self, text: str, lk: str) -> None:
        self.text = text
        self.lk = lk
        self.out = list(text)
        self.multiline: List[Tuple[int, int]] = []
        self._scan(0, len(text), False, 0)
        self.code = "".join(self.out)

    def _blank(self, k: int) -> None:
        if self.text[k] != "\n":
            self.out[k] = " "

    def _note(self, a: int, b: int) -> None:
        if self.text.find("\n", a, b) >= 0:
            self.multiline.append((a, b))

    def _scan(self, i: int, limit: int, nested: bool, depth: int) -> int:
        """Scan code from i; when `nested` (inside an interpolation) stop at the unmatched `}`
        and return its index. Returns the index where scanning stopped."""
        text = self.text
        rx = self._STARTS[self.lk]
        braces = 0
        while i < limit:
            m = rx.search(text, i, limit)
            if m is None:
                return limit
            j = m.start()
            c = text[j]
            if c == "{":
                braces += 1
                i = j + 1
            elif c == "}":
                if nested and braces == 0:
                    return j
                braces = max(0, braces - 1)
                i = j + 1
            elif c == "<":
                i = self._heredoc(j, limit, depth)
            else:
                i = self._string(j, limit, depth)
        return limit

    def _kind(self, j: int) -> Tuple[str, Optional[str], bool, bool, bool]:
        """(closing delimiter, interpolation kind, escapes?, multiline?, doubled-quote escape?)
        for the literal whose quote is at j."""
        text, lk = self.text, self.lk
        q = text[j]
        triple = q in "\"'" and text.startswith(q * 3, j)
        close = q * 3 if triple else q
        interp: Optional[str] = None
        escapes, multiline, doubled = True, triple, False
        if lk == "py":
            k = j
            while k > 0 and j - k < 2 and text[k - 1] in "rRbBuUfF":
                k -= 1
            if (k == 0 or not (text[k - 1].isalnum() or text[k - 1] == "_")) and "f" in text[k:j].lower():
                interp = "brace"
        elif lk == "js":
            if q == "`":
                multiline = True
                tagged = j > 0 and (text[j - 1].isalnum() or text[j - 1] in "_$")
                interp = None if tagged else "dollar"
        elif lk == "go":
            if q == "`":
                multiline, escapes = True, False
        elif lk == "kt":
            if q == '"':
                interp = "kt"
        elif lk == "cs":
            k = j
            while k > 0 and j - k < 2 and text[k - 1] in "$@":
                k -= 1
            prefix = text[k:j]
            if q == '"':
                if "$" in prefix:
                    interp = "brace"
                if "@" in prefix:
                    multiline, escapes, doubled = True, False, not triple
                if triple:
                    escapes = False
        elif lk == "php":
            interp = "php" if q == '"' else None
            multiline = True
        elif lk == "rb":
            interp = "hash" if q in "\"`" else None
            multiline = True
        return close, interp, escapes, multiline, doubled

    def _string(self, j: int, limit: int, depth: int) -> int:
        """Blank the literal starting at quote j (keeping interpolated code); return the index
        after it."""
        text = self.text
        close, interp, escapes, multiline, doubled = self._kind(j)
        if depth >= _NESTING_MAX:
            interp = None
        k = j + len(close)
        while k < limit:
            c = text[k]
            if c == "\\" and escapes:
                self._blank(k)
                if k + 1 < limit:
                    self._blank(k + 1)
                k += 2
                continue
            if c == "\n":
                if not multiline:
                    break
                k += 1
                continue
            if text.startswith(close, k):
                if doubled and text.startswith(close, k + 1):
                    self._blank(k)
                    self._blank(k + 1)
                    k += 2
                    continue
                self._note(j, k)
                return k + len(close)
            if interp is not None:
                nk = self._interp(k, limit, interp, depth)
                if nk != k:
                    k = nk
                    continue
            self.out[k] = " "
            k += 1
        self._note(j, k)
        return max(k, j + 1)

    def _interp(self, k: int, limit: int, kind: str, depth: int) -> int:
        """If an interpolation starts at k, keep its code, blank its delimiters and return the
        index after it; otherwise return k."""
        text = self.text
        opener = 0
        if kind == "brace" and text[k] == "{":
            if text.startswith("{{", k):
                self._blank(k)
                self._blank(k + 1)
                return k + 2
            opener = 1
        elif kind in ("dollar", "kt") and text.startswith("${", k):
            opener = 2
        elif kind == "hash" and text.startswith("#{", k):
            opener = 2
        elif kind == "php" and text.startswith("{$", k):
            opener = 1
        if opener:
            for p in range(k, k + opener):
                self._blank(p)
            end = self._scan(k + opener, limit, True, depth + 1)
            if end < limit:
                self._blank(end)
                return end + 1
            return end
        if (kind in ("kt", "php") and text[k] == "$" and k + 1 < limit
                and (text[k + 1].isalpha() or text[k + 1] == "_")):
            e = k + 1
            while e < limit and (text[e].isalnum() or text[e] == "_"):
                e += 1
            if kind == "kt":
                self._blank(k)
            return e
        return k

    def _heredoc(self, j: int, limit: int, depth: int) -> int:
        text, lk = self.text, self.lk
        if lk == "php":
            m = self._PHP_HEREDOC_RE.match(text, j)
            if m is None:
                return j + 3
            interp = "php" if m.group(1) != "'" else None
            ident = m.group(2)
        else:
            m = self._RB_HEREDOC_RE.match(text, j)
            if m is None:
                return j + 2
            bare = m.group(1) == "" and m.group(2) == ""
            if bare and (not m.group(3).isupper()
                         or (j > 0 and (text[j - 1].isalnum() or text[j - 1] == "_"))):
                return j + 2  # `a<<B` / `a << b`: a shift or append, not a heredoc
            interp = "hash" if m.group(2) != "'" else None
            ident = m.group(3)
        eol = text.find("\n", m.end(), limit)
        if eol < 0:
            return m.end()
        self._scan(m.end(), eol, False, depth)  # the rest of the marker line is code
        k = body = eol + 1
        end = limit
        while k < limit:
            e = text.find("\n", k, limit)
            e = limit if e < 0 else e
            stripped = text[k:e].strip()
            if stripped == ident or (lk == "php" and stripped.startswith(ident)
                                     and not stripped[len(ident):len(ident) + 1].isalnum()):
                end = k
                break
            k = e + 1
        p = body
        while p < end:
            if interp is not None and depth < _NESTING_MAX:
                nxt = self._interp(p, end, interp, depth)
                if nxt != p:
                    p = nxt
                    continue
            self._blank(p)
            p += 1
        stop = text.find("\n", end, limit) if end < limit else limit
        stop = limit if stop < 0 else stop
        self._note(j, stop)
        return stop


# ---------------------------------------------------------------- per-language tables
def _rx(s: str) -> Pattern[str]:
    return re.compile(s)


_SOURCES: Dict[str, Pattern[str]] = {
    "py": _rx(r"\brequest\.(?:args|form|values|json|data|files|cookies|headers|GET|POST|COOKIES|"
              r"META|FILES|query_params|path_params|body|get_json|view_args|match_info|query|"
              r"rel_url)\b|\bself\.(?:kwargs|request\.(?:GET|POST|data|query_params|COOKIES|META))\b"
              r"|\bsys\.argv\b|(?<![\w.])input\s*\("),
    "js": _rx(r"\b(?:req|request)\.(?:params|query|body|headers|cookies|signedCookies|files?)\b"
              r"|\breq\.(?:get|header|param)\s*\(|\brequest\.(?:json|formData|text)\s*\("
              r"|\bctx\.(?:params|query|querystring|headers|cookies|request\.(?:body|query|headers))\b"
              r"|\bprocess\.argv\b|\bsearchParams\.get\s*\("
              r"|\bevent\.(?:queryStringParameters|pathParameters|body|headers)\b"),
    "go": _rx(r"\b(?:r|req|request)\.(?:URL\.(?:Query\s*\(|Path\b|RawQuery\b)|(?:FormValue|"
              r"PostFormValue|Form|PostForm|MultipartForm|Header|Body|PathValue|Cookie)\b)"
              r"|\bmux\.Vars\s*\(|\bchi\.URLParam\s*\("
              r"|\b(?:c|ctx)\.(?:Param|Params\.ByName|Query|DefaultQuery|QueryArray|QueryMap|GetQuery|"
              r"PostForm|DefaultPostForm|GetPostForm|FormValue|QueryParam|QueryParams|PathParam|"
              r"GetHeader|Cookie|Params|Body)\s*\("
              r"|\bps\.ByName\s*\(|\bos\.Args\b|\bflag\.Args?\s*\("),
    "java": _rx(r"\b(?:request|req|httpRequest|servletRequest)\.(?:getParameter|getParameterValues|"
                r"getParameterMap|getHeader|getHeaders|getQueryString|getCookies|getPathInfo|"
                r"getRequestURI|getReader|getInputStream)\s*\(|\bargs\s*\[\s*\d"),
    "cs": _rx(r"\b(?:HttpContext\.Request|context\.Request|Request|req)\.(?:Query|Form|Headers|"
              r"Cookies|RouteValues|Body|QueryString|Path)\b|\bConsole\.ReadLine\s*\(|\bargs\s*\[\s*\d"),
    "php": _rx(r"\$_(?:GET|POST|REQUEST|COOKIE|SERVER|FILES)\b"
               r"|\$request\s*->\s*(?:input|get|query|post|all|only|except|route|header|cookie|json|"
               r"string|str|validated|safe)\b|\$request\s*->\s*\w+\b(?!\s*\()"
               r"|\brequest\s*\(\s*\)\s*->\s*(?:input|get|query|post|all|only|route|header|cookie|"
               r"json|string)\b|(?<![\w>$])request\s*\(\s*['\"]"
               r"|\bRequest::(?:input|get|query|all|only)\b|\bInput::(?:get|all)\b"
               r"|->\s*request\s*->\s*(?:getVar|getGet|getPost|getJSON|getRawInput)\b|\$argv\b"),
    "rb": _rx(r"(?<![\w.:@$])params\b(?!\s*=[^=>~])|\brequest\.(?:params|query_parameters|"
              r"request_parameters|path_parameters|headers|body|raw_post|query_string|cookies)\b"
              r"|(?<![\w.:@$])cookies\s*\[|\bARGV\b|(?<![\w.:@$])gets\b"),
}
_SOURCES["kt"] = _SOURCES["java"]

# Calls that bind request data into a variable passed by pointer (Go).
_BIND_RE = _rx(r"\.(?:ShouldBind\w*|Bind\w*|BodyParser|Decode)\s*\(\s*&\s*(\w+)")

# Sinks: (regex ending at the opening parenthesis, fragment?). Fragment sinks take SQL pieces
# (where clauses, raw expressions), so they need no SQL keywords, only a string literal.
_SINKS: Dict[str, List[Tuple[Pattern[str], bool]]] = {
    "py": [(_rx(r"\.(?:execute|executemany|executescript|mogrify|raw|exec_driver_sql)\s*\("
                r"|(?<![\w.])(?:RawSQL|read_sql|read_sql_query|text)\s*\("
                r"|\b(?:pd|pandas)\.read_sql\w*\s*\("), False)],
    "js": [(_rx(r"\.(?:query|execute|raw|exec|prepare|run|all|get|each|\$queryRawUnsafe|"
                r"\$executeRawUnsafe|unsafe)\s*\("), False),
           (_rx(r"\.(?:whereRaw|orWhereRaw|havingRaw|orderByRaw|groupByRaw|joinRaw|fromRaw|"
                r"selectRaw|literal)\s*\("), True)],
    "go": [(_rx(r"\.(?:Exec|Query|QueryRow|Prepare|Raw|Select|Get|MustExec|Queryx|QueryRowx|"
                r"NamedExec|NamedQuery|ExecContext|QueryContext|QueryRowContext|PrepareContext|"
                r"SelectContext|GetContext|QueryxContext|QueryRowxContext|NamedExecContext)\s*\("),
            False),
           (_rx(r"\.(?:Where|Or|Not|Order|Group|Having|Joins)\s*\("), True)],
    "java": [(_rx(r"\.(?:execute|executeQuery|executeUpdate|executeLargeUpdate|addBatch|"
                  r"prepareStatement|prepareCall|createQuery|createNativeQuery|createSQLQuery|"
                  r"queryForList|queryForObject|queryForMap|queryForRowSet|queryForStream|query|"
                  r"update|batchUpdate)\s*\("), False)],
    "cs": [(_rx(r"\bnew\s+(?:Sql|Npgsql|MySql|Sqlite|SQLite|Oracle|OleDb|Odbc|DB2)Command\s*\("
                r"|\.(?:FromSqlRaw|ExecuteSqlRaw|ExecuteSqlRawAsync|SqlQueryRaw|Query|QueryAsync|"
                r"QueryFirst|QueryFirstAsync|QueryFirstOrDefault|QueryFirstOrDefaultAsync|QuerySingle|"
                r"QuerySingleAsync|QuerySingleOrDefault|QuerySingleOrDefaultAsync|QueryMultiple|"
                r"QueryMultipleAsync|Execute|ExecuteAsync|ExecuteScalar|ExecuteScalarAsync|"
                r"ExecuteReader|ExecuteReaderAsync)\s*(?:<[^<>()\n]{0,200}>)?\s*\("), False)],
    "php": [(_rx(r"\$wpdb\s*->\s*(?:query|get_results|get_row|get_var|get_col|prepare)\s*\("
                 r"|\bDB::(?:select|selectOne|statement|unprepared|insert|update|delete|"
                 r"affectingStatement|cursor)\s*\("
                 r"|->\s*(?:query|exec|prepare|execute|rawQuery|executeQuery|executeStatement|"
                 r"fetchAll|fetchAssociative|fetchAllAssociative)\s*\("
                 r"|(?<![\w>:$])(?:mysqli_query|mysqli_real_query|mysqli_multi_query|mysql_query|"
                 r"pg_query|pg_send_query|sqlite_query|db2_exec|odbc_exec)\s*\("), False),
            (_rx(r"->\s*(?:whereRaw|orWhereRaw|havingRaw|orHavingRaw|orderByRaw|groupByRaw|"
                 r"selectRaw|fromRaw|joinRaw)\s*\(|\bDB::raw\s*\("), True)],
    "rb": [(_rx(r"\.(?:execute|exec_query|exec_update|exec_delete|exec_insert|select_all|select_one|"
                r"select_value|select_values|select_rows|find_by_sql|count_by_sql|query|exec_params|"
                r"async_exec|exec)\s*\(|(?<![\w.])execute\s*\("), False),
           (_rx(r"\.(?:where|not|rewhere|order|reorder|group|having|joins|pluck|select|from|lock|"
                r"update_all|delete_all|find_by|calculate)\s*\("), True)],
}
_SINKS["kt"] = _SINKS["java"]
_CS_TEXT_SINK = _rx(r"(?<![\w$])CommandText\s*=(?![=>])")

# Sinks whose argument is an identifier position (ORDER BY, GROUP BY expressions).
_IDENT_SINK_RE = _rx(r"(?:order|reorder|group|pluck|Order|Group|orderByRaw|groupByRaw)\s*\($")

# Arguments skipped before the SQL text: Go contexts and destinations, PHP connections.
_SKIP_ARG_RE = _rx(r"\s*(?:&|ctx\b|c\b|context\.|\$(?:conn|link|db|mysqli|connection|dbconn)\b)")

# Sanitisers: calls whose result is no longer attacker-shaped SQL text.
_SANITIZERS: Dict[str, Pattern[str]] = {
    "py": _rx(r"(?<![\w.])(?:int|float|bool|abs|len|Decimal|UUID)\s*\(|\buuid\.UUID\s*\("
              r"|\b(?:quote_name|quote_ident\w*|escape_identifier|escape_string|escape_literal|"
              r"quote|literal|Identifier|Literal)\s*\("),
    "js": _rx(r"(?<![\w.$])(?:parseInt|parseFloat|Number|Boolean|escape|escapeId|format|pgFormat)\s*\("
              r"|\bMath\.\w+\s*\(|\.(?:escape|escapeId|escapeLiteral|escapeIdentifier|format)\s*\("),
    "go": _rx(r"\bstrconv\.(?:Atoi|ParseInt|ParseUint|ParseFloat|ParseBool)\s*\("
              r"|\buuid\.(?:Parse|MustParse)\s*\(|\bpq\.(?:QuoteIdentifier|QuoteLiteral)\s*\("),
    "java": _rx(r"\b(?:Integer|Long|Short|Double|Float|Boolean|UUID|BigDecimal|BigInteger)\."
                r"(?:parse\w*|valueOf|fromString)\s*\(|\b(?:escapeSql|encodeForSQL)\s*\("),
    "cs": _rx(r"\b(?:int|long|short|double|float|decimal|bool|Guid|DateTime|Int32|Int64)\."
              r"(?:Parse|TryParse)\s*\(|\bConvert\.To(?:Int\d+|Double|Decimal|Boolean|DateTime)\s*\("),
    "php": _rx(r"(?<![\w>$])(?:intval|floatval|boolval|abs|count|mysqli_real_escape_string|"
               r"mysql_real_escape_string|pg_escape_string|pg_escape_literal|pg_escape_identifier|"
               r"addslashes|esc_sql|absint)\s*\(|->\s*(?:quote|real_escape_string|escape_string|"
               r"escape|integer|boolean|float)\s*\("),
    "rb": _rx(r"(?<![\w.])(?:Integer|Float)\s*\(|\b(?:sanitize_sql\w*|quote|quote_table_name|"
              r"quote_column_name)\s*\("),
}
_SANITIZERS["kt"] = _SANITIZERS["java"]
_PHP_CAST_RE = _rx(r"\(\s*(?:int|integer|float|double|bool|boolean)\s*\)\s*\$\w+")
_POSTFIX_SANITIZER = {"rb": _rx(r"\.(?:to_i|to_f|to_d|to_r)\b"),
                      "kt": _rx(r"\.(?:toInt|toLong|toDouble|toFloat|toBigDecimal)(?:OrNull)?\s*\(\s*\)")}

_CONTROL = ("if", "for", "foreach", "while", "switch", "catch", "else", "elseif", "elif", "try",
            "do", "with", "synchronized", "using", "lock", "fixed", "when", "return", "unless",
            "finally", "case", "until", "loop", "select", "go", "defer")
_FIRST_WORD_RE = _rx(r"[A-Za-z_]\w*")
_TOKEN_RE = _rx(r"(?<![\w$.:@>])\$?[A-Za-z_]\w*")
_IDENT_CTX_RE = _rx(r"(?:\bfrom|\bjoin|\binto|\bupdate|\btable|\bby)$")
_IDENT_TRIM = " \t\n\"'`+.{$#(@[%|,"
_PLACEHOLDER_IDENT_RE = re.compile(
    r"\b(?:from|join|into|update|table|by)\s+[`'\"\[]?(?:%[sv]|\{\w*\})", re.I)
_RB_MODIFIER_RE = _rx(r"\s(?:if|unless)\s")  # Ruby `q = "" if q.nil?`: a conditional assignment

# Parameters
_PY_ROUTE_DECO_RE = _rx(r"@[\w.]*\.(?:route|get|post|put|patch|delete|api_route|websocket)\s*\("
                        r"|@api_view\b|@action\b")
_PY_SAFE_ANN_RE = _rx(r"\b(?:int|float|bool|UUID|date|datetime|time|Decimal|PositiveInt|conint|"
                      r"Session|AsyncSession|Request|Response|BackgroundTasks|WebSocket)\b"
                      r"|Dep\b|Depends|CurrentUser|Security")
_FLASK_CONVERTER_RE = _rx(r"<(?:int|float|uuid):(\w+)>")
_JAVA_PARAM_RE = _rx(r"@(?:RequestParam|PathVariable|RequestHeader|RequestBody|CookieValue|"
                     r"MatrixVariable|ModelAttribute|QueryParam|PathParam|FormParam|HeaderParam|"
                     r"CookieParam)\b(?:\s*\([^()]{0,200}\))?\s*(?:final\s+)?"
                     r"([\w.]+(?:<[^<>]{0,200}>)?(?:\[\])?)"
                     r"\s+(\w+)")
_KT_PARAM_RE = _rx(r"@(?:RequestParam|PathVariable|RequestHeader|RequestBody|CookieValue|"
                   r"QueryParam|PathParam|FormParam|HeaderParam)\b(?:\s*\([^()]{0,200}\))?\s+(\w+)\s*:\s*"
                   r"([\w.]+)")
_JVM_SAFE_TYPES = {"int", "long", "short", "byte", "double", "float", "boolean", "char", "Integer",
                   "Long", "Short", "Byte", "Double", "Float", "Boolean", "UUID", "LocalDate",
                   "LocalDateTime", "Instant", "BigDecimal", "BigInteger", "Int", "Pageable"}
_CS_FROM_RE = _rx(r"\[\s*From(?:Query|Route|Body|Form|Header)\b[^\]\n]{0,200}\]\s*"
                  r"([\w.]+(?:<[^<>]{0,200}>)?\??)"
                  r"\s+(\w+)")
_CS_ACTION_RE = _rx(r"\[\s*(?:Http(?:Get|Post|Put|Delete|Patch)|Route)\b")
_CS_MINIMAL_RE = _rx(r"\bMap(?:Get|Post|Put|Delete|Patch|Methods)\s*\(")
_CS_STRING_PARAM_RE = _rx(r"\b(?:string|object|dynamic)\??\s+(\w+)\s*(?=[,)=])")
_CS_SAFE_TYPES = {"int", "long", "short", "byte", "double", "float", "decimal", "bool", "Guid",
                  "DateTime", "DateOnly", "int?", "long?", "Guid?", "bool?"}
_JS_PARAM_RE = _rx(r"@(?:Param|Query|Body|Headers)\s*\(([^()]{0,200})\)\s*(\w+)\s*(?::\s*(\w+))?")
_PHP_CONTROLLER_RE = _rx(r"\bextends\s+[\w\\]*Controller\b")
_PHP_PARAM_RE = _rx(r"^\s*(?:(?:public|private|protected|readonly)\s+)*(\??[\w\\]+\s+)?&?(?:\.\.\.)?(\$\w+)")


# ---------------------------------------------------------------- small helpers
def _match_paren(code: str, i: int, end: int) -> int:
    """Index of the bracket closing code[i] (one of ( [ {), or `end` when unclosed."""
    opens, closes = "([{", ")]}"
    depth = 0
    for k in range(i, end):
        c = code[k]
        if c in opens:
            depth += 1
        elif c in closes:
            depth -= 1
            if depth == 0:
                return k
    return end


def _split_args(code: str, a: int, b: int) -> List[Tuple[int, int]]:
    """Top-level comma-separated argument spans of code[a:b]."""
    spans = []
    depth = 0
    start = a
    for k in range(a, b):
        c = code[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            spans.append((start, k))
            start = k + 1
    spans.append((start, b))
    return spans


def _blank_sanitized(seg: str, lk: str) -> str:
    """Blank sanitiser calls (and their arguments) in a code segment."""
    rx = _SANITIZERS[lk]
    out = list(seg)
    pos = 0
    n = len(seg)
    while pos < n:
        m = rx.search(seg, pos)
        if m is None:
            break
        close = _match_paren(seg, m.end() - 1, n)
        for k in range(m.start(), min(close + 1, n)):
            if out[k] != "\n":
                out[k] = " "
        pos = max(close + 1, m.end())
    if lk == "php":
        for m in _PHP_CAST_RE.finditer(seg):
            for k in range(m.start(), m.end()):
                out[k] = " "
    post = _POSTFIX_SANITIZER.get(lk)
    if post is not None:
        floor = 0
        for m in post.finditer(seg):
            k = m.start()
            lo = max(floor, k - 200)
            depth = 0
            while k > lo:
                c = seg[k - 1]
                if c in ")]":
                    depth += 1
                elif c in "([":
                    if depth == 0:
                        break
                    depth -= 1
                elif depth == 0 and not (c.isalnum() or c in "_.:$@"):
                    break
                k -= 1
            for p in range(k, m.end()):
                if out[p] != "\n":
                    out[p] = " "
            floor = m.end()
    return "".join(out)


def _label(raw: str, a: int, b: int) -> str:
    """Source text for a snippet: the match plus a short accessor chain (.x, [..], (..))."""
    n = len(raw)
    if b > a and raw[b - 1] == "(":  # call-shaped sources (c.Param(, input(): take the arguments
        b -= 1
        while b > a and raw[b - 1] in " \t":
            b -= 1
    k = b
    steps = 0
    while k < n and steps < 4:
        c = raw[k]
        if c == "." and k + 1 < n and (raw[k + 1].isalpha() or raw[k + 1] == "_"):
            k += 1
            while k < n and (raw[k].isalnum() or raw[k] == "_"):
                k += 1
        elif c in "[(":
            e = _match_paren(raw, k, min(n, k + 60))
            if e >= min(n, k + 60):
                break
            k = e + 1
        else:
            break
        steps += 1
    return " ".join(raw[a:k].split())[:60]


def _identifier_ctx(window: str) -> bool:
    """`window` (string-literal text just before a value, code blanked) ends in FROM/JOIN/INTO/
    UPDATE/TABLE/BY once quotes, `{` and spaces are skipped: the interpolated value is an
    identifier, not a bindable value. Code such as " AND ".join( is blanked, so it never counts."""
    return bool(_IDENT_CTX_RE.search(window.rstrip(_IDENT_TRIM).lower()))


def _placeholder_ident(lit: str) -> bool:
    """A format placeholder in an identifier position: FROM %s, JOIN {}, ORDER BY %v ..."""
    return bool(_PLACEHOLDER_IDENT_RE.search(lit))


def _hash_value(code: str, at: int) -> bool:
    """The value at `at` is a hash/map literal value ("k" => v, {"k": v}, k: v): ORM conditions
    bind such values, so they are not SQL text."""
    before = code[max(0, at - 20):at].rstrip()
    return before.endswith("=>") or (before.endswith(":") and not before.endswith("::"))


class _Taint(NamedTuple):
    var: str
    line: int
    ident: bool


class _Stmt(NamedTuple):
    a: int          # start offset
    b: int          # end offset
    line: int       # 1-based first line
    indent: int


# ---------------------------------------------------------------- statements
def _statements(text: str, view: _View, lk: str) -> List[_Stmt]:
    code = view.code
    starts = [0]
    k = code.find("\n")
    while k >= 0:
        starts.append(k + 1)
        k = code.find("\n", k + 1)
    nlines = len(starts)
    span_end = {}  # type: Dict[int, int]
    for a, b in view.multiline:
        la = bisect.bisect_right(starts, a) - 1
        lb = bisect.bisect_right(starts, max(a, b - 1)) - 1
        if lb > span_end.get(la, -1):
            span_end[la] = lb

    def line(i: int) -> str:
        e = starts[i + 1] - 1 if i + 1 < nlines else len(code)
        return code[starts[i]:e]

    brace_lang = lk != "py" and lk != "rb"
    out: List[_Stmt] = []
    i = 0
    while i < nlines:
        ln = line(i)
        stripped = ln.strip()
        if not stripped:
            i += 1
            continue
        first = i
        last = i
        depth = 0
        reach = i
        while True:
            cur = line(last)
            depth += cur.count("(") + cur.count("[") - cur.count(")") - cur.count("]")
            if lk == "py":
                depth += cur.count("{") - cur.count("}")
            depth = max(depth, 0)
            reach = max(reach, span_end.get(last, -1))
            if last - first + 1 >= MAX_STMT_LINES or last + 1 >= nlines:
                break
            tail = cur.rstrip()
            if brace_lang and tail.endswith("{") and reach <= last:
                break
            nxt = line(last + 1).lstrip()
            more = (depth > 0 or reach > last or tail.endswith(("+", "\\"))
                    or (lk == "php" and tail.endswith(".") and not tail.endswith("..."))
                    or (nxt.startswith(("+", ".")) and not nxt.startswith("...")))
            if not more:
                break
            last += 1
        a = starts[first]
        b = starts[last + 1] - 1 if last + 1 < nlines else len(code)
        out.append(_Stmt(a, b, first + 1, len(ln) - len(ln.lstrip())))
        i = last + 1
    return out


def _is_header(code: str, lk: str, next_code: str) -> bool:
    s = code.strip()
    if lk == "py":
        return bool(re.match(r"(?:async\s+)?def\s", s))
    if lk == "rb":
        return s.startswith("def ") or s == "def"
    if lk == "go":
        return s.startswith("func ") or s.startswith("func(")
    s = s.lstrip("}) \t")
    if not s:
        return False
    m = _FIRST_WORD_RE.match(s)
    if m is not None and m.group(0) in _CONTROL:
        return False
    if lk == "php" and re.search(r"\bfunction\b", s):
        return True
    if lk == "js" and re.search(r"\bfunction\b", s):
        return True
    if lk == "kt" and re.search(r"\bfun\s", s):
        return True
    if "(" not in s:
        return False
    last_line = s.rsplit("\n", 1)[-1].rstrip()
    if last_line.endswith("{"):
        return True
    return lk in ("cs", "java", "php") and not last_line.endswith(";") and next_code.strip() == "{"


def _params(code: str, lk: str, context: str, php_controller: bool) -> List[str]:
    """Names of header parameters that carry request data; `context` is the raw text of the
    decorators, annotations or attributes directly above the header."""
    names: List[str] = []
    if lk == "py":
        m = re.search(r"\bdef\s+\w+\s*\(", code)
        if m is None:
            return names
        close = _match_paren(code, m.end() - 1, len(code))
        parts = [code[a:b] for a, b in _split_args(code, m.end(), close)]
        plain = [p.strip().split(":")[0].split("=")[0].strip().lstrip("*") for p in parts]
        route = bool(_PY_ROUTE_DECO_RE.search(context))
        req_first = (plain[:1] in (["request"], ["req"])
                     or (plain[:1] in (["self"], ["cls"]) and plain[1:2] in (["request"], ["req"])))
        if not (route or req_first):
            return names
        converted = set(_FLASK_CONVERTER_RE.findall(context))
        for p, name in zip(parts, plain):
            if not name or name in ("self", "cls", "request", "req", "args", "kwargs") or name in converted:
                continue
            ann = p.split(":", 1)[1].split("=", 1)[0] if ":" in p.split("=", 1)[0] else ""
            default = p.split("=", 1)[1] if "=" in p else ""
            if "Depends" in default or "Security" in default or (ann and _PY_SAFE_ANN_RE.search(ann)):
                continue
            names.append(name)
    elif lk == "java":
        for m in _JAVA_PARAM_RE.finditer(code):
            if m.group(1).split("<")[0].split(".")[-1] not in _JVM_SAFE_TYPES:
                names.append(m.group(2))
    elif lk == "kt":
        for m in _KT_PARAM_RE.finditer(code):
            if m.group(2).split(".")[-1].rstrip("?") not in _JVM_SAFE_TYPES:
                names.append(m.group(1))
    elif lk == "cs":
        for m in _CS_FROM_RE.finditer(code):
            if m.group(1) not in _CS_SAFE_TYPES:
                names.append(m.group(2))
        if _CS_ACTION_RE.search(context) or _CS_MINIMAL_RE.search(code):
            names.extend(m.group(1) for m in _CS_STRING_PARAM_RE.finditer(code))
    elif lk == "js":
        for m in _JS_PARAM_RE.finditer(code):
            if "Parse" in m.group(1) or m.group(3) in ("number", "boolean"):
                continue
            names.append(m.group(2))
    elif lk == "php" and php_controller:
        m = re.search(r"\bfunction\s+\w+\s*\(", code)
        if m is not None and not re.search(r"\b(?:private|protected)\s+(?:static\s+)?function\b", code):
            close = _match_paren(code, m.end() - 1, len(code))
            for a, b in _split_args(code, m.end(), close):
                pm = _PHP_PARAM_RE.match(code[a:b])
                if pm is None:
                    continue
                typ = (pm.group(1) or "").strip().lstrip("?").lower()
                if typ in ("", "string", "mixed"):
                    names.append(pm.group(2))
    return names


def _assignment(code: str, lk: str) -> Optional[Tuple[List[str], bool, int]]:
    """(target names, append?, offset of the right side) for a statement's first top-level
    assignment, or None."""
    if "=" not in code:
        return None
    depth = 0
    n = len(code)
    for k in range(n):
        c = code[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "=" and depth <= 0:
            nxt = code[k + 1] if k + 1 < n else ""
            prev = code[k - 1] if k > 0 else ""
            if nxt in "=>~" or prev in "=!<>":
                return None
            op_start = k
            append = False
            if prev in "+.":
                append, op_start = True, k - 1
            elif prev in ":|?&-*/%^":
                op_start = k - 1
                if prev in "|?" and k > 1 and code[k - 2] == prev:
                    op_start = k - 2
                elif prev in "-*/%^&":
                    append = True  # arithmetic compound: keep existing taint
            lhs = code[:op_start]
            return _targets(lhs, lk), append, k + 1
    return None


_LHS_NAME_RE = _rx(r"\$?[A-Za-z_]\w*")


def _targets(lhs: str, lk: str) -> List[str]:
    s = lhs.strip()
    if not s:
        return []
    if lk == "js" and ("{" in s or "[" in s):
        inner = s[s.find("{") if "{" in s else s.find("["):]
        names = []
        for part in inner.strip("{}[] ").split(","):
            part = part.split("=")[0]
            part = part.split(":")[-1].strip().lstrip(".")
            if _LHS_NAME_RE.fullmatch(part):
                names.append(part)
        return names
    if lk == "php" and (s.startswith("[") or s.startswith("list(")):
        return re.findall(r"\$\w+", s)
    if lk in ("py", "js", "kt") and ":" in s and not s.startswith(("[", "(")):
        s = s.split(":", 1)[0]  # drop a type annotation (x: str, const q: string, val q: String)
    parts = s.split(",") if lk in ("py", "go", "rb") else [s]
    names = []
    for p in parts:
        toks = p.split()
        if not toks:
            continue
        name = toks[-1].strip("()")
        if lk == "py" and name.startswith("*"):
            name = name[1:]
        if _LHS_NAME_RE.fullmatch(name):
            names.append(name)
    return names


_APPEND_RE = _rx(r"^\s*(\$?[A-Za-z_]\w*)\s*(?:\.|->)\s*(?:append|Append|AppendFormat|AppendLine|"
                 r"push|concat|add|Add|extend|insert)\s*\(")
_RB_APPEND_RE = _rx(r"^\s*(@?[A-Za-z_]\w*)\s*<<\s")


# ---------------------------------------------------------------- the analysis
class _Analyzer:
    def __init__(self, text: str, lk: str) -> None:
        self.text = text
        self.lk = lk
        self.view = _View(text, lk)
        self.code = self.view.code
        self.starts = [0]
        k = text.find("\n")
        while k >= 0:
            self.starts.append(k + 1)
            k = text.find("\n", k + 1)

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)

    def literal(self, a: int, b: int) -> str:
        """String-literal contents of text[a:b]: code characters blanked."""
        raw, code = self.text[a:b], self.code[a:b]
        return "".join(r if c == " " else " " for r, c in zip(raw, code))

    def uses(self, seg: str, base: int, tainted: Dict[str, _Taint]) -> List[Tuple[int, _Taint]]:
        """(absolute offset, taint) for each tainted variable or direct source in `seg`
        (a sanitiser-blanked code segment starting at `base`), in order."""
        found: List[Tuple[int, _Taint]] = []
        for m in _SOURCES[self.lk].finditer(seg):
            pos = base + m.start()
            found.append((pos, _Taint(_label(self.text, pos, base + m.end()), self.line_of(pos), False)))
        if tainted:
            for m in _TOKEN_RE.finditer(seg):
                t = tainted.get(m.group(0))
                if t is not None:
                    found.append((base + m.start(), t))
        found.sort(key=lambda x: x[0])
        return found

    def ident_ctx(self, pos: int) -> bool:
        return _identifier_ctx(self.literal(max(0, pos - 40), pos))

    def is_sql_name(self, name: str) -> bool:
        return name in self.sqlvars or (name in self.gsql and name not in self.shadow)

    def placeholder_name(self, name: str) -> bool:
        if name in self.sqlvars:
            return self.sqlvars[name]
        return name not in self.shadow and self.gsql.get(name, False)

    def run(self) -> List[Flow]:
        text, code, lk = self.text, self.code, self.lk
        stmts = _statements(text, self.view, lk)
        php_controller = lk == "php" and bool(_PHP_CONTROLLER_RE.search(code))
        tainted: Dict[str, _Taint] = {}
        # SQL-text names: per function (sqlvars), and module/class level (gsql, never cleared by a
        # function header; a local non-SQL assignment shadows one). Value: the SQL text puts a
        # format placeholder in an identifier position (FROM %s).
        self.sqlvars = {}  # type: Dict[str, bool]
        self.gsql = {}  # type: Dict[str, bool]
        self.shadow = set()  # type: Set[str]
        scope_indent = -1
        body_indent = None  # type: Optional[int]
        context: List[str] = []
        flows: Dict[int, Flow] = {}
        for idx, st in enumerate(stmts):
            seg = code[st.a:st.b]
            nxt = code[stmts[idx + 1].a:stmts[idx + 1].b] if idx + 1 < len(stmts) else ""
            head = seg.lstrip()[:1]
            if _is_header(seg, lk, nxt):
                if scope_indent < 0 or st.indent <= scope_indent:
                    tainted, scope_indent, body_indent = {}, st.indent, None
                    self.sqlvars, self.shadow = {}, set()
                for name in _params(seg, lk, "\n".join(context), php_controller):
                    tainted[name] = _Taint(name, st.line, False)
            elif 0 <= scope_indent and st.indent <= scope_indent and head != "{":
                tainted, scope_indent, body_indent = {}, -1, None
                self.sqlvars, self.shadow = {}, set()
            elif body_indent is None and head != "{":
                body_indent = st.indent
            # Decorators / annotations / attributes directly above the next header (raw text,
            # so route templates such as "<int:id>" stay visible).
            if head in ("@", "["):
                context.append(text[st.a:st.b])
            else:
                context = []
            # A re-assignment deeper than the function body's base level (inside a branch or
            # loop) may not run, so it adds taint but never clears it.
            nested = body_indent is not None and st.indent > body_indent
            self.sinks(st, seg, tainted, flows)
            self.assign(st, seg, tainted, nested, scope_indent < 0)
        return [flows[k] for k in sorted(flows)]

    def sinks(self, st: _Stmt, seg: str, tainted: Dict[str, _Taint], flows: Dict[int, Flow]) -> None:
        lk = self.lk
        if not tainted and not _SOURCES[lk].search(seg):
            return
        hits: List[Tuple[int, int, int, bool, bool]] = []  # (sink pos, arg a, arg b, fragment, ident)
        for rx, fragment in _SINKS[lk]:
            for m in rx.finditer(seg):
                if len(hits) >= MAX_SINKS_PER_STMT:  # nested sink calls would rescan the line
                    break
                open_i = m.end() - 1
                close = _match_paren(seg, open_i, len(seg))
                args = _split_args(seg, open_i + 1, close)
                while len(args) > 1 and _SKIP_ARG_RE.match(seg, args[0][0]):
                    args = args[1:]
                a, b = args[0]
                ident = bool(_IDENT_SINK_RE.search(seg, max(0, open_i - 12), open_i + 1))
                hits.append((m.start(), a, b, fragment, ident))
        if lk == "cs":
            for m in _CS_TEXT_SINK.finditer(seg):
                end = seg.find(";", m.end())
                hits.append((m.start(), m.end(), len(seg) if end < 0 else end, False, False))
        for pos, a, b, fragment, ident in hits:
            sink_line = self.line_of(st.a + pos)
            if sink_line in flows:
                continue
            arg = _blank_sanitized(seg[a:b], lk)
            used = [u for u in self.uses(arg, st.a + a, tainted) if not _hash_value(self.code, u[0])]
            if not used:
                continue
            lit = self.literal(st.a + a, st.a + b)
            sql_names = [m.group(0) for m in _TOKEN_RE.finditer(arg) if self.is_sql_name(m.group(0))]
            if fragment:
                if not ('"' in arg or "'" in arg or "`" in arg or sql_names):
                    continue
            elif not (sql_words(lit) or sql_names):
                continue
            at, t = used[0]
            is_ident = (ident or t.ident or self.ident_ctx(at) or _placeholder_ident(lit)
                        or any(self.placeholder_name(n) for n in sql_names))
            flows[sink_line] = Flow(t.line, sink_line, t.var, is_ident)

    def assign(self, st: _Stmt, seg: str, tainted: Dict[str, _Taint], nested: bool,
               module_level: bool) -> None:
        lk = self.lk
        if lk == "go":
            for m in _BIND_RE.finditer(seg):
                tainted[m.group(1)] = _Taint(m.group(1), self.line_of(st.a + m.start()), False)
        base = 0
        first = _FIRST_WORD_RE.match(seg.lstrip())
        if first is not None and first.group(0) in _CONTROL:
            nested = True  # `if q is None: q = ""` (one line) is conditional too
            if lk == "py":
                colon = seg.find(":")
                if colon < 0:
                    return
                base = colon + 1
        if lk == "rb" and _RB_MODIFIER_RE.search(seg):
            nested = True
        body = seg[base:]
        app = _APPEND_RE.match(body) or (_RB_APPEND_RE.match(body) if lk == "rb" else None)
        if app is not None:
            targets, append, rhs_at = [app.group(1)], True, app.end()
        else:
            got = _assignment(body, lk)
            if got is None:
                return
            targets, append, rhs_at = got
        if not targets:
            return
        rhs_at += base
        rhs = _blank_sanitized(seg[rhs_at:], lk)
        used = self.uses(rhs, st.a + rhs_at, tainted)
        lit = self.literal(st.a + rhs_at, st.b)
        sql_names = [m.group(0) for m in _TOKEN_RE.finditer(rhs) if self.is_sql_name(m.group(0))]
        is_sql = sql_words(lit) or bool(sql_names)
        placeholder = _placeholder_ident(lit) or any(self.placeholder_name(n) for n in sql_names)
        keep = append or nested  # do not clear existing taint or SQL-ness
        if used:
            at, t = used[0]
            direct = _SOURCES[lk].match(self.code, at) is not None
            ident = t.ident or (is_sql and (self.ident_ctx(at) or placeholder))
        for name in targets:
            if used:
                # A direct source is named after the variable it lands in; a chain keeps the
                # name of the variable that first received the source.
                if not (keep and name in tainted):
                    tainted[name] = _Taint(name if direct else t.var, t.line, ident)
            elif not keep:
                tainted.pop(name, None)
            if is_sql:
                self.sqlvars[name] = placeholder or self.sqlvars.get(name, False)
                if module_level:
                    self.gsql[name] = self.sqlvars[name]
                self.shadow.discard(name)
            elif not keep:
                self.sqlvars.pop(name, None)
                if module_level:
                    self.gsql.pop(name, None)
                elif name in self.gsql:
                    self.shadow.add(name)


def find_flows(text: str, lang: str) -> List[Flow]:
    """Source-to-sink flows in one file (see the module docstring), sorted by sink line."""
    lk = _lang_key(lang)
    if lk is None or not text or not _worth_analysing(text, lk):
        return []
    return _Analyzer(text, lk).run()


# Parameter-source markers: a file without one of these (and without a direct source) has no
# parameter that could carry request data.
_PARAM_HINTS: Dict[str, Pattern[str]] = {
    "py": _rx(r"\(\s*(?:self\s*,\s*)?(?:request|req)\b|@[\w.]*\.(?:route|get|post|put|patch|delete|"
              r"api_route|websocket)\s*\(|@api_view\b|@action\b"),
    "java": _rx(r"@(?:RequestParam|PathVariable|RequestHeader|RequestBody|CookieValue|MatrixVariable|"
                r"ModelAttribute|QueryParam|PathParam|FormParam|HeaderParam|CookieParam)\b"),
    "cs": _rx(r"\[\s*(?:From(?:Query|Route|Body|Form|Header)|Http(?:Get|Post|Put|Delete|Patch)|Route)\b"
              r"|\bMap(?:Get|Post|Put|Delete|Patch|Methods)\s*\("),
    "js": _rx(r"@(?:Param|Query|Body|Headers)\s*\("),
    "php": _PHP_CONTROLLER_RE,
}
_PARAM_HINTS["kt"] = _PARAM_HINTS["java"]


def _worth_analysing(text: str, lk: str) -> bool:
    """Cheap whole-file pre-check: some sink call, and a direct source, a bind call or a
    parameter-source marker. Files without both cannot produce a flow."""
    if not any(rx.search(text) for rx, _ in _SINKS[lk]) and not (lk == "cs" and _CS_TEXT_SINK.search(text)):
        return False
    if _SOURCES[lk].search(text):
        return True
    if lk == "go" and _BIND_RE.search(text):
        return True
    hint = _PARAM_HINTS.get(lk)
    return hint is not None and bool(hint.search(text))


def flows(text: str, lang: str) -> List[Tuple[int, int, str]]:
    """(source_line, sink_line, variable) for each untrusted value that reaches SQL text."""
    return [(f.source_line, f.sink_line, f.variable) for f in find_flows(text, lang)]
