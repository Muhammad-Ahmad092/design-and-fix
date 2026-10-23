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
        self.assertIn("hardcoded-secret", _rules({"a/b/test_c.py": 'password = "hunter2-real-pw"\n'}))
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

    def test_deep_scope_gets_no_tests(self):  # final review 5
        import dfscan.report as report
        with tempfile.TemporaryDirectory() as root:
            _write(root, "services/payments/src/a.ts", "export const a = 1;\n")
            _write(root, "services/payments/test/a.test.ts", "x\n")
            out = report.scan(root, scope=["services/payments/src"], use_git=False)
        got = {(s["rule"], s["file"]) for s in out["signals"]}
        self.assertIn(("no-tests", "services/payments/src"), got)

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

    def test_secret_in_test_file_is_low_confidence(self):  # final review 21
        conf = {s.file: s.confidence for s in _run({"a/b/test_c.py": 'password = "hunter2-real-pw"\n',
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


if __name__ == "__main__":
    unittest.main()
