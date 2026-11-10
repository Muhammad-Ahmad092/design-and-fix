from __future__ import annotations

import os
import tempfile
import time
import unittest

from helpers import FIXTURES
from dfscan import auth, registry
from dfscan.imports import _read
from dfscan.report import scan
from dfscan.routes import find_routes
from dfscan.walker import walk

AUTH = os.path.join(FIXTURES, "auth")
ENABLE = {"unauth-write-route": {"enabled": True}}


def _write(root, rel, text, newline="\n"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(text.replace("\n", newline).encode("utf-8"))


def _scan(root, scope=None):
    out = scan(root, scope=scope, use_git=False, rule_config=ENABLE)
    return [s for s in out["signals"] if s["rule"] == "unauth-write-route"]


def _sigs(files, scope=None, newline="\n"):
    """unauth-write-route signals of a temp tree (dict rel -> text) or a fixture folder name."""
    if isinstance(files, str):
        return _scan(os.path.join(AUTH, files), scope)
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text, newline)
        return _scan(root, scope)


def _flagged(sigs):
    return {tuple(s["snippet"].split(" ", 1)) for s in sigs}


def _where(sigs):
    return {(s["file"],) + tuple(s["snippet"].split(" ", 1)) for s in sigs}


class InterfaceTests(unittest.TestCase):
    def test_verdict_shape_and_states(self):
        v = auth.AuthVerdict("protected", "x")
        self.assertEqual((v.state, v.evidence), ("protected", "x"))
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.js", "const app = express();\napp.post('/orders', h);\n"
                                   "app.post('/login', h);\napp.use(requireAuth);\n"
                                   "app.post('/later', h);\n")
            files = walk(root)
            got = {r["path"]: auth.resolve(r, files, _read).state for r in find_routes(files)}
        self.assertEqual(got, {"/orders": "unprotected", "/login": "public",
                               "/later": "protected"})

    def test_adapters_dispatch_by_framework_name(self):
        for fw in ("express", "nestjs", "flask", "fastapi", "django", "gin", "chi",
                   "spring", "aspnetcore", "rails", "laravel"):
            self.assertIn(fw, auth.ADAPTERS)

    def test_rule_version_bumped(self):
        meta = registry.RULES["unauth-write-route"]
        self.assertEqual(meta.version, "7")
        self.assertIn("unresolved", meta.limitations)


class ExpressTests(unittest.TestCase):
    def test_express_router_level_middleware_protects(self):
        # app.use('/api/orders', requireAuth, orders) and router.use(requireAuth) in items.js.
        self.assertEqual(_flagged(_sigs("express")), set())

    def test_express_app_use_after_routes_does_not_protect(self):
        files = {"src/app.js": ("const express = require('express');\n"
                                "const orders = require('./routes/orders');\n"
                                "const app = express();\n"
                                "app.use('/api/orders', orders);\n"
                                "app.post('/direct', h);\n"
                                "app.use(requireAuth);\n"
                                "app.post('/later', h);\n"
                                "app.get('/health', h);\n"
                                "app.listen(3000);\n"),
                 "src/routes/orders.js": ("const router = require('express').Router();\n"
                                          "router.post('/', h);\n"
                                          "module.exports = router;\n")}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/"), ("POST", "/direct")})
        self.assertTrue(all(not s.get("uncertain_reason") for s in sigs))

    def test_express_router_function_receives_app(self):
        files = {"server.js": ("const app = express();\nrequire('./routes')(app);\n"
                               "app.get('/health', h);\napp.listen(3000);\n"),
                 "routes/index.js": ("const routes = (app) => {\n"
                                     "  const router = express.Router();\n"
                                     "  router.post('/todos', h);\n"
                                     "  app.use('/api', router);\n};\n"
                                     "module.exports = routes;\n")}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/todos")})
        files["server.js"] = files["server.js"].replace(
            "require('./routes')", "app.use(passport.authenticate('jwt'));\nrequire('./routes')")
        self.assertEqual(_flagged(_sigs(files)), set())

    def test_express_exported_router_never_mounted_is_unresolved(self):
        sigs = _sigs({"routes/orders.js": ("const router = express.Router();\n"
                                           "router.post('/orders', h);\n"
                                           "module.exports = router;\n")})
        self.assertEqual(_flagged(sigs), {("POST", "/orders")})
        self.assertIn("not mounted", sigs[0]["uncertain_reason"])
        self.assertEqual(sigs[0]["confidence"], "low")


class NestTests(unittest.TestCase):
    CTRL = ("@Controller('orders')\nexport class OrdersController {\n"
            "  @Post()\n  create() {}\n\n"
            "  @Public()\n  @Post('guest')\n  guest() {}\n}\n")

    def test_nest_global_guard_protects_and_public_decorator(self):
        main = ("async function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n"
                "  app.useGlobalGuards(new JwtAuthGuard());\n  await app.listen(3000);\n}\n")
        files = {"src/main.ts": main, "src/orders.controller.ts": self.CTRL}
        self.assertEqual(_flagged(_sigs(files)), set())
        mod = ("@Module({\n  providers: [\n    { provide: APP_GUARD, useClass: ThrottlerGuard },\n"
               "  ],\n})\nexport class AppModule {}\n")
        files = {"src/app.module.ts": mod, "src/orders.controller.ts": self.CTRL}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/orders"), ("POST", "/orders/guest")})

    def test_nest_without_bootstrap_in_scope_is_unresolved(self):
        main = ("async function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n"
                "  app.useGlobalGuards(new JwtAuthGuard());\n  await app.listen(3000);\n}\n")
        files = {"src/main.ts": main, "src/orders/orders.controller.ts": self.CTRL}
        sigs = _sigs(files, scope=["src/orders"])
        self.assertEqual(_flagged(sigs), {("POST", "/orders"), ("POST", "/orders/guest")})
        self.assertTrue(all("global guards" in s["uncertain_reason"] for s in sigs))


class FlaskTests(unittest.TestCase):
    BP = ("from flask import Blueprint, abort\nfrom flask_login import current_user\n\n"
          "bp = Blueprint('api', __name__)\n\n"
          "HOOK"
          "@bp.post('/orders')\ndef create():\n    return {}\n")
    HOOK = ("@bp.before_request\ndef check():\n"
            "    if not current_user.is_authenticated:\n        abort(401)\n\n\n")
    APP = ("from flask import Flask\nfrom api.routes import bp\n\napp = Flask(__name__)\n"
           "app.register_blueprint(bp)\n")

    def test_flask_blueprint_before_request_protects(self):
        files = {"app.py": self.APP, "api/routes.py": self.BP.replace("HOOK", self.HOOK)}
        self.assertEqual(_flagged(_sigs(files)), set())

    def test_flask_blueprint_without_hook_flagged(self):
        files = {"app.py": self.APP, "api/routes.py": self.BP.replace("HOOK", "")}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/orders")})
        self.assertFalse(sigs[0].get("uncertain_reason"))

    def test_flask_app_outside_scope_is_unresolved(self):
        files = {"app.py": self.APP, "api/routes.py": self.BP.replace("HOOK", "")}
        sigs = _sigs(files, scope=["api"])
        self.assertEqual(_flagged(sigs), {("POST", "/orders")})
        self.assertIn("Flask app", sigs[0]["uncertain_reason"])


