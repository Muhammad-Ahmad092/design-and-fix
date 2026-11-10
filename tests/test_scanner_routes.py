from __future__ import annotations

import os
import tempfile
import unittest

from helpers import FIXTURES
from dfscan.report import scan
from dfscan.routes import find_routes, route_signals as _route_signals
from dfscan.walker import walk

MONO = os.path.join(FIXTURES, "monorepo")
CLEAN = os.path.join(FIXTURES, "clean-service")


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def route_signals(routes, files):
    return [s.to_dict() for s in _route_signals(routes, files)]


def _routes_of(files):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        return find_routes(walk(root))


class RouteFixtureTests(unittest.TestCase):
    def setUp(self):
        self.mono = (find_routes(walk(MONO)), walk(MONO))
        self.clean = (find_routes(walk(CLEAN)), walk(CLEAN))

    def test_unauth_writes_found(self):
        flagged = {s["file"] for s in route_signals(*self.mono) if s["rule"] == "unauth-write-route"}
        self.assertIn("services/payments/src/handlers/charge.ts", flagged)
        self.assertIn("services/orders/src/orders.go", flagged)
        self.assertIn("services/catalog/app.py", flagged)
        self.assertNotIn("services/payments/src/handlers/refund.ts", flagged)

    def test_health_endpoint(self):
        folders = {s["file"].split("/")[1] for s in route_signals(*self.mono) if s["rule"] == "no-health-endpoint"}
        self.assertIn("catalog", folders)
        self.assertNotIn("orders", folders)

    def test_clean_service_has_no_route_signals(self):
        self.assertEqual(route_signals(*self.clean), [])

    def test_route_inventory_fields(self):
        r = [x for x in find_routes(walk(MONO)) if x["path"] == "/orders"][0]
        self.assertEqual((r["method"], r["framework"]), ("POST", "gin"))

    def test_test_files_skipped_and_auth_flag(self):
        routes = find_routes(walk(MONO))
        self.assertFalse([r for r in routes if r["file"].endswith(".test.ts")])
        refund = [r for r in routes if r["path"] == "/payments/refund"][0]
        self.assertTrue(refund["auth_detected"])
        self.assertEqual(refund["line"], 15)

    def test_signals_are_dicts(self):
        sigs = route_signals(*self.mono)
        self.assertTrue(all(isinstance(s, dict) for s in sigs))
        s = [x for x in sigs if x["rule"] == "unauth-write-route"][0]
        self.assertEqual((s["category"], s["severity_hint"], s["confidence"]),
                         ("Security", "Critical", "medium"))

    def test_scan_wires_routes(self):
        out = scan(MONO, use_git=False)
        self.assertTrue(out["routes"])
        self.assertTrue(any(s["rule"] == "unauth-write-route" for s in out["signals"]))
        self.assertEqual(scan(CLEAN, use_git=False)["routes"][0]["framework"], "fastapi")


