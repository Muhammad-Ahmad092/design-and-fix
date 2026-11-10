"""1.1.0 final review B regressions for the Python, Django, Spring, ASP.NET and Laravel auth
resolution (C3, I1 Flask/Django, I2, I9, I11, M2, M4, M5, Laravel namespace regex)."""
from __future__ import annotations

import os
import tempfile
import time
import unittest

from helpers import FIXTURES  # noqa: F401  (puts the scripts folder on sys.path)
from dfscan import auth
from dfscan.imports import _read
from dfscan.report import scan
from dfscan.routes import WRITE_METHODS, find_routes
from dfscan.walker import walk

ENABLE = {"unauth-write-route": {"enabled": True}}


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _tree(files):
    root = tempfile.TemporaryDirectory()
    for rel, text in files.items():
        _write(root.name, rel, text)
    return root


def _verdicts(files, scope=None):
    """(method, path) -> AuthVerdict for every write route of a temp tree."""
    with _tree(files) as root:
        fs = walk(root, scope=scope) if scope else walk(root)
        resolver = auth.Resolver(fs, _read)
        return {(r["method"], r["path"]): resolver.resolve(r)
                for r in find_routes(fs) if r["method"] in WRITE_METHODS}


def _routes(files):
    with _tree(files) as root:
        return {(r["method"], r["path"]) for r in find_routes(walk(root))}


def _sigs(files):
    with _tree(files) as root:
        out = scan(root, use_git=False, rule_config=ENABLE)
    return {s["snippet"]: s for s in out["signals"] if s["rule"] == "unauth-write-route"}


# ---------------------------------------------------------------- Spring (C3, M5)
_POM = "<project><artifactId>x</artifactId></project>\n"
_APP = ("package a;\n@SpringBootApplication\npublic class App {\n"
        "    public static void main(String[] a) { SpringApplication.run(App.class, a); }\n}\n")
_CTRL = ("package a;\n@RestController\n@RequestMapping(\"/api/orders\")\npublic class C {\n"
         "    @PostMapping\n    public void create() {}\n"
         "    @DeleteMapping(\"/{id}\")\n    public void delete() {}\n}\n")


def _chain(rules):
    return ("package a;\npublic class SecurityConfig {\n  @Bean\n"
            "  SecurityFilterChain chain(HttpSecurity http) throws Exception {\n"
            "    return http.csrf(c->c.disable()).authorizeHttpRequests(a -> a%s).build();\n"
            "  }\n}\n" % rules)


def _ignoring(matchers):
    return ("package a;\npublic class Web {\n  @Bean\n"
            "  WebSecurityCustomizer web() {\n    return (web) -> web.ignoring()%s;\n  }\n"
            "  @Bean\n  SecurityFilterChain chain(HttpSecurity http) throws Exception {\n"
            "    return http.authorizeHttpRequests(a -> a.anyRequest().authenticated()).build();\n"
            "  }\n}\n" % matchers)


def _spring(sec):
    return {"pom.xml": _POM, "src/App.java": _APP, "src/C.java": _CTRL, "src/Sec.java": sec}


