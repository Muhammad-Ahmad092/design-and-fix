# Baseline false-positive patterns (before the 1.0.2 fixes)

Source: `evals/precision-baseline.md` and the labelled sheets in `evals/labels/baseline/` (scanner 1.0.2 before its false-positive fixes, seed 1002, up to 20 signals per rule across the corpus). Every example cites `repo@ref path:line`. Paths are relative to the extracted repo root (`../design-and-fix-corpus/<repo>/<top folder>/`).

Refs: flask@3.1.3, itsdangerous@2.2.0, full-stack-fastapi-template@0.10.0, django-styleguide-example@a70ef43, node-express-boilerplate@v1.7.0, nestjs-prisma-starter@225e5a9, golang-gin-realworld@0.0.6, create-t3-turbo@8f945b7, chi@v5.5.0, django@5.1.4.

Only `http-no-timeout` (4/4, n = 4) measured at 80% or above. `retry-no-backoff` produced no signals on the corpus, so its precision is unmeasured; it is not listed below.

## Rules below 80% precision

| Rule | tp / (tp + fp) | Precision | Sample n (unsure) |
|---|---|---|---|
| hardcoded-secret | 0 / 20 | 0% | 20 (0) |
| swallowed-error | 0 / 19 | 0% | 20 (1) |
| unauth-write-route | 0 / 19 | 0% | 20 (1) |
| unbounded-query | 0 / 20 | 0% | 20 (0) |
| query-in-loop | 0 / 18 | 0% | 20 (2) |
| sql-string-concat | 0 / 1 | 0% | 1 (0) |
| import-cycle | 3 / 20 | 15% | 20 (0) |
| no-health-endpoint | 3 / 20 | 15% | 20 (0) |
| no-tests | 3 / 18 | 17% | 20 (2) |
| god-module | 3 / 14 | 21% | 20 (6) |
| console-logging-only | 2 / 3 | 67% | 3 (0) |

## False-positive patterns per rule

### hardcoded-secret (0 / 20)
1. **Test files** (14 of 20). Fixture passwords in tests: django@5.1.4 `tests/auth_tests/test_views.py:73`, `tests/test_client/tests.py:552`; django-styleguide-example@a70ef43 `styleguide_example/authentication/tests/apis/test_user_login.py:21`; full-stack-fastapi-template@0.10.0 `backend/tests/api/routes/test_login.py:31`; golang-gin-realworld@0.0.6 `common/unit_test.go:97`; node-express-boilerplate@v1.7.0 `tests/integration/auth.test.js:301`. These are emitted with confidence `low`, but they dominate the corpus count (django alone: 151 signals).
2. **Placeholder not in the ignore list**: `changethis` in a committed `.env` template. full-stack-fastapi-template@0.10.0 `.env:21` (SECRET_KEY), `.env:23`, `.env:39`. The ignore regex has `changeme` but not `changethis`.
3. **Hashes in seed data**: bcrypt hash `$2b$10$...` under `password:`. nestjs-prisma-starter@225e5a9 `prisma/seed.ts:16`, `:33`.
4. **Regex/template text as the value**: `"token":"([a-zA-Z0-9-_.]{115})"` in an expected-response template. golang-gin-realworld@0.0.6 `users/unit_test.go:318`.
5. **Dummy digits** in a schema-generation script: create-t3-turbo@8f945b7 `packages/auth/script/auth-cli.ts:26` (`"1234567890"`).

### swallowed-error (0 / 19, 1 unsure)
1. **Specific exception used as control flow (EAFP), often with `try/except/else`**: django@5.1.4 `django/contrib/auth/management/commands/createsuperuser.py:308` (`DoesNotExist`), `django/utils/formats.py:117` and `django/db/models/options.py:899` (`KeyError` cache miss), `django/core/validators.py:274` (`UnicodeError` + `else`), `django/db/migrations/questioner.py:118` (`ValueError` re-prompt).
2. **Commented intentional ignore**: flask@3.1.3 `src/flask/config.py:163`, django@5.1.4 `django/db/backends/mysql/compiler.py:76`, `django/contrib/auth/middleware.py:159`, create-t3-turbo@8f945b7 `packages/ui/src/theme.tsx:36` (empty `catch {}` with a comment).
3. **Best-effort cleanup of an expected OS error**: django@5.1.4 `django/contrib/sessions/backends/file.py:202` (`OSError` on unlink), `django/core/files/storage/filesystem.py:177` (`PermissionError` on chown).
4. **Fallback value assigned before the try and checked after**: itsdangerous@2.2.0 `src/itsdangerous/timed.py:114`, django@5.1.4 `django/views/debug.py:103`, `django/contrib/syndication/views.py:170`.
Suggested direction: lower confidence for narrow, named exceptions (not bare `except:` / `except Exception:`), for `try/except/else`, and when the body holds a comment (fixed in 1.0.2).

