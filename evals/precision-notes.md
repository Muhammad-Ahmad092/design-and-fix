## Baseline vs 1.1.0

The baseline (`evals/precision-baseline.md`) used the 1.0.1 rules on 10 repos. This report uses the 1.1.0 rules on 18 repos: the original 10, the Spring Boot, ASP.NET Core, Rails and Laravel apps added in 1.0.2, and 4 repos added in 1.1.0 so that more rules have signals (example-voting-app, robot-shop, awesome-compose, todo-nodejs-mongo). Same seed (1002), up to 20 labels per rule, labelled with `evals/LABELLING.md` (guide version 2). Labels from earlier runs are carried over when the signal's fingerprint is unchanged; the rest were labelled fresh.

Release decisions taken from these numbers:

- A rule measured at 0% with at least 5 decided labels ships **off by default**. In 1.1.0 that is `openapi-drift` (0 of 14, all from Express routers mounted with `app.use('/prefix', router)`, whose prefix is not resolved). Enable it in `.design-and-fix.json` if your specs and routes are not mounted that way.
- Measured precision caps signal confidence (`skills/design-and-fix/scripts/dfscan/measured.json`): high only from 80% with at least 10 labels, low under 50%, and the registry value when fewer than 5 labels were decided.
- Each rule's floor (`evals/precision-floors.json`) is its 1.1.0 precision minus 10 points; the release gate enforces it once a rule has at least 10 decided labels.

## Second labeller

A second labeller labelled five rules blind (labels and notes removed, earlier labels not visible): `evals/labels/second/`. Agreement and Cohen's kappa from `python evals/agreement.py evals/labels/after evals/labels/second`:

| Rule | Pairs | Agreement | Kappa |
|---|---|---|---|
| docker-root-user | 20 | 80% | 0.62 |
| http-no-timeout | 20 | 100% | 1.00 |
| no-tests | 20 | 85% | 0.70 |
| swallowed-error | 20 | 95% | 0.73 |
| unauth-write-route | 19 | 53% | 0.27 |

Agreement is substantial or better for four rules. It is poor for `unauth-write-route`: the first labeller marked unprotected write routes in demo apps with no user model as fp ("not worth a user's attention in a deployed service"), the second marked them tp. Read its precision as a wide band (33% with the first labels, about 83% with the second (15 tp, 3 fp)); the labelling guide needs an explicit rule for demo apps before the next measurement.

## Recurring false-positive patterns

- swallowed-error: Django's expected-exception control flow (`except X: pass` as a documented fallback, return None, cleanup).
- http-no-timeout: browser-side `fetch()` in demo React frontends and the create-react-app `serviceWorker.js` scaffold.
- unauth-write-route: demo apps with no auth model, public-by-design routes (registration, anonymous forms), Rails actions that authorize inside the method.
- unbounded-query: filtered single-row lookups and tiny seeded demo tables.
- god-module / import-cycle: cohesive same-kind Django modules; type-only imports and package `__init__` re-exports.
- console-logging-only and docker rules: sample stacks (awesome-compose dev-envs), static nginx images, browser front-end folders.
- no-tests: single-file demo apps, template wiring, static assets.

## Scan time

The largest corpus repo (django) is under the 30-second design target after the 1.1.0 parse-once file cache (see the scan time above); normal service repos take well under a second.

## Fixes after measurement

The auth-resolver and scanner fixes from the final review of 1.1.0 landed after the corpus was labelled. Rule versions were not bumped, because a bump would make the labels stale and force a re-measurement. A re-scan of all 18 corpus repos with the fixed code, diffed against the labelled populations, showed no change in `unauth-write-route` signals (19, the same signals) or in any other rule's counts, so the measured precision still applies. No labelled signal disappeared and no new one appeared.
