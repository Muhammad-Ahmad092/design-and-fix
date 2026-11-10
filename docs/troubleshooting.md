# Troubleshooting

## The scanner exits non-zero

| Exit code | Meaning | What to do |
|---|---|---|
| 1 | Unreadable root or a missing `--scope` folder; printed as `{"error": "..."}` | Check the path; scope folders are relative to the root and take `/` or `\` |
| 2 | Usage or config error | Read the message: it names the key and the fix. `.design-and-fix.json` must be valid JSON with known keys and rule ids ([configuration.md](configuration.md)) |
| 3 | `--fail-on` threshold reached | Intended. Lower the level, or drop `--fail-on` (the default never fails) |

## `python` is not found

Use `python3` (or `py -3` on Windows). The scanner needs Python 3.9 or newer and nothing else.

## A signal I expected is missing

- The rule may be off by default. Check the Default column in [rules.md](rules.md) and the scan `notes` line `rules off by default (enable in config)`. Re-enable with `"rules": {"<id>": {"enabled": true}}`.
- The file may be git-ignored (skipped unless `--no-gitignore`), larger than `limits.max_file_bytes`, excluded by config, or outside `--scope`.
- The scanner cannot see generated code, dynamic routes or Express chained routes ([limitations.md](limitations.md)).

## A signal looks wrong

Expected: scanner signals are leads. The skill reads the code and rejects false positives; the audit counts them on its "Rejected signal(s)" line. To silence one line permanently, add `// dfscan:ignore <rule>` or `# dfscan:ignore <rule>`. To silence a folder-level rule, set `"enabled": false` in config.

## A finding has Status needs-review

The scanner signal carried `uncertain_reason` (for example auth configured outside `--scope`) and the skill could not resolve it. Widen `--scope` to include the configuration the reason names, or read it yourself, then re-run.

## The scan is slow or partial

Narrow with `--scope`, add `exclude` globs, or set `limits.timeout_seconds`. A timeout or `max_files` stop gives partial results and says so in `notes`. Run `--folders-only` first to pick scopes.

## `lint_output.py` reports findings

Fix what it names (missing header, unknown ID, a score that does not match the table, an Evidence path that does not exist under `--repo`). Pass `--repo <root>` for audits, and `--scan <scan.json>` to enforce `needs-review` on uncertain signals. Lint checks structure, not whether the diagnosis is correct.

## The diff after a re-run marks matches (uncertain)

`compare_audits.py` fingerprints evidence against the repo. Without `--old-repo` (a snapshot of the old code) it cannot tell a moved file from a fixed issue with certainty, so it says so. Keep old IDs for issues that survive.

## The skill did not trigger

Confirm the plugin is installed for your harness (README, Install) and reload the window or restart the session. In non-interactive runs the skill proceeds with defaults marked `Assumed (non-interactive)`.
