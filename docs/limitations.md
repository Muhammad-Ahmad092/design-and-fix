# Known limitations

Design & Fix is evidence plus judgement. Read this before trusting a number.

## The scanner is heuristic

Every signal comes from static patterns, not from running or fully parsing your code. Signals are leads. The skill reads the cited file to confirm each one, and the report labels what it could not confirm (`needs-review`). Measured precision per rule is in the README "Measured quality" section and under `evals/`. Rules that measure below 50% after the 1.0.2 fixes carry confidence `low` and are marked in [rules.md](rules.md). Nothing is hidden.

## Languages and frameworks

| Area | Coverage |
|---|---|
| Import graph, cycles, god modules | Python, JavaScript / TypeScript (including `.mjs` / `.cjs`), Go |
| Route detection and auth markers | Express, NestJS, Flask, FastAPI, Django, Go (net/http, chi, gin), plus Spring, ASP.NET, Rails and Laravel |
| Pattern rules (`http-no-timeout`, `sql-string-concat`, `hardcoded-secret`, `swallowed-error`) | JS/TS, Python, Go, and basic patterns for Java / Kotlin, C#, Ruby, PHP |
| Infra and dependencies | Dockerfile, Kubernetes YAML, GitHub Actions / GitLab CI / CircleCI, npm, pip / Poetry |

Spring, ASP.NET, Rails and Laravel support is newer and less measured than the JS/TS/Python/Go rules: the corpus has fewer repos for them, so treat their signals as lower-trust: the precision table covers them with one repo each. Rust and Elixir rule sets (and deeper Ruby) are planned, see [ROADMAP.md](../ROADMAP.md). Other languages are detected for the stack summary only.

## What the scanner cannot see

- Runtime behaviour: load, latency, real query plans, production config, feature flags.
- Code generated at build time, dynamic routes, reflection, metaprogramming, and routes registered through wrappers it does not recognise.
- Infrastructure outside the repo (cloud console settings, secrets managers, network policy).
- Anything git-ignored (skipped by default, including local `.env` and key files; `--no-gitignore` includes them), minified or generated files, files over `max_file_bytes`, binary files.
- Authorization semantics: it sees an auth marker or its absence, not whether the check is correct.
- Dependency vulnerabilities. There is no CVE lookup; dependency checks are offline hygiene only (`missing-lockfile`, `unpinned-dependency`).

## Confidence

Each signal carries `confidence`: `high` (specific pattern, rarely wrong), `medium` (needs context the scanner only partly has), `low` (often a false positive, for example matches in test files, an intentional `pass` with an explaining comment, or a cycle through a package `__init__.py`). A rule's registry entry lists the confidence it emits in its typical path; the cases that emit lower confidence are named in its limitations. Severity is a hint; the audit assigns the final one.

The registry confidence is also a ceiling: a rule measured below 50% precision in `evals/precision.md` is registered at `low`, and every signal it emits is capped at `low`. In 1.0.2 that applies to `god-module`, `import-cycle`, `query-in-loop`, `unbounded-query`, `no-tests`, `unpinned-dependency`, `unauth-write-route`, `sql-string-concat`, `hardcoded-secret` and `swallowed-error`. A real private key or a real injection still shows as `low` confidence, but its severity stays Critical.

Known false-positive patterns from the after sample:

- `unauth-write-route`: auth applied outside the route can still be missed: Laravel `$this->middleware('auth')` in a controller constructor, route groups the scanner cannot resolve, CanCan `load_and_authorize_resource`, Rails concerns beyond one level (only `app/controllers/concerns/<name>.rb` is followed), ASP.NET `[Authorize]` on a base class outside the scanned files, and Spring Security global chains (`anyRequest().authenticated()` lowers the signal but does not clear it). Spring path constants defined in another class appear as a `{NAME}` placeholder. Login, register, session and password-reset routes are treated as public on purpose. Its 1.0.2 precision was measured before the final framework fixes, so the corpus number is a lower bound.
- `hardcoded-secret`: i18n "Password" labels, validation strings, docstring and OpenAPI examples, CI service passwords.
- `swallowed-error`: deliberate best-effort fallbacks and framework scaffolds.
- `unbounded-query` and `query-in-loop`: SQL in docstrings, introspection queries, schema-bounded loops.
- `no-tests`: subfolders whose tests live in a sibling or root `tests/` tree.
- Express chained routes (`router.route('/x').post(...)`) are not detected.

## Scoring

Scores come from the issues the audit confirmed, using the lookup table in the rubric. They are comparable between runs of the same scope, not between different projects. Context weights (for example payments, health or PII raising Security to x2) come from a documented keyword table, match whole words in the context line, and default to 1.0 (identical to 1.0.1). A hyphenated compound counts as one word, so `pii-free` does not trigger a weight, and a keyword negated just before it ("no PII", "not real-time", "without payments") is ignored. There is no syntax for setting a weight by hand; word the Context line so the keyword table matches what is true.

## Configuration and ignore rules

- Only the root `.gitignore` is applied. Nested `.gitignore` files are not supported in 1.0.2.
- `.design-and-fix.json` is JSON (no YAML) and must be valid. A bad file stops the scan with exit 2 and a message naming the key.
- A bad root path or missing `--scope` still exits 1 (unchanged from 1.0.1); exit 2 covers config and command-line usage errors.
- Disabled rules may still run; their signals are dropped before the per-rule cap and are not counted as suppressed.
- At most 200 signals per rule are listed; the summary records truncation.

## Scale

Full scans of a normal repo take seconds (median scan time is in the README). The largest corpus repo (django, 6,823 files, 2,904 scanned) took 72 seconds in the 1.0.2 run, over the 30-second target in the design, so incremental scanning and a faster walker move to the 1.2.0 roadmap. Incremental scanning and caching are not built; use `--scope` on big monorepos. Partial results (`max_files`, `timeout_seconds`) are always flagged in the output.

## Audit history

Change tracking uses fingerprints computed from each issue's evidence against the current repo. Matches that rely on fuzzy similarity are marked uncertain. If an evidence file was deleted, the issue is reported as resolved-or-uncertain, never silently dropped.

## The AI part

The agent can misread code, over-generalise, or miss problems no signal pointed to. The linter checks structure, scores, IDs and that cited `path:line` evidence exists; it cannot check that the diagnosis is right. Performance statements from code patterns are labelled Hypothesis with a measurement step. Verify High and Critical findings before acting.