### unauth-write-route (0 / 19, 1 unsure)
1. **Auth applied by middleware in another file**: golang-gin-realworld@0.0.6 `articles/routers.go:13`-`:19`, `users/routers.go:17` are mounted after `v1.Use(users.AuthMiddleware(true))` in `hello.go`.
2. **Public-by-design auth endpoints** (register, login, signup, password recovery/reset): golang-gin-realworld@0.0.6 `users/routers.go:11`, `:12`; full-stack-fastapi-template@0.10.0 `backend/app/api/routes/login.py:53`, `:77`, `backend/app/api/routes/users.py:143`.
3. **Example/demo programs**: chi@v5.5.0 `_examples/rest/main.go:81`, `_examples/todos-resource/todos.go:17`, `_examples/router-walk/main.go:21`.
4. Unsure: full-stack-fastapi-template@0.10.0 `backend/app/api/routes/private.py:23` (unauthenticated user creation, router mounted only when `ENVIRONMENT == "local"`).

### unbounded-query (0 / 20)
1. **Single-row lookups by a key in WHERE**: django@5.1.4 `django/contrib/gis/db/backends/oracle/introspection.py:22`, `django/contrib/postgres/signals.py:11`, `django/db/backends/oracle/schema.py:241`, `django/db/backends/sqlite3/introspection.py:340`; `SELECT 1 FROM DUAL` at `django/db/backends/oracle/base.py:287`.
2. **Aggregates** (`MAX(...)`): full-stack-fastapi-template@0.10.0 `backend/app/alembic/versions/d98dd8ec85a3_edit_replace_id_integers_in_all_models_.py:68`, `:69` (`COUNT` is exempt, `MAX/MIN/SUM` are not).
3. **Subqueries inside UPDATE / migrations**: same file `:33`, `:72`.
4. **ORM SQL fragments** built by a compiler: django@5.1.4 `django/db/models/sql/compiler.py:912`, `:1926`.
5. **`objects.all()` followed by `.delete()`** (bulk delete, no rows loaded): django-styleguide-example@a70ef43 `styleguide_example/tasks/management/commands/setup_periodic_tasks.py:20`-`:22`.
6. **Lazy QuerySet paginated by the caller**: django-styleguide-example@a70ef43 `styleguide_example/users/selectors.py:23`.
7. **Prisma `findMany` with `...args` spread** carrying take/skip: nestjs-prisma-starter@225e5a9 `src/posts/posts.resolver.ts:68`.
8. Small lookup tables / schema introspection: django@5.1.4 `django/contrib/auth/backends.py:78`, `django/db/backends/oracle/introspection.py:78`.

### query-in-loop (0 / 18, 2 unsure)
1. **DDL statement lists executed in order** (schema editors, migrations, SQL scripts): django@5.1.4 `django/db/backends/base/schema.py:167`, `:616`, `:637`, `:1043`, `django/db/backends/base/operations.py:473`, `django/db/migrations/operations/special.py:135`, `django/contrib/gis/db/backends/spatialite/schema.py:119`.
2. **Capitalised receiver that is a dict/constant**: django@5.1.4 `django/contrib/admin/widgets.py:482` (`SELECT2_TRANSLATIONS.get`); **`request.META.get`** at `django/utils/cache.py:354` (`META` matches the Capitalised-receiver pattern).
3. **SQLite PRAGMA per index** (no batch form): django@5.1.4 `django/db/backends/sqlite3/introspection.py:355`.
4. **Deliberate per-item transaction**, documented: django-styleguide-example@a70ef43 `styleguide_example/emails/services.py:63`.

### sql-string-concat (0 / 1)
1. **Interpolated constant identifier** (`quote_name(...)`) with `%s` placeholders for values: django@5.1.4 `django/db/backends/base/operations.py:97`. n = 1, so this is one pattern, not a rate.

### import-cycle (3 / 20)
1. **Function-local (deferred) imports counted as edges.** This is the main pattern (13 of 17 FPs); a deferred import is the standard way to break a cycle. django@5.1.4 `django/contrib/admin/decorators.py:91`, `django/contrib/auth/decorators.py:34`, `django/contrib/flatpages/models.py:41`, `django/db/migrations/serializer.py:255`, `django/contrib/sessions/backends/db.py:24`; django-styleguide-example@a70ef43 `styleguide_example/emails/tasks.py:13` (Celery pattern).
2. **Package `__init__` re-export**, where a submodule imports a sibling through the package: flask@3.1.3 `src/flask/__init__.py -> src/flask/app.py`, django@5.1.4 `django/contrib/messages/__init__.py -> api.py`, `django/test/__init__.py -> client.py`.
3. **TypeScript `import type`** (erased at runtime) in a generated file: create-t3-turbo@8f945b7 `apps/tanstack-start/src/routeTree.gen.ts:97`.
TPs were real module-level cycles: django@5.1.4 `django/db/backends/oracle/base.py <-> operations.py`, `django/db/backends/sqlite3/base.py <-> features.py`, nestjs-prisma-starter@225e5a9 `src/posts/models/post.model.ts <-> src/users/models/user.model.ts`.
Suggested direction: ignore imports that are not at module top level (Python indentation > 0, JS `import type`), and skip `__init__`/`index` re-exports (fixed in 1.0.2).

