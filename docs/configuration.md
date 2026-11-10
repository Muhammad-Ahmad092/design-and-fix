# Configuration

The scanner reads an optional `.design-and-fix.json` at the scan root (or the file given with `--config PATH`).
JSON only (no comments, no trailing commas). The JSON Schema is
`skills/design-and-fix/scripts/config.schema.json`. The `$schema` key is optional and only helps editors; the scanner ignores it. The relative path in the example works inside this repo; in your own repo, point it at your copy of the schema (for example in the plugin folder) or leave the key out. Precedence, highest first: CLI flags, the config file, a profile (`--profile` or the file's `profile` key), the built-in defaults.

```json
{
  "$schema": "./skills/design-and-fix/scripts/config.schema.json",
  "exclude": ["legacy/", "**/*.generated.ts"],
  "include": [],
  "rules": {
    "http-no-timeout": {"severity": "Medium"},
    "console-logging-only": {"enabled": false}
  },
  "limits": {"max_file_bytes": 1048576, "max_files": 50000, "timeout_seconds": 120},
  "fail_on": "High",
  "output": {"verbose": false}
}
```

## Options

| Key | Type | Default | Meaning |
|---|---|---|---|
| `exclude` | list of globs | `[]` | Files and folders skipped entirely (gitignore syntax: `*`, `**`, `?`, a leading `/` anchors, a trailing `/` matches folders only); backslashes are normalised to `/`, so `legacy\old` means `legacy/old`). Excluded files are not listed as skipped. |
| `include` | list of globs | `[]` | When non-empty, only matching files are scanned. Applied together with `exclude` (exclude wins). |
| `rules.<id>.enabled` | boolean | rule default (`on` for most rules; see the Default column in [rules.md](rules.md)) | `false` drops the rule's signals; `true` turns on a rule that is off by default. Rules still run internally; their signals are removed before the per-rule cap of 200 and are not counted in `summary.suppressed`. This is the only way to silence folder-level signals (line 0, for example `no-tests`). |
| `rules.<id>.severity` | `Critical`, `High`, `Medium`, `Low` | rule default | Replaces `severity_hint` on the rule's signals (also used by `fail_on` and SARIF levels). |
| `limits.max_file_bytes` | integer >= 1 | `1048576` | Larger code and config files are skipped (`too_large`). |
| `limits.max_files` | integer >= 1 | `50000` | The walk stops with partial results when reached. |
| `limits.timeout_seconds` | number >= 0 or null | off | Wall-clock budget for the scan; partial results when exceeded. `0` or `null` means no timeout. |
| `fail_on` | `Critical`, `High`, `Medium`, `Low`, `none` | `none` | Exit code 3 when a non-suppressed signal is at or above this severity. `--fail-on` overrides it (case-insensitive on the command line). |
| `output.verbose` | boolean | `false` | Same as `--verbose` (timing in `notes`). |
| `profile` | profile name | none | Built-in profile applied under this file (see Profiles); `--profile` replaces it. |
| `$schema` | string | none | Editor hint only. |

