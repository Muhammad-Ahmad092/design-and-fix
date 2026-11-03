# Evals

Three kinds of checks. Run the first two before each release; re-run the precision study when rules change.

| Kind | What | How |
|---|---|---|
| Scanner evals | `scan_repo.py` finds every expected signal on the fixtures and nothing on `clean-service` (`scanner-expected.json`) | Automated, `unittest` |
| Skill cases | The agent follows the skill end to end on five prompts (`skill-cases/*.json`) | `python evals/run.py`: headless `claude -p`, graded deterministically (no LLM judge). Paid |
| Precision study | Precision per rule on real open-source repos (`corpus.json`), labelled by reading the code | `fetch_corpus.py` + `run_precision.py`, labels in `labels/` |

## Precision study

The corpus is 14 public repos pinned to a release tag or commit (`corpus.json`: URL, archive SHA-256, scope, kind and why each was chosen): 10 original repos plus Spring Boot, ASP.NET Core, Rails and Laravel apps. Results:

- `precision.md`: the 1.0.2 rules on all 14 repos (labels in `labels/after/`). The README "Measured quality" numbers come from here.
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

A full run (no `--case`) rewrites `skill-results.md`; its first line after the title is the `N / M cases pass.` line that the README skill-evals block repeats. `--case` runs print results only. `--keep` keeps the temp folders, `--timeout` sets seconds per case (default 1200; on timeout the whole process tree is killed).

What is isolated and what is not:

- File writes: `--permission-mode acceptEdits` accepts edits only inside the temp working folder, and there are no `Write`/`Edit` allow rules, so writes anywhere else are denied (`--permission-prompts none`). Bash is allowed only for `python` on the three bundled scripts.
- Tamper check: the repo's `skills/`, `.claude-plugin/`, `evals/skill-cases/`, `evals/run.py` and the staged plugin are hashed before and after each run; any change fails the case.
- Settings and MCP: `--setting-sources project,local` and an empty `--strict-mcp-config` skip your user settings and MCP servers. `--bare` (needs `ANTHROPIC_API_KEY`) also skips CLAUDE.md discovery and plugin hooks. The isolation in effect is recorded in `skill-results.md`.
- Not isolated: managed policy settings, your installed plugins and skills (and `~/.claude/CLAUDE.md` unless `--bare`), auth, the network, and reads: the agent can read any file your user can. For full isolation use a throwaway machine or the manual `Skill evals` GitHub workflow (`.github/workflows/skill-evals.yml`), which uploads transcripts as an artifact kept 7 days.

| Case | Covers |
|---|---|
| `01-audit-monorepo` | Full Mode 2 audit: scanner first, verified evidence, scoring, roadmap |
| `02-quick-question` | Step 0: a single question is answered in chat, no files written |
| `03-non-interactive` | Headless run: no questions, defaults recorded |
| `04-design-food-delivery` | Mode 1: capacity, data model, endpoints, ADRs |
| `05-rerun-diff` | Re-run: archive, Change Since Last Audit, stable IDs |

## Grading

- Grading is deterministic: no LLM judge. A case passes when every check in its `.json` file holds and claude exited cleanly.
- A release ships only when all five cases pass in one full run on the final tree. Failures become fixes in the skill or new checks; keep the transcript.
- The `.md` checklists describe intent that the `.json` checks cannot fully express (for example "answers in a few lines"); read the transcripts in `evals/runs/` against them when a result looks off.

## Skill eval history

Notes on runs before the published result, kept here because `run.py` regenerates `skill-results.md`.

- 1.0.2, first full run: `01-audit-monorepo` failed. Asked for a "full audit" of a small scope, the skill wrote the one-file Quick report, because `SKILL.md` checked the small-scope rule before the "full" override. The override is now checked first (`SKILL.md`, `references/quick-mode.md`), and case 01 then passed with the three-file audit. Because that change touches mode selection, all five cases were run again together on the final tree for the published result.
- 1.0.2, earlier runs fixed three harness defects: the linter accepts absence evidence and `a.py:3, 9-11` continuations, the grader matches line ranges, and case 04 checks `erDiagram` in the file the template puts it in.
- 1.0.2 runner hardening: a case now fails on a non-zero claude exit or a timeout, case 02 requires an answer in the transcript, line needles no longer match longer line numbers, case 04 lints its ADRs, and writes outside the temp folder are no longer allowed.