class FastAPITests(unittest.TestCase):
    def test_fastapi_router_dependencies_protect(self):
        # APIRouter(dependencies=[Depends(get_current_user)]) and include_router(..., dependencies=).
        self.assertEqual(_flagged(_sigs("fastapi")), set())

    def test_fastapi_router_included_without_auth_flagged(self):
        files = {"app/main.py": ("from fastapi import FastAPI\nfrom app.api import items\n"
                                 "app = FastAPI()\napp.include_router(items.router)\n"),
                 "app/api/items.py": ("from fastapi import APIRouter\n"
                                      "router = APIRouter(prefix='/items')\n\n"
                                      "@router.post('/')\ndef create(payload: dict):\n"
                                      "    return payload\n")}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/")})
        self.assertFalse(sigs[0].get("uncertain_reason"))
        files["app/main.py"] = files["app/main.py"].replace("FastAPI()",
                                                            "FastAPI(dependencies=[Depends(verify_token)])")
        self.assertEqual(_flagged(_sigs(files)), set())
        # Router file scanned alone: who includes it is not known.
        sigs = _sigs(files, scope=["app/api"])
        self.assertEqual(_flagged(sigs), {("POST", "/")})
        self.assertIn("not included", sigs[0]["uncertain_reason"])
        # Multi-line decorator with a current_*user dependency (corpus: full-stack template).
        files["app/api/items.py"] = files["app/api/items.py"].replace(
            "@router.post('/')", "@router.post(\n    '/',\n"
            "    dependencies=[Depends(get_current_active_superuser)],\n)")
        files["app/main.py"] = files["app/main.py"].replace(
            "FastAPI(dependencies=[Depends(verify_token)])", "FastAPI()")
        self.assertEqual(_flagged(_sigs(files)), set())

    def test_fastapi_relative_import_include_chain(self):
        files = {"svc/main.py": ("from fastapi import Depends, FastAPI\nfrom .api import api_router\n"
                                 "app = FastAPI()\n"
                                 "app.include_router(api_router, dependencies=[Depends(get_current_user)])\n"),
                 "svc/api/__init__.py": ("from fastapi import APIRouter\nfrom . import items\n"
                                         "api_router = APIRouter()\napi_router.include_router(items.router)\n"),
                 "svc/api/items.py": ("from fastapi import APIRouter\nrouter = APIRouter()\n\n"
                                      "@router.delete('/items/{i}')\ndef rm(i: int):\n    return None\n")}
        self.assertEqual(_flagged(_sigs(files)), set())
        files["svc/main.py"] = files["svc/main.py"].replace(
            ", dependencies=[Depends(get_current_user)]", "")
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("DELETE", "/items/{i}")})
        self.assertFalse(sigs[0].get("uncertain_reason"))


class DjangoTests(unittest.TestCase):
    def test_django_drf_default_permission_classes_protect(self):
        self.assertEqual(_flagged(_sigs("django")), set())

    def test_drf_allowany_view_is_public(self):
        settings = ("INSTALLED_APPS = ['rest_framework']\nREST_FRAMEWORK = {\n"
                    "    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],\n}\n")
        views = ("from rest_framework.permissions import AllowAny\n"
                 "from rest_framework.views import APIView\n"
                 "from rest_framework import viewsets\n\n\n"
                 "class Contact(APIView):\n    permission_classes = [AllowAny]\n\n"
                 "    def post(self, request):\n        return None\n\n\n"
                 "class Orders(APIView):\n    def post(self, request):\n        return None\n\n"
                 "    def delete(self, request, pk):\n        return None\n\n\n"
                 "class Notes(viewsets.ModelViewSet):\n    permission_classes = (IsOwner,)\n")
        urls = ("from django.urls import path\nfrom rest_framework.routers import DefaultRouter\n"
                "from shop import views\n\nrouter = DefaultRouter()\n"
                "router.register(r'notes', views.Notes)\n\n"
                "urlpatterns = [\n    path('contact/', views.Contact.as_view()),\n"
                "    path(\n        'orders/',\n        views.Orders.as_view(),\n    ),\n]\n")
        sigs = _sigs({"proj/settings.py": settings, "shop/views.py": views, "shop/urls.py": urls})
        self.assertEqual(_flagged(sigs), {("POST", "orders/"), ("DELETE", "orders/")})
        self.assertTrue(all(not s.get("uncertain_reason") for s in sigs))

    def test_django_route_methods_from_views(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "shop/views.py", "from rest_framework import generics\n\n"
                                          "class OrderCreate(generics.CreateAPIView):\n    pass\n")
            _write(root, "shop/urls.py", "urlpatterns = [\n"
                                         "    path('orders/', views.OrderCreate.as_view()),\n"
                                         "    path('x/', views.unknown),\n]\n")
            rs = find_routes(walk(root))
        self.assertEqual([(r["method"], r["path"]) for r in rs], [("POST", "orders/"), ("ANY", "x/")])

    def test_django_dispatch_decorator_and_wrapped_views(self):  # corpus: django.contrib
        views = ("from django.views.generic import FormView, RedirectView\n\n\n"
                 "class PasswordChange(FormView):\n"
                 "    @method_decorator(login_required)\n"
                 "    def dispatch(self, *args, **kwargs):\n"
                 "        return super().dispatch(*args, **kwargs)\n\n\n"
                 "class Contact(FormView):\n    pass\n\n\n"
                 "class Old(RedirectView):\n    def post(self, request):\n        return None\n")
        urls = ("urlpatterns = [\n"
                "    path('password_change/', views.PasswordChange.as_view()),\n"
                "    path('contact/', views.Contact.as_view()),\n"
                "    path('old/', wrap(views.Old.as_view())),\n"
                "    path('password_reset/', views.Contact.as_view()),\n"
                "    path('reset/<uidb64>/<token>/', views.Contact.as_view()),\n]\n")
        sigs = _sigs({"proj/settings.py": "MIDDLEWARE = []\n", "web/views.py": views,
                      "web/urls.py": urls})
        self.assertEqual(_flagged(sigs), {("POST", "contact/")})

    def test_auth_config_outside_scope_is_unresolved(self):  # Review Focus 2
        sigs = _sigs("django", scope=["api"])
        self.assertEqual([s["rule"] for s in sigs], ["unauth-write-route"])
        self.assertEqual(_flagged(sigs), {("POST", "orders/")})
        self.assertTrue(sigs[0]["uncertain_reason"])
        self.assertIn("settings", sigs[0]["uncertain_reason"])
        self.assertEqual(sigs[0]["confidence"], "low")


