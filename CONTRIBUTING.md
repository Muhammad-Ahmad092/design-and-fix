# Contributing

## Setup

Python 3.9 or newer. There are no dependencies: the scanner and tests use only the standard library. Code must stay 3.9-compatible (`from __future__ import annotations`, no `match`, no runtime `X | Y`), use LF line endings and root-relative POSIX paths in all output.

```bash
python -W error::SyntaxWarning -W error::DeprecationWarning -m unittest discover -s tests -v
python tests/validate_manifests.py
python skills/design-and-fix/scripts/gen_rule_docs.py --check
```

Use `python3` where `python` is missing. Run a single file with `python -m unittest discover -s tests -p "test_scanner_patterns.py" -v`. Tests never run git.

## Add a scanner rule

1. Write one function per rule in the module that fits (`patterns.py`, `routes.py`, `imports.py`, `api_rules.py`, `infra_rules.py`, `deps_rules.py`). Its docstring must state the false positives it knows about. Return `Signal(rule, category, severity, path, line, snippet, confidence)`.
2. Register it in `skills/design-and-fix/scripts/dfscan/registry.py` with a `RuleMeta`: `id`, `version`, `category`, `default_severity`, `confidence`, `languages`, `description`, `limitations`, `remediation`. `confidence` is the confidence the rule emits in its main (non-test, typical) path; name the cases that emit lower confidence in `limitations`. Bump `version` whenever the rule's behaviour changes. The registry drops invalid signals, so a malformed one is counted in `summary.invalid_signals` and never reported.
3. Respect the rule contract: comment stripping before matching, per-rule cap of 200, secrets masked in snippets (the report layer masks, but do not put values into `snippet` on purpose).
4. Add a fixture that triggers it under `tests/fixtures/` (the `monorepo` fixture or a framework fixture). Fixture "secrets" must not match real provider patterns (no `sk_live_`, `AKIA...`, `ghp_`, private-key headers).
5. Add the expected entry to `evals/scanner-expected.json` (`must_find`, plus a `must_not_find` for the false positive it should avoid).
6. Add a unit test covering a hit, a comment or test-file miss, and a realistic negative. Use a temp tree (`tempfile` plus `tests/helpers.py`) for a minimal reproduction of every false positive you fix.
7. Regenerate the rule catalog: `python skills/design-and-fix/scripts/gen_rule_docs.py`. CI fails on drift (`gen_rule_docs.py --check`).
8. Measure it. Add or reuse corpus repos in `evals/corpus.json`, then sample and label (see [evals/README.md](evals/README.md)). Precision labels follow the measurement: a rule whose precision stays below 50% after fixes gets confidence `low` in the registry and a note in `docs/rules.md`. Never hide a rule for being noisy; label it. Publish the sample size with every number.
9. Add the rule to the table in `README.md`, and to `CHANGELOG.md`.

## Evals

Before each release run the scanner evals (the unit test suite), the five skill cases in `evals/skill-cases/` (`python evals/run.py`, headless with your Claude CLI; transcripts stay in the git-ignored `evals/runs/`; paid) and the precision study if rules changed. All cases must pass. See [evals/README.md](evals/README.md).

## Record the demo

The demo GIF cannot be recorded by an agent; record it yourself so it matches the current output.

1. Copy the fixture: `cp -r tests/fixtures/monorepo /tmp/monorepo-demo && cd /tmp/monorepo-demo`.
2. Start a screen recorder (for example `asciinema rec`, then convert with `agg`, or a screen-to-GIF tool) at about 100 columns, in a clean terminal with a short prompt.
3. Run `claude` in that folder and send exactly:

   ```text
   Quick audit of ./services. Checkout is slow.
   ```

4. Let it finish, then show the result: `cat docs/architecture/quick-audit.md`.
5. Keep it under 60 seconds (trim waiting), save as `docs/assets/demo.gif` (under 5 MB) and replace the placeholder comment near the top of `README.md` with `![Demo](docs/assets/demo.gif)`.
6. Check that no real paths, usernames or tokens are visible.

## Release checklist

- [ ] Tests pass on the CI matrix (Python 3.9, 3.11, 3.12, 3.13, 3.14 on ubuntu and windows), including the clean-install job and the rule-docs drift check
- [ ] Skill evals run in full on the final tree (`python evals/run.py`, all five cases in one run, after the last change to `skills/`), transcripts kept; it rewrites `evals/skill-results.md` (pass line, model, CLI, plugin version, date, isolation). Copy its `N / M` pass line into the README skill-evals block; keep notes in the history section of `evals/README.md`, not in `skill-results.md`
- [ ] Precision table re-run if rules changed (`evals/precision.md`), and the README "Measured quality" blocks updated to match
- [ ] Bump `version` in the three plugin manifests only (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.cursor-plugin/plugin.json`); the marketplace files intentionally have no version
- [ ] Complete the CHANGELOG entry and set its date
- [ ] Demo and `examples/` regenerated if the output format changed (the examples must still pass `lint_output.py`)
- [ ] `python skills/design-and-fix/scripts/gen_rule_docs.py --check`, `python tests/validate_manifests.py` and `claude plugin validate . --strict` pass
- [ ] Known limitations reviewed (`docs/limitations.md`), `ROADMAP.md` and `docs/security.md` still accurate
- [ ] Tag the release (`git tag v<version>`, push the tag) and create the GitHub Release with the CHANGELOG entry as notes
