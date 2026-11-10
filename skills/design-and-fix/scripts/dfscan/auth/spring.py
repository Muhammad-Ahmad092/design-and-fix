"""Spring adapter: Spring Security filter chains (authorizeHttpRequests / authorizeRequests /
authorizeExchange, Java fluent and lambda DSL, Kotlin `authorize(...)` DSL) read as ordered
matcher rules, first match wins; `web.ignoring()` patterns; securityMatcher-scoped chains."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .core import _LOCAL, PROTECTED, PUBLIC, UNPROTECTED, UNRESOLVED, AuthVerdict, Resolver

_SPRING_EXTS = (".java", ".kt")
_CHAIN_RE = re.compile(r"\b(authorizeHttpRequests|authorizeRequests|authorizeExchange)\b")
_IGNORING_RE = re.compile(r"\bignoring\s*\(\s*\)")
_BOOT_RE = re.compile(r"@SpringBootApplication\b|\bSpringApplication\s*\.\s*run\b"
                      r"|\brunApplication\s*<")
_SCOPE_RE = re.compile(r"\b(?:securityMatchers?|antMatcher|mvcMatcher|regexMatcher)\s*\(")
_HEADER_RE = re.compile(r"@Bean\b|\bvoid\s+configure\s*\(|\bfun\s+\w+\s*\(")
_MATCHERS = {"antMatchers", "mvcMatchers", "requestMatchers", "regexMatchers", "pathMatchers"}
_ANY = {"anyRequest", "anyExchange"}
_PERMIT = {"permitAll", "anonymous"}
_PROTECT = {"authenticated", "fullyAuthenticated", "hasRole", "hasAnyRole", "hasAuthority",
            "hasAnyAuthority", "access", "denyAll", "rememberMe", "hasIpAddress"}
_TOKEN_RE = re.compile(r"\b(%s|authorize)\b\s*(\()?" % "|".join(
    sorted(_MATCHERS | _ANY | _PERMIT | _PROTECT)))
_STR_RE = re.compile(r"\"((?:\\.|[^\"\\\n])*)\"")
_VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE")
_METHOD_ARG_RE = re.compile(r"\s*(?:HttpMethod\s*\.\s*)?(%s)\s*$" % "|".join(_VERBS))
_IGNORE_MATCHER_RE = re.compile(r"\b(?:antMatchers|mvcMatchers|requestMatchers)\s*\(")
_IGNORE_ANY_RE = re.compile(r"\b(?:anyRequest|anyExchange)\b")
_CONST_RE = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?::\s*\w+(?:<\w+>)?\s*)?(?:\[\s*\]\s*)?=\s*"
    r"(\{[^{}]*\}"
    r"|(?:arrayOf|listOf|List\.of|Arrays\.asList|new\s+String\s*\[\s*\]\s*)\s*[({][^(){}]*[)}]"
    r"|\"(?:\\.|[^\"\\\n])*\")")
# Matcher arguments that match no application route (static resources, actuator, preflight).
_NO_ROUTE_RE = re.compile(r"toStaticResources|EndpointRequest|isPreFlightRequest|PathRequest")

# (methods or None for all, patterns or None for any request, unknown argument seen)
_Matcher = Tuple[Optional[List[str]], Optional[List[str]], bool]
# (matcher, decision: "permit" / "protect", file, line)
_Rule = Tuple[_Matcher, str, str, int]
# web.ignoring() pattern: (methods or None for all, pattern, line)
_Ignored = Tuple[Optional[List[str]], str, int]
_IgnoredCall = Tuple[Optional[List[str]], List[str], int]


class _Chain:
    def __init__(self, rel: str, line: int, scope: Optional[List[str]]) -> None:
        self.rel = rel
        self.line = line
        self.scope = scope  # securityMatcher patterns (None: every request)
        self.rules: List[_Rule] = []


def _blank(text: str) -> str:
    return _STR_RE.sub(lambda m: "\"" + " " * (len(m.group(0)) - 2) + "\"", text)


def _close(blank: str, i: int) -> int:
    """Index of the bracket closing blank[i] (string contents already blanked)."""
    depth = 0
    for j in range(i, len(blank)):
        c = blank[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return j
    return len(blank) - 1


def _split(text: str) -> List[str]:
    """Top-level comma-separated arguments (strings and brackets respected)."""
    out: List[str] = []
    depth = start = 0
    for i, c in enumerate(_blank(text)):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [p for p in (x.strip() for x in out) if p]


def _consts(text: str) -> Dict[str, List[str]]:
    """Same-file String / String[] / arrayOf / listOf constants -> their string literals."""
    out: Dict[str, List[str]] = {}
    for m in _CONST_RE.finditer(text):
        out.setdefault(m.group(1), _STR_RE.findall(m.group(2)))
    return out


def _matcher(args: str, consts: Dict[str, List[str]]) -> _Matcher:
    methods: List[str] = []
    patterns: List[str] = []
    unknown = False
    for part in _split(args):
        mm = _METHOD_ARG_RE.match(part)
        if mm:
            methods.append(mm.group(1))
            continue
        if _NO_ROUTE_RE.search(part):
            continue
        strs = _STR_RE.findall(part)
        if strs:
            # new AntPathRequestMatcher("/x", "POST"), antMatcher(HttpMethod.POST, "/x")
            hm = re.search(r"\bHttpMethod\s*\.\s*([A-Z]+)", part)
            if hm:
                methods.append(hm.group(1))
            verbs = [s for s in strs[1:] if s in _VERBS]
            methods += verbs
            patterns += [s for s in strs if s not in verbs]
            continue
        name = re.match(r"\s*(?:\w+\s*\.\s*)*([A-Za-z_]\w*)\s*$", part)
        if name and name.group(1) in consts:
            patterns += consts[name.group(1)]
        else:
            unknown = True
    if methods and not patterns and not unknown:
        patterns = ["/**"]  # antMatchers(HttpMethod.OPTIONS): every path for that method
    return (methods or None), patterns, unknown


def _decision(word: str, args: str) -> Optional[str]:
    if word in _PERMIT:
        return "permit"
    if word == "access":
        return "permit" if re.search(r"permitAll|isAnonymous", args) else "protect"
    if word in _PROTECT:
        return "protect"
    return None


def _kotlin_rule(args: str, consts: Dict[str, List[str]]) -> Optional[Tuple[_Matcher, str]]:
    """authorize("/x", permitAll) / authorize(HttpMethod.POST, "/x", hasRole("A")) /
    authorize(anyRequest, authenticated)."""
    parts = _split(args)
    if len(parts) < 2:
        return None
    word = re.match(r"\s*(\w+)", parts[-1])
    decision = _decision(word.group(1), parts[-1]) if word else None
    if decision is None:
        return None
    if any(re.match(r"\s*(?:anyRequest|anyExchange)\s*$", p) for p in parts[:-1]):
        return (None, None, False), decision
    return _matcher(", ".join(parts[:-1]), consts), decision


def _region(text: str, blank: str, start: int) -> Tuple[int, int]:
    """Text span holding a chain's rules: the lambda/Kotlin block after authorizeXxx, or (old
    fluent style, `authorizeRequests()` with no argument) the chain up to `.and()` or `;`."""
    i = start
    while i < len(blank) and blank[i] in " \t\r\n":
        i += 1
    if i < len(blank) and blank[i] in "({":
        close = _close(blank, i)
        if blank[i + 1:close].strip():
            return i + 1, close
        i = close + 1
    end = len(blank)
    for pat in (r"\.\s*and\s*\(\s*\)", r";"):
        m = re.compile(pat).search(blank, i)
        if m and m.start() < end:
            end = m.start()
    return i, end


def _rules(text: str, blank: str, lo: int, hi: int, rel: str,
           consts: Dict[str, List[str]]) -> List[_Rule]:
    rules: List[_Rule] = []
    pending: Optional[_Matcher] = None
    pos = lo
    while True:
        m = _TOKEN_RE.search(blank, pos, hi)
        if not m:
            break
        word = m.group(1)
        args = ""
        nxt = m.end()
        if m.group(2):
            close = _close(blank, m.end() - 1)
            args = text[m.end():close]
            nxt = close + 1
        line = blank.count("\n", 0, m.start()) + 1
        if word == "authorize" and m.group(2):
            kr = _kotlin_rule(args, consts)
            if kr:
                rules.append((kr[0], kr[1], rel, line))
        elif word in _MATCHERS and m.group(2):
            pending = _matcher(args, consts)
            if word == "regexMatchers":  # regular expressions, not evaluated: unknown
                pending = (pending[0], [], True)
        elif word in _ANY:
            pending = (None, None, False)
        elif pending is not None:
            decision = _decision(word, args)
            if decision:
                rules.append((pending, decision, rel, line))
                pending = None
        pos = nxt
    return rules


def _scope(text: str, blank: str, lo: int, hi: int) -> Optional[List[str]]:
    """securityMatcher / antMatcher patterns that scope the chain, None when unscoped."""
    found: Optional[List[str]] = None
    for m in _SCOPE_RE.finditer(blank, lo, hi):
        close = _close(blank, m.end() - 1)
        found = (found or []) + _STR_RE.findall(text[m.end():close])
    return found


def _parse(rel: str, raw: str) -> Tuple[List[_Chain], List[_Ignored]]:
    """Filter chains and web.ignoring() patterns of one file."""
    from ..text import strip_comments
    lang = "Kotlin" if rel.endswith(".kt") else "Java"
    text = strip_comments(raw, lang)
    blank = _blank(text)
    consts = _consts(text)
    chains: List[_Chain] = []
    headers = [m.start() for m in _HEADER_RE.finditer(blank)]
    for m in _CHAIN_RE.finditer(blank):
        head = max([h for h in headers if h < m.start()] or [0])
        lo, hi = _region(text, blank, m.end())
        chain = _Chain(rel, blank.count("\n", 0, m.start()) + 1,
                       _scope(text, blank, head, m.start()))
        chain.rules = _rules(text, blank, lo, hi, rel, consts)
        chains.append(chain)
    ignored: List[_Ignored] = []
    for m in _IGNORING_RE.finditer(blank):
        lo, hi = _region(text, blank, m.end())
        for methods, pats, line in _ignoring_matchers(text, blank, lo, hi, consts):
            ignored += [(methods, p, line) for p in pats]
    return chains, ignored


def _ignoring_matchers(text: str, blank: str, lo: int, hi: int,
                       consts: Dict[str, List[str]]) -> List[_IgnoredCall]:
    """Matcher calls after web.ignoring(): (methods or None, patterns, line), patterns with no
    decision call after them; anyRequest() ignores every path."""
    out: List[_IgnoredCall] = []
    for m in _IGNORE_MATCHER_RE.finditer(blank, lo, hi):
        close = _close(blank, m.end() - 1)
        methods, pats, _ = _matcher(text[m.end():close], consts)
        out.append((methods, pats or [], blank.count("\n", 0, m.start()) + 1))
    for m in _IGNORE_ANY_RE.finditer(blank, lo, hi):
        out.append((None, ["/**"], blank.count("\n", 0, m.start()) + 1))
    return out


@lru_cache(maxsize=1024)
def _ant(pattern: str) -> "re.Pattern[str]":
    """Ant/MVC path pattern -> regex over a route path whose parameters are \\x00."""
    p = pattern.split("?", 1)[0].rstrip("/") or "/"
    out = []
    segs = [s for s in p.split("/") if s]
    for s in segs:
        if s == "**":
            out.append("(?:/.*)?")
            continue
        seg = ""
        i = 0
        while i < len(s):
            c = s[i]
            if c == "*":
                seg += "[^/]*"
            elif c == "{":
                j = s.find("}", i)
                j = len(s) - 1 if j < 0 else j
                seg += "[^/]+"
                i = j
            else:
                seg += re.escape(c)
            i += 1
        out.append("/" + seg)
    return re.compile("^" + ("".join(out) or "/?") + "/?$")


def _route_path(path: str) -> str:
    """Route path with each {param} replaced by \\x00 (matches wildcards, never a literal)."""
    return re.sub(r"\{[^}]*\}", "\x00", path.rstrip("/")) or "/"


def _matches(pattern: str, path: str) -> bool:
    return bool(_ant(pattern).match(path))


_CATCH_ALL = {"/**", "/*", "**", "*", "/", ""}
# A <prefix>/** permit whose last literal segment names a public area is public by design.
_PUBLIC_AREA_RE = re.compile(r"(?:^|/)(?:public|open|anonymous|anon|guest|static|assets)/\*\*$",
                             re.I)


def _breadth(pattern: str) -> str:
    """"all" for a catch-all pattern (/**, /*), "prefix" for <prefix>/**, else "specific"."""
    p = pattern.split("?", 1)[0].strip()
    if p in _CATCH_ALL:
        return "all"
    if p.endswith("**"):
        return "specific" if _PUBLIC_AREA_RE.search(p) else "prefix"
    return "specific"


def _permit_verdict(hits: Optional[List[str]], what: str, at: str) -> AuthVerdict:
    """Verdict of a permit rule (or web.ignoring() pattern) that matches a write route. Only a
    specific matcher (a literal path, or a pattern not ending in /**) is public by design. A
    catch-all (anyRequest()/anyExchange(), /**, /*) switches security off for every request,
    write methods included: unprotected. A <prefix>/** permit is unresolved (deliberate public
    area or blanket opt-out), unless the prefix ends in a public-area segment (/public/**,
    /open/**, /guest/**, /static/** ...), which is public. `hits` are the matching patterns (None: anyRequest)."""
    kinds = {"all"} if hits is None else {_breadth(p) for p in hits}
    if "specific" in kinds:
        return AuthVerdict(PUBLIC, "Spring Security %s at %s" % (what, at))
    if "prefix" in kinds:
        pats = ", ".join(sorted({p for p in hits or [] if _breadth(p) == "prefix"}))
        return AuthVerdict(UNRESOLVED, "Spring Security %s on %s at %s covers a whole path "
                                       "prefix, write methods included; whether the prefix is "
                                       "meant to be public is not resolved" % (what, pats, at))
    return AuthVerdict(UNPROTECTED, "%s covers every request at %s" % (what, at))


def _evaluate(chain: _Chain, method: str, path: str) -> AuthVerdict:
    where = "%s:%d" % (chain.rel, chain.line)
    for (methods, patterns, unknown), decision, rel, line in chain.rules:
        if methods is not None and method not in methods:
            continue
        at = "%s:%d" % (rel, line)
        if patterns is None:
            hit = True
        else:
            hit = any(_matches(p, path) for p in patterns)
            if not hit and unknown:
                if decision == "permit":
                    return AuthVerdict(UNRESOLVED, "Spring Security permitAll() matcher at %s "
                                                   "uses patterns that are not read" % at)
                continue
        if hit:
            if decision == "permit":
                hits = None if patterns is None else [p for p in patterns if _matches(p, path)]
                return _permit_verdict(hits, "permitAll()", at)
            return AuthVerdict(PROTECTED, "Spring Security filter chain requires auth (%s)" % at)
    return AuthVerdict(UNRESOLVED, "Spring Security filter chain at %s has no matcher for this "
                                   "route (anyRequest() not set)" % where)


_IgnoredAt = Tuple[Optional[List[str]], str, str, int]  # (methods, pattern, file, line)
_Security = Tuple[List[_Chain], List[_IgnoredAt], List[str], List[str]]


def _security(ctx: Resolver) -> _Security:
    """(filter chains, ignored patterns (methods, pattern, file, line), files showing Spring Security
    is used, files holding the Spring application class)."""
    def build() -> _Security:
        chains: List[_Chain] = []
        ignored: List[_IgnoredAt] = []
        evidence: List[str] = []
        boot: List[str] = []
        for f in ctx.files:
            if f.is_example:
                continue
            name = f.rel.rsplit("/", 1)[-1]
            if name in ("pom.xml", "build.gradle", "build.gradle.kts"):
                if re.search(r"spring-boot-starter-security|spring-security", ctx.raw(f.rel)):
                    evidence.append(f.rel)
                continue
            if f.ext not in _SPRING_EXTS:
                continue
            raw = ctx.raw(f.rel)
            if "springframework.security" in raw:
                evidence.append(f.rel)
            if _BOOT_RE.search(raw):
                boot.append(f.rel)
            if not _CHAIN_RE.search(raw) and not _IGNORING_RE.search(raw):
                continue
            cs, ig = _parse(f.rel, raw)
            chains += cs
            ignored += [(ms, p, f.rel, ln) for ms, p, ln in ig]
        return chains, ignored, evidence, boot
    return ctx.memo("spring_security", build)  # type: ignore[return-value]


def adapter(ctx: Resolver, route: dict) -> AuthVerdict:
    """Security configuration applies only within the route's own app or service unit
    (Resolver.same_app): a filter chain of another service never protects this one."""
    all_chains, all_ignored, all_evidence, all_boot = _security(ctx)
    rel = route["file"]
    chains = [c for c in all_chains if ctx.same_app(rel, c.rel)]
    method = route["method"]
    path = _route_path(route["path"])
    ign = [(p, prel, line) for ms, p, prel, line in all_ignored
           if (ms is None or method in ms) and ctx.same_app(rel, prel) and _matches(p, path)]
    if ign:
        pats = sorted({p for p, _, _ in ign})
        return _permit_verdict([p for p, _, _ in ign], "web.ignoring() %s" % ", ".join(pats),
                               "%s:%d" % (ign[0][1], ign[0][2]))
    applicable = [c for c in chains
                  if c.scope is None or any(_matches(p, path) for p in c.scope)]
    if applicable:
        verdicts = [_evaluate(c, method, path) for c in applicable]
        states = {v.state for v in verdicts}
        if len(states) == 1:
            return verdicts[0]
        return AuthVerdict(UNRESOLVED, "Spring Security filter chains disagree for this route "
                                       "(%s); their order is not resolved"
                           % ", ".join("%s:%d" % (c.rel, c.line) for c in applicable))
    if chains:
        return AuthVerdict(UNRESOLVED, "no Spring Security filter chain covers this route "
                                       "(securityMatcher)")
    if ctx.signup_route(route):
        return AuthVerdict(PUBLIC, "sign-up route (public by design)")
    if all_chains:
        return AuthVerdict(UNRESOLVED, "the Spring Security filter chain in %s belongs to another "
                                       "module or service; this route's own chain is not in the "
                                       "scanned files" % all_chains[0].rel)
    evidence = [e for e in all_evidence if ctx.same_app(rel, e)]
    if evidence:
        return AuthVerdict(UNRESOLVED, "Spring Security is used (%s) but its filter chain "
                                       "(SecurityFilterChain / WebSecurityConfigurerAdapter) is "
                                       "not in the scanned files" % evidence[0])
    if not any(ctx.same_app(rel, b) for b in all_boot):
        return AuthVerdict(UNRESOLVED, "the Spring application class (@SpringBootApplication) and "
                                       "its configuration, where Spring Security would be set up, "
                                       "are not in the scanned files")
    return _LOCAL