class GoTests(unittest.TestCase):
    def test_gin_group_middleware_protects(self):
        # orders.Register(api.Group("/orders")) under r.Group("/api", AuthRequired()); items is open.
        self.assertEqual(_where(_sigs("gin")), {("items/routes.go", "POST", "/")})

    def test_gin_use_before_and_after_registration(self):
        files = {"hello.go": ("package main\nfunc main() {\n\tr := gin.Default()\n"
                              "\tv1 := r.Group(\"/api\")\n\tusers.Open(v1.Group(\"/users\"))\n"
                              "\tv1.Use(users.AuthMiddleware(true))\n"
                              "\tarticles.Register(v1.Group(\"/articles\"))\n\tr.Run()\n}\n"),
                 "articles/routers.go": ("package articles\nfunc Register(router *gin.RouterGroup) {\n"
                                         "\trouter.POST(\"/\", ArticleCreate)\n}\n"),
                 "users/routers.go": ("package users\nfunc Open(router *gin.RouterGroup) {\n"
                                      "\trouter.POST(\"/follow\", Follow)\n}\n")}
        self.assertEqual(_where(_sigs(files)), {("users/routers.go", "POST", "/follow")})
        # The caller outside the scope: unresolved, not a confident finding.
        sigs = _sigs(files, scope=["articles"])
        self.assertEqual(_flagged(sigs), {("POST", "/")})
        self.assertIn("caller", sigs[0]["uncertain_reason"])

    def test_chi_with_middleware_protects(self):
        src = ("package main\nfunc main() {\n\tr := chi.NewRouter()\n"
               "\tr.With(AuthMiddleware).Post(\"/orders\", create)\n"
               "\tr.Post(\"/open\", open)\n\tr.Get(\"/health\", h)\n"
               "\thttp.ListenAndServe(\":3000\", r)\n}\n")
        self.assertEqual(_flagged(_sigs({"main.go": src})), {("POST", "/open")})


class SharedTests(unittest.TestCase):
    def test_public_auth_routes_not_flagged(self):
        src = ("const app = express();\n"
               "app.post('/login', h);\napp.post('/register', h);\napp.post('/password/reset', h);\n"
               "app.post('/token', h);\napp.post('/health', h);\napp.post('/orders', h);\n")
        # Final review B I4: write routes never get the health exemption (POST /health is flagged).
        self.assertEqual(_flagged(_sigs({"app.js": src})), {("POST", "/orders"), ("POST", "/health")})

    def test_webhook_public_only_with_signature_check(self):
        signed = ("const app = express();\napp.post('/webhooks/stripe', (req, res) => {\n"
                  "  stripe.webhooks.constructEvent(req.body, req.headers['stripe-signature'], k);\n"
                  "});\n")
        self.assertEqual(_flagged(_sigs({"hooks.js": signed})), set())
        unsigned = "const app = express();\napp.post('/webhooks/stripe', (req, res) => res.end());\n"
        self.assertEqual(_flagged(_sigs({"hooks.js": unsigned})), {("POST", "/webhooks/stripe")})

    def test_multiline_route_registration_crlf(self):  # Review Focus 3
        js_multi = ("const router = express.Router();\n"
                    "router.post(\n  '/orders',\n  requireAuth,\n  create\n);\n"
                    "router.delete(\n  '/orders/:id',\n  remove\n);\n")
        js_single = ("const router = express.Router();\n"
                     "router.post('/orders', requireAuth, create);\n"
                     "router.delete('/orders/:id', remove);\n")
        py_multi = ("from fastapi import Depends, FastAPI\napp = FastAPI()\n\n"
                    "@app.post(\n    '/items',\n    dependencies=[Depends(get_current_user)],\n)\n"
                    "def create(payload: dict):\n    return payload\n\n\n"
                    "@app.put(\n    '/items/{item_id}',\n)\n"
                    "def update(\n    item_id: int,\n    user=Depends(get_current_user),\n):\n"
                    "    return None\n\n\n"
                    "@app.delete(\n    '/items/{item_id}',\n    status_code=204,\n)\n"
                    "def delete(item_id: int):\n    return None\n")
        py_single = ("from fastapi import Depends, FastAPI\napp = FastAPI()\n\n"
                     "@app.post('/items', dependencies=[Depends(get_current_user)])\n"
                     "def create(payload: dict):\n    return payload\n\n\n"
                     "@app.put('/items/{item_id}')\n"
                     "def update(item_id: int, user=Depends(get_current_user)):\n    return None\n\n\n"
                     "@app.delete('/items/{item_id}', status_code=204)\n"
                     "def delete(item_id: int):\n    return None\n")
        expected = {("DELETE", "/orders/:id"), ("DELETE", "/items/{item_id}")}
        multi = {"svc/routes.js": js_multi, "svc/app.py": py_multi}
        lf = _sigs(multi)
        crlf = _sigs(multi, newline="\r\n")
        self.assertEqual(_flagged(lf), expected)
        self.assertEqual([(s["file"], s["line"], s["snippet"]) for s in crlf],
                         [(s["file"], s["line"], s["snippet"]) for s in lf])
        single = _sigs({"svc/routes.js": js_single, "svc/app.py": py_single})
        self.assertEqual(_flagged(single), expected)

    def test_multiline_django_path_crlf(self):  # Review Focus 3
        files = {"proj/settings.py": "INSTALLED_APPS = []\n",
                 "shop/views.py": ("from rest_framework.views import APIView\n\n\n"
                                   "class Orders(APIView):\n    def post(self, request):\n"
                                   "        return None\n"),
                 "shop/urls.py": ("urlpatterns = [\n    path(\n        'orders/',\n"
                                  "        views.Orders.as_view(),\n    ),\n]\n")}
        lf, crlf = _sigs(files), _sigs(files, newline="\r\n")
        self.assertEqual(_flagged(lf), {("POST", "orders/")})
        self.assertEqual([(s["line"], s["snippet"]) for s in crlf],
                         [(s["line"], s["snippet"]) for s in lf])


