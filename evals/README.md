# Evals

Three kinds of checks. Run the first two before each release; re-run the precision study when rules change.

| Kind | What | How |
|---|---|---|
| Scanner evals | `scan_repo.py` finds every expected signal on the fixtures and nothing on `clean-service` (`scanner-expected.json`) | Automated, `unittest` |
| Skill cases | The agent follows the skill end to end on nine prompts (`skill-cases/*.json`) | `python evals/run.py`: headless `claude -p`, graded deterministically (no LLM judge). Paid |
| Precision study | Precision per rule on real open-source repos (`corpus.json`), labelled by reading the code | `fetch_corpus.py` + `run_precision.py`, labels in `labels/` |

## Precision study

The corpus is 18 public repos pinned to a release tag or commit (`corpus.json`: URL, archive SHA-256, scope, kind and why each was chosen): 10 original repos plus Spring Boot, ASP.NET Core, Rails and Laravel apps and four more added for 1.1.0. Results:

- `precision.md`: the 1.1.0 rules on all 18 repos (labels in `labels/after/`). The README "Measured quality" numbers come from here.
- `precision-baseline.md`: the rules before the 1.0.2 false-positive fixes, on the original 10 repos (labels in `labels/baseline/`), with the false-positive patterns behind it in `baseline-findings.md`.

```bash
python evals/fetch_corpus.py                 # HTTPS archives (no git) into ../design-and-fix-corpus/
python evals/run_precision.py sample --per-rule 20 --seed 1002 --out evals/labels/after
# label each entry in evals/labels/after/<rule>.json: "tp" | "fp" | "unsure", plus a one-line note
python evals/run_precision.py report --labels evals/labels/after --out evals/precision.md --tag after
# the baseline study: same commands with evals/labels/baseline and evals/precision-baseline.md
```

- `fetch_corpus.py` is the only evals tool that downloads anything. It accepts HTTPS URLs on `codeload.github.com` and `github.com` only (redirects included), caps the download (500 MB) and the extracted size and entry count, and writes into a folder **outside** the repo (default `../design-and-fix-corpus/`, `--dest` to change). It checks each archive's SHA-256 (`--record-sha` fills empty ones; a mismatching archive is deleted so the next run downloads it again) and refuses archives with absolute paths, `..` or links. A repo whose archive has links needs `"skip_links": true` in `corpus.json`. Links are then skipped and never created.
- `sample` scans each repo (scope = `subdir`, git off) and draws up to N signals per rule across the whole corpus, round-robin over repos with a fixed seed. It writes `labels/<tag>/<rule>.json` and `_population.json` (signal counts per repo, used to extrapolate). It refuses to overwrite labelled sheets unless you pass `--force`.
- Label strictly: `tp` only when the code, read in context, really has the problem the rule claims. Put the reason in `note`.
- `report` computes precision = tp / (tp + fp) with a Wilson 95% interval, extrapolated FPs per repo, recall on the fixture monorepo, repeat-run consistency, scan time and peak memory (`tracemalloc`), and records OS, Python and scanner version. `--no-measure` skips the timing scans. Every number is published with its sample size. The per-rule rate is pooled with equal weight per sampled signal, so small repos weigh more than their share of signals in the FP extrapolation. `--append NOTES.md` adds hand-written analysis at the end so it survives regeneration.

## Precision after verification

The precision study above measures raw scanner signals. The skill then reads the code and keeps, downgrades or drops each signal, so the precision a reader sees is a separate number. `verify_precision.py` measures it on issues whose Source is `scanner:<rule>`; `report_quality.py` runs deterministic checks on the audit itself. Both are offline and reuse the parsers in `lint_output.py` and `compare_audits.py`.

Release procedure (run by hand, one Python repo, one JS repo and one framework repo from the corpus):

