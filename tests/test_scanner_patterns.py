from __future__ import annotations

import os
import tempfile
import unittest

from helpers import FIXTURES
from dfscan.patterns import run_rules, strip_comments
from dfscan.report import scan
from dfscan.walker import candidate_folders, walk

MONO = os.path.join(FIXTURES, "monorepo")
CLEAN = os.path.join(FIXTURES, "clean-service")

EXPECTED_MONO = {
    ("http-no-timeout", "services/payments/src/handlers/charge.ts"),
    ("http-no-timeout", "services/orders/src/orders.go"),
    ("http-no-timeout", "services/catalog/app.py"),
    ("query-in-loop", "services/payments/src/handlers/charge.ts"),
    ("unbounded-query", "services/orders/src/orders.go"),
    ("hardcoded-secret", "services/catalog/app.py"),
    ("sql-string-concat", "services/catalog/app.py"),
    ("swallowed-error", "services/payments/src/handlers/webhook.ts"),
    ("swallowed-error", "services/catalog/app.py"),
    ("no-tests", "services/orders"),
}


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _run(files):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        fs = walk(root)
        return run_rules(fs, candidate_folders(fs))


def _rules(files):
    return [s.rule for s in _run(files)]


class PatternFixtureTests(unittest.TestCase):
    def setUp(self):
        self.mono = (walk(MONO), candidate_folders(walk(MONO)))
        self.clean = (walk(CLEAN), candidate_folders(walk(CLEAN)))

    def test_expected_signals(self):
        got = {(s.rule, s.file) for s in run_rules(*self.mono)}
        self.assertTrue(EXPECTED_MONO <= got, EXPECTED_MONO - got)

    def test_refund_axios_with_timeout_not_flagged(self):
        self.assertNotIn(("http-no-timeout", "services/payments/src/handlers/refund.ts"),
                         {(s.rule, s.file) for s in run_rules(*self.mono)})

    def test_clean_service_zero_signals(self):
        self.assertEqual(run_rules(*self.clean), [])

    def test_comments_and_tests_ignored(self):  # Review Focus 5
        got = {(s.rule, s.file) for s in run_rules(*self.mono)}
        self.assertNotIn(("http-no-timeout", "services/payments/src/handlers/charge.test.ts"), got)
        self.assertEqual(
            _rules({"svc/x/a.ts": "// await fetch(url)\n/* axios.get(u) */\nexport const x = 1;\n"}),
            ["no-tests"])
        self.assertEqual(_rules({"svc/x/a.py": "# requests.get(u)\nx = 1\n"}), ["no-tests"])

    def test_no_tests_signal_shape(self):
        sig = [s for s in run_rules(*self.mono) if s.rule == "no-tests" and s.file == "services/orders"][0]
        self.assertEqual((sig.line, sig.category, sig.severity_hint, sig.confidence),
                         (0, "Maintainability", "Medium", "medium"))

    def test_console_logging_only_on_payments(self):
        got = {(s.rule, s.file) for s in run_rules(*self.mono)}
        self.assertIn(("console-logging-only", "services/payments"), got)
        self.assertNotIn(("console-logging-only", "services/catalog"), got)

    def test_scan_wires_signals_and_summary(self):
        out = scan(MONO, use_git=False)
        rules = {s["rule"] for s in out["signals"]}
        self.assertIn("http-no-timeout", rules)
        self.assertIn("unauth-write-route", rules)
        cats = out["summary"]["signals_by_category"]
        self.assertEqual(sum(cats.values()), len(out["signals"]))
        self.assertEqual(out["summary"]["truncated"], {})


class StripCommentsTests(unittest.TestCase):
    def test_strip_comments_preserves_lines(self):
        src = "a\n/* x\ny */\nb // c\n"
        out = strip_comments(src, "TypeScript")
        self.assertEqual(out.count("\n"), src.count("\n"))
        self.assertNotIn("x", out)
        self.assertNotIn("c", out)

    def test_strings_survive(self):
        out = strip_comments('u = "http://x.test/a" // c\n', "TypeScript")
        self.assertIn("http://x.test/a", out)
        out = strip_comments("s = '# not a comment'  # real\n", "Python")
        self.assertIn("# not a comment", out)
        self.assertNotIn("real", out)


