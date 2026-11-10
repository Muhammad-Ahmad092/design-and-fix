from __future__ import annotations

import os
import tempfile
import unittest

from helpers import FIXTURES
from dfscan.model import FileInfo
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
        self.assertIn("unbounded-query",
                      _rules({"a/b/c.py": "def items(request):\n    rows = Item.objects.all()\n"}))

    def test_sql_string_concat(self):  # 1.1.0: a traced request source is required
        self.assertIn("sql-string-concat", _rules({"a/b/c.py": 'def f(request, i):\n'
                                                   '    db.execute(f"SELECT * FROM t WHERE id={i}")\n'}))
        self.assertIn("sql-string-concat", _rules({"a/b/c.ts": "app.get('/', (req, res) => {\n"
                                                   "  db.query(`DELETE FROM t WHERE id=${req.query.i}`);\n"
                                                   "});\n"}))
        self.assertNotIn("sql-string-concat", _rules({"a/b/c.py": 'q = f"SELECT * FROM t WHERE id={i}"\n'}))
        self.assertNotIn("sql-string-concat",
                         _rules({"a/b/c.ts": 'db.query("DELETE FROM t WHERE id=$1", [req.query.i]);\n'}))

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
                      _rules({"a/b/c.ts": "const t = `x`;\n"
                                          "db.query(`DELETE FROM t WHERE id=${req.query.i}`);\n"}))

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
            "svc/.env.production": "export API_KEY=hunter2-real-key9\n",
            "svc/config.yml": "db:\n  password: hunter2-real-pw\n",
            "svc/config.json": '{"auth": {"token": "hunter2-real-tok3n"}}\n',
            "svc/app.toml": 'secret = "hunter2-real-s3cret"\n',
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
            "svc/.env": "DB_PASSWORD=s3cr3tValue99Q\n",
            "svc/c.yml": 'apiKey: "abcd1234efgh5678"\n',
            "svc/c.toml": 'stripe_secret_key = "longvalue123456"\n',
            "svc/c2.yml": "stripeApiKey: s3cr3tValue99Q\n",
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
                      _rules({"a/b/c.ts": 'const a = "`";\n'
                                          'db.query(`DELETE FROM t WHERE id=${req.query.i}`);\n'}))



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

    def test_commented_pass_not_reported(self):  # R2-fp4 (1.1.0: documented intent is dropped)
        src = ("def f(d):\n    try:\n        return d.load()\n    except Exception:\n"
               "        # the file may be missing, which is fine\n        pass\n")
        self.assertEqual(_sigs({"a/b/c.py": src}, "swallowed-error"), [])
        trailing = "try:\n    f()\nexcept Exception:\n    pass  # best effort\n"
        self.assertEqual(_sigs({"a/b/c.py": trailing}, "swallowed-error"), [])
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
        # A directive followed by an explanation is still documented intent (1.1.0: dropped).
        src = "try:\n    f()\nexcept Exception:  # noqa: E722 - best effort cleanup\n    pass\n"
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), [])

    def test_swallowed_narrow_exception_low(self):  # patterns 1, 3, 4
        for exc in ("ValueError", "UnicodeError", "OSError", "PermissionError",
                    "(TypeError, ValueError)"):
            src = "try:\n    parse(x)\nexcept %s:\n    pass\n" % exc
            self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["low"], exc)
        self.assertEqual(self._conf({"a/b/c.py": "try:\n    f()\nexcept:\n    pass\n"},
                                    "swallowed-error"), ["high"])

    def test_swallowed_fallback_assignment_dropped(self):  # pattern 4 (1.1.0: not reported)
        src = ("ts: int | None = None\ntry:\n    ts = decode(b)\nexcept Exception:\n    pass\n"
               "if ts is not None:\n    use(ts)\n")
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), [])
        src = "try:\n    ts = decode(b)\nexcept Exception:\n    pass\nreturn ts\n"
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), ["high"])

    def test_swallowed_try_except_else_dropped(self):  # pattern 1 (1.1.0: not reported)
        src = "try:\n    v = f()\nexcept Exception:\n    pass\nelse:\n    use(v)\n"
        self.assertEqual(self._conf({"a/b/c.py": src}, "swallowed-error"), [])

    def test_swallowed_commented_js_catch_dropped(self):  # pattern 2 (1.1.0: not reported)
        src = "try {\n  apply(theme);\n} catch {\n  // localStorage may be unavailable\n}\n"
        self.assertEqual(self._conf({"a/b/c.tsx": src}, "swallowed-error"), [])
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

    def test_unbounded_lazy_queryset_outside_handler(self):  # patterns 6, 8; 1.1.0 labels
        # A selector returning a lazy QuerySet is paginated by its caller: not flagged; inside a
        # request handler the same read stays a low-confidence lead.
        self.assertEqual(self._conf({"a/b/c.py": "def user_list():\n    return User.objects.all()\n"},
                                    "unbounded-query"), [])
        self.assertEqual(self._conf({"a/b/c.py": "def user_list(request):\n    return User.objects.all()\n"},
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

    def test_loop_per_item_transaction_not_flagged(self):  # pattern 4 (deliberate, documented)
        src = ("for email in emails:\n    with transaction.atomic():\n"
               "        Email.objects.filter(id=email.id).update(status=1)\n")
        self.assertEqual(self._conf({"a/b/c.py": src}, "query-in-loop"), [])

    # ---- sql-string-concat
    def test_sql_concat_quoted_identifier_not_flagged(self):  # pattern 1 (1.1.0: needs a source)
        src = ('def f(self):\n    k = self.quote_name("cache_key")\n'
               '    return f"SELECT {k} FROM %s ORDER BY {k} LIMIT 1 OFFSET %%s"\n')
        self.assertEqual(self._conf({"a/b/c.py": src}, "sql-string-concat"), [])
        src = ('def f(request, t):\n'
               '    db.execute("DELETE FROM %s" % connection.ops.quote_name(t) + " WHERE 1")\n')
        self.assertEqual(self._conf({"a/b/c.py": src}, "sql-string-concat"), [])

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


class QueryRuleReworkTests(unittest.TestCase):
    """1.1.0 rework of query-in-loop and unbounded-query (false positives from evals/labels/after)."""

    def _sigs(self, rel, src, rule):
        return [s for s in _run({rel: src}) if s.rule == rule]

    def _flags(self, rel, src, rule):
        return bool(self._sigs(rel, src, rule))

    # ---- query-in-loop
    def test_n_plus_one_orm_in_loop_flagged(self):
        cases = [
            ("a/b/c.py", "for o in orders:\n    items = o.items.all()\n"),
            ("a/b/c.py", "for i in ids:\n    order = Order.objects.get(id=i)\n"),
            ("a/b/c.py", "for u in users:\n    session.query(Order).filter_by(user_id=u.id).all()\n"),
            ("a/b/c.py", "for u in users:\n    cursor.execute(SQL, (u.id,))\n"),
            ("a/b/c.py", "rows = [Order.objects.get(id=i) for i in ids]\n"),
            ("a/b/c.ts", "for (const id of ids) {\n  await prisma.user.findUnique({ where: { id } });\n}\n"),
            ("a/b/c.ts", "for (const id of ids) {\n  await this.userRepository.findOne({ where: { id } });\n}\n"),
            ("a/b/c.ts", "const tasks = req.body.map(async (id) => {\n  return repository.findById(id);\n});\n"),
            ("a/b/c.ts", "for (const id of ids) {\n  await User.findByPk(id);\n}\n"),
            ("a/b/c.go", "for _, id := range ids {\n\trow := db.QueryRow(\"SELECT name FROM t WHERE id = $1\", id)\n}\n"),
            ("a/b/C.java", "for (Long id : ids) {\n    orderRepository.findById(id);\n}\n"),
            ("a/b/C.cs", "foreach (var id in ids)\n{\n    var o = _context.Orders.Find(id);\n}\n"),
            ("a/b/c.php", "<?php\nforeach ($ids as $id) {\n    $u = User::find($id);\n}\n"),
        ]
        for rel, src in cases:
            self.assertTrue(self._flags(rel, src, "query-in-loop"), src)

    def test_select_related_prefetch_not_flagged(self):
        src = ('orders = Order.objects.prefetch_related("items")\n'
               "for o in orders:\n    n = o.items.all()\n")
        self.assertFalse(self._flags("a/b/c.py", src, "query-in-loop"))
        src = ('orders = Order.objects.select_related("customer").prefetch_related("items")\n'
               "for o in orders:\n    total += o.items.count()\n")
        self.assertFalse(self._flags("a/b/c.py", src, "query-in-loop"))

    def test_bulk_create_and_in_query_not_flagged(self):
        quiet = [
            "objs = []\nfor r in rows:\n    objs.append(Item(name=r))\nItem.objects.bulk_create(objs)\n",
            "for chunk in chunks:\n    Item.objects.bulk_create(chunk)\n",
            "for group in groups:\n    rows = Item.objects.filter(id__in=group)\n",
            'for group in groups:\n    cursor.execute("SELECT a FROM t WHERE id = ANY(%s)", (group,))\n',
            "for batch in batches:\n    cursor.execute(SQL, batch)\n",
            "ids = [o.id for o in orders]\nitems = Item.objects.filter(order_id__in=ids)\n",
        ]
        for src in quiet:
            self.assertFalse(self._flags("a/b/c.py", src, "query-in-loop"), src)
        self.assertFalse(self._flags(
            "a/b/c.ts", "for (const g of groups) {\n  await prisma.user.findMany({ where: { id: { in: g } } });\n}\n",
            "query-in-loop"))

    def test_ddl_loop_over_schema_objects_not_flagged(self):
        quiet = [
            # django spatialite schema editor: DDL per geometry field / geometry table
            ("for field in model._meta.local_fields:\n    if isinstance(field, GeometryField):\n"
             "        self.execute(\n            self.sql_remove_geometry_metadata\n"
             '            % {"table": t, "column": field.column}\n        )\n'),
            ("for geom_table in self.geometry_tables:\n    try:\n        self.execute(\n"
             "            self.sql_discard_geometry_columns % {\"geom_table\": geom_table}\n"
             "        )\n    except DatabaseError:\n        pass\n"),
            "for field in model._meta.local_fields:\n    schema_editor.execute(sql % field.column)\n",
            "for column in columns:\n    connection.execute(self.sql_alter % column)\n",
            # django mysql check_constraints: one integrity query per FK column
            ("for table_name in table_names:\n    relations = get_relations(cursor, table_name)\n"
             "    for column_name, ref in relations.items():\n        cursor.execute(\n"
             '            """\n            SELECT REFERRING.`%s` FROM `%s` as REFERRING\n'
             '            """ % (column_name, table_name)\n        )\n'),
            # DDL literal in a triple-quoted string
            'for t in names:\n    cursor.execute(\n        """\n        CREATE INDEX x ON %s\n        """ % t\n    )\n',
        ]
        for src in quiet:
            self.assertFalse(self._flags("a/b/c.py", src, "query-in-loop"), src)

    def test_introspection_loop_not_flagged(self):
        quiet = [
            ("for line in table_info:\n    has_json = cursor.execute(\n        \"\"\"\n"
             "        SELECT sql\n        FROM sqlite_master\n        WHERE type = 'table'\n"
             "        \"\"\",\n        [t],\n    ).fetchone()\n"),
            ('for row in cursor.fetchall():\n    cursor.execute(\n        "SELECT sql FROM sqlite_master "\n'
             '        "WHERE type=\'index\' AND name=%s" % q(index)\n    )\n'),
            ('for t in names:\n    cursor.execute("SELECT column_name FROM information_schema.columns '
             'WHERE table_name = %s", [t])\n'),
        ]
        for src in quiet:
            self.assertFalse(self._flags("a/b/c.py", src, "query-in-loop"), src)

    def test_small_fixed_loops_not_flagged(self):
        quiet = [
            ("a/b/c.py", "for i in range(3):\n    cursor.execute(SQL, (i,))\n"),
            ("a/b/c.py", 'for name in ("admin", "staff"):\n    Group.objects.get_or_create(name=name)\n'),
            ("a/b/C.java", "for (int i = 0; i < 5; i++) {\n    stmt.execute();\n}\n"),
            ("a/b/c.ts", "['a', 'b'].forEach((n) => db.query(n));\n"),
            ("a/b/c.go", "for i := 0; i < 3; i++ {\n\tdb.Exec(q, i)\n}\n"),
        ]
        for rel, src in quiet:
            self.assertFalse(self._flags(rel, src, "query-in-loop"), src)
        self.assertTrue(self._flags("a/b/c.py", "for i in range(n):\n    cursor.execute(SQL, (i,))\n",
                                    "query-in-loop"))

    def test_non_db_call_named_get_not_flagged(self):
        quiet = [
            ("a/b/c.py", "for k in keys:\n    v = d.get(k)\n"),
            ("a/b/c.py", "for u in urls:\n    r = requests.get(u, timeout=3)\n"),
            ("a/b/c.py", "for u in urls:\n    r = session.get(u, timeout=3)\n"),
            ("a/b/c.py", "for k in keys:\n    v = os.environ.get(k)\n"),
            ("a/b/c.ts", "for (const k of ks) {\n  m.get(k);\n}\n"),
            ("a/b/c.ts", "for (const k of ks) {\n  Config.get(k);\n}\n"),
            ("a/b/C.java", "for (String p : paths) {\n    Files.lines(Paths.get(p)).findFirst().get();\n}\n"),
            ("a/b/c.py", "for item in items:\n    self.execute(item)\n"),
        ]
        for rel, src in quiet:
            self.assertFalse(self._flags(rel, src, "query-in-loop"), src)

    def test_corpus_lookalikes_not_flagged(self):  # 1.1.0 corpus re-run
        quiet = [
            # dict methods on the loop variable, private caches
            ("a/b/c.py", "for message in messages:\n    for e in message.error_dict.values():\n        pass\n"),
            ("a/b/c.py", "for base in bases:\n    f = base.__dict__.get(name)\n"),
            ("a/b/c.py", "for obj in objs:\n    v = obj._prefetched_objects_cache.get(n)\n"),
            ("a/b/c.py", "for opt in opts:\n    vals = opt.choices.values()\n"),
            # one query per unique check: bounded by the model's constraints
            ("a/b/c.py", "for model_class, check in unique_checks:\n"
                         "    qs = model_class._default_manager.filter(**lookup)\n"),
            # collection literal defined in code (management command data)
            ("a/b/c.py", "def handle(self):\n    tasks = []\n    tz = get_tz()\n    for t in tasks:\n"
                         "        Schedule.objects.create(timezone=tz, **t)\n"),
            # Go sync.Pool
            ("a/b/c.go", "for _, w := range writers {\n\tenc := pool.Get().(Encoder)\n}\n"),
            # Java Optional.map runs once
            ("a/b/C.java", "return articleRepository\n    .findBySlug(slug)\n    .map(\n"
                           "        article -> {\n          articleRepository.remove(article);\n"
                           "          return ok();\n        })\n    .orElseThrow(NotFound::new);\n"),
        ]
        for rel, src in quiet:
            self.assertFalse(self._flags(rel, src, "query-in-loop"), src)
        # the same names still count when the data really comes from the loop
        self.assertTrue(self._flags("a/b/c.py", "def handle(self, rows):\n    tasks = []\n"
                                    "    for r in rows:\n        tasks.append(r)\n    for t in tasks:\n"
                                    "        Schedule.objects.create(**t)\n", "query-in-loop"))
        self.assertTrue(self._flags("a/b/C.java", "ids.stream()\n    .map(id -> orderRepository.findById(id))\n"
                                    "    .collect(toList());\n", "query-in-loop"))

    def test_docstrings_after_the_first_are_blanked(self):
        src = ('"""Module."""\n\n\nclass A:\n    """Class doc."""\n\n    def f(self):\n'
               '        """\n        SELECT * FROM "foo" WHERE somecond\n        """\n'
               '        for o in orders:\n            pass\n\n\nclass B:\n    """\n'
               '    Example:\n        for o in orders:\n            Order.objects.get(id=o.id)\n    """\n')
        self.assertFalse(self._flags("a/b/c.py", src, "unbounded-query"))
        self.assertFalse(self._flags("a/b/c.py", src, "query-in-loop"))

    def test_snippet_names_call_and_loop(self):
        sig = self._sigs("a/b/c.py", "x = 1\nfor o in orders:\n    c = Customer.objects.get(id=o.cid)\n",
                         "query-in-loop")[0]
        self.assertEqual(sig.line, 3)
        self.assertRegex(sig.snippet, r"inside (for|while|map) .*line \d+")
        self.assertEqual(sig.snippet, "Customer.objects.get inside for loop at line 2")
        sig = self._sigs("a/b/c.ts", "ids.map(async (i) => {\n  await db.query(q, [i]);\n});\n",
                         "query-in-loop")[0]
        self.assertEqual(sig.snippet, "db.query inside map callback at line 1")

    def test_nested_loop_reports_innermost_data_loop(self):
        src = "for u in users:\n    for attempt in range(3):\n        cursor.execute(SQL, (u.id,))\n"
        sig = self._sigs("a/b/c.py", src, "query-in-loop")[0]
        self.assertEqual(sig.snippet, "cursor.execute inside for loop at line 1")

    # ---- unbounded-query
    def test_unbounded_select_in_docstring_not_flagged(self):
        quiet = [
            ('def f():\n    """\n    Example:\n        SELECT * FROM "foo" WHERE somecond\n    """\n'
             '    return 1\n'),
            ('class Q:\n    def setup(self):\n        """\n        Compute the columns to select from the\n'
             '        database and also by the select_related() call.\n        """\n'),
            '"""Module doc: SELECT name FROM users."""\nx = 1\n',
        ]
        for src in quiet:
            self.assertFalse(self._flags("a/b/c.py", src, "unbounded-query"), src)
        loud = 'def f(cursor):\n    cursor.execute(\n        """\n        SELECT a FROM t\n        """\n    )\n'
        self.assertTrue(self._flags("a/b/c.py", loud, "unbounded-query"))

    def test_introspection_query_not_flagged(self):
        quiet = [
            'cursor.execute("SELECT table_name FROM information_schema.tables")\n',
            'cursor.execute("SELECT relname FROM pg_catalog.pg_class")\n',
            'cursor.execute("SELECT name FROM sqlite_master")\n',
            ('cursor.execute(\n    """\n    SELECT t.srid FROM (\n        SELECT * FROM geometry_columns\n'
             '        UNION ALL\n        SELECT * FROM geography_columns\n    ) AS t WHERE t.f = %s\n    """\n)\n'),
            ('query = """\nWITH tables AS (\n    SELECT %s name\n    UNION\n    SELECT sqlite_master.name\n'
             '    FROM sqlite_master\n    JOIN tables ON (sql REGEXP %s)\n) SELECT name FROM tables;\n"""\n'),
            # table name supplied at runtime: generic backend / library code
            'cursor.execute("SELECT REFERRING.`%s` FROM `%s` as REFERRING" % (c, t))\n',
            'qparts = [sql.SQL("SELECT * FROM "), name, sql.SQL("(")]\n',
            'q = f"SELECT * FROM {table}"\n',
        ]
        for src in quiet:
            self.assertFalse(self._flags("a/b/c.py", src, "unbounded-query"), src)
        self.assertFalse(self._flags("a/b/c.ts", "const q = `SELECT * FROM ${table}`;\n", "unbounded-query"))

    def test_collection_endpoint_without_limit_flagged(self):
        view = ("def item_list(request):\n    items = Item.objects.all()\n"
                "    return JsonResponse({\"items\": list(items.values())})\n")
        self.assertTrue(self._flags("shop/views.py", view, "unbounded-query"))
        flask = ('from flask import Flask\napp = Flask(__name__)\n\n@app.route("/items")\ndef items():\n'
                 '    return jsonify([i.name for i in Item.objects.all()])\n')
        self.assertTrue(self._flags("shop/app.py", flask, "unbounded-query"))
        express = ('router.get("/products", async (req, res) => {\n'
                   "  res.json(await collection.find({}).toArray());\n});\n")
        self.assertTrue(self._flags("shop/server.js", express, "unbounded-query"))
        go = ('func list(w http.ResponseWriter, r *http.Request) {\n'
              '\trows, _ := db.Query("SELECT title FROM blog")\n}\n')
        self.assertTrue(self._flags("shop/main.go", go, "unbounded-query"))
        multiline = ("class ItemView(View):\n    def get(\n        self,\n        request,\n    ):\n"
                     "        items = Item.objects.all()\n        return render(request, 'x.html', {'items': items})\n"
                     "\n    def helper(self):\n        return Item.objects.all()\n")
        self.assertEqual([s.line for s in self._sigs("shop/views.py", multiline, "unbounded-query")], [6])
        # small lookup table read in a helper (no request, no routes in the file): not an endpoint
        helper = ("class ModelBackend:\n    def _get_permissions(self, user_obj):\n"
                  "        perms = Permission.objects.all()\n        return perms\n")
        self.assertFalse(self._flags("auth/backends.py", helper, "unbounded-query"))

class QueryRuleFixRound1Tests(unittest.TestCase):
    """Task 6 review fixes: DRF list views, transactions, cached paths, docstrings, linear time."""

    def _sigs(self, files, rule):
        return [s for s in _run(files) if s.rule == rule]

    # ---- unbounded-query: DRF / class-based list views
    VIEWSET = ("from rest_framework import viewsets\n\n\nclass ItemViewSet(viewsets.ModelViewSet):\n"
               "    queryset = Item.objects.all()\n    serializer_class = ItemSerializer\n")

    def test_drf_viewset_queryset_flagged(self):
        sigs = self._sigs({"shop/api.py": self.VIEWSET}, "unbounded-query")
        self.assertEqual([s.line for s in sigs], [5])
        lv = ("class ItemList(ListView):\n    model = Item\n\n    def get_queryset(self):\n"
              "        return Item.objects.all()\n")
        self.assertEqual(len(self._sigs({"shop/views.py": lv}, "unbounded-query")), 1)

    def test_drf_viewset_with_pagination_not_flagged(self):
        paged = self.VIEWSET + "    pagination_class = PageNumberPagination\n"
        self.assertEqual(self._sigs({"shop/api.py": paged}, "unbounded-query"), [])
        for opt_out in ("    pagination_class = None\n", "    paginate_by = None\n"):
            self.assertEqual(len(self._sigs({"shop/api.py": self.VIEWSET + opt_out},
                                            "unbounded-query")), 1, opt_out)
        settings = ('REST_FRAMEWORK = {\n    "DEFAULT_PAGINATION_CLASS": '
                    '"rest_framework.pagination.LimitOffsetPagination",\n}\n')
        self.assertEqual(self._sigs({"shop/api.py": self.VIEWSET, "proj/settings.py": settings},
                                    "unbounded-query"), [])
        off = 'REST_FRAMEWORK = {\n    "DEFAULT_PAGINATION_CLASS": None,\n}\n'
        self.assertEqual(len(self._sigs({"shop/api.py": self.VIEWSET, "proj/settings.py": off},
                                        "unbounded-query")), 1)
        plain = "class Repo(Base):\n    queryset = Item.objects.all()\n"
        self.assertEqual(self._sigs({"shop/repo.py": plain}, "unbounded-query"), [])

    def test_go_query_row_same_line_not_unbounded(self):
        src = 'row := db.QueryRow("SELECT name FROM users WHERE email = $1", e)\n'
        self.assertEqual(self._sigs({"a/b/c.go": src}, "unbounded-query"), [])

    def test_dict_value_sql_is_not_a_docstring(self):
        for src in ('QUERIES = {"list": """\nSELECT a FROM t\n"""}\n',
                    'QUERIES = {\n    "list":\n        """SELECT a FROM t"""\n}\n'):
            self.assertEqual(len(self._sigs({"a/b/c.py": src}, "unbounded-query")), 1, src)

    def test_long_line_of_selects_is_linear(self):
        import time
        from dfscan import patterns
        from dfscan.model import FileInfo
        # built directly: the walker skips minified-looking files with very long lines
        for unit, expected in (("SELECT a FROM t ", 1), ("SELECT a FROM t LIMIT 1 ", 0),
                               ("SELECT a FROM `%s` ", 0)):
            text = 'q = "' + unit * 2000 + '"\n'
            fi = FileInfo("a/b/c.py", "/nonexistent/a/b/c.py", ".py", "Python", 1, len(text), False)
            t0 = time.perf_counter()
            sigs = patterns.rule_unbounded_query(fi, strip_comments(text, "Python"))
            self.assertLess(time.perf_counter() - t0, 3.0, unit)
            self.assertEqual(len(sigs), expected, unit)  # one signal per line at most

    # ---- query-in-loop: transactions and caches lower confidence instead of hiding the call
    def test_transaction_in_loop_reported_low(self):
        begin = ('for u in users:\n    cur.execute("BEGIN")\n'
                 '    cur.execute("SELECT a FROM t WHERE u = %s", (u,))\n')
        sigs = self._sigs({"a/b/c.py": begin}, "query-in-loop")
        self.assertTrue(sigs)
        self.assertEqual({s.confidence for s in sigs}, {"low"})
        prisma = ("for (const id of ids) {\n  await prisma.$transaction(async (tx) => {\n"
                  "    await tx.order.findMany({ where: { userId: id } });\n  });\n}\n")
        sigs = self._sigs({"a/b/c.ts": prisma}, "query-in-loop")
        self.assertEqual([(s.line, s.confidence) for s in sigs], [(3, "low")])
        two = ("for email in emails:\n    with transaction.atomic():\n"
               "        Email.objects.filter(id=email.id).update(status=1)\n"
               "        Log.objects.create(email=email)\n")
        self.assertEqual({s.confidence for s in self._sigs({"a/b/c.py": two}, "query-in-loop")}, {"low"})

    def test_cached_path_reported_low(self):
        src = ("for k in keys:\n    v = cache.get(k)\n    if v is None:\n"
               "        v = Item.objects.get(key=k)\n")
        sig = self._sigs({"a/b/c.py": src}, "query-in-loop")[0]
        self.assertEqual(sig.confidence, "low")
        self.assertTrue(sig.snippet.endswith(" (cached path)"), sig.snippet)
        memo = ("from functools import lru_cache\n\n\n@lru_cache(maxsize=None)\ndef names(ids):\n"
                "    for i in ids:\n        Item.objects.get(id=i)\n")
        sig = self._sigs({"a/b/c.py": memo}, "query-in-loop")[0]
        self.assertEqual((sig.confidence, sig.snippet),
                         ("low", "Item.objects.get inside for loop at line 6 (cached path)"))

    def test_rebound_literal_is_data(self):
        src = ("def handle(self, rows):\n    tasks = []\n    tasks = load(rows)\n    for t in tasks:\n"
               "        Schedule.objects.create(**t)\n")
        self.assertEqual(len(self._sigs({"a/b/c.py": src}, "query-in-loop")), 1)


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

    def test_sql_words_equals_regex(self):  # 1.1.0: moved to taint.py, case-insensitive
        import re
        from dfscan import taint
        rx = re.compile(r"\bSELECT\b.*\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\b.*\bSET\b"
                        r"|\bDELETE\s+FROM\b|\bMERGE\s+INTO\b", re.I | re.S)
        lines = ["SELECT a FROM t", "FROM a SELECT b", "UPDATE t SET a", "SET a UPDATE",
                 "INSERT INTO t", "DELETE FROM t", "SELECTED FROM", "SELECT x UPDATE y SET z",
                 "select a from t", "Update t\nset a"]
        for ln in lines:
            self.assertEqual(taint.sql_words(ln), bool(rx.search(ln)), ln)

    def test_long_select_lines_are_fast(self):
        import time
        from dfscan import patterns
        t0 = time.perf_counter()
        patterns._select_starts("select " * 10000)
        from dfscan import taint
        taint.sql_words("SELECT " * 10000 + "+ y")
        self.assertLess(time.perf_counter() - t0, 2.0)

    def test_query_rules_linear_on_long_lines(self):  # 1.1.0 query-in-loop / unbounded-query
        import time
        from dfscan import patterns
        bodies = {
            "a/b/c.py": "x = [" + " for i" * 20000 + " in y]\n" + "for o in orders:\n    "
                        + "a" * 50000 + ".objects.get(\n" + "Item.objects.all()\n" * 2000,
            "a/b/c.ts": "ids.map((i) => " + "x" * 50000 + "Repository.findOne(i) $" + "b" * 50000 + ");\n",
            "a/b/c.php": "<?php\nforeach ($a as $b) {\n  $" + "c" * 50000 + "->query(\n}\n",
        }
        with tempfile.TemporaryDirectory() as root:
            for rel, src in bodies.items():
                _write(root, rel, src)
            fs = walk(root)
            t0 = time.perf_counter()
            for fi in fs:
                text = strip_comments(open(fi.abs, encoding="utf-8").read(), fi.lang)
                patterns.rule_query_in_loop(fi, text)
                patterns.rule_unbounded_query(fi, text)
            self.assertLess(time.perf_counter() - t0, 5.0)

    def test_many_swallowed_errors_are_fast(self):
        import time
        block = "try:\n    f()\nexcept Exception:\n    pass\n"
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "app", "m.py")
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(block * 4000 + "try:\n    x = f()\nexcept ValueError:\n    pass\n"
                         "else:\n    g()\n" + "try:\n    g()\nexcept ValueError:\n    pass\n")
            fs = walk(root)
            t0 = time.perf_counter()
            sigs = [s for s in run_rules(fs, candidate_folders(fs)) if s.rule == "swallowed-error"]
            self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(len(sigs), 4001)
        self.assertEqual(sigs[-1].severity_hint, "Low")  # else-idiom skipped; a narrow class is Low
        self.assertEqual(sigs[0].line, 3)


# ---------------------------------------------------------------- 1.1.0 sql-string-concat (Task 7)
class SqlSourceToSinkTests(unittest.TestCase):
    """sql-string-concat reports only a traced request/CLI source reaching an SQL sink."""

    def _sql(self, rel, src):
        return [s for s in _run({rel: src}) if s.rule == "sql-string-concat"]

    def test_request_param_concatenated_into_execute_flagged(self):
        src = ("from flask import request\n\ndef search():\n"
               "    name = request.args.get(\"name\")\n"
               "    cur.execute(\"SELECT * FROM users WHERE name = '\" + name + \"'\")\n")
        sigs = self._sql("app/views.py", src)
        self.assertEqual(len(sigs), 1)
        sig = sigs[0]
        self.assertEqual((sig.line, sig.confidence, sig.uncertain_reason), (5, "medium", ""))
        self.assertEqual(sig.snippet, "name from line 4 reaches SQL at line 5")

    def test_parameterised_query_not_flagged(self):
        src = ("def search():\n    name = request.args.get(\"name\")\n"
               "    cursor.execute(\"SELECT * FROM users WHERE name = %s\", (name,))\n")
        self.assertEqual(self._sql("app/views.py", src), [])

    def test_orm_filter_not_flagged(self):
        src = ("def search(request):\n    name = request.GET[\"name\"]\n"
               "    return User.objects.filter(name=name).exclude(email__startswith=name)\n")
        self.assertEqual(self._sql("app/views.py", src), [])

    def test_constant_identifier_interpolation_not_flagged(self):
        src = ("TABLE = \"users\"\n\n\ndef rows(request):\n"
               "    cursor.execute(f\"SELECT * FROM {TABLE} WHERE id = %s\", (request.GET[\"id\"],))\n")
        self.assertEqual(self._sql("app/views.py", src), [])

    def test_dynamic_identifier_from_request_flagged_with_reason(self):
        src = ("def rows():\n    table = request.args[\"table\"]\n"
               "    cursor.execute(f\"SELECT * FROM {table}\")\n")
        sigs = self._sql("app/views.py", src)
        self.assertEqual(len(sigs), 1)
        sig = sigs[0].to_dict()
        self.assertEqual(sig["confidence"], "low")
        self.assertIn("identifier", sig["uncertain_reason"])
        self.assertEqual(sig["snippet"], "table from line 2 reaches SQL at line 3")

    def test_validated_int_input_not_flagged(self):
        src = ("def item():\n    item_id = int(request.args[\"id\"])\n"
               "    cursor.execute(\"SELECT * FROM items WHERE id = %d\" % item_id)\n"
               "    cursor.execute(\"SELECT * FROM items WHERE id = \" + str(int(request.args[\"id\"])))\n")
        self.assertEqual(self._sql("app/views.py", src), [])

    def test_concatenation_without_source_not_flagged(self):
        for rel, src in {
            "a/b.py": 'def f(i):\n    db.execute("SELECT * FROM t WHERE id=" + str(i))\n',
            "a/b.ts": "function f(i) {\n  db.query(`DELETE FROM t WHERE id=${i}`);\n}\n",
            "a/b.go": 'func f(id string) {\n\tdb.Query("SELECT * FROM t WHERE id = " + id)\n}\n',
            "a/B.java": 'void f(String id) {\n    jdbc.query("SELECT * FROM t WHERE id = " + id);\n}\n',
        }.items():
            self.assertEqual(self._sql(rel, src), [], rel)

    def test_label_django_quote_name_constant_not_flagged(self):  # evals/labels/after fp 1
        src = ("class BaseDatabaseOperations:\n    def cache_key_culling_sql(self):\n"
               "        cache_key = self.quote_name(\"cache_key\")\n"
               "        return f\"SELECT {cache_key} FROM %s ORDER BY {cache_key} LIMIT 1 OFFSET %%s\"\n")
        self.assertEqual(self._sql("django/db/backends/base/operations.py", src), [])

    def test_label_rails_migration_datetime_now_not_flagged(self):  # evals/labels/after fp 2
        src = ("class AddConfirmableToDevise < ActiveRecord::Migration[8.0]\n  def up\n"
               "    execute <<~SQL\n      UPDATE users SET confirmed_at = '#{DateTime.now}'\n    SQL\n"
               "    execute(\"UPDATE users SET confirmed_at = '#{DateTime.now}'\")\n  end\nend\n")
        self.assertEqual(self._sql("db/migrate/20250630092055_add_confirmable_to_devise.rb", src), [])

    def test_languages_covered(self):
        flagged = {
            "src/a.js": ("app.post('/o', (req, res) => {\n"
                         "  db.query(\"DELETE FROM o WHERE id = \" + req.body.id);\n});\n"),
            "src/b.go": ("func h(w http.ResponseWriter, r *http.Request) {\n"
                         "\tdb.Exec(\"DELETE FROM o WHERE id = '\" + r.FormValue(\"id\") + \"'\")\n}\n"),
            "src/C.java": ("public void d(@RequestParam String id) {\n"
                           "    jdbc.update(\"DELETE FROM o WHERE id = '\" + id + \"'\");\n}\n"),
            "src/D.kt": ("fun d(@PathVariable id: String) {\n"
                         "    jdbc.update(\"DELETE FROM o WHERE id = '$id'\")\n}\n"),
            "src/E.cs": ("public void D([FromRoute] string id)\n{\n"
                         "    _db.Execute($\"DELETE FROM o WHERE id = '{id}'\");\n}\n"),
            "src/f.php": "<?php\n$r = DB::select(\"SELECT * FROM o WHERE id = \" . $request->input('id'));\n",
            "lib/g.rb": "def d\n  Order.find_by_sql(\"SELECT * FROM o WHERE id = '#{params[:id]}'\")\nend\n",
        }
        got = {s.file for s in _run(flagged) if s.rule == "sql-string-concat"}
        self.assertEqual(got, set(flagged))

    def test_long_line_is_linear(self):
        import time
        from dfscan import patterns
        src = ("def f(request, a):\n    q = \"SELECT * FROM t WHERE a = '\"" + " + a" * 40000 + "\n"
               "    cursor.execute(q" + ", (a)" * 40000 + ")\n" + "    x = '" + "\\'" * 60000 + "\n")
        fi = FileInfo("app/v.py", "app/v.py", ".py", "Python", 4, len(src), False)
        t0 = time.perf_counter()
        sigs = patterns.rule_sql_string_concat(fi, strip_comments(src, fi.lang))
        self.assertLess(time.perf_counter() - t0, 3.0)
        self.assertEqual([s.line for s in sigs], [3])



# ---------------------------------------------------------------- 1.1.0 hardcoded-secret (Task 8)
def _rand(n, seed, alphabet="abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"):
    """A random-looking secret built at runtime (no secret literal is committed)."""
    import random
    rng = random.Random(seed)
    return "".join(rng.choice(alphabet) for _ in range(n))


def _secret_sigs(files):
    return [s for s in _run(files) if s.rule == "hardcoded-secret"]


class SecretPrecisionTests(unittest.TestCase):
    """1.0.2 measured 10% precision; one negative test per labelled false-positive pattern."""

    def test_known_provider_format_flagged_high_entropy_not_needed(self):
        tokens = {
            "stripe": "sk_" + "live_" + "x" * 24,
            "github": "gh" + "p_" + "a" * 36,
            "google": "AI" + "za" + "b" * 35,
            "slack": "xo" + "xb-" + "1" * 10 + "-" + "c" * 12,
            "gitlab": "gl" + "pat-" + "d" * 20,
            "aws": "AK" + "IA" + "Q" * 16,
        }
        for name, tok in tokens.items():
            sigs = _secret_sigs({"svc/k.js": 'const k = "%s";\n' % tok})
            self.assertEqual([(s.line, s.severity_hint) for s in sigs], [(1, "Critical")], name)
            self.assertNotIn(tok, sigs[0].snippet, name)
        # A known format under a secret key is reported once (as the known format), not twice.
        sigs = _secret_sigs({"svc/k.py": 'api_key = "%s"\n' % tokens["stripe"]})
        self.assertEqual([s.severity_hint for s in sigs], ["Critical"])
        sigs = _secret_sigs({"svc/.env": "STRIPE_KEY=%s\nAPI_KEY=%s\n" % (tokens["stripe"], tokens["github"])})
        self.assertEqual([s.severity_hint for s in sigs], ["Critical", "Critical"])

    def test_placeholders_not_flagged(self):
        tail = _rand(12, 1)
        for value in ("YOUR_API_KEY", "your-api-key-" + tail, "changeme-" + tail,
                      "example-" + tail, "xxx" + tail, "<secret-%s>" % tail, "${VAR_%s}" % tail,
                      "dummy-" + tail, "placeholder-" + tail, "sample-" + tail, "fake-" + tail,
                      "{{secret_%s}}" % tail, "replace-me-" + tail, "redacted-" + tail):
            self.assertEqual(_secret_sigs({"a/b/c.py": 'api_key = "%s"\n' % value}), [], value)
            self.assertEqual(_secret_sigs({"svc/c.yml": "api_key: %s\n" % value}), [], value)

    def test_i18n_password_label_not_flagged(self):
        files = {
            "config/locales/en.yml": 'en:\n  user:\n    password: "Password"\n',
            "config/locales/views.en.yml": "en:\n  password: Password\n  secret_key: Secret Key\n",
            "src/i18n/en.json": '{"login": {"password": "Password", "token": "Access token"}}\n',
            "src/strings.ts": 'export const labels = { password: "Password", apiKey: "API key" };\n',
        }
        self.assertEqual(_secret_sigs(files), [])

    def test_validation_message_not_flagged(self):
        files = {
            "src/v.ts": 'const m = { password: "password must be 8 characters" };\n',
            "app/Http/Requests/RegisterUser.php":
                "<?php\nreturn [\n    'password' => 'required|string|min:8|confirmed',\n"
                "    'password_confirmation' => 'required|min:6',\n];\n",
            "app/Http/Requests/LoginUser.php":
                "<?php\nreturn ['password' => 'required|string|email|max:255'];\n",
            "src/rules.js": "const rules = { password: 'required|min:8|max:64' };\n",
        }
        self.assertEqual(_secret_sigs(files), [])

    def test_openapi_and_docstring_example_not_flagged(self):
        jwt_like = "ey" + "J" + _rand(30, 2) + "." + _rand(40, 3) + "." + _rand(20, 4)
        aws = "AK" + "IA" + _rand(16, 5, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
        files = {
            "src/docs/components.yml": (
                "components:\n  schemas:\n    AuthTokens:\n      type: object\n"
                "      example:\n        access:\n          token: %s\n"
                "          expires: 2020-05-12T16:18:04.793Z\n" % jwt_like),
            "api/openapi.yaml": ("openapi: 3.0.0\ncomponents:\n  securitySchemes:\n    k:\n"
                                 "      apiKey: %s\n" % _rand(32, 6)),
            "api/swagger.json": ('{"swagger": "2.0", "definitions": {"T": {"token": "%s"}}}\n'
                                 % _rand(32, 7)),
            "app/schemas.py": ('class Token(BaseModel):\n    model_config = {"json_schema_extra": '
                               '{"example": {"token": "%s"}}}\n' % _rand(32, 8)),
            "app/client.py": ('def presign():\n    """Return a presigned post.\n\n'
                              "    >>> token = '%s'\n    password = \"%s\"\n    key id %s\n"
                              '    """\n    return 1\n' % (_rand(24, 9), _rand(24, 10), aws)),
            "lib/tpl.py": "x = 1\n>>> token = '%s'\n" % _rand(24, 11),
            # A bare string statement used as an inline doc block (not the first statement).
            "app/aws.py": ('def presign(c):\n    url = c.generate()\n\n    """\n    Sample response:\n'
                           '    {"fields": {"AWSAccessKeyId": "%s", "password": "%s"}}\n    """\n'
                           '    return url\n' % (aws, _rand(24, 19))),
        }
        # Generic values there are quiet; the AWS key ids (a known format) are reported, low.
        self.assertEqual(sorted((s.file, s.line, s.severity_hint, s.confidence)
                                for s in _secret_sigs(files)),
                         [("app/aws.py", 6, "Critical", "low"), ("app/client.py", 6, "Critical", "low")])
        # A triple-quoted string that is part of an expression is still scanned.
        kept = {"app/cfg.py": 'CFG = (\n    """\n    password = "%s"\n    """\n)\n' % _rand(24, 20),
                "app/k.py": 'KEY = """%s"""\n' % aws}
        self.assertEqual(sorted((s.file, s.line) for s in _secret_sigs(kept)),
                         [("app/cfg.py", 3), ("app/k.py", 1)])

    def test_ci_service_password_not_flagged(self):
        pw = _rand(24, 12)
        wf = ("name: tests\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    services:\n"
              "      postgres:\n        image: postgres:16\n        env:\n"
              "          POSTGRES_PASSWORD: %s\n          MYSQL_ROOT_PASSWORD: %s\n"
              "    steps:\n      - run: make test\n" % (pw, pw))
        self.assertEqual(_secret_sigs({".github/workflows/ci.yml": wf,
                                       ".gitlab-ci.yml": ("test:\n  services:\n    - name: postgres\n"
                                                          "      variables:\n"
                                                          "        POSTGRES_PASSWORD: %s\n" % pw)}),
                         [])
        # Other literal credentials, and DB passwords outside `services:`, are still reported.
        sigs = _secret_sigs({".github/workflows/ci.yml": wf + "env:\n  DEPLOY_TOKEN: %s\n" % pw,
                             ".github/workflows/b.yml": ("jobs:\n  t:\n    env:\n"
                                                         "      DB_PASSWORD: %s\n" % pw),
                             ".gitlab-ci.yml": "variables:\n  POSTGRES_PASSWORD: %s\n" % pw})
        self.assertEqual(sorted((s.file, s.line) for s in sigs),
                         [(".github/workflows/b.yml", 4), (".github/workflows/ci.yml", 15),
                          (".gitlab-ci.yml", 2)])

    def test_entropy_alone_never_high_severity(self):
        files = {"svc/a.py": 'password = "%s"\n' % _rand(40, 13),
                 "svc/.env": "API_KEY=%s\n" % _rand(40, 14),
                 "svc/c.yml": "db:\n  password: %s\n" % _rand(40, 15)}
        sigs = _secret_sigs(files)
        self.assertEqual(sorted(s.file for s in sigs), sorted(files))
        for s in sigs:
            self.assertEqual(s.severity_hint, "Medium", s.file)
        # Weak generic values (short, few character classes, letters only) are not reported.
        low = {"svc/a.py": 'password = "postgres"\n', "svc/b.py": 'secret = "aaaabbbbccccdddd"\n',
               "svc/.env": "DB_PASSWORD=hunter2hunter2\n"}
        self.assertEqual(_secret_sigs(low), [])

    def test_identifier_values_not_flagged(self):
        files = {
            "src/config/tokens.js": ("const tokenTypes = {\n  ACCESS: 'access',\n"
                                     "  RESET_PASSWORD: 'resetPassword',\n"
                                     "  VERIFY_EMAIL_TOKEN: 'verifyEmailTokenType',\n};\n"),
            "django/contrib/auth/views.py": ('INTERNAL_RESET_SESSION_TOKEN = "_password_reset_token"\n'
                                             'reset_url_token = "set-password"\n'),
            "app/models/setting.rb": ("LEGACY = {\n  github_token: \"github_token_env_name\",\n"
                                      "  github_secret: \"GITHUB_CLIENT_SECRET_NAME\"\n}\n"),
            "config/queue.php": "<?php\nreturn ['secret' => 'your-pusher-app-secret'];\n",
            "src/k.py": 'SESSION_TOKEN = "auth.session.token.key"\n',
        }
        self.assertEqual(_secret_sigs(files), [])

    def test_fixture_private_key_file_low_confidence(self):  # 1.0.2 behaviour kept
        pem = ("-----BEGIN " + "PRIVATE KEY-----\n" + _rand(64, 16) + "\n-----END "
               + "PRIVATE KEY-----\n")
        fixtures = {"testdata/key.pem": pem, "test/fixtures/server.key": pem,
                    "spec/fixtures/id_rsa": pem, "src/__fixtures__/k.pem": pem}
        self.assertEqual(sorted((s.file, s.severity_hint, s.confidence) for s in _secret_sigs(fixtures)),
                         sorted((rel, "Critical", "low") for rel in fixtures))
        sigs = _secret_sigs({"certs/server.pem": pem})
        self.assertEqual([(s.file, s.severity_hint, s.confidence) for s in sigs],
                         [("certs/server.pem", "Critical", "high")])

    def test_known_formats_in_docstrings_reported(self):
        aws = "AK" + "IA" + "QWERTYUIOPASDFGH"
        gh = "gh" + "p_" + _rand(36, 40)
        pem = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
        for body in (aws, gh, pem):
            files = {
                "app/mod.py": '"""Module doc.\n\n    %s\n"""\nx = 1\n' % body,
                "app/fn.py": 'def f():\n    """Doc.\n\n    %s\n    """\n    return 1\n' % body,
                "app/cls.py": 'class C:\n    """Doc.\n\n    %s\n    """\n' % body,
            }
            got = sorted((s.file, s.line, s.severity_hint, s.confidence) for s in _secret_sigs(files))
            self.assertEqual(got, [("app/cls.py", 4, "Critical", "low"),
                                   ("app/fn.py", 4, "Critical", "low"),
                                   ("app/mod.py", 3, "Critical", "low")], body[:6])
            for s in _secret_sigs(files):
                self.assertNotIn(body, s.snippet)

    def test_pem_header_variants_in_code(self):
        for kind in ("", "RSA ", "EC ", "DSA ", "OPENSSH ", "ENCRYPTED "):
            src = 'K = """-----BEGIN %sPRIVATE KEY-----\nabc"""\n' % kind
            sigs = _secret_sigs({"svc/k.py": src})
            self.assertEqual([(s.line, s.severity_hint) for s in sigs], [(1, "Critical")], kind)

    def test_short_strong_passwords_fire_and_words_do_not(self):
        for value in ("Summer2024!", "Welcome1!x", "P@ssw0rd123"):
            sigs = _secret_sigs({"svc/a.py": 'password = "%s"\n' % value,
                                 "svc/.env": "DB_PASSWORD=%s\n" % value})
            self.assertEqual(sorted((s.file, s.severity_hint) for s in sigs),
                             [("svc/.env", "Medium"), ("svc/a.py", "Medium")], value)
        # Passphrases and letters-only words are a documented miss / not credentials.
        for value in ("correct-horse-battery-staple", "PasswordMismatch", "Zugriffstoken"):
            self.assertEqual(_secret_sigs({"svc/a.py": 'password = "%s"\n' % value,
                                           "svc/c.yml": "token: %s\n" % value}), [], value)

    def test_labelled_true_positives_still_flagged(self):
        django_key = _rand(50, 17, "abcdefghijklmnopqrstuvwxyz0123456789!@#%^&*(-_=+)")
        jwt_secret = _rand(86, 18, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
        files = {"config/django/base.py": 'DEBUG = False\nSECRET_KEY = "%s"\n' % django_key,
                 "src/main/resources/application.properties":
                     "server.port=8080\njwt.secret=%s\n" % jwt_secret}
        sigs = _secret_sigs(files)
        self.assertEqual(sorted((s.file, s.line, s.severity_hint) for s in sigs),
                         [("config/django/base.py", 2, "Medium"),
                          ("src/main/resources/application.properties", 2, "Medium")])
        for s in sigs:
            self.assertNotIn(django_key, s.snippet)
            self.assertNotIn(jwt_secret, s.snippet)

    def test_new_secret_helpers_linear(self):
        import time
        from dfscan import patterns
        long_values = ["a" * 100000, "a|" * 50000, "a:" * 50000 + "|", "aB" * 50000 + "1",
                       "a_" * 50000 + "1", "sk-" + "a-" * 50000, "gh" + "p_" + "a" * 100000,
                       "xo" + "xb-" + "-" * 100000, "AI" + "za" + "_" * 100000,
                       "sk_" + "live_" + "a" * 100000, "gl" + "pat-" + "-" * 100000]
        t0 = time.perf_counter()
        for v in long_values:
            patterns._generic_value_skipped(v)
            patterns._provider_matches(v)
            patterns._provider_matches(v + "!")
        for src in ('"""' + "a" * 100000, "'" * 100000, '"""a"""\n' * 20000, "x = (\n" * 20000,
                    "\n" * 100000 + '"""'):
            patterns._blank_py_doc_blocks(src)
        list(patterns._PEM_RE.finditer("-----BEGIN " + "A " * 50000))
        self.assertLess(time.perf_counter() - t0, 5.0)
        # Long indents (8,000 spaces): ancestor-key and line-key regexes stay linear.
        pad = " " * 8000
        self.assertIsNone(patterns._EXAMPLE_KEY_RE.match(pad + "x"))
        self.assertIsNone(patterns._SERVICES_KEY_RE.match(pad + "x"))
        self.assertEqual(patterns._LINE_KEY_RE.match(pad + "- " + pad + "!").group(1), "-")
        deep = "".join(" " * (8000 - i) + "k%d:\n" % i for i in range(200))
        deep += " " * 8001 + "POSTGRES_PASSWORD: %s\n" % _rand(24, 41)
        t0 = time.perf_counter()
        for rel in (".github/workflows/ci.yml", "svc/deep.yml"):
            fi = FileInfo(rel, rel, ".yml", None, 1, len(deep), False, is_config=True)
            self.assertEqual(len(patterns.rule_hardcoded_secret(fi, deep)), 1)
        self.assertLess(time.perf_counter() - t0, 5.0)  # 1.6 MB of indent; was minutes
        # Many secret keys under a deep YAML tree: the example-ancestor walk stays bounded.
        values = [_rand(20, i) for i in range(5000)]
        yml = "root:\n" + "".join("  k%d:\n    password: %s\n" % (i, v) for i, v in enumerate(values))
        fi = FileInfo("svc/big.yml", "svc/big.yml", ".yml", None, 1, len(yml), False, is_config=True)
        t0 = time.perf_counter()
        sigs = patterns.rule_hardcoded_secret(fi, yml)
        self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(len(sigs), sum(not patterns._generic_value_skipped(v) for v in values))
        self.assertGreater(len(sigs), 4500)



# ---------------------------------------------------------------- 1.1.0 swallowed-error (Task 9)
def _swallowed(rel, src):
    """Raw (uncapped) swallowed-error signals as (line, severity, confidence)."""
    return [(s.line, s.severity_hint, s.confidence) for s in _run({rel: src})
            if s.rule == "swallowed-error"]


class SwallowedErrorIntentTests(unittest.TestCase):
    """Intentional handling (logged, re-raised, documented, idiomatic fallback) is not reported;
    an empty handler that hides a failure is."""

    def test_logged_or_reraised_handler_not_flagged(self):
        cases = {
            "a/b/c.py": "try:\n    f()\nexcept Exception as exc:\n    logger.exception(exc)\n",
            "a/b/d.py": "try:\n    f()\nexcept Exception:\n    raise\n",
            "a/b/e.py": "try:\n    f()\nexcept ValueError as e:\n    raise BadInput() from e\n",
            "a/b/f.ts": "try { f(); } catch (e) { console.error(e); }\n",
            "a/b/g.ts": "try { f(); } catch (e) { throw new AppError(e); }\n",
            "a/b/h.rb": "def f\n  g\nrescue StandardError => e\n  Rails.logger.error(e)\nend\n",
            "a/b/i.go": "if err != nil {\n\tlog.Printf(\"x: %v\", err)\n}\n",
        }
        for rel, src in cases.items():
            self.assertEqual(_swallowed(rel, src), [], rel)

    def test_cache_miss_and_optional_import_fallback_not_flagged(self):
        cache = ("def clear(self):\n    try:\n"
                 "        self.instance._prefetched_objects_cache.pop(self.name)\n"
                 "    except (AttributeError, KeyError):\n        pass\n")
        self.assertEqual(_swallowed("a/b/c.py", cache), [])
        memo = "try:\n    return self._cache[key]\nexcept AttributeError:\n    pass\n"
        self.assertEqual(_swallowed("a/b/c.py", memo), [])
        # Optional import guarded by a non-ImportError class (django.contrib.gis.utils).
        gis = ("try:\n    from pkg.layermapping import LayerMapping\n\n"
               "    __all__ += [\"LayerMapping\"]\n\nexcept ImproperlyConfigured:\n    pass\n")
        self.assertEqual(_swallowed("a/b/c.py", gis), [])
        # Stock scaffold: optional front-end requires (Laravel resources/assets/js/bootstrap.js).
        boot = ("try {\n    window.$ = window.jQuery = require('jquery');\n\n"
                "    require('bootstrap-sass');\n} catch (e) {}\n")
        self.assertEqual(_swallowed("resources/assets/js/bootstrap.js", boot), [])
        rb = "begin\n  require 'optional_gem'\nrescue LoadError\nend\n"
        self.assertEqual(_swallowed("lib/x.rb", rb), [])
        # A broad except around a cache read is still a hidden failure.
        broad = "try:\n    return cache.get(key)\nexcept Exception:\n    pass\n"
        self.assertEqual(_swallowed("a/b/c.py", broad), [(3, "Medium", "high")])

    def test_bare_pass_on_broad_exception_still_flagged(self):
        self.assertEqual(_swallowed("a/b/c.py", "try:\n    f()\nexcept Exception:\n    pass\n"),
                         [(3, "Medium", "high")])
        self.assertEqual(_swallowed("a/b/c.py", "try:\n    f()\nexcept:\n    pass\n"),
                         [(3, "Medium", "high")])
        # Broad except, fall-through return after it: the failure is still lost (fixture catalog).
        src = ("def get(i):\n    try:\n        r = requests.get(u)\n        return r.json()\n"
               "    except Exception:\n        pass\n    return {}, 502\n")
        self.assertEqual(_swallowed("a/b/c.py", src), [(5, "Medium", "high")])
        js = "try {\n  const row = await db.query(q);\n  res.json(row);\n} catch (e) {}\n"
        self.assertEqual(_swallowed("a/b/c.ts", js), [(4, "Medium", "high")])
        # Labelled true positive (homeland plugin.rb): a plugin require failure silently dropped.
        rb = ("ActiveSupport.on_load(:after_initialize) do\n  Homeland.plugins.each do |plugin|\n"
              "    require plugin.name\n  rescue LoadError\n  end\nend\n")
        self.assertEqual(_swallowed("lib/homeland/plugin.rb", rb), [(4, "Low", "low")])
        # A TODO is not documented intent: still reported (vague comment -> confidence low).
        todo = "try:\n    f()\nexcept Exception:\n    pass  # TODO: handle this\n"
        self.assertEqual(_swallowed("a/b/c.py", todo), [(3, "Medium", "low")])
        js_todo = "try {\n  save();\n} catch {\n  // FIXME\n}\n"
        self.assertEqual(_swallowed("a/b/c.ts", js_todo), [(3, "Medium", "low")])

    def test_lazy_import_next_to_real_work_still_flagged(self):
        py = "try:\n    import json\n    process(data)\n    save(data)\nexcept Exception:\n    pass\n"
        self.assertEqual(_swallowed("a/b/c.py", py), [(5, "Medium", "high")])
        js = ("try {\n  const fs = require('fs');\n  fs.writeFileSync(p, d);\n  notify();\n"
              "} catch (e) {}\n")
        self.assertEqual(_swallowed("a/b/c.js", js), [(5, "Medium", "high")])
        rb = "begin\n  require 'json'\n  publish(event)\nrescue => e\nend\n"
        self.assertEqual(_swallowed("lib/x.rb", rb), [(4, "Medium", "high")])
        # An import-failure class keeps the idiom even with other statements in the body.
        rb_ok = "begin\n  require 'json'\n  JSON_OK = true\nrescue LoadError\nend\n"
        self.assertEqual(_swallowed("lib/x.rb", rb_ok), [])

    def test_vague_comment_is_low_not_dropped(self):
        cases = {
            "a/b/c.py": ("try:\n    f()\nexcept Exception:\n    pass  # ignore\n", 3),
            "a/b/d.py": ("try:\n    f()\nexcept Exception:\n    # nothing to do here\n    pass\n", 3),
            "a/b/e.ts": ("try {\n  f();\n} catch (e) {\n  // ignore errors\n}\n", 3),
            "a/b/f.java": ("class A { void f() { try { g(); } catch (Exception e) { /* ok */ } } }\n", 1),
            "a/b/g.go": ("func f() {\n\t_ = err // swallow\n}\n", 2),
            "a/b/h.rb": ("begin\n  g\nrescue => e # silent\nend\n", 3),
        }
        for rel, (src, line) in cases.items():
            self.assertEqual(_swallowed(rel, src), [(line, "Medium", "low")], rel)
        explained = {
            "a/b/e.ts": "try {\n  f();\n} catch (e) {\n  // storage may be disabled in private mode\n}\n",
            "a/b/f.java": ("class A { void f() { try { g(); } catch (Exception e) "
                           "{ /* stream already closed by peer */ } } }\n"),
            "a/b/g.go": "func f() {\n\t_ = err // close errors are reported by Flush\n}\n",
            "a/b/h.rb": "begin\n  g\nrescue => e # cache server optional in development\nend\n",
        }
        for rel, src in explained.items():
            self.assertEqual(_swallowed(rel, src), [], rel)

    def test_cache_write_is_not_a_cache_miss(self):
        src = "try:\n    self._cache[key] = compute(key)\nexcept ValueError:\n    pass\n"
        self.assertEqual(_swallowed("a/b/c.py", src), [(3, "Low", "low")])

    def test_many_catches_on_one_line_brace_lookup_is_fast(self):
        import time
        from dfscan import patterns
        text = "try { f(); } catch (e) {}" * 40000 + "\n"
        t0 = time.perf_counter()
        pairs = patterns._brace_pairs(text)
        for m in patterns._CATCH_RE.finditer(text):
            self.assertEqual(patterns._brace_try_body(text, m.start(), pairs), " f(); ")
        self.assertLess(time.perf_counter() - t0, 3.0)

    def test_commented_best_effort_not_flagged(self):
        cases = {
            "a/b/c.py": ("try:\n    value = loads(value)\nexcept Exception:\n"
                         "    # Keep the value as a string if loading failed.\n    pass\n"),
            "a/b/d.py": "try:\n    f()\nexcept TypeError:\n    pass  # string has no placeholder\n",
            "a/b/e.py": "try:\n    f()\nexcept Exception:  # noqa: E722 - best effort cleanup\n    pass\n",
            "a/b/f.tsx": ("try {\n  localStorage.setItem(k, v);\n} catch {\n"
                          "  // Silently fail if localStorage is unavailable\n}\n"),
            "a/b/g.rb": "begin\n  migrate\nrescue NoDatabaseError # no database yet at boot\nend\n",
        }
        for rel, src in cases.items():
            self.assertEqual(_swallowed(rel, src), [], rel)

    def test_try_except_else_and_fallback_value_not_flagged(self):
        cases = [
            "try:\n    v = f()\nexcept ValidationError:\n    pass\nelse:\n    use(v)\n",
            "for f in finders:\n    try:\n        errs = f.check()\n    except NotImplementedError:\n"
            "        pass\n    else:\n        out.extend(errs)\n",
            ("ts: int | None = None\ntry:\n    ts = decode(b)\nexcept Exception:\n    pass\n"
             "if ts is not None:\n    use(ts)\n"),
        ]
        for src in cases:
            self.assertEqual(_swallowed("a/b/c.py", src), [], src)
        # Without the fallback value the assignment is lost: still reported.
        src = "try:\n    ts = decode(b)\nexcept Exception:\n    pass\nreturn ts\n"
        self.assertEqual(_swallowed("a/b/c.py", src), [(3, "Medium", "high")])

    def test_narrow_fall_through_fallback_not_flagged(self):
        src = ("def prep(value):\n    if value and ':' in value:\n        try:\n"
               "            return clean_ipv6_address(value)\n"
               "        except exceptions.ValidationError:\n            pass\n"
               "    return str(value)\n")
        self.assertEqual(_swallowed("a/b/c.py", src), [])
        # sqlite adapt: second except clause of one try, the body imports the optional module.
        src = ("try:\n    import sqlite3\n\n    value = sqlite3.adapt(value)\nexcept ImportError:\n"
               "    pass\nexcept sqlite3.ProgrammingError:\n    pass\nif isinstance(value, bool):\n"
               "    return str(int(value))\n")
        self.assertEqual(_swallowed("a/b/c.py", src), [])
        # Narrow class with no idiom: still reported, Low.
        self.assertEqual(_swallowed("a/b/c.py", "try:\n    rows.sort(key=k)\nexcept ValueError:\n"
                                                "    pass\n"), [(3, "Low", "low")])

    def test_best_effort_cleanup_not_flagged(self):
        cases = {
            "a/b/c.py": "try:\n    self.file.close()\nexcept OSError:\n    pass\n",
            "a/b/d.py": "try:\n    os.chown(path, uid=-1, gid=gid)\nexcept PermissionError:\n    pass\n",
            "a/b/e.py": "try:\n    os.remove(tmp)\nexcept Exception:\n    pass\n",
            "a/b/f.ts": "try { fs.unlinkSync(tmp); } catch (e) {}\n",
            "a/b/g.java": "class A { void f() { try { stream.close(); } catch (IOException e) {} } }\n",
        }
        for rel, src in cases.items():
            self.assertEqual(_swallowed(rel, src), [], rel)
        # Cleanup plus real work in the body is not best-effort cleanup.
        src = "try:\n    save(x)\n    f.close()\nexcept Exception:\n    pass\n"
        self.assertEqual(_swallowed("a/b/c.py", src), [(4, "Medium", "high")])

    def test_crlf_gives_same_result(self):
        def run(rel, src, newline):
            with tempfile.TemporaryDirectory() as root:
                path = os.path.join(root, *rel.split("/"))
                os.makedirs(os.path.dirname(path))
                with open(path, "w", encoding="utf-8", newline=newline) as fh:
                    fh.write(src)
                fs = walk(root)
                return [(s.line, s.severity_hint, s.confidence)
                        for s in run_rules(fs, candidate_folders(fs)) if s.rule == "swallowed-error"]
        src = ("try {\n  localStorage.setItem(k, v);\n} catch {\n  // storage may be off\n}\n"
               "try { f(); } catch (e) {}\n")
        self.assertEqual(run("a/b/c.ts", src, "\r\n"), [(6, "Medium", "high")])
        self.assertEqual(run("a/b/c.ts", src, "\n"), [(6, "Medium", "high")])
        py = ("def f():\n    if x:\n        try:\n            return parse(v)\n"
              "        except ValueError:\n            pass\n    return v\n"
              "try:\n    self.file.close()\nexcept OSError:\n    pass\ntry:\n    f()\nexcept:\n    pass\n")
        self.assertEqual(run("a/b/c.py", py, "\r\n"), [(14, "Medium", "high")])
        self.assertEqual(run("a/b/c.py", py, "\n"), [(14, "Medium", "high")])

    def test_long_bodies_and_many_handlers_stay_linear(self):
        import time
        from dfscan import patterns
        from dfscan.imports import _read
        # A 3000-clause except chain after a long try body, long try bodies, a line of 20000
        # open braces, and a long rescue chain.
        py = ("try:\n" + "    x = 1\n" * 3000
              + "".join("except E%d:\n    pass\n" % i for i in range(3000)))
        js = ("try {\n" + "  a();\n" * 3000 + "} catch (e) {}\n") * 50
        js += "try { " + "{" * 20000 + " } catch (e) {}\n" + "} catch (e) {}" * 1000 + "\n"
        rb = "begin\n" + "  g\n" * 3000 + "rescue Foo\nend\n" + "rescue Bar\nend\n" * 3000
        langs = {".py": "Python", ".ts": "TypeScript", ".rb": "Ruby"}
        for rel, src in (("a/m.py", py), ("a/m.ts", js), ("a/m.rb", rb)):
            ext = os.path.splitext(rel)[1]
            fi = FileInfo(rel, "/nonexistent/" + rel, ext, langs[ext], src.count("\n"), len(src),
                          False)
            patterns._read = lambda f, _s=src: _s  # no comments: raw text equals stripped text
            try:
                t0 = time.perf_counter()
                patterns.rule_swallowed_error(fi, src)
                self.assertLess(time.perf_counter() - t0, 3.0, rel)
            finally:
                patterns._read = _read


# ---------------------------------------------------------------- 1.1.0 no-tests (Task 9)
class NoTestsLayoutTests(unittest.TestCase):
    """no-tests sees root and sibling test trees, test runner config and CI test steps."""

    CODE = "def f():\n    return 1\n"

    def _units(self, files, scope=None):
        return sorted(s["file"] for s in _sigs(files, "no-tests", scope=scope))

    def test_no_tests_sees_sibling_and_root_test_trees(self):
        js = "module.exports = function f() { return 1; };\n"
        # node-express-boilerplate: layer folders under src/, integration tests in a root tests/.
        files = {"src/controllers/user.controller.js": js, "src/routes/v1/user.route.js": js,
                 "src/validations/user.validation.js": js, "src/models/user.model.js": js,
                 "tests/integration/user.test.js": "require('../../src/app');\n",
                 "tests/unit/models/user.model.test.js": "x();\n",
                 "jest.config.js": "module.exports = {};\n"}
        self.assertEqual(self._units(files), [])
        # aspnetcore-realworld: src/Conduit covered by tests/Conduit.IntegrationTests.
        cs = "namespace Conduit { class A { } }\n"
        files = {"src/Conduit/Features/A.cs": cs, "src/Conduit/B.cs": cs,
                 "tests/Conduit.IntegrationTests/ApiTests.cs": cs}
        self.assertEqual(self._units(files), [])
        # laravel-realworld: tests/Feature covers app/*; lang and views folders are skipped.
        php = "<?php\nfunction f() { return 1; }\n"
        files = {"app/Http/Controllers/A.php": php, "app/Providers/AppServiceProvider.php": php,
                 "app/Helpers/Helper.php": php, "resources/lang/en/auth.php": php,
                 "resources/views/welcome.blade.php": php,
                 "tests/Feature/Api/LoginTest.php": php, "phpunit.xml": "<phpunit/>\n"}
        self.assertEqual(self._units(files), [])
        # Without the runner config the feature tree is not proven to run: reported low.
        del files["phpunit.xml"]
        self.assertEqual(self._units(files), ["app/Helpers", "app/Http", "app/Providers"])
        # Colocated convention (nestjs-prisma-starter): siblings carry their own specs, so a
        # feature folder without one is still reported even with a root e2e folder.
        ts = "export class A {}\n"
        files = {"src/posts/posts.resolver.ts": ts, "src/users/users.service.ts": ts,
                 "src/auth/password.service.ts": ts, "src/auth/password.service.spec.ts": ts,
                 "test/app.e2e-spec.ts": ts, "package.json": '{"scripts": {"test": "jest"}}\n'}
        self.assertEqual(self._units(files), ["src/posts", "src/users"])
        # No test anywhere (create-t3-turbo apps/expo, packages/auth): still reported.
        files = {"apps/expo/src/app.tsx": ts, "packages/auth/src/index.ts": ts}
        self.assertEqual(self._units(files), ["apps/expo", "packages/auth"])

    def test_no_tests_sees_ci_test_step_and_framework_config(self):
        code = self.CODE
        base = {"svc/billing/a.py": code, "svc/orders/b.py": code, "tests/test_other.py": "x = 1\n"}
        runners = ({"pytest.ini": "[pytest]\naddopts = -q\n"},
                   {"jest.config.js": "module.exports = { testEnvironment: 'node' };\n"},
                   {"phpunit.xml": "<phpunit></phpunit>\n"},
                   {".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: go test ./...\n"},
                   {".gitlab-ci.yml": "test:\n  script:\n    - python -m pytest\n"})
        # Only an unrelated root test and no runner evidence: low-confidence signals remain.
        self.assertEqual(self._units(base), ["svc/billing", "svc/orders"])
        # One unrelated root test plus a runner config proves nothing about a deep unit: low.
        for extra in runners:
            got = _sigs(dict(base, **extra), "no-tests")
            self.assertEqual(sorted((s["file"], s["confidence"]) for s in got),
                             [("svc/billing", "low"), ("svc/orders", "low")], sorted(extra))
        # An integration tree that the configured runner or CI step runs covers them.
        tree = dict(base, **{"tests/integration/test_api.py": "x = 1\n"})
        for extra in runners:
            self.assertEqual(self._units(dict(tree, **extra)), [], sorted(extra))
        # The integration tree without any runner evidence: still reported (low).
        self.assertEqual(self._units(tree), ["svc/billing", "svc/orders"])
        # A placeholder npm test script is not runner evidence.
        for script in ('echo \\"Error: no test specified\\" && exit 1', "exit 0", "echo ok"):
            pkg = '{"scripts": {"test": "%s"}}\n' % script
            self.assertEqual(self._units(dict(tree, **{"package.json": pkg})),
                             ["svc/billing", "svc/orders"], script)
        # pytest testpaths naming a folder outside tests/ makes it a root test tree.
        files = {"svc/billing/a.py": code, "qa/test_billing.py": "x = 1\n",
                 "setup.cfg": "[tool:pytest]\ntestpaths = qa\n"}
        self.assertEqual(self._units(files), [])
        # Only test-named files under testpaths count: `testpaths = src tests` does not turn
        # src/ code into tests.
        files = {"src/pkg/a.py": code, "src/pkg/b.py": code, "tests/test_other.py": "x = 1\n",
                 "setup.cfg": "[tool:pytest]\ntestpaths = src tests\n"}
        got = _sigs(files, "no-tests")
        self.assertEqual([(s["file"], s["confidence"]) for s in got], [("src/pkg", "low")])
        # A CI workflow that never runs tests is not evidence.
        files = dict(tree, **{".github/workflows/ci.yml":
                              "jobs:\n  b:\n    steps:\n      - run: make build\n"})
        self.assertEqual(self._units(files), ["svc/billing", "svc/orders"])
        # Runner evidence never covers a unit when no test file exists outside it.
        files = {"svc/billing/a.py": code, "pytest.ini": "[pytest]\n"}
        self.assertEqual(self._units(files), ["svc/billing"])

    def test_ci_step_matches_only_commands(self):
        from dfscan import patterns
        negatives = [
            "jobs:\n  jest-coverage-badge:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: make badge\n",
            "jobs:\n  b:\n    steps:\n      # TODO add pytest\n      - run: make build  # pytest later\n",
            "jobs:\n  b:\n    steps:\n      - name: tox\n        run: make build\n",
            "jobs:\n  b:\n    steps:\n      - run: pip install pytest\n",
            "jobs:\n  b:\n    steps:\n      - run: pip install -r requirements-unittest.txt\n",
            "jobs:\n  b:\n    steps:\n      - run: python -m pip install -r requirements-unittest.txt\n",
            "build:\n  script:\n    - npm ci\n    - npm run build\n",
            "jobs:\n  b:\n    steps:\n      - run:\n          name: tox\n          command: make\n",
        ]
        for text in negatives:
            self.assertFalse(patterns._ci_test_step(text), text)
        positives = [
            "jobs:\n  t:\n    steps:\n      - run: |\n          pip install -e .\n          pytest -q\n",
            "jobs:\n  t:\n    steps:\n      - run:\n          name: tests\n          command: go test ./...\n",
            "test:\n  script:\n    - bin/rails test\n",
            "jobs:\n  t:\n    steps:\n      - run: pip install pytest && pytest\n",
            "jobs:\n  t:\n    steps:\n      - run: vendor/bin/phpunit\n",
        ]
        for text in positives:
            self.assertTrue(patterns._ci_test_step(text), text)

    def test_tooling_and_non_code_folders_skipped(self):
        js = "module.exports = { semi: false };\n"
        files = {"tooling/eslint/base.js": js, "tooling/prettier/index.js": js,
                 "lib/templates/model.rb": "class X\nend\n", "db/migrate/001_init.rb": "class M\nend\n",
                 "database/factories/UserFactory.php": "<?php\n",
                 "database/seeds/DatabaseSeeder.php": "<?php\n", "resources/lang/en/a.php": "<?php\n",
                 "resources/views/welcome.blade.php": "<?php echo 1;\n",
                 "app/views/home.html.erb": "<%= 1 %>\n"}
        self.assertEqual(self._units(files), [])
        # Real code is still reported: a package, and a views folder of components.
        self.assertEqual(self._units({"packages/auth/index.ts": "export const a = 1;\n",
                                      "src/views/Home.tsx": "export const Home = () => 1;\n"}),
                         ["packages/auth", "src/views"])

    def test_ci_detection_is_linear(self):
        import time
        from dfscan import patterns
        text = ("mvn " * 50000) + "\n" + ("gradle " * 50000) + "\n" + ("x" * 200000)
        t0 = time.perf_counter()
        self.assertFalse(patterns._ci_test_step(text))
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertTrue(patterns._ci_test_step("      - run: ./mvnw -B verify\n"))
        self.assertTrue(patterns._ci_test_step("script: bundle exec rails test\n"))
        self.assertFalse(patterns._ci_test_step("run: npm run build\n"))


if __name__ == "__main__":
    unittest.main()
