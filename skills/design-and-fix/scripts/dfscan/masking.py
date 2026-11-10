from __future__ import annotations

import functools
import re

# Secret-naming key words shared by snippet masking and the hardcoded-secret rule, so detection and
# masking stay in sync. `_` or `-` may separate the words (api_key, api-key, X-API-Key,
# aws_secret_access_key, client-secret). After the word only an optional `key` may follow.
SECRET_WORDS = (r"(?:password|passwd|secret[_-]?(?:access[_-]?)?key|client[_-]?secret|secret"
                r"|api[_-]?key|access[_-]?key|token|private[_-]?key)")
SECRET_KEY = SECRET_WORDS + r"(?:[._-]?key)?(?![\w.-])"

# Lookahead + search at every position so a closing quote of an earlier string is never consumed.
_MASK_DQ_RE = re.compile(r"(?=" + SECRET_KEY + r"[\"']?\s*(?:=>|[:=])\s*\"([^\"]{6,}))", re.I)
_MASK_SQ_RE = re.compile(r"(?=" + SECRET_KEY + r"[\"']?\s*(?:=>|[:=])\s*'([^']{6,}))", re.I)
_MASK_UNQUOTED_RE = re.compile(
    r"^([ \t]*(?:export[ \t]+)?[\w.-]*" + SECRET_WORDS
    + r"(?:[._-]?key)?[ \t]*[:=][ \t]*)([^\s\"'#<*$]\S{5,})", re.I)
# Query-string parameters and auth headers: value runs to & / quote / whitespace.
_MASK_QUERY_RE = re.compile(
    r"([?&;](?:api[_-]?key|key|token|access[_-]?token|secret|password|passwd|pwd|auth)=)"
    r"([^&\s\"'`<>)]{3,})", re.I)
_MASK_BEARER_RE = re.compile(r"(\b(?:Bearer|Basic|Token)[ \t]+)([A-Za-z0-9._~+/=-]{8,})")
_MASK_AUTH_HEADER_RE = re.compile(
    r"(\bauthorization[\"']?[ \t]*[:=][ \t]*[\"']?)(?!(?:Bearer|Basic|Token)\b)([^\s\"',}]{6,})", re.I)
# URL userinfo password: scheme://user:PASSWORD@host
_MASK_URL_RE = re.compile(r"(://[^/\s:@'\"`]+:)([^@\s/'\"`]+)(?=@)")
# requests-style basic auth tuple: auth=('user', 'PASSWORD')
_MASK_AUTH_TUPLE_RE = re.compile(
    r"(\bauth[ \t]*=[ \t]*\([ \t]*(['\"])[^'\"\n]*\2[ \t]*,[ \t]*(['\"]))([^'\"\n]+)(?=\3)")
_MASK_SLACK_HOOK_RE = re.compile(r"(hooks\.slack\.com/services/)[A-Za-z0-9/_-]+")
_MASK_TOKEN_RE = re.compile(
    r"AKIA[0-9A-Z]{16}|\b(?:sk|pk|rk)_(?:live|test)_[0-9A-Za-z]{8,}|\bgh[pousr]_[0-9A-Za-z]{20,}"
    r"|\bgithub_pat_[0-9A-Za-z_]{20,}|\bglpat-[0-9A-Za-z_-]{20,}"
    r"|\bxox[abprs]-[0-9A-Za-z-]{10,}|\beyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}(?:\.[0-9A-Za-z_-]*)?"
    r"|\bAIza[0-9A-Za-z_-]{35}|\bsk-(?:proj-)?[0-9A-Za-z_-]{20,}")
_ALREADY_MASKED = ("<redacted>", "***")

# Provider token formats that hardcoded-secret reports as a known format (Critical) wherever they
# appear; every one is also masked by _MASK_TOKEN_RE above. Each starts with a literal prefix
# (so the regex engine scans for it quickly) followed by one character class, so matching is
# linear. No leading `\b`: the rule checks that a match does not follow a word character.
PROVIDER_FORMATS = (
    ("AWS access key id", r"AKIA[0-9A-Z]{16}"),
    ("Stripe secret key", r"[sr]k_live_[0-9A-Za-z]{16,}"),
    ("GitHub token", r"gh[pousr]_[0-9A-Za-z]{36,}"),
    ("GitHub token", r"github_pat_[0-9A-Za-z_]{40,}"),
    ("GitLab token", r"glpat-[0-9A-Za-z_-]{20,}"),
    ("Slack token", r"xox[abprs]-[0-9A-Za-z-]{10,}"),
    ("Google API key", r"AIza[0-9A-Za-z_-]{35}"),
    ("Anthropic API key", r"sk-ant-[0-9A-Za-z_-]{20,}"),
    ("OpenAI API key", r"sk-(?:proj|svcacct|admin)-[0-9A-Za-z_-]{20,}"),
)


MASK_MEMO_SIZE = 256  # distinct snippets remembered (signals on one line share one snippet)


@functools.lru_cache(maxsize=MASK_MEMO_SIZE)
def mask_secrets(snippet: str) -> str:
    """Replace secret-looking values in a snippet with `***` (applied to every rule's snippet).

    Masks the value of key = "value" assignments whose key names a secret (password, secret, token,
    api key, access key, client secret, secret access key, private key; `_` or `-` between words),
    unquoted KEY=value in dotenv/YAML style lines, well-known token shapes (AWS key ids, Stripe,
    GitHub ghp_/github_pat_, GitLab glpat-, Slack, Google API keys, OpenAI/Anthropic-style sk- keys,
    JWTs, Slack webhook paths), URL passwords
    (scheme://user:***@host), basic-auth tuples (auth=('user', '***')), query-string secrets
    (?api_key=...) and Bearer / Authorization header values. Already masked values are left alone.
    False-positive risk: a non-secret value under a secret-sounding key is masked too, which only
    hides text from the report. Memoised (a pure function of the snippet), so thousands of
    signals on one long line mask it once; report.scan clears the memo at the start of a scan.
    """
    spans = []
    for rx in (_MASK_DQ_RE, _MASK_SQ_RE):
        for m in rx.finditer(snippet):
            if not m.group(1).startswith(_ALREADY_MASKED):
                spans.append((m.start(1), m.end(1)))
    out = snippet
    for start, end in sorted(set(spans), reverse=True):  # right to left keeps offsets valid
        out = out[:start] + "***" + out[end:]
    out = _MASK_UNQUOTED_RE.sub(lambda m: m.group(1) + "***", out)
    out = _MASK_QUERY_RE.sub(lambda m: m.group(1) + "***", out)
    out = _MASK_BEARER_RE.sub(lambda m: m.group(1) + "***", out)
    out = _MASK_AUTH_HEADER_RE.sub(lambda m: m.group(1) + "***", out)
    out = _MASK_URL_RE.sub(lambda m: m.group(1) + "***", out)
    out = _MASK_AUTH_TUPLE_RE.sub(lambda m: m.group(1) + "***", out)
    out = _MASK_SLACK_HOOK_RE.sub(lambda m: m.group(1) + "***", out)
    return _MASK_TOKEN_RE.sub("***", out)