class RuleBehaviourTests(unittest.TestCase):
    def test_provider_secret_regexes(self):  # strings assembled at runtime
        key = "AKIA" + "Q" * 16
        pem = "-----BEGIN " + "PRIVATE KEY-----"
        for body in ('x = "%s"\n' % key, 'x = """%s\nabc"""\n' % pem):
            self.assertEqual(_rules({"svc/k.py": body}).count("hardcoded-secret"), 1)

    def test_secret_placeholders_and_tests(self):
        self.assertNotIn("hardcoded-secret", _rules({"a/b/c.py": 'password = "changeme-please"\n'}))
        self.assertNotIn("hardcoded-secret", _rules({"a/b/c.py": 'token = os.environ["T"]\n'}))
        self.assertNotIn("hardcoded-secret", _rules({"a/b/test_c.py": 'password = "hunter2-real-pw"\n'}))
        sig = [s for s in _run({"a/b/c.py": 'password = "hunter2-real-pw"\n'})
               if s.rule == "hardcoded-secret"][0]
        self.assertNotIn("hunter2", sig.snippet)

    def test_other_rules_skip_tests(self):
        self.assertNotIn("swallowed-error", _rules({"a/b/c.test.ts": "try { f() } catch (e) {}\n"}))

    def test_fetch_with_signal_ok_and_multiline(self):
        self.assertNotIn("http-no-timeout", _rules({"a/b/c.ts": "fetch(u, {\n  signal: s,\n});\n"}))
        self.assertIn("http-no-timeout", _rules({"a/b/c.ts": "fetch(u, {\n  method: 'GET',\n});\n"}))

    def test_python_and_go_timeouts(self):
        self.assertIn("http-no-timeout", _rules({"a/b/c.py": "requests.post(u, json=d)\n"}))
        self.assertNotIn("http-no-timeout", _rules({"a/b/c.py": "requests.post(u, timeout=3)\n"}))
        self.assertIn("http-no-timeout", _rules({"a/b/c.go": "c := &http.Client{}\n"}))
        self.assertNotIn("http-no-timeout", _rules({"a/b/c.go": "c := &http.Client{Timeout: 5}\n"}))

    def test_query_in_loop_python_and_scope(self):
        loop = "for u in users:\n    x = db.query(u)\ny = db.query(1)\n"
        sigs = [s for s in _run({"a/b/c.py": loop}) if s.rule == "query-in-loop"]
        self.assertEqual([s.line for s in sigs], [2])
        self.assertIn("query-in-loop",
                      _rules({"a/b/c.ts": "ids.forEach(async (i) => {\n await db.findOne(i);\n});\n"}))
        self.assertNotIn("query-in-loop",
                         _rules({"a/b/c.ts": "for (const i of ids) {\n f(i);\n}\ndb.find(1);\n"}))

    def test_unbounded_query_variants(self):
        self.assertNotIn("unbounded-query", _rules({"a/b/c.py": 'q = "SELECT a FROM t LIMIT 5"\n'}))
        self.assertIn("unbounded-query", _rules({"a/b/c.py": 'q = "SELECT a FROM t"\n'}))
        self.assertIn("unbounded-query", _rules({"a/b/c.ts": "db.find({});\n"}))
        self.assertIn("unbounded-query", _rules({"a/b/c.ts": "p.user.findMany({ where: w });\n"}))
        self.assertNotIn("unbounded-query", _rules({"a/b/c.ts": "p.user.findMany({ take: 10 });\n"}))
        self.assertIn("unbounded-query", _rules({"a/b/c.py": "rows = Item.objects.all()\n"}))

    def test_sql_string_concat(self):
        self.assertIn("sql-string-concat", _rules({"a/b/c.py": 'q = f"SELECT * FROM t WHERE id={i}"\n'}))
        self.assertIn("sql-string-concat", _rules({"a/b/c.ts": "q(`DELETE FROM t WHERE id=${i}`);\n"}))
        self.assertNotIn("sql-string-concat",
                         _rules({"a/b/c.ts": 'q("DELETE FROM t WHERE id=$1", [i]);\n'}))

    def test_retry_no_backoff(self):
        bad = "for (let attempt = 0; attempt < 3; attempt++) {\n  await fetch(u, {signal: s});\n}\n"
        good = (
            "for (let attempt = 0; attempt < 3; attempt++) {\n  await fetch(u, {signal: s});\n"
            "  await sleep(100);\n}\n")
        self.assertIn("retry-no-backoff", _rules({"a/b/c.ts": bad}))
        self.assertNotIn("retry-no-backoff", _rules({"a/b/c.ts": good}))

    def test_swallowed_error_go(self):
        self.assertIn("swallowed-error", _rules({"a/b/c.go": "_ = err\n"}))
        self.assertIn("swallowed-error", _rules({"a/b/c.go": "if err != nil {\n}\n"}))

    def test_console_logging_with_library_not_flagged(self):
        files = {"svc/x/a.ts": "import pino from 'pino';\nconsole.log('x');\n" + "const a = 1;\n" * 120}
        self.assertNotIn("console-logging-only", _rules(files))
        files = {"svc/x/a.ts": "console.log('x');\n" + "const a = 1;\n" * 120}
        self.assertIn("console-logging-only", _rules(files))

    def test_no_tests_only_depth_two(self):
        files = {"svc/x/a.ts": "export const a = 1;\n", "svc/y/a.ts": "export const a = 1;\n",
                 "svc/y/a.test.ts": "x\n"}
        got = [(s.rule, s.file) for s in _run(files) if s.rule == "no-tests"]
        self.assertEqual(got, [("no-tests", "svc/x")])


