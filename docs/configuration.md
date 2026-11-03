# Configuration

The scanner reads an optional `.design-and-fix.json` at the scan root (or the file given with `--config PATH`).
JSON only (no comments, no trailing commas). The JSON Schema is
`skills/design-and-fix/scripts/config.schema.json`. The `$schema` key is optional and only helps editors; the scanner ignores it. The relative path in the example works inside this repo; in your own repo, point it at your copy of the schema (for example in the plugin folder) or leave the key out. Precedence: CLI flags, then the config file, then defaults.

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
| `rules.<id>.enabled` | boolean | `true` | `false` drops the rule's signals. Rules still run internally; their signals are removed before the per-rule cap of 200 and are not counted in `summary.suppressed`. This is the only way to silence folder-level signals (line 0, for example `no-tests`). |
| `rules.<id>.severity` | `Critical`, `High`, `Medium`, `Low` | rule default | Replaces `severity_hint` on the rule's signals (also used by `fail_on` and SARIF levels). |
| `limits.max_file_bytes` | integer >= 1 | `1048576` | Larger code and config files are skipped (`too_large`). |
| `limits.max_files` | integer >= 1 | `50000` | The walk stops with partial results when reached. |
| `limits.timeout_seconds` | number >= 0 or null | off | Wall-clock budget for the scan; partial results when exceeded. `0` or `null` means no timeout. |
| `fail_on` | `Critical`, `High`, `Medium`, `Low`, `none` | `none` | Exit code 3 when a non-suppressed signal is at or above this severity. `--fail-on` overrides it (case-insensitive on the command line). |
| `output.verbose` | boolean | `false` | Same as `--verbose` (timing in `notes`). |
| `$schema` | string | none | Editor hint only. |

Rule ids are the `id` values in the scan output `signals` and in `meta.rule_versions`. An unknown key, unknown rule id or wrong type is a config error. So is a glob git could never match, such as the reversed range `[z-a]` or an unknown class `[[:nope:]]` (the message names the key, for example `exclude[1]`); brackets support ranges, `!`/`^` negation and POSIX classes such as `[[:digit:]]`. (In the repository's own `.gitignore` such a pattern matches nothing, as in git, and the scan lists it in `notes`.)

Settings that hide findings are disclosed in the scan output `notes`, so an audit can say what was not looked at: `config disabled rules: ...`, `config exclude patterns: ...` and `config include patterns: ...`.
`meta.config_hash` in the scan output is a digest of the effective settings (an empty config has its own fixed hash).

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
python3 scan_repo.py <path> [--config PATH] [--fail-on LEVEL] [--format json|markdown|sarif]
```

`--format sarif` prints SARIF 2.1.0 for code-scanning tools. Locations are root-relative forward-slash URIs with
`uriBaseId` `%SRCROOT%`; levels are `error` (Critical, High), `warning` (Medium), `note` (Low); the signal
fingerprint is `partialFingerprints["dfscan/v1"]`. Folder-level signals point at the folder and have no region.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Scan completed (and no `fail_on` threshold hit) |
| 1 | Runtime error (unreadable path, scan failure) |
| 2 | Usage or config error (the message names the key, the problem and the fix) |
| 3 | `fail_on` threshold exceeded by a non-suppressed signal |

Errors print as `{"error": "..."}` on stdout. Without `fail_on` the scanner never exits 3.
