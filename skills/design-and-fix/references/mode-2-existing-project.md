# Mode 2: Existing Project

Trigger: the user points to a codebase, folders, services or existing docs and wants an audit. If the scanner reports < 2,000 approx LOC in the audited scope, use Quick mode instead (`references/quick-mode.md`) unless the user says "full" or "detailed".

## Workflow

```
Scan -> Quick? -> Context -> Archive + diff -> Verify signals -> Read more -> Score -> Fill 3 templates -> Self-check -> Deliver
```

1. **Scan.** Scope already named: run the `--scope <path>` scan (repeatable) directly; its output still carries whole-root `detected_stack`, `frameworks` and `candidate_folders`. Scope unknown: run the fast `--folders-only` scan of the root once (command in `SKILL.md`, Step 2) to learn `detected_stack`, `frameworks` and `candidate_folders` (`path`, `file_count`, `approx_loc`, sized recursively); the evidence scan then uses `--scope`.
2. **Decide Quick mode** from `summary.approx_loc` of the scoped scan before asking anything, when the scope is known.
3. **Context.** Follow `references/context-collection.md`: never ask about stack, frameworks, database or folder layout; max 4 questions (3 in Quick mode); propose focus from `candidate_folders`. If the scope was just agreed, run the `--scope` scan now and decide Quick mode. Never audit the whole repo unless asked.
4. **Archive + diff.** If earlier output exists, follow `references/archive-and-diff.md`: archive and read the previous issues first, so IDs are reconciled before any new ID is assigned.
5. **Verify signals** (below). Every issue starts from a verified signal or from your own reading; a surviving issue keeps its previous ID, a new one gets the next unused number.
6. **Read more.** Open the entry points, the files in `hotspots.files` and `import_graph.god_modules`, and the code behind the user's pain points. Look for what the scanner cannot see (see Analysis Areas). Then write Change Since Last Audit on a re-run.
7. **Score** with `references/health-score-rubric.md`.
8. **Fill templates** from `templates/mode-2/` into `docs/architecture/`.
9. **Self-check**, then **deliver**: the 3 files, the headline band and score, and the archive location on a re-run.

## Using the Scanner Output

| JSON field | Use |
|---|---|
| `detected_stack`, `frameworks` | Stack facts; never ask the user for them |
| `candidate_folders[]` | Propose scope; default scope = top 3 non-overlapping folders by `approx_loc` |
| `signals[]` (`rule`, `category`, `severity_hint`, `file`, `line`, `snippet`, `confidence`) | Leads to verify, one by one |
| `routes[]` (`method`, `path`, `file`, `line`, `framework`, `auth_detected`) | Endpoint Inventory; check `auth_detected: false` write routes by reading the file |
| `import_graph` (`modules`, `edges`, `cycles`, `god_modules`) | System Design and Maintainability evidence |
| `hotspots` (`available`, `since`, `files[]` with `commits`, `loc`, `score`) | Which files to read first; `available: false` means no git history, not no risk |
| `summary` (`files_scanned`, `approx_loc`, `signals_by_category`, `truncated`, `routes_total`) | Evidence line, Quick-mode threshold; a non-empty `truncated` (or `routes_total` above 500) means more hits exist than listed |
| `notes[]` | Mention skipped analyses (for example no git history) in one line when they limit the audit |

Flags: `--scope PATH` (repeatable), `--list-depth N` (folder depth listed as candidates, default 2), `--top N` (max candidate folders and hotspots), `--no-git` (skip `git log`), `--since 12.months` (hotspot window), `--format json|markdown` (markdown for a quick human read, JSON for evidence), `--folders-only` (scope discovery: stack, frameworks and candidate folders only; analyzers skipped).

## Verify Each Signal

Signals are leads, not findings. For every signal:

1. Open `file` at `line` and read enough surrounding code to judge it (the call, the middleware chain, the loop body).
2. **Confirm** it (the problem is real in context) or **reject** it (false positive: timeout set on a shared client, auth applied by a router-level middleware, placeholder secret).
3. A confirmed signal becomes an issue with an ID, an `Evidence` cell of `path:line`, and a severity you set from the Severity Definitions; `severity_hint` is a starting point, not the answer.
4. Rejected signals are not reported. Count them for the Evidence line: `Evidence: <n> files read, <m> scanner signals reviewed, <k> confirmed`.

Several signals with one root cause (for example the same client used without a timeout in five handlers) become one issue citing the main location and listing the rest in the Root cause cell.