class NegativeTests(unittest.TestCase):
    def _none(self, rule, rel, text):
        self.assertNotIn(rule, _rules({rel: text}))

    def test_query_in_loop_non_db_calls(self):
        self._none("query-in-loop", "a/b/c.py", "for k in keys:\n    v = headers.get(k)\n")
        self._none("query-in-loop", "a/b/c.ts", "ids.map(i => cache.get(i));\n")
        self._none("query-in-loop", "a/b/c.ts", "for (const k of ks) {\n  params.get(k);\n}\n")

    def test_query_in_loop_db_receivers(self):
        self.assertIn("query-in-loop", _rules({"a/b/c.ts": "for (const k of ks) {\n  await User.find(k);\n}\n"}))
        self.assertIn("query-in-loop", _rules({"a/b/c.py": "for k in ks:\n    repo.get(k)\n"}))

    def test_secret_label_not_flagged(self):
        self._none("hardcoded-secret", "a/b/c.ts",
                   'const m = { password: "Password must be at least 8 characters" };\n')

    def test_sql_concat_english_not_flagged(self):
        self._none("sql-string-concat", "a/b/c.ts", "const t = `Select a file from ${dir}`;\n")
        self._none("sql-string-concat", "a/b/c.ts", "console.log('Failed to select ' + name + ' from list');\n")
        self._none("sql-string-concat", "a/b/c.ts", "console.log('SELECT * FROM t WHERE id=' + id);\n")

    def test_http_no_timeout_negatives(self):
        self._none("http-no-timeout", "a/b/c.ts", "axios.get(u, { timeout: 100 });\nconst r = refetch(x);\n")

    def test_swallowed_import_error_exempt(self):
        self._none("swallowed-error", "a/b/c.py", "try:\n    import x\nexcept ImportError:\n    pass\n")
        self._none("swallowed-error", "a/b/c.py", "try:\n    import x\nexcept ModuleNotFoundError:\n    pass\n")
        self.assertIn("swallowed-error", _rules({"a/b/c.py": "try:\n    f()\nexcept ValueError:\n    pass\n"}))


FLASK_ROUTES = ("from flask import Flask\n\napp = Flask(__name__)\n\n\n"
                '@app.route("/items")\ndef items():\n    print("listing items")\n    return "ok"\n'
                + "".join("\n\ndef helper_%d():\n    return %d\n" % (i, i) for i in range(28)))