```bash
# 1. run the skill (Mode 2) on the corpus repo; the audit lands in docs/architecture/ inside it
# 2. deterministic quality gate on that audit (exit 1 and one line per failure)
python evals/report_quality.py ../design-and-fix-corpus/<repo>/docs/architecture --repo ../design-and-fix-corpus/<repo>
# 3. one labelling sheet per repo (a row per scanner-sourced issue and rule)
python evals/verify_precision.py extract ../design-and-fix-corpus/<repo>/docs/architecture --out evals/labels/verified/<repo>.json
# 4. label each row "tp" | "fp" | "unsure" by reading the cited code (evals/LABELLING.md), then
python evals/verify_precision.py report evals/labels/verified --out evals/runs/verified.md
```

Paste the table into `precision.md` under "Precision after verification" with the three repos and the plugin version. Rows with status `not-reproducible` are counted as rejected by the skill and left out of precision. `unsure` and unlabelled rows are shown but not decided.

`report_quality.py` fails on: an Evidence path that does not exist, a line beyond the end of the file, a `confirmed` issue without Evidence, a file or route named in the Issue, Root cause or Consequence text that is not in the repo, and an issue ID reused with a different title in `history/`. Run it on `examples/mode-2-audit-monorepo` with `--repo tests/fixtures/monorepo` to see a clean pass. Tests: `tests/test_eval_quality.py`.

## Scanner evals

From the repo root:

```bash
python -m unittest discover -s tests -v
```

Use `python3` where `python` is missing. The suite also checks the skill docs, templates and `examples/`.

## Skill cases

Each case is a `skill-cases/<id>.json` file (prompt, setup and checks) with a `<id>.md` beside it that describes the expected behaviour for humans.

```bash
python evals/run.py [--case ID] [--claude PATH] [--model M] [--keep] [--timeout S] [--bare]
```

Every case is one real, **paid** `claude -p` call. For each case the runner:

1. Copies the case setup (usually `tests/fixtures/monorepo`, plus any pre-existing files) into a fresh temp folder.
2. Runs `claude -p "<prompt>"` there with a staged copy of this plugin loaded via `--plugin-dir` (no install, no settings change). The transcript goes to the git-ignored `evals/runs/<timestamp>/<id>.txt`.
3. Grades the result: the claude exit code (a crash, login failure or timeout fails the case), required and forbidden files, required strings in files (`path:N` needles also match evidence ranges), forbidden strings (case-insensitive), required strings in the transcript, and a clean `lint_output.py` run on `docs/architecture/` (and `docs/adr/` when present).

A full run (no `--case`) rewrites `skill-results.md` (per-case runs can be merged into it with `--merge-results`); its first line after the title is the `N / M cases pass.` line that the README skill-evals block repeats. `--case` runs print results only. `--keep` keeps the temp folders, `--timeout` sets seconds per case (default 1200; on timeout the whole process tree is killed).

What is isolated and what is not:

- File writes: `--permission-mode acceptEdits` accepts edits only inside the temp working folder, and there are no `Write`/`Edit` allow rules, so writes anywhere else are denied (`--permission-prompts none`). Bash is allowed only for `python` on the three bundled scripts.
- Tamper check: the repo's `skills/`, `.claude-plugin/`, `evals/skill-cases/`, `evals/run.py` and the staged plugin are hashed before and after each run; any change fails the case.
- Settings and MCP: `--setting-sources project,local` and an empty `--strict-mcp-config` skip your user settings and MCP servers. `--bare` (needs `ANTHROPIC_API_KEY`) also skips CLAUDE.md discovery and plugin hooks. The isolation in effect is recorded in `skill-results.md`.
- Not isolated: managed policy settings, your installed plugins and skills (and `~/.claude/CLAUDE.md` unless `--bare`), auth, the network, and reads: the agent can read any file your user can. For full isolation use a throwaway machine or the `Skill evals` GitHub workflow (`.github/workflows/skill-evals.yml`, weekly and manual), which uploads transcripts as an artifact kept 7 days.