class FixRound1Tests(unittest.TestCase):
    def test_token_parameter_public_only_as_reset_link(self):
        src = ("const app = express();\n"
               "app.delete('/api-keys/{token}', h);\n"
               "app.post('/users/:token/delete', h);\n"
               "app.post('/users/confirm/:token', h);\n"
               "app.post('/account/reset/:uid/:token', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": src})),
                         {("DELETE", "/api-keys/{token}"), ("POST", "/users/:token/delete")})
        self.assertTrue(auth.is_public_path("reset/<uidb64>/<token>/"))
        self.assertFalse(auth.is_public_path("/api-keys/<token>"))

    def test_public_segment_only_at_the_end(self):
        src = ("const app = express();\n"
               "app.post('/register/admin', h);\n"
               "app.delete('/admin/sessions/:id/users', h);\n"
               "app.post('/users/register', h);\n"
               "app.post('/login/access-token', h);\n"
               "app.delete('/sessions/:id', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": src})),
                         {("POST", "/register/admin"), ("DELETE", "/admin/sessions/:id/users")})

    def test_strings_and_bare_handler_are_not_auth_markers(self):
        js = ("const app = express();\napp.post('/superusers', createSuperuser);\n"
              "app.post('/authors', authorCreate);\napp.post('/orders', requireAuth, create);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})),
                         {("POST", "/superusers"), ("POST", "/authors")})
        py = ("from fastapi import FastAPI\napp = FastAPI()\n\n"
              "@app.post('/admins', summary='Create superuser account (auth required)')\n"
              "def create(payload: dict):\n    return payload\n")
        self.assertEqual(_flagged(_sigs({"app.py": py})), {("POST", "/admins")})
        go = ('package main\nfunc main() {\n\tr := gin.Default()\n'
              '\tr.POST("/superusers", createSuperuser)\n\tr.POST("/orders", AuthRequired(), create)\n}\n')
        self.assertEqual(_flagged(_sigs({"main.go": go})), {("POST", "/superusers")})

    def test_login_not_required_opts_out_of_middleware(self):
        settings = ("MIDDLEWARE = [\n    'django.contrib.auth.middleware.LoginRequiredMiddleware',\n]\n")
        views = ("from django.contrib.auth.decorators import login_not_required\n"
                 "from django.views.decorators.http import require_POST\n\n\n"
                 "@login_not_required\n@require_POST\ndef contact(request):\n    return None\n\n\n"
                 "@require_POST\ndef orders(request):\n    return None\n")
        urls = ("urlpatterns = [\n    path('contact/', views.contact),\n"
                "    path('orders/', views.orders),\n]\n")
        sigs = _sigs({"proj/settings.py": settings, "web/views.py": views, "web/urls.py": urls})
        self.assertEqual(_flagged(sigs), {("POST", "contact/")})

    def test_multiline_go_and_chained_express(self):
        go = ('package main\nfunc main() {\n\tr := gin.Default()\n'
              '\tr.POST(\n\t\t"/orders",\n\t\tcreate,\n\t)\n'
              '\tr.DELETE(\n\t\t"/orders/:id",\n\t\tAuthRequired(),\n\t\tremove,\n\t)\n}\n')
        self.assertEqual(_flagged(_sigs({"main.go": go})), {("POST", "/orders")})
        js = ("const app = express();\napp\n  .post('/orders', create);\n"
              "app\n  .delete('/orders/:id', requireAuth, remove);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/orders")})

    def test_express_app_imported_from_outside_scope_is_unresolved(self):
        files = {"src/app.js": ("const express = require('express');\nconst app = express();\n"
                                "app.use(requireAuth);\nmodule.exports = app;\n"),
                 "src/routes/orders.js": ("const app = require('../app');\n"
                                          "app.post('/orders', h);\n")}
        self.assertEqual(_flagged(_sigs(files)), set())
        sigs = _sigs(files, scope=["src/routes"])
        self.assertEqual(_flagged(sigs), {("POST", "/orders")})
        self.assertIn("not in the scanned files", sigs[0]["uncertain_reason"])


# ------------------------------------------------------------------------------- Task 5
_SPRING_CTRL = ("package a;\n@RestController\n@RequestMapping(\"/api\")\npublic class C {\n"
                "    @PostMapping(\"/orders\")\n    public void create() {}\n"
                "    @PostMapping(\"/public/upload\")\n    public void upload() {}\n"
                "    @PostMapping(\"/reports\")\n    public void report() {}\n}\n")
_SPRING_APP = ("package a;\n@SpringBootApplication\npublic class App {\n"
               "    public static void main(String[] a) { SpringApplication.run(App.class, a); }\n}\n")


class SpringTests(unittest.TestCase):
    def test_spring_security_chain_permitall_vs_authenticated(self):
        # Fixture: requestMatchers(OPEN constant).permitAll(), POST /api/users permitAll,
        # /api/admin/** hasRole, anyRequest().authenticated().
        # The OPEN constant holds "/public/**": a public-area prefix permit stays public.
        sigs = _sigs("spring")
        self.assertEqual(_flagged(sigs), set())
        # No anyRequest(): a route no matcher covers is unresolved, not protected.
        sec = ("package a;\npublic class Sec {\n    @Bean\n"
               "    SecurityFilterChain chain(HttpSecurity http) throws Exception {\n"
               "        return http.authorizeHttpRequests(a -> a\n"
               "            .requestMatchers(\"/api/public/**\").permitAll()\n"
               "            .requestMatchers(HttpMethod.POST, \"/api/orders\").authenticated())\n"
               "            .build();\n    }\n}\n")
        sigs = _sigs({"src/C.java": _SPRING_CTRL, "src/Sec.java": sec})
        self.assertEqual(_flagged(sigs), {("POST", "/api/reports")})
        reasons = {s["snippet"]: s["uncertain_reason"] for s in sigs}
        self.assertIn("no matcher", reasons["POST /api/reports"])
        # 1.1.0 final review C3: a prefix permit that does not name a public area covers the
        # write route but is unresolved (low), not public.
        sigs = _sigs({"src/C.java": _SPRING_CTRL,
                      "src/Sec.java": sec.replace("/api/public/**", "/api/**")})
        reasons = {s["snippet"]: s["uncertain_reason"] for s in sigs}
        self.assertIn("path prefix", reasons["POST /api/public/upload"])

    def test_spring_old_style_chain_first_match_wins(self):  # corpus: spring-boot-realworld
        sec = ("package a;\npublic class Sec extends WebSecurityConfigurerAdapter {\n"
               "  @Override\n  protected void configure(HttpSecurity http) throws Exception {\n"
               "    http.csrf().disable().authorizeRequests()\n"
               "        .antMatchers(HttpMethod.OPTIONS).permitAll()\n"
               "        .antMatchers(HttpMethod.GET, \"/api/**\").permitAll()\n"
               "        .antMatchers(HttpMethod.POST, \"/api/public/*\").permitAll()\n"
               "        .anyRequest().authenticated();\n  }\n}\n")
        self.assertEqual(_flagged(_sigs({"src/C.java": _SPRING_CTRL, "src/Sec.java": sec})),
                         set())

    def test_spring_kotlin_dsl(self):
        sec = ("package a\nclass Sec {\n  @Bean\n  fun chain(http: HttpSecurity): SecurityFilterChain {\n"
               "    http {\n      authorizeHttpRequests {\n"
               "        authorize(\"/api/public/**\", permitAll)\n"
               "        authorize(anyRequest, authenticated)\n      }\n    }\n"
               "    return http.build()\n  }\n}\n")
        # "/api/public/**" names a public area: public; POST /api/reports is authenticated.
        sigs = _sigs({"src/C.java": _SPRING_CTRL, "src/Sec.kt": sec})
        self.assertEqual(_flagged(sigs), set())

    def test_spring_security_outside_scope_is_unresolved(self):  # Review Focus 2
        sigs = _sigs("spring", scope=["src/main/java/com/shop/api"])
        self.assertEqual(_flagged(sigs), {("POST", "/api/orders"), ("DELETE", "/api/admin/orders/{id}"),
                                          ("POST", "/api/users"), ("POST", "/public/contact")})
        for s in sigs:
            self.assertIn("@SpringBootApplication", s["uncertain_reason"])
        # Spring Security in the build file but no filter chain scanned: unresolved.
        sigs = _sigs("spring", scope=["pom.xml", "src/main/java/com/shop/api"])
        self.assertEqual(len(sigs), 4)
        for s in sigs:
            self.assertIn("Spring Security is used (pom.xml)", s["uncertain_reason"])
        # Application scanned, no Spring Security anywhere: plain unprotected routes.
        plain = _sigs({"src/C.java": _SPRING_CTRL, "src/App.java": _SPRING_APP})
        self.assertEqual(len(plain), 3)
        self.assertFalse(any(s.get("uncertain_reason") for s in plain))