class RouteFrameworkTests(unittest.TestCase):
    def test_flask_default_get_and_blueprint(self):
        rs = _routes_of({"a.py": '@app.route("/a")\ndef a(): pass\n\n@bp.post("/b")\ndef b(): pass\n'})
        self.assertEqual([(r["method"], r["path"]) for r in rs], [("GET", "/a"), ("POST", "/b")])

    def test_flask_methods_list(self):
        rs = _routes_of({"a.py": '@app.route("/a", methods=["GET", "DELETE"])\ndef a(): pass\n'})
        self.assertEqual({r["method"] for r in rs}, {"GET", "DELETE"})

    def test_django_any(self):
        rs = _routes_of({"urls.py": 'urlpatterns = [\n    path("x/", views.x),\n]\n'})
        self.assertEqual((rs[0]["method"], rs[0]["framework"]), ("ANY", "django"))

    def test_go_frameworks(self):
        rs = _routes_of({"m.go": 'r.Post("/c", h)\nhttp.HandleFunc("/n", h)\n'})
        self.assertEqual({(r["method"], r["path"]) for r in rs}, {("POST", "/c"), ("ANY", "/n")})

    def test_nest_with_guard(self):
        src = ("@Controller('cats')\nexport class C {\n  @UseGuards(AuthGuard)\n  @Post('x')\n"
               "  make() {}\n\n  @Delete('y')\n  rm() {}\n}\n")
        rs = _routes_of({"c.ts": src})
        by = {r["method"]: r for r in rs}
        self.assertTrue(by["POST"]["auth_detected"])
        self.assertFalse(by["DELETE"]["auth_detected"])

    def test_router_level_use(self):
        src = ("const router = Router();\nrouter.use(requireAuth);\n"
               "router.post('/a', h);\n")
        self.assertTrue(_routes_of({"a.js": src})[0]["auth_detected"])
        src2 = "router.post('/a', h);\nrouter.use(requireAuth);\n"
        self.assertFalse(_routes_of({"a.js": src2})[0]["auth_detected"])

    def test_path_text_is_not_an_auth_marker(self):
        rs = _routes_of({"a.js": "app.post('/auth/login', h);\n"})
        self.assertFalse(rs[0]["auth_detected"])

    def test_commented_routes_ignored(self):
        self.assertEqual(_routes_of({"a.js": "// app.get('/x', h);\n"}), [])

    def test_go_clients_and_getters_are_not_routes(self):
        src = ('http.Post("https://api.x/y", "a", nil)\nclient.Post("/y", nil)\n'
               'cache.Delete("key")\ndb.Delete("/k")\nr.Header.Get("Authorization")\n'
               'viper.Get("db.host")\nctx.Get("user")\nhttp.Get("/z")\n')
        self.assertEqual(_routes_of({"m.go": src}), [])
        rs = _routes_of({"m.go": 'v1.POST("/ok", h)\n'})
        self.assertEqual([r["path"] for r in rs], ["/ok"])

    def test_one_line_def_does_not_borrow_next_route_auth(self):
        src = ('@app.post("/a")\ndef a(): pass\n\n'
               '@app.post("/b")\n@login_required\ndef b(): pass\n')
        rs = {r["path"]: r["auth_detected"] for r in _routes_of({"a.py": src})}
        self.assertEqual(rs, {"/a": False, "/b": True})

    def test_def_name_and_comment_not_scanned_for_markers(self):
        src = '@app.post("/a")\ndef authorize_thing(x):  # login_required\n    pass\n'
        self.assertFalse(_routes_of({"a.py": src})[0]["auth_detected"])

    def test_django_health_without_leading_slash(self):  # final review 11
        with tempfile.TemporaryDirectory() as root:
            _write(root, "proj/api/urls.py",
                   'urlpatterns = [\n    path("health/", views.health),\n'
                   '    path("orders/", views.orders),\n]\n')
            files = walk(root)
            rules = [s["rule"] for s in route_signals(find_routes(files), files)]
            self.assertNotIn("no-health-endpoint", rules)

    def test_root_file_service_folder(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.js", "app.get('/x', h);\napp.listen(3000);\n")
            files = walk(root)
            sigs = route_signals(find_routes(files), files)
            self.assertEqual([(s["rule"], s["file"]) for s in sigs],
                             [("no-health-endpoint", "app.js")])



def _route_sigs(files, rule=None):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        fs = walk(root)
        sigs = route_signals(find_routes(fs), fs)
    return [s for s in sigs if rule is None or s["rule"] == rule]


FLASK_SVC = ('from flask import Flask\n\napp = Flask(__name__)\n\n\n'
             '@app.route("/items")\ndef items():\n    return "ok"\n\n\n'
             'if __name__ == "__main__":\n    app.run(port=8000)\n')


class R2RouteFalsePositiveTests(unittest.TestCase):
    def test_no_health_only_for_services(self):  # R2-fp3
        # Library package: route-like decorators, a pyproject.toml, no server entry point.
        lib = {"pyproject.toml": '[project]\nname = "lib"\n',
               "src/lib/ctx.py": '@app.route("/")\ndef index():\n    return "hi"\n',
               "src/lib/app.py": "class Flask:\n    def run(self, host=None):\n        pass\n"}
        self.assertEqual(_route_sigs(lib, "no-health-endpoint"), [])
        # Service: app.run( entry point + routes in another file, no health -> one signal on the entry file.
        svc = {"svc/server.py": FLASK_SVC.replace('@app.route("/items")\ndef items():\n    return "ok"\n', ""),
               "svc/api/routes.py": '@bp.get("/orders")\ndef orders():\n    return []\n',
               "svc/api/more.py": '@bp.get("/users")\ndef users():\n    return []\n'}
        got = _route_sigs(svc, "no-health-endpoint")
        self.assertEqual([s["file"] for s in got], ["svc/server.py"])
        # The same service with a health route anywhere under it -> none.
        svc["svc/api/health.py"] = '@bp.get("/health")\ndef health():\n    return "ok"\n'
        self.assertEqual(_route_sigs(svc, "no-health-endpoint"), [])


class BaselineRouteFalsePositiveTests(unittest.TestCase):
    """One regression test per routes false-positive pattern in evals/baseline-findings.md."""

    def test_unauth_auth_middleware_in_other_file_low(self):  # unauth-write-route 1
        files = {"hello.go": ('package main\nfunc main() {\n\tr := gin.Default()\n\tv1 := r.Group("/api")\n'
                              '\tv1.Use(users.AuthMiddleware(true))\n\tarticles.Register(v1.Group("/a"))\n'
                              '\tr.Run()\n}\n'),
                 "articles/routers.go": ('package articles\nfunc Register(router *gin.RouterGroup) {\n'
                                         '\trouter.POST("/", ArticleCreate)\n}\n')}
        # 1.1.0 (rule version 7): the call site articles.Register(v1.Group("/a")) after
        # v1.Use(AuthMiddleware) is followed, so the route is protected.
        self.assertEqual(_route_sigs(files, "unauth-write-route"), [])
        # In-file use on any receiver (v1.Use) marks later routes as authed.
        rs = _routes_of({"m.go": 'v1.Use(AuthMiddleware(true))\nv1.POST("/x", h)\n'})
        self.assertTrue(rs[0]["auth_detected"])

    def test_unauth_public_auth_endpoints_exempt(self):  # unauth-write-route 2
        src = ('@router.post("/login/access-token")\ndef login(): pass\n\n'
               '@router.post("/password-recovery/{email}")\ndef rec(): pass\n\n'
               '@router.post("/signup")\ndef signup(): pass\n\n'
               '@router.post("/reset-password/")\ndef reset(): pass\n\n'
               '@router.post("/items")\ndef create(): pass\n')
        got = _route_sigs({"app/routes.py": src}, "unauth-write-route")
        self.assertEqual([s["snippet"] for s in got], ["POST /items"])
        go = 'router.POST("/", UsersRegistration)\nrouter.POST("/login", UsersLogin)\n'
        # Only the path counts: "/" mounted under /users elsewhere is still flagged.
        got = _route_sigs({"users/routers.go": go}, "unauth-write-route")
        self.assertEqual([s["snippet"] for s in got], ["POST /"])

    def test_unauth_public_exemption_is_path_segment_only(self):  # fix round 1, over-suppression guard
        src = ("app.post('/register-admin', makeAdmin);\n"
               "app.delete('/users/:id', deactivateUser);\n"
               "app.post('/event-registrations', createReg);\n"
               "app.post('/transfer', doTransfer); // TODO: add login check\n"
               "app.post('/tokenize', tok);\n")
        got = _route_sigs({"svc/app.js": src}, "unauth-write-route")
        self.assertEqual(sorted(s["snippet"] for s in got),
                         ["DELETE /users/:id", "POST /event-registrations", "POST /register-admin",
                          "POST /tokenize", "POST /transfer"])

    def test_status_subpath_is_not_health(self):  # fix round 1, over-suppression guard
        svc = {"svc/server.js": "app.get('/orders/:id/status', h);\napp.listen(3000);\n"}
        self.assertEqual(len(_route_sigs(svc, "no-health-endpoint")), 1)
        for path in ("/status", "/api/status", "/api/v1/status/"):
            svc = {"svc/server.js": "app.get('%s', h);\napp.listen(3000);\n" % path}
            self.assertEqual(_route_sigs(svc, "no-health-endpoint"), [], path)

    def test_auth_use_without_services_stays_per_file(self):  # fix round 1
        files = {"lib/a.js": "router.use(requireAuth);\nrouter.get('/x', h);\n",
                 "lib/b.js": "router.post('/y', h);\n"}
        got = _route_sigs(files, "unauth-write-route")
        self.assertEqual([(s["file"], s["confidence"]) for s in got], [("lib/b.js", "medium")])

    def test_example_programs_skipped(self):  # unauth 3, no-health 5
        files = {"_examples/rest/main.go": ('package main\nfunc main() {\n\tr := chi.NewRouter()\n'
                                            '\tr.Post("/articles", create)\n'
                                            '\thttp.ListenAndServe(":3333", r)\n}\n'),
                 "examples/todo/app.js": "app.post('/todos', h);\napp.listen(3000);\n"}
        self.assertEqual(_route_sigs(files), [])
        # Scoping into an examples folder scans it as ordinary code.
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                _write(root, rel, text)
            out = scan(root, scope=["examples/todo"], use_git=False)
        self.assertEqual(sorted(s["rule"] for s in out["signals"] if s["file"].endswith("app.js")),
                         ["no-health-endpoint", "unauth-write-route"])

    def test_routes_in_docstrings_ignored(self):  # no-health 2
        src = ('from flask import Flask\napp = Flask(__name__)\n\n'
               'def helper():\n    """Example::\n\n        @app.route("/")\n        def index(): ...\n\n'
               '        urlpatterns = [path("static/<path>", serve)]\n    """\n')
        self.assertEqual(_routes_of({"svc/app.py": src}), [])
        js = "/**\n * app.post('/x', h)\n */\nconst a = 1;\n"
        self.assertEqual(_routes_of({"svc/a.js": js}), [])

    def test_health_check_names_and_gin_group_prefix(self):  # no-health 3
        fa = {"backend/app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
              "backend/app/api/routes/utils.py": '@router.get("/health-check/")\ndef hc(): return True\n',
              "backend/app/api/routes/items.py": '@router.get("/items")\ndef items(): return []\n'}
        self.assertEqual(_route_sigs(fa, "no-health-endpoint"), [])
        go = {"hello.go": ('package main\nfunc main() {\n\tr := gin.Default()\n'
                           '\tusers.Register(r.Group("/api/users"))\n'
                           '\ttestAuth := r.Group("/api/ping")\n\ttestAuth.GET("/", pong)\n\tr.Run()\n}\n'),
              "users/routers.go": 'package users\nfunc Register(router *gin.RouterGroup) {\n\trouter.GET("/:id", get)\n}\n'}
        self.assertEqual(_route_sigs(go, "no-health-endpoint"), [])
        rs = _routes_of({"m.go": 'g := r.Group("/api/ping")\ng.GET("/", pong)\n'})
        self.assertEqual([r["path"] for r in rs], ["/api/ping/"])

    def test_one_signal_per_service(self):  # no-health 4
        files = {"manage.py": "from django.core.management import execute_from_command_line\n"
                              "if __name__ == '__main__':\n    execute_from_command_line()\n",
                 "proj/api/urls.py": 'urlpatterns = [path("orders/", v.orders)]\n',
                 "proj/users/urls.py": 'urlpatterns = [path("users/", v.users)]\n',
                 "proj/errors/urls.py": 'urlpatterns = [path("errors/", v.errors)]\n'}
        self.assertEqual(len(_route_sigs(files, "no-health-endpoint")), 1)

    def test_route_lines_use_newline_numbering(self):  # carried from Task 2 review
        src = "// a\x0cb\napp.get('/x', h);\n"
        self.assertEqual(_routes_of({"a.js": src})[0]["line"], 2)


# ------------------------------------------------------------- release review 1.0.2 (area B)
class ReleaseReviewRouteTests(unittest.TestCase):
    def test_passport_strategy_registration_is_not_auth(self):  # I5
        src = ("const app = express();\n"
               "passport.use(new JwtStrategy(opts, verify));\n"
               "passport.use('local', new LocalStrategy(check));\n"
               "app.use('jwt', new Strategy(o));\n"
               "app.post('/orders', (req, res) => res.json(createOrder(req.body)));\n"
               "app.listen(3000);\n")
        rs = _routes_of({"server.js": src})
        self.assertEqual([(r["method"], r["path"], r["auth_detected"]) for r in rs],
                         [("POST", "/orders", False)])
        sigs = _route_sigs({"server.js": src, "config/passport.js":
                            "passport.use(new JwtStrategy(opts, verify));\n"},
                           "unauth-write-route")
        self.assertEqual(len(sigs), 1)
        self.assertNotIn("auth middleware applied", sigs[0]["snippet"])
        self.assertEqual(sigs[0]["confidence"], "medium")
        # Real middleware still counts.
        self.assertTrue(_routes_of({"a.js": "app.use(passport.authenticate('jwt'));\n"
                                    "app.post('/x', h);\n"})[0]["auth_detected"])

    def test_gin_group_middleware_argument(self):  # I6
        src = ('package main\nfunc main() {\n\tr := gin.Default()\n'
               '\tauthorized := r.Group("/admin", gin.BasicAuth(gin.Accounts{"foo": "bar"}))\n'
               '\tauthorized.POST("/secrets", postSecrets)\n'
               '\tapi := r.Group("/api", AuthMiddleware())\n'
               '\tapi.POST("/orders", createOrder)\n'
               '\tnested := api.Group("/v2")\n\tnested.POST("/items", h)\n'
               '\tpub := r.Group("/pub")\n\tpub.POST("/signup2", h)\n\tr.Run()\n}\n')
        got = {r["path"]: r["auth_detected"] for r in _routes_of({"main.go": src})}
        self.assertEqual(got, {"/admin/secrets": True, "/api/orders": True,
                               "/api/v2/items": True, "/pub/signup2": False})

    def test_go_use_applies_to_its_group_only(self):  # I6
        src = ('r := gin.Default()\nv1 := r.Group("/v1")\nv1.Use(AuthRequired())\n'
               'v1.POST("/a", h)\nr.POST("/b", h)\n')
        got = {r["path"]: r["auth_detected"] for r in _routes_of({"m.go": src})}
        self.assertEqual(got, {"/v1/a": True, "/b": False})
        src2 = 'r := gin.Default()\nr.Use(AuthRequired())\ng := r.Group("/g")\ng.POST("/c", h)\n'
        self.assertTrue(_routes_of({"m.go": src2})[0]["auth_detected"])

    def test_go_group_variable_resolved_in_line_order(self):  # M15
        src = ('package main\n'
               'func a(r *gin.Engine) {\n\tg := r.Group("/a")\n\tg.POST("/x", h)\n}\n'
               'func b(r *gin.Engine) {\n\tg := r.Group("/b")\n\tg.POST("/y", h)\n}\n')
        rs = _routes_of({"m.go": src})
        self.assertEqual([r["path"] for r in rs], ["/a/x", "/b/y"])

    def test_framework_health_routes_end_to_end(self):  # I14
        import shutil
        dockerfile = "FROM eclipse-temurin:21.0.2_13-jre\nEXPOSE 8080\nCMD [\"run\"]\n"
        drop = {"spring": ("src/main/java/com/example/shop/HealthController.java", None),
                "aspnet": ("Program.cs", "MapHealthChecks"),
                "rails": ("config/routes.rb", "rails/health"),
                "laravel": ("routes/api.php", "'/health'")}
        for name, (rel, token) in sorted(drop.items()):
            with tempfile.TemporaryDirectory() as tmp:
                root = os.path.join(tmp, name)
                shutil.copytree(os.path.join(FIXTURES, name), root)
                _write(root, "Dockerfile", dockerfile)
                fs = walk(root)
                rules = [s["rule"] for s in route_signals(find_routes(fs), fs)]
                self.assertNotIn("no-health-endpoint", rules, name)
                path = os.path.join(root, *rel.split("/"))
                if token is None:
                    os.remove(path)
                else:
                    with open(path, encoding="utf-8") as fh:
                        lines = [ln for ln in fh.read().split("\n") if token not in ln]
                    with open(path, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write("\n".join(lines))
                fs = walk(root)
                rules = [s["rule"] for s in route_signals(find_routes(fs), fs)]
                self.assertEqual(rules.count("no-health-endpoint"), 1, name)

    def test_rails8_session_and_password_routes_public(self):  # coordinator / M9
        src = ('app.post("/session", h);\napp.delete("/session", h);\n'
               'app.post("/passwords", h);\napp.patch("/passwords/:token", h);\n'
               'app.post("/users/sign_in", h);\napp.post("/api/sessions", h);\n'
               'app.put("/users/:id/password", h);\napp.post("/sessionize", h);\n')
        sigs = _route_sigs({"a.js": src}, "unauth-write-route")
        self.assertEqual(sorted(s["snippet"] for s in sigs),
                         ["POST /sessionize", "PUT /users/:id/password"])

    def test_rule_versions(self):
        from dfscan import registry
        self.assertEqual(registry.RULES["unauth-write-route"].version, "7")
        self.assertEqual(registry.RULES["no-health-endpoint"].version, "5")
        self.assertIn("passport.use", registry.RULES["unauth-write-route"].limitations)


if __name__ == "__main__":
    unittest.main()