## Analysis Areas

One per rubric category; the issue ID prefix follows the category.

| Category (prefix) | Look for |
|---|---|
| System Design (`SYS-`) | Unclear boundaries, import cycles, god modules, wrong pattern for scale, shared mutable state |
| API Design (`API-`) | Inconsistent naming, missing pagination, no versioning, mixed error formats, chatty endpoints, missing rate limits |
| Scalability (`SCL-`) | Queries in loops (N+1), unbounded queries, stateful services, synchronous work that should be async |
| Reliability (`REL-`) | Missing timeouts, retries without backoff, no idempotency, swallowed errors |
| Security (`SEC-`) | Unauthenticated write routes, hardcoded secrets, SQL built from strings, data exposure |
| Observability (`OBS-`) | No health endpoint, print-only logging, no request IDs, no metrics or alerts |
| Maintainability (`MNT-`) | Untested critical paths, duplicated logic, tangled layers, undocumented contracts |

## Evidence Rules

- Every issue cites `path:line` from a file you read. A claim you could not verify is marked `Not verified`.
- For an absence (no tests, no health route, no logger), cite the scanned folder or the route file(s) inspected as Evidence; never mark a confirmed absence `Not verified`.
- A category with no file read is `Not assessed` (see the rubric).
- Respect the user's constraints ("cannot change X"): recommend fixes that work within them.
- Investigate the user's stated pain points first; issue and sequence ordering follow Ordering Rules.

## Severity Definitions

| Severity | Meaning | Examples |
|---|---|---|
| Critical | Data loss, security breach or outage risk now | Unauthenticated write endpoint, hardcoded production secret, SQL injection |
| High | Will break or block growth soon | N+1 on a hot path, no timeout on external calls, import cycle across core modules |
| Medium | Slows the team or degrades performance | Inconsistent error format, missing pagination on large lists, no health endpoint |
| Low | Cleanup and polish | Naming inconsistencies, dead code |

## Output

Headers, in order, and table columns are defined in `references/formatting-rules.md` and laid out in `templates/mode-2/`.

- **Issue IDs:** `PREFIX-NNN` per the Issue IDs table in `references/formatting-rules.md`. `API-*` issues are defined only in `02-api-optimization.md`; every other issue only in `01-architecture-issues.md`. Other files reference IDs, never restate the row. On a re-run, IDs stay stable (`references/archive-and-diff.md`).
- **Issue tables** start with `ID` and carry `Evidence` (`path:line`) and `Consequence` (free text); "Impact" only ever means Low / Medium / High.
- **Endpoint Inventory** (`02`): routes from the scanner's `routes[]` in the audited scope (method, path, file, auth detected), capped at 20 rows with a count of the rest ("+N more not shown").
- **API Health Snapshot** (`02`): one row per endpoint group with its worst severity and issue IDs.
- **Roadmap** (`03`): every defined ID appears in exactly one `Action (IDs)` row across the three horizon tables (Quick Wins, Medium-term, Long-term), placed by effort; Suggested Sequence may cite IDs again and follows the order rule in `references/formatting-rules.md` (Roadmap Table).
- Optional: one current-state container diagram (Mermaid) in `01` only when it clarifies a System Design issue.
- ADRs only when the user asks (`references/adr-guide.md`).
- Length: about 2–3 pages per file, about half a page per section.

## Ordering Rules

- Issues sorted by severity, then within each severity by reach: hot paths and the user's stated pain points first.
- Suggested Sequence: the order rule in `references/formatting-rules.md` (Roadmap Table).

## Self-Check Before Delivering

- [ ] Scanner run with `--scope` matching what the user pointed to (or the documented default)
- [ ] Every signal opened and confirmed or rejected; only confirmed ones reported; Evidence line counts match
- [ ] Every issue has one ID, defined once in the right file, with `path:line` evidence or `Not verified`
- [ ] Issue tables use the `ID … Evidence … Consequence … Why` columns; Severity / Effort / Impact use the fixed vocabulary
- [ ] Sub-scores match the lookup table; headline is `## Architecture Health Score: <Band> (<X.X> / 10)`; categories without a file read are `Not assessed`
- [ ] Endpoint Inventory ≤ 20 rows with the rest counted
- [ ] Every defined ID sits in exactly one roadmap `Action (IDs)` row
- [ ] Re-run: previous output archived, IDs kept, Change Since Last Audit filled
- [ ] User constraints respected in every fix; no filler sections
- [ ] Three files; no `04-*` unless requested