class AspNetTests(unittest.TestCase):
    PROGRAM = ("var builder = WebApplication.CreateBuilder(args);\n"
               "builder.Services.AddControllers();\nvar app = builder.Build();\n"
               "app.MapControllers();\n%s"
               "app.MapPost(\"/minimal/orders\", Create);\napp.Run();\n")
    CTRL = ("[ApiController]\n[Route(\"api/[controller]\")]\n"
            "public class OrdersController : ControllerBase\n{\n"
            "    [HttpPost]\n    public IActionResult Create() => Ok();\n"
            "    [HttpPost(\"feedback\"), AllowAnonymous]\n    public IActionResult Feedback() => Ok();\n"
            "}\n")

    def test_aspnet_fallback_policy_and_allowanonymous(self):
        # FallbackPolicy protects controllers and minimal APIs; [AllowAnonymous] and
        # .AllowAnonymous() opt out (public).
        self.assertEqual(_flagged(_sigs("aspnet")), set())
        no_policy = _sigs({"Program.cs": self.PROGRAM % "",
                           "Controllers/OrdersController.cs": self.CTRL})
        self.assertEqual(_flagged(no_policy), {("POST", "/api/orders"),
                                               ("POST", "/api/orders/feedback"),
                                               ("POST", "/minimal/orders")})

    def test_aspnet_global_authorize_filter_covers_controllers_only(self):
        prog = self.PROGRAM.replace("AddControllers()", "AddControllers(o => o.Filters.Add("
                                    "new AuthorizeFilter()))") % ""
        sigs = _sigs({"Program.cs": prog, "Controllers/OrdersController.cs": self.CTRL})
        self.assertEqual(_flagged(sigs), {("POST", "/minimal/orders")})
        prog = self.PROGRAM.replace("app.MapControllers();",
                                    "app.MapControllers().RequireAuthorization();") % ""
        sigs = _sigs({"Program.cs": prog, "Controllers/OrdersController.cs": self.CTRL})
        self.assertEqual(_flagged(sigs), {("POST", "/minimal/orders")})

    def test_aspnet_register_beside_login_is_public(self):  # corpus: aspnetcore-realworld
        ctrl = ("[Route(\"users\")]\npublic class UsersController(IMediator m)\n{\n"
                "    [HttpPost]\n    public Task<object> Create() => null;\n"
                "    [HttpPost(\"login\")]\n    public Task<object> Login() => null;\n}\n"
                "[Route(\"notes\")]\npublic class NotesController\n{\n"
                "    [HttpPost]\n    public Task<object> Create() => null;\n}\n")
        sigs = _sigs({"Program.cs": self.PROGRAM % "", "Features/UsersController.cs": ctrl})
        self.assertEqual(_flagged(sigs), {("POST", "/notes"), ("POST", "/minimal/orders")})

    def test_aspnet_startup_outside_scope_is_unresolved(self):  # Review Focus 2
        sigs = _sigs("aspnet", scope=["Controllers"])
        self.assertEqual(_flagged(sigs), {("POST", "/api/orders"), ("POST", "/api/orders/feedback")})
        for s in sigs:
            self.assertIn("Program.cs", s["uncertain_reason"])

    def test_aspnet_inherited_authorize_independent_of_file_order(self):  # final review I3
        # The file declaring [Authorize] SecureBase also asks for a cross-file base (SharedBase).
        # Whether it is walked before or after OrdersController.cs, /orders stays protected.
        prog = "var app = builder.Build();\napp.MapControllers();\napp.Run();\n"
        decl = ("[Authorize]\npublic abstract class SecureBase : ControllerBase {}\n"
                "[Route(\"a\")]\npublic class %sController : SharedBase\n{\n"
                "    [HttpGet]\n    public IActionResult Get() => Ok();\n}\n")
        shared = "public abstract class SharedBase : ControllerBase {}\n"
        orders = ("[Route(\"orders\")]\npublic class OrdersController : SecureBase\n{\n"
                  "    [HttpPost]\n    public IActionResult Create() => Ok();\n}\n")
        for name in ("A", "Z"):  # AController.cs sorts before OrdersController.cs, Z after
            with self.subTest(first=name == "A"):
                with tempfile.TemporaryDirectory() as root:
                    for rel, text in {"Program.cs": prog,
                                      "Controllers/%sController.cs" % name: decl % name,
                                      "Controllers/Shared/SharedBase.cs": shared,
                                      "Controllers/OrdersController.cs": orders}.items():
                        _write(root, rel, text)
                    out = scan(root, use_git=False, rule_config=ENABLE)
                routes = {(r["method"], r["path"]): r["auth_detected"] for r in out["routes"]}
                self.assertIs(routes[("POST", "/orders")], True)
                self.assertEqual([s for s in out["signals"] if s["rule"] == "unauth-write-route"
                                  and s["file"].endswith("OrdersController.cs")], [])

    def test_scan_clears_framework_memos(self):  # final review M5
        from dfscan import frameworks
        with tempfile.TemporaryDirectory() as root:
            _write(root, "Program.cs", "var app = builder.Build();\napp.MapControllers();\n")
            _write(root, "Controllers/OrdersController.cs",
                   "[Route(\"orders\")]\npublic class OrdersController : SecureBase\n{\n"
                   "    [HttpPost]\n    public IActionResult Create() => Ok();\n}\n")
            scan(root, use_git=False, rule_config=ENABLE)
        self.assertIsNotNone(frameworks._details_files)
        with tempfile.TemporaryDirectory() as root:
            scan(root, use_git=False)  # an empty scan resets every memo and fills none
        self.assertIsNone(frameworks._cs_scan_files)
        self.assertEqual(frameworks._cs_scan_table, {})
        self.assertIsNone(frameworks._details_files)
        self.assertEqual(frameworks._details_cache, {})
        self.assertIsNone(frameworks._close_text)
        self.assertEqual(frameworks._READ_CACHE, {})
        self.assertEqual(frameworks._CS_TABLE_CACHE, {})