### no-health-endpoint (3 / 20)
1. **Library code / framework internals**: chi@v5.5.0 `middleware/profiler.go:26`; django@5.1.4 `django/conf/urls/i18n.py:38`, `django/contrib/admin/options.py:726`.
2. **Routes matched inside docstrings**: flask@3.1.3 `src/flask/ctx.py:126`, django@5.1.4 `django/template/defaulttags.py:1396`, `django/views/static.py:36`. Route detection reads raw lines, so docstring examples count as routes.
3. **Health endpoint under a name the scanner does not know**: full-stack-fastapi-template@0.10.0 `backend/app/api/routes/utils.py:29` (`/health-check/`), so `backend/app/api/routes/items.py:13` is flagged; golang-gin-realworld@0.0.6 has `/api/ping/` via `r.Group("/api/ping")` + `GET("/")` in `hello.go`, so `articles/routers.go:13` is flagged (the group prefix is not joined, and the ping route sits in a different folder from the flagged one).
4. **One service reported per sub-folder**: django-styleguide-example@a70ef43 is flagged 5 times (`styleguide_example/api/urls.py:4` is the TP; `users/urls.py:6`, `errors/urls.py:10`, `custom_admin/sites.py:17`, `blog_examples/.../raw/urls.py:9` are duplicates). The same applies to golang-gin-realworld `articles/` and `users/`.
5. **Example programs**: chi@v5.5.0 `_examples/hello-world/main.go:16`, `_examples/fileserver/main.go:33`.

### no-tests (3 / 18, 2 unsure)
1. **Tests in the repo-root `tests/` tree for depth-2 units** (fixed in 1.0.2): flask@3.1.3 `src/flask`, itsdangerous@2.2.0 `src/itsdangerous`, django@5.1.4 `django/core`, `django/forms`, `django/views`; node-express-boilerplate@v1.7.0 `src/middlewares` (mirrored in `tests/unit/middlewares/`).
2. **Tests in a sibling folder**: full-stack-fastapi-template@0.10.0 `backend/app` (tests in `backend/tests/`), `frontend/src` (tests in `frontend/tests/`).
3. **Config / settings / docs / migrations folders**: django-styleguide-example@a70ef43 `config/settings`; node-express-boilerplate@v1.7.0 `src/config`, `src/docs`; nestjs-prisma-starter@225e5a9 `prisma/migrations`; create-t3-turbo@8f945b7 `tooling/tailwind`.
4. **Example programs**: chi@v5.5.0 `_examples/hello-world`, `_examples/logging`.

### god-module (3 / 14, 6 unsure)
1. **Cohesive collections of same-kind classes or functions** (validators, widgets, filters, lookups, DB functions): django@5.1.4 `django/core/validators.py`, `django/forms/widgets.py`, `django/template/defaultfilters.py`, `django/db/models/lookups.py`, `django/contrib/gis/db/models/functions.py`, `django/utils/html.py`.
2. **High fan-in from tests**: django@5.1.4 `django/contrib/auth/models.py` (fan-in 100, mostly tests importing `User`), `django/core/management/base.py` (fan-in 65 from command subclasses).
3. **Test fixture modules**: django@5.1.4 `tests/model_fields/models.py`.
Suggested direction: count fan-in from non-test files only; skip test files as candidates.

### console-logging-only (2 / 3)
1. **Example programs**: chi@v5.5.0 `_examples/rest` (uses chi `middleware.Logger`; prints are demo output). n = 3, so this is one pattern, not a rate.

## Patterns that cut across rules
- **Example/demo folders** (`_examples/`, `examples/`) caused FPs in unauth-write-route, no-health-endpoint, no-tests and console-logging-only (chi@v5.5.0).
- **Test files** dominate hardcoded-secret, and as fan-in sources they inflate god-module.
- **Framework source code** (django@5.1.4) dominates swallowed-error (170 signals), hardcoded-secret (151) and query-in-loop (44). Its idioms (EAFP, deferred imports, DDL lists) are the main FP sources there.
