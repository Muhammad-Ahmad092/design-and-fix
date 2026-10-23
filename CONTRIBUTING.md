# Contributing

## Setup

Python 3.9 or newer. There are no dependencies: the scanner and tests use only the standard library. Code must stay 3.9-compatible (`from __future__ import annotations`, no `match`).

```bash
python -m unittest discover -s tests -v
python tests/validate_manifests.py
```

Use `python3` where `python` is missing. Run a single file with `python -m unittest discover -s tests -p "test_scanner_patterns.py" -v`.

## Add a scanner rule

1. Write the rule function in `skills/design-and-fix/scripts/dfscan/patterns.py` (or `routes.py` / `imports.py`) and register it in `run_rules`. Return `Signal(rule, category, severity, path, line, snippet, confidence)`.
2. Add a fixture line that triggers it under `tests/fixtures/monorepo/`. Fixture "secrets" must not match real provider patterns (no `sk_live_`, `AKIA...`, `ghp_`, private-key headers).
3. Add the expected entry to `evals/scanner-expected.json` (`must_find`, plus a `must_not_find` for the false positive it should avoid).
4. Add a unit test in `tests/` covering a hit, a comment or test-file miss, and the fixture entry.
5. Add the rule to the table in `README.md`.

## Evals

Before each release run the scanner evals (the unit test suite) and the five skill cases in `evals/skill-cases/`; all must pass. See [evals/README.md](evals/README.md).

## Record the demo

Record a Claude Code run on a copy of `tests/fixtures/monorepo`, save it as `docs/assets/demo.gif`, and replace the placeholder comment near the top of `README.md` with `![Demo](docs/assets/demo.gif)`.

## Release checklist

- [ ] Bump `version` in the three plugin manifests only (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.cursor-plugin/plugin.json`); the marketplace files intentionally have no version
- [ ] Add a CHANGELOG entry
- [ ] `python -m unittest discover -s tests -v` and `python tests/validate_manifests.py` pass
- [ ] All five skill cases pass
- [ ] `claude plugin validate . --strict`
- [ ] Tag the release
