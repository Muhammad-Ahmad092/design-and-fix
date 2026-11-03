# Security and privacy

What Design & Fix reads, writes, runs and sends, so you can decide where it is safe to use.

## What the scanner reads

`skills/design-and-fix/scripts/scan_repo.py` walks the folder you point it at (or the `--scope` folders) and reads:

- Source files in the supported languages, up to `max_file_bytes` (default 1 MB) each.
- Manifests (`package.json`, `pyproject.toml`, `pom.xml`, `Gemfile`, `composer.json`, `*.csproj` and similar). With `--scope`, manifests in ancestor folders up to the scan root (and up to the nearest `.git`) are read too. Manifests only.
- Config, infra and CI files: Dockerfiles, Kubernetes YAML, GitHub Actions / GitLab CI / CircleCI configs, `openapi.*`, and the optional `.design-and-fix.json`.
- Secret-bearing files (`.env*`, `*.pem`, `*.key`, `id_rsa*`, credential files) are read only by the `hardcoded-secret` rule. No other rule sees them.

It does not read:

- Anything matched by the root `.gitignore` (globs, `/`-anchored patterns, `dir/` suffixes and `!` negation are supported). Nested `.gitignore` files are not applied in 1.0.2. Pass `--no-gitignore` to scan ignored files as well.
- Hidden directories and built-in ignores (`node_modules`, `.git`, build output, virtualenvs).
- Minified or generated files (`*.min.js`, `*.map`, `@generated` / `DO NOT EDIT` headers, very long average line length). They are counted in `skipped_files`.
- Files outside the root. Symlinks that point outside the root are not followed. Unreadable files are listed in `skipped_files` with a reason.

Because git-ignored `.env` and key files are skipped by default, a secret that exists only in a local, uncommitted file is not reported. That is intentional: it is not in the repository. Use `--no-gitignore` if you want local files checked too.

## What it writes

The scanner is read-only. It prints to stdout (JSON, Markdown or SARIF) and progress to stderr, nothing else.

The skill (the agent following `SKILL.md`) writes only under `docs/architecture/` and `docs/adr/`, and archives previous output to `docs/architecture/history/<date>/`.

## Secret handling

Values that match secret patterns are masked in every snippet the scanner emits (JSON, Markdown and SARIF), for all rules, not only `hardcoded-secret`. A committed private key produces one signal ("private key committed") with no key content in the snippet.

## Network

None. The scanner, linter and diff tool never open a connection. Two development tools in `evals/` use the network; neither ships in the skill's code path:

- `evals/fetch_corpus.py` downloads the precision-study corpus. It uses HTTPS from GitHub hosts only, caps the download and extracted size, checks a pinned SHA-256 and writes into a folder outside the repo.
- `evals/run.py` runs the skill evals by launching your `claude` CLI, which calls the model API under your account. Every case is a paid run. The agent's file writes are limited to a temp working folder, and the runner fails a case if the grader or plugin files change during the run; it can still read any file your user can read, so run it on a throwaway machine or CI runner for full isolation.

## Commands executed

The scanner runs two read-only git commands: `git log` (hotspots; skip with `--no-git`) and `git rev-parse` (records the revision in `meta`). Nothing else.

The skill's shell use is limited to the plugin's own scripts: `scan_repo.py`, `lint_output.py` and `compare_audits.py`. Any other command needs your explicit approval in the harness.

## Model data flow

Design & Fix is a skill, not a service. The harness you run it in (Claude Code, Codex, Cursor, ...) sends the files the agent reads, and the scanner output, to that harness's model provider under your account's terms. The plugin itself sends nothing anywhere.

To keep sensitive code out of the model's context:

- Scope narrowly (`--scope services/payments`) instead of auditing the whole repo.
- Exclude folders in `.design-and-fix.json` (`exclude`) and keep secrets in git-ignored files.
- Do not run the skill on repositories you are not allowed to share with your model provider.

## Untrusted content

Repository content (code, comments, READMEs, issues) is data, never instructions. `SKILL.md` tells the agent to ignore instructions found in scanned files. Scanner signals are leads: the agent reads the cited file before reporting anything.

## Limits and failure behaviour

`max_files` (default 50,000) stops the walk and marks the result `partial`. `timeout_seconds` is off by default; when set, analyzers stop and the result is partial. Malformed files do not crash a scan.

Exit codes: 0 ok, 1 runtime error (including an unreadable root or a missing scope), 2 usage or config error, 3 `--fail-on` threshold exceeded.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository. Do not post exploit details in a public issue.