class FinalReviewRuleTests(unittest.TestCase):
    def test_flat_flask_app_folder_rules(self):  # final review 5
        self.assertGreaterEqual(FLASK_ROUTES.count("\n"), 110)
        got = {(s.rule, s.file) for s in _run({"app/routes.py": FLASK_ROUTES})}
        self.assertIn(("no-tests", "app"), got)
        self.assertIn(("console-logging-only", "app"), got)

    def test_flat_app_with_top_level_tests(self):  # final review 5
        got = {(s.rule, s.file) for s in _run({"app/routes.py": FLASK_ROUTES,
                                               "tests/test_routes.py": "def test_x():\n    pass\n"})}
        self.assertNotIn(("no-tests", "app"), got)
        self.assertNotIn(("no-tests", "tests"), got)

    def test_root_only_code_unit(self):  # final review 5
        got = {(s.rule, s.file) for s in _run({"routes.py": FLASK_ROUTES})}
        self.assertIn(("no-tests", "."), got)
        self.assertIn(("console-logging-only", "."), got)

    def test_deep_scope_gets_no_tests(self):  # final review 5; R2-fp1 sibling tests count
        import dfscan.report as report
        with tempfile.TemporaryDirectory() as root:
            _write(root, "services/payments/src/a.ts", "export const a = 1;\n")
            _write(root, "services/billing/test/a.test.ts", "x\n")
            out = report.scan(root, scope=["services/payments/src"], use_git=False)
            got = {(s["rule"], s["file"]) for s in out["signals"]}
            self.assertIn(("no-tests", "services/payments/src"), got)
            # Tests next to the scope root (outside the scope) are seen since R2-fp1.
            _write(root, "services/payments/test/a.test.ts", "x\n")
            out = report.scan(root, scope=["services/payments/src"], use_git=False)
        got = {(s["rule"], s["file"]) for s in out["signals"]}
        self.assertNotIn(("no-tests", "services/payments/src"), got)

    def test_tagged_sql_templates_not_concat(self):  # final review 9
        self._none_sql("const r = await prisma.$queryRaw`SELECT * FROM users WHERE id = ${id}`;\n")
        self._none_sql("const r = await db.query(sql`SELECT * FROM users WHERE id = ${id}`);\n")
        self._none_sql("const r = await db.query(SQL`DELETE FROM users WHERE id = ${id}`);\n")
        self._none_sql("const r = await prisma.$queryRaw`\n  SELECT * FROM users\n"
                       "  WHERE id = ${id}\n`;\n")
        self.assertIn("sql-string-concat",
                      _rules({"a/b/c.ts": "const t = `x`;\nq(`DELETE FROM t WHERE id=${i}`);\n"}))

    def _none_sql(self, src):
        self.assertNotIn("sql-string-concat", _rules({"a/b/c.ts": src}))

    def test_secret_in_test_file_is_low_confidence(self):  # final review 21; baseline secret 1
        # Test files: key = value fixtures are not reported, provider formats are (low).
        key = "AKIA" + "Q" * 16
        conf = {s.file: s.confidence for s in _run({"a/b/test_c.py": 'password = "hunter2-real-pw"\n'
                                                                       'k = "%s"\n' % key,
                                                     "a/b/c.py": 'password = "hunter2-real-pw"\n'})
                if s.rule == "hardcoded-secret"}
        self.assertEqual(conf, {"a/b/test_c.py": "low", "a/b/c.py": "high"})

    def test_secrets_in_config_files(self):  # final review 8
        flagged = {
            "svc/.env": "DB_PASSWORD=hunter2-real-pw\n",
            "svc/.env.production": "export API_KEY=hunter2-real-key\n",
            "svc/config.yml": "db:\n  password: hunter2-real-pw\n",
            "svc/config.json": '{"auth": {"token": "hunter2-real-token"}}\n',
            "svc/app.toml": 'secret = "hunter2-real-secret"\n',
            "svc/app.ini": "[db]\npassword = hunter2-real-pw\n",
            "svc/app.properties": "db.password=hunter2-real-pw\n",
        }
        for rel, text in flagged.items():
            self.assertEqual(_rules({rel: text}), ["hardcoded-secret"], rel)
        sig = _run({"svc/.env": "DB_PASSWORD=hunter2-real-pw\n"})[0]
        self.assertEqual((sig.file, sig.line, sig.confidence), ("svc/.env", 1, "high"))
        self.assertNotIn("hunter2", sig.snippet)

    def test_config_secret_negatives(self):  # final review 8
        quiet = {
            "svc/.env.example": "DB_PASSWORD=hunter2-real-pw\n",
            "svc/.env.sample": "DB_PASSWORD=hunter2-real-pw\n",
            "svc/.env.template": "DB_PASSWORD=hunter2-real-pw\n",
            "svc/package-lock.json": '{"token": "hunter2-real-token"}\n',
            "svc/pnpm-lock.yaml": "token: hunter2-real-token\n",
            "svc/.env": "DB_PASSWORD=${DB_PASS}\nAPI_KEY=changeme-please\nTOKEN=\n# SECRET=hunter2-real-pw\n",
            "svc/config.yml": "password: Password must be long\nmax_token_length: 100000000\n",
            "node_modules/x/config.json": '{"token": "hunter2-real-token"}\n',
            "svc/q.yml": "query: SELECT * FROM t WHERE id=${x}\n",
        }
        for rel, text in quiet.items():
            self.assertEqual(_rules({rel: text}), [], rel)
        self.assertNotIn("hardcoded-secret", _rules({"a/b/c.py": "token = some_function_value\n"}))

    def test_config_secret_descriptor_suffixes_and_values(self):  # polish 1
        for rel, text in {
            "svc/config.yml": "tokenUrl: https://auth.corp.io/oauth/token\n",
            "svc/c2.yml": "password_file: /run/secrets/db_password\n",
            "svc/c3.yml": "secretName: my-app-credentials\n",
            "svc/c4.yml": "api-key-header: X-Api-Key-Value\n",
            "svc/c5.yml": "token_endpoint: oauth-token-endpoint\n",
            "svc/c6.yml": "secret_path: C:\\secrets\\key.txt\n",
            "svc/c7.yml": "password: https://vault.corp.io/db\n",
            "svc/c8.json": '{"tokenUrl": "https://auth.corp.io/oauth/token"}\n',
        }.items():
            self.assertEqual(_rules({rel: text}), [], rel)
        for rel, text in {
            "svc/.env": "DB_PASSWORD=s3cr3tValue99\n",
            "svc/c.yml": 'apiKey: "abcd1234efgh5678"\n',
            "svc/c.toml": 'stripe_secret_key = "longvalue123456"\n',
            "svc/c2.yml": "stripeApiKey: s3cr3tValue99\n",
        }.items():
            self.assertEqual(_rules({rel: text}), ["hardcoded-secret"], rel)

    def test_secret_word_only_allows_key_suffix(self):  # polish fix round 1
        for rel, text in {
            "a/b/c.py": 'tokenizer = "bert-base-uncased"\n',
            "a/b/d.py": 'max_tokens = "unlimited-tokens"\n',
            "a/b/e.py": 'password_hash = "pbkdf2:sha256:abcdefgh"\n',
            "a/b/f.py": 'password_reset_template = "reset_password.html"\n',
            "a/b/g.py": 'secret_manager = "aws-secretsmanager"\n',
            "a/b/h.py": 'token_expiry = "3600seconds"\n',
            "svc/.env": "PASSWORD_HASH=abcdefghijk123\n",
            "svc/c.yml": 'token_expiry: "86400seconds"\ntokens: abcdefghijk123\n',
        }.items():
            self.assertNotIn("hardcoded-secret", _rules({rel: text}), rel)
        for rel, text in {
            "a/b/c.py": 'api_key = "abcd1234efgh5678"\n',
            "a/b/d.py": 'password = "hunter2-prod-catalog"\n',
            "a/b/e.py": 'secret_key = "abcdefgh12345678"\n',
            "a/b/f.py": 'stripe_secret_key = "longvalue123456"\n',
            "a/b/g.js": 'const o = { apiKey: "abcd1234efgh5678" };\n',
        }.items():
            self.assertIn("hardcoded-secret", _rules({rel: text}), rel)

    def test_stray_backtick_in_string_keeps_tagged_pairing(self):  # polish 2
        tagged = "const r = await db.query(sql`SELECT * FROM users WHERE id = ${id}`);\n"
        for head in ('const a = "\\`";\n', "const a = '`';\n", 'const a = "`";\n',
                     "// a ` in a comment\n", "/* ` */\n"):
            self.assertNotIn("sql-string-concat", _rules({"a/b/c.ts": head + tagged}), head)
        self.assertIn("sql-string-concat",
                      _rules({"a/b/c.ts": 'const a = "`";\nq(`DELETE FROM t WHERE id=${i}`);\n'}))