class SpringCatchAllPermitTests(unittest.TestCase):
    def test_c3_any_request_permit_all_is_unprotected(self):
        # The review's input: actuator permit, then anyRequest().permitAll().
        sec = _chain(".requestMatchers(\"/actuator/**\").permitAll().anyRequest().permitAll()")
        got = _verdicts(_spring(sec))
        for key in (("POST", "/api/orders"), ("DELETE", "/api/orders/{id}")):
            self.assertEqual(got[key].state, "unprotected", key)
            self.assertIn("permitAll() covers every request at src/Sec.java:5",
                          got[key].evidence)
        sigs = _sigs(_spring(sec))
        self.assertEqual(set(sigs), {"POST /api/orders", "DELETE /api/orders/{id}"})
        self.assertTrue(all(not s.get("uncertain_reason") for s in sigs.values()))

    def test_c3_catch_all_patterns_and_kotlin(self):
        for rules in (".requestMatchers(\"/**\").permitAll()",
                      ".requestMatchers(HttpMethod.POST).permitAll().anyRequest().authenticated()",
                      ".anyRequest().anonymous()"):
            got = _verdicts(_spring(_chain(rules)))
            self.assertEqual(got[("POST", "/api/orders")].state, "unprotected", rules)
        kt = ("package a\nclass Sec {\n  @Bean\n  fun chain(http: HttpSecurity): SecurityFilterChain {\n"
              "    http {\n      authorizeHttpRequests {\n        authorize(anyRequest, permitAll)\n"
              "      }\n    }\n    return http.build()\n  }\n}\n")
        files = _spring("")
        del files["src/Sec.java"]
        files["src/Sec.kt"] = kt
        self.assertEqual(_verdicts(files)[("POST", "/api/orders")].state, "unprotected")

    def test_c3_single_level_catch_all(self):
        ctrl = ("package a;\n@RestController\npublic class R {\n"
                "    @PostMapping(\"/orders\")\n    public void create() {}\n}\n")
        files = {"pom.xml": _POM, "src/App.java": _APP, "src/R.java": ctrl,
                 "src/Sec.java": _chain(".requestMatchers(\"/*\").permitAll()")}
        self.assertEqual(_verdicts(files)[("POST", "/orders")].state, "unprotected")

    def test_c3_prefix_permit_is_unresolved_literal_stays_public(self):
        got = _verdicts(_spring(_chain(
            ".requestMatchers(\"/api/**\").permitAll().anyRequest().authenticated()")))
        v = got[("POST", "/api/orders")]
        self.assertEqual(v.state, "unresolved")
        self.assertIn("/api/**", v.evidence)
        self.assertIn("src/Sec.java:5", v.evidence)
        got = _verdicts(_spring(_chain(
            ".requestMatchers(HttpMethod.POST, \"/api/orders\").permitAll()"
            ".anyRequest().authenticated()")))
        self.assertEqual(got[("POST", "/api/orders")].state, "public")
        self.assertEqual(got[("DELETE", "/api/orders/{id}")].state, "protected")
        # A GET-only catch-all does not cover writes.
        got = _verdicts(_spring(_chain(
            ".requestMatchers(HttpMethod.GET, \"/**\").permitAll().anyRequest().authenticated()")))
        self.assertEqual(got[("POST", "/api/orders")].state, "protected")

    def test_c3_web_ignoring(self):
        got = _verdicts(_spring(_ignoring(".requestMatchers(\"/**\")")))
        v = got[("POST", "/api/orders")]
        self.assertEqual(v.state, "unprotected")
        self.assertIn("web.ignoring() /** covers every request at src/Sec.java:5", v.evidence)
        v = _verdicts(_spring(_ignoring(".antMatchers(\"/api/**\")")))[("POST", "/api/orders")]
        self.assertEqual(v.state, "unresolved")
        self.assertIn("/api/**", v.evidence)
        got = _verdicts(_spring(_ignoring(".requestMatchers(\"/api/orders\")")))
        self.assertEqual(got[("POST", "/api/orders")].state, "public")
        self.assertEqual(got[("DELETE", "/api/orders/{id}")].state, "protected")
        # web.ignoring() limited to GET leaves the write routes to the filter chain.
        got = _verdicts(_spring(_ignoring(".requestMatchers(HttpMethod.GET, \"/**\")")))
        self.assertEqual(got[("POST", "/api/orders")].state, "protected")