class RailsTests(unittest.TestCase):
    def test_rails_cancan_load_and_authorize_resource_protects(self):
        # Fixture: TeamUsersController load_and_authorize_resource only: accept/reject and
        # before_action :authorize_team_owner! for the rest; Admin::PluginsController inherits
        # authenticate_user! from Admin::ApplicationController (a `#{...}` regex in routes.rb
        # must not end the namespace block).
        self.assertEqual(_flagged(_sigs("rails")), set())
        files = {"config/routes.rb": ("Rails.application.routes.draw do\n"
                                      "  resources :notes do\n    member do\n      post :share\n"
                                      "    end\n  end\nend\n"),
                 "app/controllers/application_controller.rb": "class ApplicationController < ActionController::Base\nend\n",
                 "app/controllers/notes_controller.rb": (
                     "class NotesController < ApplicationController\n"
                     "  load_and_authorize_resource except: [:share]\n"
                     "  def index; end\n  def show; end\n  def create; end\n  def update; end\n"
                     "  def destroy; end\n  def share; end\nend\n")}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/notes/:id/share")})

    def test_rails_route_without_controller_action_not_emitted(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/routes.rb", "Rails.application.routes.draw do\n"
                                             "  resources :nodes\n  resources :tags\nend\n")
            _write(root, "app/controllers/application_controller.rb",
                   "class ApplicationController < ActionController::Base\nend\n")
            _write(root, "app/controllers/nodes_controller.rb",
                   "class NodesController < ApplicationController\n  def index\n  end\nend\n")
            got = {(r["method"], r["path"]) for r in find_routes(walk(root))}
        # Write routes without an action are dropped; GET routes stay (implicit rendering).
        self.assertEqual({p for p in got if p[1].startswith("/nodes")},
                         {("GET", "/nodes"), ("GET", "/nodes/:id")})
        # TagsController is not scanned: its resources routes are all kept.
        self.assertEqual(len({p for p in got if p[1].startswith("/tags")}), 5)

    def test_rails_controller_outside_scope_is_unresolved(self):  # Review Focus 2
        sigs = _sigs("rails", scope=["config"])
        self.assertTrue(sigs)
        for s in sigs:
            self.assertIn("not in the scanned files", s["uncertain_reason"])


class LaravelTests(unittest.TestCase):
    def test_laravel_controller_constructor_middleware(self):
        # Fixture: ProfileController $this->middleware('auth.api')->except('show');
        # ArticleController HasMiddleware new Middleware('auth:sanctum', except: [...]);
        # NoteController only 'auth.api:optional' (not auth); AuthController@register public.
        self.assertEqual(_flagged(_sigs("laravel")), {("POST", "/notes")})

    def test_laravel_route_service_provider_group_outside_scope_unresolved(self):
        sigs = _sigs("laravel", scope=["routes"])
        self.assertEqual(_flagged(sigs), {("POST", "/profiles/{user}/follow"),
                                          ("DELETE", "/profiles/{user}/follow"),
                                          ("POST", "/articles"), ("PUT", "/articles/{id}"),
                                          ("DELETE", "/articles/{id}"), ("POST", "/notes")})
        for s in sigs:
            self.assertIn("RouteServiceProvider", s["uncertain_reason"])

    def test_laravel_provider_group_auth_protects(self):
        files = {"routes/admin.php": "<?php\nRoute::post('purge', fn () => 1);\n",
                 "app/Providers/RouteServiceProvider.php": (
                     "<?php\nclass RouteServiceProvider {\n  public function boot() {\n"
                     "    Route::middleware(['web', 'auth'])->prefix('admin')\n"
                     "        ->group(base_path('routes/admin.php'));\n  }\n}\n")}
        self.assertEqual(_flagged(_sigs(files)), set())
        files["app/Providers/RouteServiceProvider.php"] = files[
            "app/Providers/RouteServiceProvider.php"].replace("'web', 'auth'", "'web'")
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/purge")})


_POM = "<project><artifactId>x</artifactId></project>\n"
_SEC_ALL = ("package a;\npublic class Sec {\n  @Bean\n"
            "  SecurityFilterChain c(HttpSecurity http) throws Exception {\n"
            "    return http.authorizeHttpRequests(a -> a.anyRequest().authenticated()).build();\n"
            "  }\n}\n")


def _spring_ctrl(cls, prefix, *routes):
    head = "package a;\n@RestController\n"
    if prefix:
        head += "@RequestMapping(\"%s\")\n" % prefix
    body = "".join("    @PostMapping(\"%s\")\n    public void %s() {}\n" % (p, m) for p, m in routes)
    return head + "public class %s {\n%s}\n" % (cls, body)


def _express_tree(k, n):
    """An Express app mounting k router files of n write routes each (app.use('/rI', rI))."""
    files = {"package.json": '{"dependencies": {"express": "4"}}\n',
             "app.js": "const express = require('express');\nconst app = express();\n" + "".join(
                 "const r%d = require('./routes/r%d');\napp.use('/r%d', r%d);\n" % (i, i, i, i)
                 for i in range(k)) + "app.listen(3000);\n"}
    for i in range(k):
        files["routes/r%d.js" % i] = (
            "const express = require('express');\nconst router = express.Router();\n"
            + "".join("router.post('/w%d', h);\n" % j for j in range(n))
            + "module.exports = router;\n")
    return files


