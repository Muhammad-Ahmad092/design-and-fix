# Quickstart: your first audit in five minutes

You need Python 3.9+ and one supported harness. This walkthrough uses the bundled fixture, `tests/fixtures/monorepo`, so you do not have to point it at your own code first.

## 1. Install

Pick your harness (all variants in the [README](../README.md#install)). For Claude Code:

```bash
claude plugin marketplace add Muhammad-Ahmad092/design-and-fix
claude plugin install design-and-fix@design-and-fix
```

## 2. Try the scanner alone

From a clone of this repo:

```bash
python skills/design-and-fix/scripts/scan_repo.py tests/fixtures/monorepo --no-git --format markdown
```

Use `python3` where `python` is missing. The default `--format json` is what the skill consumes; `--format sarif` is for code-scanning tools. Add `--fail-on High` to exit 3 when a High or Critical signal exists.

## 3. Run the skill

Copy the fixture somewhere writable so the audit files do not land in the repo, open that folder in your harness, and send the prompt.

### Claude Code

```bash
cp -r tests/fixtures/monorepo /tmp/monorepo-demo && cd /tmp/monorepo-demo && claude
```

```text
Quick audit of ./services. Checkout is slow.
```

### Codex

```bash
cp -r tests/fixtures/monorepo /tmp/monorepo-demo && cd /tmp/monorepo-demo && codex
```

Use the same prompt, or invoke the skill by name.

The skill evals can drive Codex headlessly (`python evals/run.py --client codex`); this is not yet claimed as end-to-end tested. See `evals/README.md`, Clients.

### Cursor

Open the copied folder in Cursor with the plugin installed (see the README) and send the same prompt in Agent chat.

Headless runs use `cursor-agent` (`python evals/run.py --client cursor`); not yet claimed as end-to-end tested. See `evals/README.md`, Clients.

### Other clients

Install with `npx skills add Muhammad-Ahmad092/design-and-fix`, open the copied folder and send the same prompt.

On Windows PowerShell, copy with `Copy-Item -Recurse tests\fixtures\monorepo $env:TEMP\monorepo-demo`.

## 4. What you get

`docs/architecture/quick-audit.md`: a health band, a score, and ranked issues with stable IDs and `path:line` evidence. Say "full audit" for the three-file version. Compare with the real example in [examples](../examples/README.md).

## 5. Check and re-run

The checker scripts ship with the skill. Point `DF` at the `skills/design-and-fix` folder: in a clone of this repo that is `<clone>/skills/design-and-fix`; with a marketplace install it sits in your harness's plugin cache (for Claude Code, under `~/.claude/plugins/`). Then, still inside `/tmp/monorepo-demo`:

```bash
DF=/path/to/design-and-fix-repo/skills/design-and-fix
python "$DF/scripts/lint_output.py" docs/architecture --repo .
```

Fix something and run the audit again: the new report opens with a Change Since Last Audit section. `python "$DF/scripts/compare_audits.py" <old-dir> <new-dir> --repo .` produces the same diff standalone.

## Next

- Tune the scan: [configuration](configuration.md)
- What each signal means: [rules](rules.md)
- What the scanner cannot see: [limitations](limitations.md)
- What leaves your machine: [security and privacy](security.md)