class SpringRequestMappingTests(unittest.TestCase):
    def test_m5_request_mapping_without_method_is_a_write_route(self):
        ctrl = ("package a;\n@RestController\n@RequestMapping(\"/api/orders\")\npublic class C {\n"
                "    @RequestMapping(value=\"/import\")\n    public void imp() {}\n"
                "    @RequestMapping(value=\"/list\", method=RequestMethod.GET)\n"
                "    public void list() {}\n}\n")
        files = {"pom.xml": _POM, "src/App.java": _APP, "src/C.java": ctrl}
        self.assertEqual(_routes(files), {("ANY", "/api/orders/import"),
                                          ("POST", "/api/orders/import"),
                                          ("GET", "/api/orders/list")})
        self.assertEqual(set(_sigs(files)), {"POST /api/orders/import"})

    def test_m5_openapi_drift_counts_the_twin_once(self):
        ctrl = ("package a;\n@RestController\npublic class C {\n"
                "    @RequestMapping(\"/import\")\n    public void imp() {}\n"
                "    @GetMapping(\"/items\")\n    public void items() {}\n}\n")
        spec = ("openapi: 3.0.0\ninfo:\n  title: x\n  version: '1'\npaths:\n"
                "  /import:\n    get:\n      responses:\n        '200':\n          description: ok\n"
                "  /items:\n    get:\n      responses:\n        '200':\n          description: ok\n")
        with _tree({"pom.xml": _POM, "src/App.java": _APP, "src/C.java": ctrl,
                    "openapi.yaml": spec}) as root:
            out = scan(root, use_git=False, rule_config={"openapi-drift": {"enabled": True}})
        self.assertEqual([s for s in out["signals"] if s["rule"] == "openapi-drift"], [])


# ---------------------------------------------------------------- ASP.NET (I9)
class AspNetObjectReturnTests(unittest.TestCase):
    PROGRAM = ("var builder = WebApplication.CreateBuilder(args);\n"
               "builder.Services.AddControllers();\nvar app = builder.Build();\n"
               "app.MapControllers();\napp.Run();\n")
    CTRL = ("[ApiController]\n[Route(\"api/devices\")]\n"
            "public class DevicesController : ControllerBase\n{\n"
            "    [HttpPost(\"bulk\")]\n    public object Bulk() => Ok();\n"
            "    [HttpPut(\"{id}\")]\n    public object Update(int id)\n    {\n"
            "        return Ok();\n    }\n"
            "    [HttpDelete(\"{id}\")]\n    public IActionResult Delete(int id) => Ok();\n"
            "}\n")

    def test_i9_object_return_type_keeps_the_route(self):
        files = {"Api/Api.csproj": "<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>\n",
                 "Api/Program.cs": self.PROGRAM, "Api/DevicesController.cs": self.CTRL}
        self.assertEqual(_routes(files), {("POST", "/api/devices/bulk"),
                                          ("PUT", "/api/devices/{id}"),
                                          ("DELETE", "/api/devices/{id}")})
        got = _verdicts(files)
        self.assertEqual(got[("POST", "/api/devices/bulk")].state, "unprotected")

    def test_i9_kotlin_object_still_a_class(self):
        kt = ("package a\n@RestController\n@RequestMapping(\"/api\")\nobject Ctl {\n"
              "    @PostMapping(\"/a\")\n    fun a() {}\n}\n")
        self.assertEqual(_routes({"src/Ctl.kt": kt}), {("POST", "/api/a")})


# ---------------------------------------------------------------- Flask (I1, I11, M2)
_FLASK_HOOK_APP = ("from flask import Flask, abort, request\n\napp = Flask(__name__)\n\n"
                   "@app.before_request\ndef check():\n"
                   "    if not request.headers.get('Authorization'):\n        abort(401)\n\n"
                   "@app.post('/a/items')\ndef a_items():\n    return {}\n\n"
                   "if __name__ == '__main__':\n    app.run()\n")
_FLASK_PLAIN_APP = ("from flask import Flask\n\napp = Flask(__name__)\n\n"
                    "@app.post('/b/items')\ndef b_items():\n    return {}\n\n"
                    "if __name__ == '__main__':\n    app.run()\n")