class FixRound1Task5Tests(unittest.TestCase):
    """Task 5 fix round 1: service scoping, sign-up rule, Rails includes."""

    def test_spring_chain_applies_only_to_its_own_service(self):
        files = {"svca/pom.xml": _POM, "svca/src/App.java": _SPRING_APP,
                 "svca/src/Sec.java": _SEC_ALL,
                 "svca/src/C.java": _spring_ctrl("C", "", ("/svca/orders", "create")),
                 "svcb/pom.xml": _POM, "svcb/src/App.java": _SPRING_APP,
                 "svcb/src/C.java": _spring_ctrl("C", "", ("/svcb/orders", "create"))}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/svcb/orders")})

    def test_aspnet_policy_applies_only_to_its_own_service(self):
        prog = ("var builder = WebApplication.CreateBuilder(args);\n%s"
                "var app = builder.Build();\napp.MapPost(\"/%s/orders\", Create);\napp.Run();\n")
        policy = ("builder.Services.AddAuthorization(o => o.FallbackPolicy = new "
                  "AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build());\n")
        files = {"svca/A.csproj": "<Project />\n", "svca/Program.cs": prog % (policy, "svca"),
                 "svcb/B.csproj": "<Project />\n", "svcb/Program.cs": prog % ("", "svcb")}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/svcb/orders")})

    def test_laravel_loader_applies_only_to_its_own_app(self):
        rsp = ("<?php\nclass RouteServiceProvider {\n  public function map() {\n"
               "    Route::middleware(['api', 'auth'])->group(base_path('routes/api.php'));\n"
               "  }\n}\n")
        files = {"appa/composer.json": "{}\n", "appa/artisan": "#!/usr/bin/env php\n",
                 "appa/routes/api.php": "<?php\nRoute::post('a', fn () => 1);\n",
                 "appa/app/Providers/RouteServiceProvider.php": rsp,
                 "appb/composer.json": "{}\n",
                 "appb/routes/api.php": "<?php\nRoute::post('b', fn () => 1);\n"}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/b")})
        self.assertIn("RouteServiceProvider", sigs[0]["uncertain_reason"])

    def test_signup_rule_needs_a_registration_handler(self):
        app = {"src/App.java": _SPRING_APP}
        admin = _spring_ctrl("AdminController", "", ("/users", "createUser"),
                             ("/users/login", "login"))
        self.assertEqual(_flagged(_sigs(dict(app, **{"src/AdminController.java": admin}))),
                         {("POST", "/users")})
        under_admin = _spring_ctrl("UsersController", "/admin", ("/users", "createUser"),
                                   ("/users/login", "login"))
        self.assertEqual(_flagged(_sigs(dict(app, **{"src/U.java": under_admin}))),
                         {("POST", "/admin/users")})
        prog = ("var builder = WebApplication.CreateBuilder(args);\nvar app = builder.Build();\n"
                "app.MapControllers();\napp.Run();\n")
        cs = ("[Route(\"users\")]\npublic class AdminUsersController : ControllerBase\n{\n"
              "    [HttpPost]\n    public IActionResult CreateUser() => Ok();\n"
              "    [HttpPost(\"login\")]\n    public IActionResult Login() => Ok();\n}\n")
        self.assertEqual(_flagged(_sigs({"Program.cs": prog, "Controllers/A.cs": cs})),
                         {("POST", "/users")})
        routes = ("<?php\nRoute::post('users/login', 'AuthController@login');\n"
                  "Route::apiResource('users', 'UserController');\n")
        rsp = ("<?php\nclass RouteServiceProvider {\n  public function map() {\n"
               "    Route::middleware('api')->group(base_path('routes/api.php'));\n  }\n}\n")
        ctrl = ("<?php\nclass UserController extends Controller\n{\n"
                "    public function store() {}\n    public function update($id) {}\n"
                "    public function destroy($id) {}\n}\n")
        sigs = _sigs({"routes/api.php": routes, "app/Providers/RouteServiceProvider.php": rsp,
                      "app/Http/Controllers/UserController.php": ctrl})
        self.assertIn(("POST", "/users"), _flagged(sigs))

    def test_signup_evidence_names_the_rule(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "Program.cs", "var builder = WebApplication.CreateBuilder(args);\n"
                                       "var app = builder.Build();\napp.MapControllers();\n")
            _write(root, "Controllers/UsersController.cs",
                   "[Route(\"users\")]\npublic class UsersController\n{\n"
                   "    [HttpPost]\n    public IActionResult Create() => null;\n"
                   "    [HttpPost(\"login\")]\n    public IActionResult Login() => null;\n}\n")
            files = walk(root)
            got = {r["path"]: auth.resolve(r, files, _read) for r in find_routes(files)}
        self.assertEqual(got["/users"].evidence, "sign-up route (public by design)")

    def _rails_users(self, include, extra_files):
        files = {"config/routes.rb": "Rails.application.routes.draw do\n  resources :users\nend\n",
                 "app/controllers/application_controller.rb":
                     "class ApplicationController < ActionController::Base\nend\n",
                 "app/controllers/users_controller.rb": (
                     "class UsersController < ApplicationController\n  %s\n"
                     "  def index; end\n  def show; end\nend\n" % include)}
        files.update(extra_files)
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                _write(root, rel, text)
            return {(r["method"], r["path"]) for r in find_routes(walk(root))}

    def test_rails_nested_concern_defining_create_keeps_route(self):
        got = self._rails_users("include Users::Base", {
            "app/controllers/users/base.rb": "module Users\n  module Base\n    include Creatable\n"
                                             "  end\nend\n",
            "app/controllers/concerns/creatable.rb": "module Creatable\n  def create; end\nend\n"})
        self.assertIn(("POST", "/users"), got)
        self.assertNotIn(("DELETE", "/users/:id"), got)

    def test_rails_unresolved_or_multi_module_include_keeps_routes(self):
        got = self._rails_users("include A, Creatable", {
            "app/controllers/concerns/creatable.rb": "module Creatable\n  def helper; end\nend\n"})
        self.assertIn(("DELETE", "/users/:id"), got)
        got = self._rails_users("include NotScanned", {})
        self.assertIn(("POST", "/users"), got)
        # Rails framework modules define no actions: the phantom routes are still dropped.
        got = self._rails_users("include ActionController::Caching", {})
        self.assertNotIn(("POST", "/users"), got)


class FrameworkCrlfTests(unittest.TestCase):
    def test_crlf_same_as_lf(self):  # Review Focus 3
        files = {}
        for name in ("spring", "aspnet", "rails", "laravel"):
            base = os.path.join(AUTH, name)
            for dirpath, _, names in os.walk(base):
                for n in names:
                    path = os.path.join(dirpath, n)
                    rel = os.path.relpath(path, AUTH).replace(os.sep, "/")
                    with open(path, encoding="utf-8") as fh:
                        files[rel] = fh.read()
        for name in ("spring", "aspnet", "rails", "laravel"):
            tree = {k[len(name) + 1:]: v for k, v in files.items() if k.startswith(name + "/")}
            lf = _where(_sigs(tree))
            self.assertEqual(_where(_sigs(tree, newline="\r\n")), lf, name)
            self.assertEqual(lf, _where(_sigs(name)), name)


