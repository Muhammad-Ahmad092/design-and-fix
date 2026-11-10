# Compatibility

## Version policy

- The version is in the three plugin manifests and the changelog, and in the `> Audit:` line of every audit.
- Scan JSON is schema 3. Changes inside schema 3 are additive: new fields are optional and nothing is renamed or removed.
- Exit codes are stable: 0 ok, 1 bad root or scope or unwritable `--output-dir`, 2 config or usage error, 3 `--fail-on` threshold.
- A rule whose output changes gets a new `rule_version`. A rule can ship off by default; the scan names default-off rules in `notes`, and a config that enables one keeps it.
- Audit files carry `> Format: 1.0.2`. Older files still lint so archived runs stay readable.
- Python 3.9 and newer, standard library only.

## Harnesses

The tested status of each harness (Claude Code, Codex, Cursor, other Agent Skills clients) is the table in the [README](../README.md#compatibility). In short: Claude Code is validated and end-to-end tested; the eval runner supports `claude`, `codex` and `cursor`, and only Claude has been run end to end.

## Languages and frameworks

Detailed coverage and caveats are in [limitations.md](limitations.md). Per-rule numbers are in [rules.md](rules.md) and the README.

| Area | Languages and frameworks | Trust |
|---|---|---|
| Import graph, cycles, god modules | Python, JavaScript / TypeScript, Go | Measured; low precision on cycles and god modules |
| Routes and auth markers | Express, NestJS, Flask, FastAPI, Django, Go (net/http, chi, gin) | Measured on a mix of repos |
| Routes and auth markers | Spring, ASP.NET, Rails, Laravel | Newer, fewer repos; auth set outside `--scope` yields `uncertain_reason` and Status `needs-review` |
| Pattern rules | JS/TS, Python, Go; basic for Java / Kotlin, C#, Ruby, PHP | Per rule, see rules.md |
| Infra | Dockerfile, Kubernetes YAML, GitHub Actions, GitLab CI, CircleCI | Per rule |
| Dependencies | npm, pip / Poetry | Offline hygiene only, no CVE lookup |
| Other languages | Detected for the stack summary only | No rules |
