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
            _write(root, "app.js", "app.get('/x', h);\n")
            files = walk(root)
            sigs = route_signals(find_routes(files), files)
            self.assertEqual([(s["rule"], s["file"]) for s in sigs],
                             [("no-health-endpoint", "app.js")])


if __name__ == "__main__":
    unittest.main()