class PerformanceTests(unittest.TestCase):
    """Auth resolution is linear in the number of routes (fix round 1: 2,000 Express routes took
    25 s when each route re-read its file)."""

    N = 2000

    def _timed(self, files):
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                _write(root, rel, text)
            start = time.perf_counter()
            out = scan(root, use_git=False, rule_config=ENABLE)
            return time.perf_counter() - start, out

    def test_2000_routes_scan_fast(self):
        n = self.N
        trees = {
            "express": {"app.js": "const express = require('express');\nconst app = express();\n"
                        + "".join("app.post('/w%d', h);\n" % i for i in range(n))
                        + "app.listen(3000);\n"},
            "gin": {"main.go": "package main\nfunc main() {\n\tr := gin.Default()\n"
                    "\tv1 := r.Group(\"/api\")\n"
                    + "".join('\tv1.POST("/w%d", h)\n' % i for i in range(n)) + "\tr.Run()\n}\n"},
            "flask": {"app.py": "from flask import Flask\napp = Flask(__name__)\n\n"
                      + "".join("@app.post('/w%d')\ndef w%d():\n    return ''\n\n" % (i, i)
                                for i in range(n)) + "app.run()\n"},
        }
        self._check(trees)

    def test_2000_framework_routes_scan_fast(self):  # Task 5 adapters
        n = self.N
        trees = {
            "spring": {
                "src/C.java": ("package a;\n@RestController\npublic class C {\n"
                               + "".join("    @PostMapping(\"/w%d\")\n    public void w%d() {}\n"
                                         % (i, i) for i in range(n)) + "}\n"),
                "src/Sec.java": ("package a;\npublic class Sec {\n  @Bean\n"
                                 "  SecurityFilterChain c(HttpSecurity http) throws Exception {\n"
                                 "    return http.authorizeHttpRequests(a -> a\n"
                                 "      .requestMatchers(\"/open/**\").permitAll()\n"
                                 "      .requestMatchers(\"/w1*\").authenticated()).build();\n"
                                 "  }\n}\n")},
            "aspnet": {
                "Program.cs": ("var builder = WebApplication.CreateBuilder(args);\n"
                               "var app = builder.Build();\napp.MapControllers();\napp.Run();\n"),
                "Controllers/WController.cs": (
                    "[Route(\"api\")]\npublic class WController : ControllerBase\n{\n"
                    + "".join("    [HttpPost(\"w%d\")]\n    public IActionResult W%d() => Ok();\n"
                              % (i, i) for i in range(n)) + "}\n")},
            "rails": {
                "config/routes.rb": ("Rails.application.routes.draw do\n"
                                     + "".join("  post \"w%d\", to: \"orders#w%d\"\n" % (i, i)
                                               for i in range(n)) + "end\n"),
                "app/controllers/application_controller.rb":
                    "class ApplicationController < ActionController::Base\nend\n",
                "app/controllers/orders_controller.rb": (
                    "class OrdersController < ApplicationController\n"
                    "  before_action :authenticate_user!, only: %i[w1 w2]\n"
                    + "".join("  def w%d; end\n" % i for i in range(n)) + "end\n")},
            "laravel": {
                "routes/api.php": ("<?php\nRoute::group(['namespace' => 'Api'], function () {\n"
                                   + "".join("    Route::post('w%d', 'WController@w%d');\n" % (i, i)
                                             for i in range(n)) + "});\n"),
                "app/Providers/RouteServiceProvider.php": (
                    "<?php\nclass RouteServiceProvider {\n  public function map() {\n"
                    "    Route::middleware('api')->group(base_path('routes/api.php'));\n"
                    "  }\n}\n"),
                "app/Http/Controllers/Api/WController.php": (
                    "<?php\nclass WController extends Controller\n{\n"
                    "    public function __construct()\n    {\n"
                    "        $this->middleware('auth')->only(['w1', 'w2']);\n    }\n"
                    + "".join("    public function w%d() {}\n" % i for i in range(n)) + "}\n")},
        }
        self._check(trees)

    def test_400_rails_resources_large_controller_fast(self):  # fix round 1
        files = {
            "config/routes.rb": ("Rails.application.routes.draw do\n"
                                 + "".join("  resources :r%d, controller: \"big\"\n" % i
                                           for i in range(400)) + "end\n"),
            "app/controllers/application_controller.rb":
                "class ApplicationController < ActionController::Base\n  include Shared\nend\n",
            "app/controllers/concerns/shared.rb": "module Shared\n  def helper; end\nend\n",
            "app/controllers/big_controller.rb": (
                "class BigController < ApplicationController\n"
                "  before_action :authenticate_user!, only: %i[index]\n"
                + "".join("  def m%d; end\n" % i for i in range(3000))
                + "  def index; end\n  def create; end\nend\n")}
        took, out = self._timed(files)
        # index, show and create per resource (update/destroy are dropped: no such action).
        self.assertEqual(out["summary"]["routes_total"], 400 * 3)
        self.assertLess(took, 5.0)

    def test_multi_file_trees_scan_fast(self):  # fix round 1: 100 files x 20 routes each
        n = 20

        def trees(k):
            return {
                "spring": dict({"pom.xml": _POM, "src/App.java": _SPRING_APP,
                                "src/Sec.java": _SEC_ALL},
                               **{"src/C%d.java" % f: _spring_ctrl(
                                   "C%d" % f, "/c%d" % f, *[("/w%d" % i, "w%d" % i) for i in range(n)])
                                  for f in range(k)}),
                "aspnet": dict({"App.csproj": "<Project />\n",
                                "Program.cs": ("var builder = WebApplication.CreateBuilder(args);\n"
                                               "var app = builder.Build();\napp.MapControllers();\n")},
                               **{"Controllers/C%dController.cs" % f: (
                                   "[Route(\"c%d\")]\npublic class C%dController : ControllerBase\n{\n"
                                   % (f, f) + "".join(
                                       "    [HttpPost(\"w%d\")]\n    public IActionResult W%d() => Ok();\n"
                                       % (i, i) for i in range(n)) + "}\n") for f in range(k)}),
                "rails": dict({"config/routes.rb": (
                    "Rails.application.routes.draw do\n" + "".join(
                        "  post \"c%d/w%d\", to: \"c%d#w%d\"\n" % (f, i, f, i)
                        for f in range(k) for i in range(n)) + "end\n"),
                    "app/controllers/application_controller.rb":
                        "class ApplicationController < ActionController::Base\nend\n"},
                    **{"app/controllers/c%d_controller.rb" % f: (
                        "class C%dController < ApplicationController\n" % f
                        + "".join("  def w%d; end\n" % i for i in range(n)) + "end\n")
                       for f in range(k)}),
                "laravel": dict({"routes/api.php": "<?php\n" + "".join(
                    "Route::post('c%d/w%d', 'C%dController@w%d');\n" % (f, i, f, i)
                    for f in range(k) for i in range(n)),
                    "app/Providers/RouteServiceProvider.php": (
                        "<?php\nclass RouteServiceProvider {\n  public function map() {\n"
                        "    Route::middleware('api')->group(base_path('routes/api.php'));\n"
                        "  }\n}\n")},
                    **{"app/Http/Controllers/C%dController.php" % f: (
                        "<?php\nclass C%dController extends Controller\n{\n" % f
                        + "".join("    public function w%d() {}\n" % i for i in range(n)) + "}\n")
                       for f in range(k)}),
            }

        # Task 16: each tree's CPU time (time.process_time, which other processes' load barely
        # moves) is compared with a smaller tree's, best of two or three runs each; a generous
        # absolute wall-clock cap stays too.
        small, large = trees(25), trees(100)
        for name in large:
            t_small, t_large = self._best_pair(small[name], large[name], self.N, name)
            # Linear growth is about 4x (3.3x to 5.4x measured); quadratic would be about 16x.
            self.assertLess(t_large, 8 * max(t_small, 0.05), name)
        # Express: an app mounting 100 vs 800 router files (8x). Linear code grows about 8.5x
        # here; the 1.1.0 task-9 code (every router rescanned every mount in app.js) grew about
        # 18x. 50 vs 400 does not separate them reliably (about 9x vs 11x: fixed costs dominate).
        t_small, t_large = self._best_pair(_express_tree(100, 1), _express_tree(800, 1), 800,
                                           "express", runs=3)
        self.assertLess(t_large, 13 * max(t_small, 0.05), "express")

    def _cpu_timed(self, files):
        """(CPU seconds, wall seconds, scan output) of one scan of `files`."""
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                _write(root, rel, text)
            cpu, wall = time.process_time(), time.perf_counter()
            out = scan(root, use_git=False, rule_config=ENABLE)
            return time.process_time() - cpu, time.perf_counter() - wall, out

    def _best_pair(self, small, large, routes_total, name, runs=2):
        """Best CPU times (small tree, large tree); the large tree's wall time must stay < 30 s."""
        t_small = min(self._cpu_timed(small)[0] for _ in range(runs))
        results = [self._cpu_timed(large) for _ in range(runs)]
        self.assertEqual(results[0][2]["summary"]["routes_total"], routes_total, name)
        self.assertLess(min(wall for _, wall, _ in results), 30.0, name)
        return t_small, min(cpu for cpu, _, _ in results)

    def _check(self, trees):
        for name, files in trees.items():
            took, out = self._timed(files)
            self.assertEqual(out["summary"]["routes_total"], self.N, name)
            self.assertLess(took, 5.0, name)


if __name__ == "__main__":
    unittest.main()
