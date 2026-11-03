## Baseline vs 1.0.2

The baseline (`evals/precision-baseline.md`) used the 1.0.1 rules on 10 repos. This report uses the 1.0.2 rules on 14 repos (the same 10 plus Spring Boot, ASP.NET Core, Rails and Laravel apps), with the same seed (1002) and up to 20 labels per rule. On the original 10 repos, corpus signals fell from 648 to about 236 after the false-positive fixes. Rules below 50% precision are registered at confidence `low`, and every signal they emit is capped at `low`.

## Recurring false-positive patterns

- unauth-write-route: auth applied outside the route can still be missed. Examples: Laravel `$this->middleware('auth')` in a controller constructor, CanCan `load_and_authorize_resource`, Rails resource routes for actions the controller never defines, and Spring Security global chains (lowered, not cleared). Most of its corpus signals come from the Rails and Laravel repos.
- hardcoded-secret: i18n "Password" labels, validation strings, docstring and OpenAPI examples, CI service passwords.
- swallowed-error: deliberate best-effort fallbacks and framework scaffolds.
- unbounded-query / query-in-loop: SQL in docstrings, introspection queries, and schema-bounded loops in django internals.
- no-tests: subfolders whose tests live in a sibling or root `tests/` tree.
- import-cycle / god-module: package `__init__` re-exports and function-local imports; large but cohesive modules.

## Incremental-scanning decision

The largest corpus repo (django) takes longer than the 30-second target in the design (see the scan time above). Scans of normal service repos take a few seconds. 1.0.2 ships without a cache: `--scope`, `--max-files` and `--timeout` cover large repos. Incremental scanning and a faster walker are on the 1.2.0 roadmap.