def _sigs(files, rule, scope=None):
    import dfscan.report as report
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        out = report.scan(root, scope=scope, use_git=False)
    return [s for s in out["signals"] if s["rule"] == rule]


class R2FalsePositiveTests(unittest.TestCase):
    """Spec section 5 (review2) regression tests."""

    def test_no_tests_sees_sibling_and_parent_tests(self):  # R2-fp1
        code = "def f():\n    return 1\n"
        # src/pkg with a repo-root tests/ that imports the package -> no signal.
        got = _sigs({"src/pkg/a.py": code, "tests/test_basic.py": "import pkg\n"}, "no-tests")
        self.assertEqual(got, [])
        # tests/<unit>/ mirror and tests/test_<unit>* names also count.
        self.assertEqual(_sigs({"src/middlewares/a.js": "module.exports = 1;\n",
                                "tests/unit/middlewares/a.test.js": "x\n"}, "no-tests"), [])
        self.assertEqual(_sigs({"src/pkg/a.py": code, "tests/test_pkg_api.py": "x = 1\n"},
                               "no-tests"), [])
        # Sibling tests/ folder next to the unit (backend/app + backend/tests) -> no signal.
        self.assertEqual(_sigs({"backend/app/a.py": code, "backend/tests/test_x.py": "x = 1\n"},
                               "no-tests"), [])
        # Same with --scope: the tests live outside the scope.
        self.assertEqual(_sigs({"src/pkg/a.py": code, "tests/test_basic.py": "import pkg\n"},
                               "no-tests", scope=["src"]), [])
        # Only an unrelated top-level test for a depth-2 unit -> still flagged, confidence low.
        got = _sigs({"svc/x/a.py": code, "tests/test_other.py": "x = 1\n"}, "no-tests")
        self.assertEqual([(s["file"], s["confidence"]) for s in got], [("svc/x", "low")])

    def test_commented_pass_low_confidence(self):  # R2-fp4
        src = ("def f(d):\n    try:\n        return d.load()\n    except Exception:\n"
               "        # the file may be missing, which is fine\n        pass\n")
        got = _sigs({"a/b/c.py": src}, "swallowed-error")
        self.assertEqual([(s["confidence"], s["severity_hint"]) for s in got], [("low", "Low")])
        trailing = "try:\n    f()\nexcept Exception:\n    pass  # best effort\n"
        got = _sigs({"a/b/c.py": trailing}, "swallowed-error")
        self.assertEqual([(s["confidence"], s["severity_hint"]) for s in got], [("low", "Low")])
        # Undocumented broad pass keeps Medium severity (confidence is capped at "low"
        # for swallowed-error by measured precision).
        bare = "try:\n    f()\nexcept Exception:\n    pass\n"
        got = _sigs({"a/b/c.py": bare}, "swallowed-error")
        self.assertEqual([(s["confidence"], s["severity_hint"]) for s in got], [("low", "Medium")])
        # ImportError exemption kept, comment or not.
        self.assertEqual(_sigs({"a/b/c.py": "try:\n    import x\nexcept ImportError:\n"
                                            "    # optional\n    pass\n"}, "swallowed-error"), [])