class FlaskServiceScopeTests(unittest.TestCase):
    def test_i1_hook_of_another_service_does_not_protect(self):
        files = {"svc_a/app.py": _FLASK_HOOK_APP, "svc_b/app.py": _FLASK_PLAIN_APP}
        got = _verdicts(files)
        self.assertEqual(got[("POST", "/a/items")].state, "protected")
        self.assertEqual(got[("POST", "/b/items")].state, "unprotected")
        self.assertNotIn("svc_a", got[("POST", "/b/items")].evidence)
        sigs = _sigs(files)
        self.assertEqual(set(sigs), {"POST /b/items"})
        self.assertFalse(sigs["POST /b/items"].get("uncertain_reason"))

    def test_m2_hook_that_only_loads_the_user_is_unresolved(self):
        app = ("from flask import Flask, g, request\n\napp = Flask(__name__)\n\n"
               "@app.before_request\ndef load_user():\n    g.user = None\n"
               "    token = request.headers.get('Authorization')\n    if token:\n"
               "        g.user = decode(token)\n\n"
               "@app.post('/items')\ndef items():\n    return {}\n")
        v = _verdicts({"app.py": app})[("POST", "/items")]
        self.assertEqual(v.state, "unresolved")
        self.assertIn("never rejects", v.evidence)
        for reject in ("        return {'error': 'no'}, 401\n",
                       "        raise Unauthorized()\n",
                       "        return redirect(url_for('auth.login'))\n"):
            hook = app.replace("    if token:\n        g.user = decode(token)\n",
                               "    if not token:\n" + reject)
            self.assertEqual(_verdicts({"app.py": hook})[("POST", "/items")].state,
                             "protected", reject)

    def test_i11_blueprints_without_app_scale_linearly(self):
        n = 400
        files = {}
        for i in range(n):
            files["bp/m%d.py" % i] = (
                "from flask import Blueprint\n\nbp%d = Blueprint('b%d', __name__)\n\n" % (i, i)
                + "".join("@bp%d.post('/r%d/w%d')\ndef w%d():\n    return {}\n\n" % (i, i, j, j)
                          for j in range(5)))
        with _tree(files) as root:
            fs = walk(root)
            routes = [r for r in find_routes(fs) if r["method"] in WRITE_METHODS]
            self.assertEqual(len(routes), 5 * n)
            resolver = auth.Resolver(fs, _read)
            reads = []
            raw = resolver.raw
            resolver.raw = lambda rel: reads.append(rel) or raw(rel)  # type: ignore[assignment]
            t0 = time.perf_counter()
            states = {resolver.resolve(r).state for r in routes}
            elapsed = time.perf_counter() - t0
        self.assertEqual(states, {"unresolved"})
        # Linear: file texts are read a bounded number of times per file, not once per
        # (route, file) pair as before (5 * n * n reads).
        self.assertLess(len(reads), 20 * n)
        self.assertLess(elapsed, 6.0)


# ---------------------------------------------------------------- Django (I1, I2, M4)
_MANAGE = ("import sys\nfrom django.core.management import execute_from_command_line\n"
           "execute_from_command_line(sys.argv)\n")
_DRF_AUTH = ("INSTALLED_APPS = ['rest_framework']\nREST_FRAMEWORK = {\n"
             "    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],\n}\n")
_LRM = ("INSTALLED_APPS = ['rest_framework']\nMIDDLEWARE = [\n"
        "    'django.contrib.auth.middleware.LoginRequiredMiddleware',\n]\n")
_CREATE_VIEW = ("from rest_framework import generics\n\n\n"
                "class OrderCreate(generics.CreateAPIView):\n    pass\n")
_URLS = ("from django.urls import path\nfrom . import views\n\n"
         "urlpatterns = [\n    path('orders/', views.OrderCreate.as_view()),\n]\n")