| Case | Covers |
|---|---|
| `01-audit-monorepo` | Full Mode 2 audit: scanner first, verified evidence, scoring, roadmap |
| `02-quick-question` | Step 0: a single question is answered in chat, no files written |
| `03-non-interactive` | Headless run: no questions, defaults recorded |
| `04-design-food-delivery` | Mode 1: capacity, data model, endpoints, ADRs |
| `05-rerun-diff` | Re-run: archive, Change Since Last Audit, stable IDs |
| `06-no-tests-repo` | Python app with no tests: the audit reports the gap |
| `07-huge-repo` | A 20,000-file repo: the audit still completes |
| `08-prompt-injection` | Instructions inside a README are treated as data and not followed |
| `09-unsupported-language` | Elixir-only repo, no scanner rules: handled without invented scanner findings |

## Clients

`python evals/run.py --client claude|codex|cursor` (default `claude`) runs the same nine cases through another agent CLI. Looked up 2026-11-05 from the vendors' published references (Context7 snapshots of `github.com/openai/codex` source and `cursor.com/docs/cli`). Neither `codex` nor `cursor-agent` was installed on the build machine, so **these commands have not been run**; confirm each flag with `--help` before relying on a client, and fix `build_command_for` if one changed. Codex and Cursor stay unverified until `skill-results.md` holds a PASS row for them. A client counts as tested only when `skill-results.md` holds a PASS row for it (the CLI cell ends in `(Codex)` or `(Cursor)`), which `tests/test_eval_runner.py::test_compat_matrix_claims_match_results` enforces against the README compatibility table.

| Client | Command (cwd = temp folder) | Source |
|---|---|---|
| Claude Code | `claude -p <prompt> --plugin-dir <staged> --permission-mode acceptEdits --allowedTools ...` (see `run.py`) | `claude --help`, run in 1.0.2 |
| Codex | `codex exec --sandbox workspace-write --skip-git-repo-check --ephemeral -c approval_policy='never' -c sandbox_workspace_write.network_access=false [--model M] <prompt>` | `codex-rs/exec/src/cli.rs` (`--skip-git-repo-check`, `--ephemeral`), `codex-rs/utils/cli/src/shared_options.rs` (`--sandbox`, `--cd`), `codex-rs/exec/tests/suite/output_last_message.rs` (`--sandbox workspace-write -c approval_policy='never'`) |
| Cursor | `cursor-agent -p --force --trust --sandbox enabled --plugin-dir <staged plugin> --output-format text [--model M] <prompt>` | cursor.com/docs/cli/reference/parameters (`-p`, `--force`, `--trust`, `--sandbox`, `--plugin-dir`, `--output-format`, `--model`), cursor.com/docs/cli/headless |

How each client loads the plugin from the staged copy:

- Codex has no plugin-directory flag. The runner copies the staged `skills/design-and-fix` to `.agents/skills/design-and-fix` inside the temp folder (the repo-scoped skill location) before the baseline is taken, hashes that copy for the tamper check, and the prompt is sent on stdin for `.cmd` shims. If Codex does not find the skill there at release, the case fails on its transcript checks rather than passing silently.
- Cursor takes the staged plugin (manifests plus `skills/`) with `--plugin-dir`. The binary is `cursor-agent`; the runner also accepts `agent`. Its prompt always goes in argv, so on Windows the runner refuses (exit 2) when Cursor resolves to a `.cmd`/`.bat` shim and any case prompt contains `"`, `&`, `|`, `<`, `>`, `^` or `%` (cmd.exe would mangle them); pass `--claude` the real executable instead.
- Codex gets `--` before the prompt, so a prompt starting with `-` works. Cursor has no documented `--`: a Cursor prompt must not start with `-` (none of the cases do).
- A full run for Codex or Cursor writes `evals/skill-results-<client>.md`, not `skill-results.md`; combine with `python evals/run.py --merge-results evals/skill-results.md evals/skill-results-codex.md`. Each row's CLI cell records the client and version.