class BaselineFalsePositiveTests(unittest.TestCase):
    """One regression test per false-positive pattern in evals/baseline-findings.md."""

    def _conf(self, files, rule):
        return [s.confidence for s in _run(files) if s.rule == rule]

    # ---- hardcoded-secret
    def test_secret_fixture_in_test_file_not_flagged(self):  # pattern 1
        self.assertNotIn("hardcoded-secret",
                         _rules({"tests/test_login.py": 'data = {"password": "testpass123"}\n'}))
        self.assertNotIn("hardcoded-secret",
                         _rules({"src/auth.test.js": "const password = 'password1-strong';\n"}))
        key = "AKIA" + "Q" * 16
        self.assertEqual(self._conf({"tests/test_k.py": 'k = "%s"\n' % key}, "hardcoded-secret"),
                         ["low"])

    def test_secret_config_in_test_folder_still_reported(self):  # fix round 1 guard
        for rel, text in {"config/test/settings.yaml": "db_password: S3cr3tValue99x\n",
                          "deploy/test/.env": "API_KEY=S3cr3tValue99x\n"}.items():
            self.assertEqual(self._conf({rel: text}, "hardcoded-secret"), ["low"], rel)

    def test_secret_changethis_placeholder(self):  # pattern 2
        self.assertEqual(_rules({"svc/.env": "SECRET_KEY=changethis\nPOSTGRES_PASSWORD=changethis\n"}),
                         [])

    def test_secret_password_hash_not_flagged(self):  # pattern 3
        h = "$2b$10$" + "EpRnTzVlqHNP0.fUbXUwSOyuiXe/QLSUG6xNekdHgTGmrpHEfIoxm"
        self.assertNotIn("hardcoded-secret",
                         _rules({"prisma/seed.ts": "const u = { password: '%s' };\n" % h}))

    def test_secret_regex_template_value_not_flagged(self):  # pattern 4
        src = 'var tpl = `{"token":"([a-zA-Z0-9-_.]{115})"}`\n'
        self.assertNotIn("hardcoded-secret", _rules({"users/routes.go": src}))

    def test_secret_dummy_digits_not_flagged(self):  # pattern 5
        self.assertNotIn("hardcoded-secret",
                         _rules({"packages/auth/cli.ts": 'const opts = { secret: "1234567890" };\n'}))

    # ---- swallowed-error
    def test_swallowed_lookup_miss_exempt(self):  # pattern 1 (EAFP control flow)
        for exc in ("KeyError", "LookupError", "StopIteration", "self.UserModel.DoesNotExist",
                    "(KeyError, LookupError)"):
            src = "try:\n    v = cache[k]\nexcept %s:\n    pass\n" % exc
            self.assertNotIn("swallowed-error", _rules({"a/b/c.py": src}), exc)
        # Can hide real bugs: reported, but low (fix round 1).
        for exc in ("AttributeError", "IndexError", "FileNotFoundError", "(KeyError, IndexError)"):
            src = "try:\n    v = obj.attr[0]\nexcept %s:\n    pass\n" % exc
            self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["low"], exc)

    def test_swallowed_tool_directive_is_not_intent(self):  # fix round 1
        for c in ("# noqa: E722", "# pylint: disable=broad-except", "# type: ignore", "# nosec"):
            src = "try:\n    f()\nexcept Exception:  %s\n    pass\n" % c
            self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["high"], c)
        js = "try { f() } catch (e) { /* eslint-disable-line no-empty */ }\n"
        self.assertEqual(self._conf({"a/b/c.ts": js}, "swallowed-error"), ["high"])
        # A directive followed by an explanation is still documented intent.
        src = "try:\n    f()\nexcept Exception:  # noqa: E722 - best effort cleanup\n    pass\n"
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["low"])

    def test_swallowed_narrow_exception_low(self):  # patterns 1, 3, 4
        for exc in ("ValueError", "UnicodeError", "OSError", "PermissionError",
                    "(TypeError, ValueError)"):
            src = "value = None\ntry:\n    value = parse(x)\nexcept %s:\n    pass\n" % exc
            self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["low"], exc)
        self.assertEqual(self._conf({"a/b/c.py": "try:\n    f()\nexcept:\n    pass\n"},
                                    "swallowed-error"), ["high"])

    def test_swallowed_fallback_assignment_low(self):  # pattern 4 (fallback set before, checked after)
        src = ("ts: int | None = None\ntry:\n    ts = decode(b)\nexcept Exception:\n    pass\n"
               "if ts is not None:\n    use(ts)\n")
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["low"])
        src = "try:\n    ts = decode(b)\nexcept Exception:\n    pass\nreturn ts\n"
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["high"])

    def test_swallowed_try_except_else_low(self):  # pattern 1 (try/except/else)
        src = "try:\n    v = f()\nexcept Exception:\n    pass\nelse:\n    use(v)\n"
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["low"])

    def test_swallowed_commented_js_catch_low(self):  # pattern 2
        src = "try {\n  apply(theme);\n} catch {\n  // localStorage may be unavailable\n}\n"
        self.assertEqual(self._conf({"a/b/c.tsx": src}, "swallowed-error"), ["low"])
        self.assertEqual(self._conf({"a/b/c.ts": "try { f() } catch (e) {}\n"}, "swallowed-error"),
                         ["high"])

    # ---- unbounded-query
    def test_unbounded_single_row_lookup(self):  # patterns 1, 8
        quiet = [
            'cursor.execute("SELECT oid, typarray FROM pg_type WHERE typname = %s", (n,))\n',
            'cursor.execute("SELECT sql FROM sqlite_master WHERE type=\'index\'")\n',
            'cursor.execute("SELECT 1 FROM DUAL")\n',
            'cursor.execute("SELECT view_name FROM user_views")\n',
            'cur.execute("SELECT name FROM t WHERE id = %s", (i,))\n',
            'cur.execute("SELECT name FROM t WHERE name = %s", (n,))\nrow = cur.fetchone()\n',
        ]
        for src in quiet:
            self.assertNotIn("unbounded-query", _rules({"a/b/c.py": src}), src)
        self.assertIn("unbounded-query",
                      _rules({"a/b/c.go": 'db.Query("SELECT id FROM orders WHERE user_id = $1", u)\n'}))

    def test_unbounded_aggregates(self):  # pattern 2
        for agg in ("MAX(id)", "MIN(id)", "SUM(total)", "AVG(total)"):
            src = 'op.execute("SELECT %s FROM orders")\n' % agg
            self.assertNotIn("unbounded-query", _rules({"a/b/c.py": src}), agg)

    def test_unbounded_subquery_in_update(self):  # pattern 3
        src = 'op.execute("UPDATE item SET owner = (SELECT id FROM user WHERE x)")\n'
        self.assertNotIn("unbounded-query", _rules({"a/b/c.py": src}))
        self.assertNotIn("unbounded-query",
                         _rules({"a/b/c.py": 'q = "INSERT INTO b SELECT a FROM t"\n'}))

    def test_unbounded_derived_table_fragment(self):  # pattern 4
        self.assertNotIn("unbounded-query",
                         _rules({"a/b/c.py": 'r = ["SELECT * FROM (", *result, ")"]\n'}))
        self.assertNotIn("unbounded-query",
                         _rules({"a/b/c.py": 'r = RawSQL("SELECT * FROM (%s) subquery" % sql, p)\n'}))

    def test_unbounded_objects_all_chained(self):  # pattern 5
        for tail in (".delete()", ".update(a=1)", ".count()", ".exists()", ".first()"):
            src = "Item.objects.all()%s\n" % tail
            self.assertNotIn("unbounded-query", _rules({"a/b/c.py": src}), tail)

    def test_unbounded_lazy_queryset_stays_low(self):  # patterns 6, 8 (not fixable)
        self.assertEqual(self._conf({"a/b/c.py": "def user_list():\n    return User.objects.all()\n"},
                                    "unbounded-query"), ["low"])

    def test_unbounded_findmany_spread_args(self):  # pattern 7
        self.assertNotIn("unbounded-query",
                         _rules({"a/b/c.ts": "return this.prisma.post.findMany({ ...args, where: w });\n"}))

    def test_unbounded_and_loop_skip_migrations(self):  # unbounded 2/3, query-in-loop 1
        mig = ('def upgrade():\n    op.execute("SELECT id FROM item")\n'
               '    for s in items:\n        op.execute(s)\n')
        for rel in ("backend/app/alembic/versions/d98_edit.py", "app/migrations/0002_x.py"):
            got = _rules({rel: mig})
            self.assertNotIn("unbounded-query", got, rel)
            self.assertNotIn("query-in-loop", got, rel)

    # ---- query-in-loop
    def test_loop_ddl_statement_lists(self):  # pattern 1
        for src in ("for sql in self.deferred_sql:\n    self.execute(sql, None)\n",
                    "for statement in statements:\n    schema_editor.execute(statement, params=None)\n",
                    "for f in fs:\n    self.execute(self._create_unique_sql(model, f))\n",
                    "for sql in sql_list:\n    cursor.execute(sql)\n",
                    "for sql, params in post_actions:\n    self.execute(sql, params)\n",
                    "for template in statements:\n    stmt = template % p\n    cursor.execute(stmt)\n",
                    "for f in fs:\n    self.execute(\n        *self._alter_column_sql(model, f)\n    )\n"):
            self.assertNotIn("query-in-loop", _rules({"a/b/c.py": src}), src)
        self.assertIn("query-in-loop",
                      _rules({"a/b/c.py": "for i in ids:\n    cursor.execute(SQL, (i,))\n"}))

    def test_loop_statement_words_need_identifier_boundaries(self):  # fix round 1 guard
        for src in ("for subscription in subscriptions:\n    cursor.execute(sql, (subscription.id,))\n",
                    "for description in descriptions:\n    cursor.execute(query)\n",
                    "for user in users:\n    cursor.execute(sql_text, (user.id,))\n"):
            self.assertIn("query-in-loop", _rules({"a/b/c.py": src}), src)

    def test_loop_constant_and_attribute_receivers(self):  # pattern 2
        self.assertNotIn("query-in-loop",
                         _rules({"a/b/c.py": "while i:\n    c = SELECT2_TRANSLATIONS.get(k)\n"}))
        self.assertNotIn("query-in-loop",
                         _rules({"a/b/c.py": "for h in hs:\n    v = request.META.get(h)\n"}))
        self.assertIn("query-in-loop", _rules({"a/b/c.py": "for k in ks:\n    User.get(k)\n"}))

    def test_loop_pragma_per_index(self):  # pattern 3
        src = 'for index in idx:\n    cursor.execute("PRAGMA index_info(%s)" % q(index))\n'
        self.assertNotIn("query-in-loop", _rules({"a/b/c.py": src}))
        src = 'for index in idx:\n    cursor.execute(\n        "PRAGMA index_info(%s)" % q(index)\n    )\n'
        self.assertNotIn("query-in-loop", _rules({"a/b/c.py": src}))

    def test_loop_per_item_transaction_low(self):  # pattern 4 (deliberate, documented)
        src = ("for email in emails:\n    with transaction.atomic():\n"
               "        Email.objects.filter(id=email.id).update(status=1)\n")
        self.assertEqual(self._conf({"a/b/c.py": src}, "query-in-loop"), ["low"])

    # ---- sql-string-concat
    def test_sql_concat_quoted_identifier_low(self):  # pattern 1
        src = ('def f(self):\n    k = self.quote_name("cache_key")\n'
               '    return f"SELECT {k} FROM %s ORDER BY {k} LIMIT 1 OFFSET %%s"\n')
        self.assertEqual(self._conf({"a/b/c.py": src}, "sql-string-concat"), ["low"])
        self.assertEqual(self._conf({"a/b/c.py": 'q = "DELETE FROM %s" % quote_name(t) + " WHERE 1"\n'},
                                    "sql-string-concat"), ["low"])
        self.assertEqual(self._conf({"a/b/c.py": 'q = f"SELECT * FROM t WHERE id={i}"\n'},
                                    "sql-string-concat"), ["medium"])

    # ---- no-tests / folder rules
    def test_no_tests_config_docs_migrations_units(self):  # no-tests pattern 3
        files = {"config/settings/base.py": "DEBUG = False\n",
                 "config/django/base.py": "DEBUG = False\n",
                 "src/config/config.js": "module.exports = {};\n",
                 "src/docs/swaggerDef.js": "module.exports = {};\n",
                 "prisma/migrations/0001_init/migration.sql": "CREATE TABLE t (id int);\n",
                 "tooling/tailwind/eslint.config.ts": "export default [];\n",
                 "tooling/tailwind/postcss-config.js": "module.exports = {};\n",
                 "src/app/main.py": "x = 1\n"}
        got = [s.file for s in _run(files) if s.rule == "no-tests"]
        self.assertEqual(got, ["src/app"])

    def test_no_tests_feature_settings_folder_flagged(self):  # fix round 1 guard
        files = {"app/settings/views.py": "def page():\n    return 1\n",
                 "proj/settings/base.py": "INSTALLED_APPS = []\nDATABASES = {}\n"}
        got = [s.file for s in _run(files) if s.rule == "no-tests"]
        self.assertEqual(got, ["app/settings"])

    def test_example_programs_skipped_by_folder_rules(self):  # cross-cutting: examples
        files = {"_examples/hello-world/main.go": 'package main\nfunc main() { fmt.Println("hi") }\n'
                 + "// pad\n" * 120,
                 "examples/rest/app.js": "console.log('x');\n" + "const a = 1;\n" * 120}
        got = {s.rule for s in _run(files)}
        self.assertNotIn("no-tests", got)
        self.assertNotIn("console-logging-only", got)