Rule ids are the `id` values in the scan output `signals` and in `meta.rule_versions`. An unknown key, unknown rule id or wrong type is a config error. So is a glob git could never match, such as the reversed range `[z-a]` or an unknown class `[[:nope:]]` (the message names the key, for example `exclude[1]`); brackets support ranges, `!`/`^` negation and POSIX classes such as `[[:digit:]]`. (In the repository's own `.gitignore` such a pattern matches nothing, as in git, and the scan lists it in `notes`.)

Settings that hide findings are disclosed in the scan output `notes`, so an audit can say what was not looked at: `rules disabled by config or profile: ...`, `config exclude patterns: ...` and `config include patterns: ...`.

Some rules may ship off by default (the Default column in [rules.md](rules.md)). Their signals are dropped unless the config sets `"rules": {"<id>": {"enabled": true}}`, so a config that already enables such a rule keeps getting it. Each default-off rule the config does not mention is listed in `summary.default_off_rules` and in a `notes` line `rules off by default (enable in config): <ids>`, so nothing disappears silently. A rule the config disables explicitly appears only in the `rules disabled by config or profile` note.

A signal may carry an optional `uncertain_reason` string when the rule could not decide (for example, auth configured outside the scanned scope). The key is present only when non-empty. In SARIF it becomes the result property `uncertainReason`. Secrets in it are masked like snippets, and it is capped at 240 characters.

`meta.config_hash` in the scan output is a digest of the effective settings, profile included (an empty config has its own fixed hash; `balanced` gives the same hash as no profile).

## Profiles

A profile is a built-in partial config that tunes rule selection and limits without changing what any rule means: it only turns rules off or changes `limits`, never a severity, and it never turns on a rule that is off by default. Pick one with `--profile NAME` or `"profile": "NAME"` in the config file. The config file wins on every key it sets (rule entries merge key by key, so `{"rules": {"http-no-timeout": {"enabled": true}}}` brings back a rule the profile turned off), and CLI flags win over both. The scan `notes` say which profile was applied.

| Profile | Keeps | Changes |
|---|---|---|
| `balanced` | every rule at its default | nothing (same as no profile) |
| `security-focused` | Security rules plus `missing-lockfile`, `unpinned-dependency` | other rules off |
| `api-focused` | API Design rules plus `unauth-write-route` | other rules off |
| `performance-focused` | Scalability rules plus `http-no-timeout`, `list-route-no-pagination` | other rules off |
| `architecture-focused` | System Design and Maintainability rules | other rules off |
| `quick` | every rule at its default | `max_files` 20000, `max_file_bytes` 262144, `timeout_seconds` 60 (a scan that hits either limit is partial; with `fail_on` set it exits 3) |

Rule categories are listed by `scan_repo.py --list-rules`. The definitions live in `skills/design-and-fix/scripts/dfscan/profiles.py`.

## Scope report

`summary.scope_report` says what the scan looked at: `scanned` (files analysed, the same number as `summary.files_scanned`), `skipped_by_reason` (files the walk skipped, counted per reason: `too_large`, `binary`, `minified`, `generated`, `permission`, `symlink_outside`, `max_files`) and `excluded_by_config` (paths dropped by `exclude` globs, where an excluded folder counts once, plus files left out by `include`). With `--scope` all three count only paths inside the scope (an excluded folder that contains the scope counts too). The first 200 skipped files are listed with their reason in `skipped_files`.

## Inline suppressions

Put `dfscan:ignore <rule>` in a comment on the flagged line or the line above it:

```js
const res = await fetch(url); // dfscan:ignore http-no-timeout
```

```python
# dfscan:ignore
requests.get(url)
```

- Grammar: `dfscan:ignore[:] [rule-a[, rule-b | rule-b]...] [-- reason]`, optionally closed by `*/`.
- `dfscan:ignore` alone (or `dfscan:ignore -- reason`) suppresses every rule on that line; `dfscan:ignore rule-a, rule-b` suppresses the listed rules. Rule ids are lowercase and separated by commas or spaces.
- Anything else after the marker (for example `dfscan:ignore Reason: flaky`) makes the comment **not** a suppression, so a typo never silences every rule. Put a reason after `--`.
- Recognised comment starters: `//`, `#`, `/*`. The marker is looked up in the original source, and only inside a real comment: in code files the comment is found the way the scanner strips comments, so `"http://x/dfscan:ignore"` in a string does not suppress. In config files, manifests and Dockerfiles the comment must start at the line start or after whitespace.
- The line above suppresses the next line only when it is a comment by itself; a marker at the end of a code line covers that line only. A UTF-8 BOM before a line-1 comment is ignored.
- Folder-level signals (line 0) cannot be suppressed inline; disable the rule in the config instead.
- Suppressed signals are removed and counted in `summary.suppressed`.

## CLI

```
python3 scan_repo.py <path> [--config PATH] [--profile NAME] [--fail-on LEVEL]
                     [--format json|markdown|sarif] [--max-findings N] [--output-dir DIR]
python3 scan_repo.py --version
python3 scan_repo.py --list-rules [--format json]
python3 scan_repo.py --explain-rule ID
```

| Flag | Effect |
|---|---|
| `--version` | Prints `design-and-fix scanner <version> (schema <n>)` and exits 0. |
| `--list-rules` | One line per rule: id, category, default severity, confidence, default `on`/`off` (`--format json` for a JSON list). |
| `--explain-rule ID` | The rule's version, category, severity, confidence, default, languages, description, limitations and remediation, plus its measured precision from `evals/precision.md` when that file is present (it ships with the repository, not with an installed plugin). An unknown id exits 2. |
| `--profile NAME` | Applies a built-in profile under the config file (see Profiles). |
| `--max-findings N` | Keeps the N most severe signals (stable order within a severity) and adds a `notes` line `max-findings N: showing N of M signals ...`. `summary` counts and `--fail-on` still cover every signal. |
| `--output-dir DIR` | Also writes the JSON result to `DIR/scan.json` (the folder is created; stdout keeps the chosen `--format`). A write failure exits 1. |

The skill runs the evidence scan with `--output-dir docs/architecture/.scan` and lints with `lint_output.py docs/architecture --scan docs/architecture/.scan/scan.json`. The linter does not walk dot folders, and the scanner skips them too, so the saved JSON never shows up as output or as scanned code. The file holds the absolute scan root and (masked) code snippets, so keep it out of version control: add this line to the audited repo's `.gitignore`:

```
docs/architecture/.scan/
```

`--format sarif` prints SARIF 2.1.0 for code-scanning tools. Locations are root-relative forward-slash URIs with
`uriBaseId` `%SRCROOT%`; levels are `error` (Critical, High), `warning` (Medium), `note` (Low); the signal
fingerprint is `partialFingerprints["dfscan/v1"]`. Folder-level signals point at the folder and have no region.
A signal's `uncertain_reason`, when set, is the result's `properties.uncertainReason`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Scan completed (and no `fail_on` threshold hit; with `fail_on` set, the scan was not partial) |
| 1 | Runtime error (unreadable path, scan failure, unwritable `--output-dir`) |
| 2 | Usage or config error (the message names the key, the problem and the fix) |
| 3 | `fail_on` threshold exceeded by a non-suppressed signal, or `fail_on` set and the scan partial (`summary.partial`: timeout or `max_files` reached; the gate fails closed and a note says `partial scan: --fail-on gate fails closed`) |

Errors print as `{"error": "..."}` on stdout. Without `fail_on` the scanner never exits 3.