class DjangoTests(unittest.TestCase):
    def test_i1_settings_of_another_project_do_not_apply(self):
        files = {"admin_site/manage.py": _MANAGE, "admin_site/proj/settings.py": _DRF_AUTH,
                 "admin_site/proj/__init__.py": "",
                 "shop/manage.py": _MANAGE, "shop/shop/settings.py": "INSTALLED_APPS = []\n",
                 "shop/orders/views.py": _CREATE_VIEW, "shop/orders/urls.py": _URLS}
        v = _verdicts(files)[("POST", "orders/")]
        self.assertEqual(v.state, "unprotected")
        self.assertNotIn("admin_site", v.evidence)
        # The same view inside admin_site is protected by its own DRF default.
        files["admin_site/orders/views.py"] = _CREATE_VIEW
        files["admin_site/orders/urls.py"] = _URLS.replace("orders/", "admin-orders/")
        got = _verdicts(files)
        self.assertEqual(got[("POST", "admin-orders/")].state, "protected")
        self.assertEqual(got[("POST", "orders/")].state, "unprotected")
        # shop with no settings of its own: unresolved, naming the other project's settings.
        del files["shop/shop/settings.py"]
        v = _verdicts(files)[("POST", "orders/")]
        self.assertEqual(v.state, "unresolved")
        self.assertIn("another app or service", v.evidence)

    def test_i2_login_required_middleware_does_not_cover_drf_views(self):
        files = {"proj/settings.py": _LRM, "orders/views.py": _CREATE_VIEW,
                 "orders/urls.py": _URLS}
        v = _verdicts(files)[("POST", "orders/")]
        self.assertEqual(v.state, "unprotected")
        self.assertIn("AllowAny", v.evidence)
        # A plain Django view is still covered by the middleware.
        views = ("from django.views.generic import CreateView\n\n\n"
                 "class OrderCreate(CreateView):\n    pass\n")
        files["orders/views.py"] = views
        self.assertEqual(_verdicts(files)[("POST", "orders/")].state, "protected")

    def test_m4_strings_and_author_names_are_not_auth(self):
        settings = "INSTALLED_APPS = ['rest_framework']\n"
        views = ("from rest_framework.decorators import api_view\n"
                 "from rest_framework.views import APIView\n\n\n"
                 "@api_view(['POST'])\n@extend_schema(tags=['authors'])\n"
                 "def create(request):\n    return None\n\n\n"
                 "class AuthorMixin:\n    pass\n\n\n"
                 "class Orders(AuthorMixin, APIView):\n"
                 "    def post(self, request):\n        return None\n\n\n"
                 "class Notes(APIView):\n    @extend_schema(tags=[\"authors\"])\n"
                 "    def post(self, request):\n        return None\n\n\n"
                 "class Guarded(LoginRequiredMixin, APIView):\n"
                 "    def post(self, request):\n        return None\n")
        urls = ("from django.urls import path\nfrom . import views\n\nurlpatterns = [\n"
                "    path('create/', views.create),\n"
                "    path('orders/', views.Orders.as_view()),\n"
                "    path('notes/', views.Notes.as_view()),\n"
                "    path('guarded/', views.Guarded.as_view()),\n]\n")
        got = _verdicts({"proj/settings.py": settings, "web/views.py": views,
                         "web/urls.py": urls})
        self.assertEqual(got[("POST", "create/")].state, "unprotected")
        self.assertEqual(got[("POST", "orders/")].state, "unprotected")
        self.assertEqual(got[("POST", "notes/")].state, "unprotected")
        self.assertEqual(got[("POST", "guarded/")].state, "protected")


# ---------------------------------------------------------------- Laravel namespace regex
class LaravelNamespaceRegexTests(unittest.TestCase):
    def test_namespace_regex_is_linear_and_line_bound(self):
        from dfscan.auth.laravel import _NS_RE
        text = "\n" * 40000 + "<?php class X {}\n"
        t0 = time.perf_counter()
        self.assertIsNone(_NS_RE.search(text))
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(_NS_RE.search("<?php\n\n  \tnamespace App\\Http;\n").group(1),
                         "App\\Http")


if __name__ == "__main__":
    unittest.main()