class FolderCapTests(unittest.TestCase):
    def test_no_tests_beyond_top_15_folders(self):
        import dfscan.report as report
        with tempfile.TemporaryDirectory() as root:
            for i in range(20):
                _write(root, "svc/m%02d/a.ts" % i, "export const a = 1;\n" * (30 - i))
            out = report.scan(root, use_git=False)
        flagged = {s["file"] for s in out["signals"] if s["rule"] == "no-tests"}
        self.assertEqual(len(flagged), 20)
        self.assertLessEqual(len(out["candidate_folders"]), 15)


class CapTests(unittest.TestCase):
    def test_per_rule_cap_and_truncated(self):
        import dfscan.report as report
        body = "".join("fetch(u%d);\n" % i for i in range(205))
        with tempfile.TemporaryDirectory() as root:
            _write(root, "svc/x/a.ts", body)
            out = report.scan(root, use_git=False)
        n = sum(1 for s in out["signals"] if s["rule"] == "http-no-timeout")
        self.assertEqual(n, 200)
        self.assertEqual(out["summary"]["truncated"], {"http-no-timeout": 205})
        self.assertEqual(sum(out["summary"]["signals_by_category"].values()), len(out["signals"]))


# ---------------------------------------------------------------- release review 1.0.2 (area A)
class QuadraticPathTests(unittest.TestCase):  # review M8
    SQL_SAMPLES = [
        "x = 'SELECT a FROM t'", "SELECT SELECT a FROM t; select b from u", "select * from (q)",
        "SELECT a FROM\n  t", "q = 'SELECT a' + 'FROM t'", "SELECT 1; FROM x", "XSELECT a FROM t",
        "Select a From t", "SELECT a FROM FROM t", "SELECT x\nFROM t", "select a from `t` select b from",
    ]

    def test_select_starts_equals_regex(self):
        from dfscan import patterns
        for text in self.SQL_SAMPLES:
            self.assertEqual(patterns._select_starts(text),
                             [m.start() for m in patterns._SELECT_RE.finditer(text)], text)

    def test_sql_words_equals_regex(self):
        from dfscan import patterns
        lines = ["SELECT a FROM t", "FROM a SELECT b", "UPDATE t SET a", "SET a UPDATE",
                 "INSERT INTO t", "DELETE FROM t", "SELECTED FROM", "SELECT x UPDATE y SET z"]
        for ln in lines:
            self.assertEqual(patterns._sql_words(ln), bool(patterns._SQL_WORDS_RE.search(ln)), ln)

    def test_long_select_lines_are_fast(self):
        import time
        from dfscan import patterns
        t0 = time.perf_counter()
        patterns._select_starts("select " * 10000)
        patterns._sql_words("SELECT " * 10000 + "+ y")
        self.assertLess(time.perf_counter() - t0, 2.0)

    def test_many_swallowed_errors_are_fast(self):
        import time
        block = "try:\n    f()\nexcept Exception:\n    pass\n"
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "app", "m.py")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(block * 4000 + "try:\n    x = f()\nexcept ValueError:\n    pass\n"
                         "else:\n    g()\n")
            fs = walk(root)
            t0 = time.perf_counter()
            sigs = [s for s in run_rules(fs, candidate_folders(fs)) if s.rule == "swallowed-error"]
            self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(len(sigs), 4001)
        self.assertEqual(sigs[-1].severity_hint, "Low")  # try/except/else still downgraded
        self.assertEqual(sigs[0].line, 3)


if __name__ == "__main__":
    unittest.main()