Safety gaps (each is written into the results file's `Isolation:` line):

| Control | Claude Code | Codex | Cursor |
|---|---|---|---|
| No interactive prompts | `--permission-prompts none` | `exec` has no approval flag; `approval_policy='never'` | `-p` (headless) |
| Writes confined to temp folder | `acceptEdits`, no Write/Edit rules | `--sandbox workspace-write` (also allows `/tmp` and `$TMPDIR` unless `sandbox_workspace_write.exclude_*` is set) | **Gap**: docs say print mode has full write access; `--sandbox enabled` covers shell commands only |
| Bash limited to the three scripts | allow rules per script | **Gap**: any command inside the sandbox | **Gap**: `--force` allows commands unless denied; no allowlist is configured |
| User settings, rules, MCP skipped | `--setting-sources`, empty MCP config | **Gap**: `~/.codex` config, `AGENTS.md` and skills still load | **Gap**: user rules and MCP servers still load |
| Network off | not enforced | `network_access=false` | not enforced |

Because of these gaps, run Codex and Cursor on a throwaway machine or in the `Skill evals` workflow, never against a checkout with secrets. The tamper check still catches edits to the grader and plugin; for Cursor and Codex it does not stop writes outside the temp folder.

## Grading

- Grading is deterministic: no LLM judge. A case passes when every check in its `.json` file holds and claude exited cleanly.
- A release ships only when every case passes on the final tree: one full run, or per-case runs merged with `--merge-results`, as long as every case ran after the last change to `skills/`. Failures become fixes in the skill or new checks; keep the transcript.
- The `.md` checklists describe intent that the `.json` checks cannot fully express (for example "answers in a few lines"); read the transcripts in `evals/runs/` against them when a result looks off.

## Skill eval history

Notes on runs before the published result, kept here because `run.py` regenerates `skill-results.md`.

- 1.0.2, first full run: `01-audit-monorepo` failed. Asked for a "full audit" of a small scope, the skill wrote the one-file Quick report, because `SKILL.md` checked the small-scope rule before the "full" override. The override is now checked first (`SKILL.md`, `references/quick-mode.md`), and case 01 then passed with the three-file audit. Because that change touches mode selection, all five cases were run again together on the final tree for the published result.
- 1.0.2, earlier runs fixed three harness defects: the linter accepts absence evidence and `a.py:3, 9-11` continuations, the grader matches line ranges, and case 04 checks `erDiagram` in the file the template puts it in.
- 1.0.2 runner hardening: a case now fails on a non-zero claude exit or a timeout, case 02 requires an answer in the transcript, line needles no longer match longer line numbers, case 04 lints its ADRs, and writes outside the temp folder are no longer allowed.
- 1.1.0, first run (9 cases × Opus and Sonnet): every Sonnet case and Opus case 07 failed the grader tamper check, because a test suite running at the same time rewrote `__pycache__` files in the plugin folder. Children now use a private `PYTHONPYCACHEPREFIX` (so no run reads a repo bytecode cache) and the tamper check ignores cache files. The runner also crashed printing a failure on a cp1252 Windows console; console output now replaces unprintable characters.
- 1.1.0, Sonnet answered audits of small repos in chat without running the scanner or writing files. Two skill fixes: `SKILL.md` Step 0 says an audit always scans and writes its file(s), however small the repo, and the skill description says to use it for every audit. Mode 1 also says how to mark a requirement value that is partly given and partly assumed. After these fixes all nine Sonnet cases passed.
- 1.1.0, published result: 16 / 18, assembled from per-case parallel runs (stamps 20261105-215439, 215440, 220245, 220246, 220247) merged with `--merge-results`, not from one full run. The two Opus failures (04, 08) happened because the eval's script allow-list denied the agent's lint command, so it delivered unlinted output (the prompt injection in 08 was not followed); both cases passed on the previous run. Other passing runs also report denied commands (a `cd` prefix, shell variables, compound commands and relative script paths do not match the allow rules). `SKILL.md` and the templates changed after these runs, so all cases are to be re-run on the final tree before the next release. Telling the agent to call each script as one plain absolute-path command is in 1.1.0; having the runner flag denied script commands is on the roadmap.
