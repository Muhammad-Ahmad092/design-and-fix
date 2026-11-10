"""Final review B (1.1.0) regression tests: JS/Go/Nest auth resolution, public-path context,
health/webhook exemptions, Express inventory, FastAPI import determinism, caller indexes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import FIXTURES  # noqa: F401  (sets sys.path for dfscan)
from dfscan import auth
from dfscan.auth.core import Resolver
from dfscan.imports import _read
from dfscan.report import scan
from dfscan.routes import find_routes
from dfscan.walker import walk

ENABLE = {"unauth-write-route": {"enabled": True}}
from helpers import SCRIPTS as _SCRIPTS  # noqa: E402


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _sigs(files, scope=None):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        out = scan(root, scope=scope, use_git=False, rule_config=ENABLE)
    return [s for s in out["signals"] if s["rule"] == "unauth-write-route"]


def _flagged(sigs):
    return {tuple(s["snippet"].split(" ", 1)) for s in sigs}


def _verdicts(files):
    """(method, path) -> AuthVerdict of every write route of a temp tree."""
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        fs = walk(root)
        res = Resolver(fs, _read)
        return {(r["method"], r["path"]): res.resolve(r) for r in find_routes(fs)
                if r["method"] in ("POST", "PUT", "PATCH", "DELETE")}


def _routes(files):
    with tempfile.TemporaryDirectory() as root:
        for rel, text in files.items():
            _write(root, rel, text)
        return {(r["method"], r["path"]) for r in find_routes(walk(root))}


EXPRESS = "const express = require('express');\nconst app = express();\n"


class C1AuthUseTests(unittest.TestCase):
    def test_login_router_mount_is_not_auth(self):
        js = EXPRESS + "app.use('/api/auth', authRouter);\napp.post('/orders', h);\n"
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/orders")})
        self.assertIsNone(auth.auth_use("app.use('/api/auth', authRouter);"))

    def test_login_router_in_own_file(self):
        app = (EXPRESS + "const authRoutes = require('./routes/auth');\n"
               "const orderRoutes = require('./routes/orders');\n"
               "app.use('/api/auth', authRoutes);\napp.use('/api/orders', orderRoutes);\n"
               "app.listen(3000);\n")
        router = "const express = require('express');\nconst router = express.Router();\n"
        files = {"app.js": app,
                 "routes/auth.js": router + "router.post('/login', h);\nmodule.exports = router;\n",
                 "routes/orders.js": router + "router.post('/', h);\nrouter.delete('/:id', h);\n"
                                              "module.exports = router;\n"}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/"), ("DELETE", "/:id")})
        self.assertTrue(all(s["confidence"] == "low" and not s.get("uncertain_reason")
                            for s in sigs), sigs)

    def test_router_named_auth_bound_to_routes_file(self):
        app = (EXPRESS + "const auth = require('./routes/auth');\n"
               "app.use(auth);\napp.post('/orders', h);\n")
        files = {"app.js": app, "routes/auth.js": "const router = require('express').Router();\n"
                                                  "router.post('/login', h);\nmodule.exports = router;\n"}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/orders")})

    def test_inline_required_router_is_not_auth_elsewhere_in_service(self):
        files = {"server.js": EXPRESS + "app.use('/auth', require('./auth'));\napp.listen(3000);\n",
                 "auth.js": "const router = require('express').Router();\n"
                            "router.post('/login', h);\nmodule.exports = router;\n",
                 "jobs.js": EXPRESS + "app.post('/jobs', h);\n"}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/jobs")})
        self.assertFalse(sigs[0].get("uncertain_reason"), sigs)

    def test_cors_allow_list_is_not_auth(self):
        js = (EXPRESS + "app.use(cors({ allowedHeaders: ['Content-Type', 'Authorization'] }));\n"
              "app.post('/orders', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/orders")})
        go = ('package main\nfunc main() {\n\tr := gin.Default()\n'
              '\tr.Use(cors.New(cors.Config{AllowHeaders: []string{"Origin", "Authorization"}}))\n'
              '\tr.POST("/orders", create)\n\tr.Run()\n}\n')
        self.assertEqual(_flagged(_sigs({"main.go": go})), {("POST", "/orders")})

    def test_real_middleware_still_protects(self):
        for use in ("app.use(requireAuth);", "app.use(passport.authenticate('jwt'));",
                    "app.use(jwt({ secret: k, algorithms: ['HS256'] }));",
                    "app.use(require('./middleware/auth'));"):
            js = EXPRESS + use + "\napp.post('/orders', h);\n"
            self.assertEqual(_flagged(_sigs({"app.js": js})), set(), use)
        js = EXPRESS + "app.use(passport.initialize());\napp.post('/orders', h);\n"
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/orders")})


class C2ScopeTests(unittest.TestCase):
    def test_path_scoped_middleware(self):
        js = (EXPRESS + "app.use('/admin', requireAuth);\napp.post('/orders', h);\n"
              "app.post('/admin/users', h);\n")
        got = _verdicts({"app.js": js})
        self.assertEqual(got[("POST", "/orders")].state, "unprotected")
        self.assertEqual(got[("POST", "/admin/users")].state, "protected")
        self.assertIn("app.js:3", got[("POST", "/admin/users")].evidence)

    def test_path_scope_unknown_prefix_is_unresolved(self):
        js = (EXPRESS + "app.use(prefix, requireAuth);\napp.post('/orders', h);\n")
        got = _verdicts({"app.js": js})
        self.assertEqual(got[("POST", "/orders")].state, "unresolved")

    def test_evidence_names_the_use_line(self):
        js = EXPRESS + "app.use(requireAuth);\napp.post('/later', h);\n"
        v = _verdicts({"app.js": js})[("POST", "/later")]
        self.assertEqual(v.state, "protected")
        self.assertIn("app.js:3", v.evidence)

    def test_other_receiver_use_does_not_protect(self):
        js = (EXPRESS + "const admin = express.Router();\nadmin.use(requireAuth);\n"
              "app.post('/orders', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/orders")})

    def test_chi_closure_shadowing(self):
        go = ('package main\nfunc main() {\n\tr := chi.NewRouter()\n'
              '\tr.Group(func(r chi.Router) {\n\t\tr.Use(jwtauth.Authenticator)\n'
              '\t\tr.Delete("/orders/{id}", del)\n\t})\n'
              '\tr.Put("/items/{id}", put)\n\thttp.ListenAndServe(":3000", r)\n}\n')
        self.assertEqual(_flagged(_sigs({"main.go": go})), {("PUT", "/items/{id}")})

    def test_go_use_in_another_function(self):
        go = ('package main\nfunc setup(r *gin.Engine) {\n\tr.Use(AuthRequired())\n}\n'
              'func routes(e *gin.Engine) {\n\te.POST("/orders", create)\n}\n'
              'func main() {\n\tr := gin.Default()\n\troutes(r)\n\tr.Run()\n}\n')
        self.assertEqual(_flagged(_sigs({"main.go": go})), {("POST", "/orders")})

    def test_module_use_on_other_receiver(self):
        files = {"app.js": (EXPRESS + "const adminRouter = express.Router();\n"
                            "adminRouter.use(requireAuth);\nmodule.exports = app;\n"),
                 "server.js": "const app = require('./app');\napp.post('/orders', h);\n"
                              "app.listen(3000);\n"}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/orders")})
        files["app.js"] = EXPRESS + "app.use(requireAuth);\nmodule.exports = app;\n"
        self.assertEqual(_flagged(_sigs(files)), set())


class I3PublicContextTests(unittest.TestCase):
    def test_express_admin_and_resource_paths(self):
        paths = ["/admin/users/:id/reset-password", "/api/cache/refresh", "/api/devices/register",
                 "/api/users/:id/token"]
        js = EXPRESS + "".join("app.post('%s', h);\n" % p for p in paths)
        js += "app.delete('/api/users/:id/sessions', h);\napp.post('/api/auth/token', h);\n"
        js += "app.post('/users/register', h);\napp.post('/api/v1/login', h);\n"
        got = _flagged(_sigs({"app.js": js}))
        self.assertEqual(got, {("POST", p) for p in paths} | {("DELETE", "/api/users/:id/sessions")})
        v = _verdicts({"app.js": js})
        self.assertEqual(v[("POST", "/api/cache/refresh")].state, "unresolved")
        self.assertIn("named like an auth endpoint", v[("POST", "/api/cache/refresh")].evidence)
        self.assertEqual(v[("POST", "/admin/users/:id/reset-password")].state, "unprotected")

    def test_admin_panel_login_stays_public(self):
        # Deliberate exception to "never public under admin": an admin area's own login/logout
        # is unauthenticated by necessity.
        js = EXPRESS + "app.post('/admin/login', h);\napp.post('/admin/register', h);\n"
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/admin/register")})

    def test_rails_admin_namespace(self):
        files = {"config/routes.rb": ("Rails.application.routes.draw do\n  namespace :admin do\n"
                                      "    resources :users do\n      member do\n"
                                      "        post :reset_password\n        post :register\n"
                                      "      end\n    end\n  end\nend\n"),
                 "app/controllers/application_controller.rb":
                     "class ApplicationController < ActionController::Base\nend\n",
                 "app/controllers/admin/users_controller.rb": (
                     "module Admin\n  class UsersController < ApplicationController\n"
                     "    def index; end\n    def reset_password; end\n    def register; end\n"
                     "  end\nend\n")}
        got = _flagged(_sigs(files))
        self.assertIn(("POST", "/admin/users/:id/reset_password"), got)
        self.assertIn(("POST", "/admin/users/:id/register"), got)

    def test_laravel_admin_controller_action(self):
        rsp = ("<?php\nclass RouteServiceProvider {\n  public function map() {\n"
               "    Route::middleware('api')->group(base_path('routes/api.php'));\n  }\n}\n")
        routes = ("<?php\nuse App\\Http\\Controllers\\Admin;\n"
                  "Route::post('/admin/devices', [Admin\\DeviceController::class, 'register']);\n")
        ctrl = ("<?php\nnamespace App\\Http\\Controllers\\Admin;\n"
                "class DeviceController extends Controller\n{\n"
                "    public function register() {}\n}\n")
        files = {"routes/api.php": routes, "app/Providers/RouteServiceProvider.php": rsp,
                 "app/Http/Controllers/Admin/DeviceController.php": ctrl}
        self.assertEqual(_flagged(_sigs(files)), {("POST", "/admin/devices")})

    def test_aspnet_admin_route_login_action(self):
        prog = ("var builder = WebApplication.CreateBuilder(args);\nvar app = builder.Build();\n"
                "app.MapControllers();\napp.Run();\n")
        cs = ("[ApiController]\n[Route(\"api/admin/devices\")]\n"
              "public class DevicesController : ControllerBase\n{\n"
              "    [HttpPost]\n    public IActionResult Register() => Ok();\n"
              "    [HttpPost(\"x\")]\n    public IActionResult Login() => Ok();\n}\n")
        self.assertEqual(_flagged(_sigs({"Program.cs": prog, "Controllers/D.cs": cs})),
                         {("POST", "/api/admin/devices"), ("POST", "/api/admin/devices/x")})


class I4HealthTests(unittest.TestCase):
    def test_write_routes_never_health(self):
        js = (EXPRESS + "app.put('/api/orders/:id/ready', h);\napp.post('/api/streams/:id/live', h);\n"
              "app.post('/health', h);\napp.put('/status', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})),
                         {("PUT", "/api/orders/:id/ready"), ("POST", "/api/streams/:id/live"),
                          ("POST", "/health"), ("PUT", "/status")})

    def test_fastapi_router_prefix_status(self):
        main = ("from fastapi import FastAPI\nfrom app import orders\napp = FastAPI()\n"
                "app.include_router(orders.router)\n")
        orders = ("from fastapi import APIRouter\n"
                  "router = APIRouter(prefix='/orders/{order_id}')\n\n"
                  "@router.put('/status')\ndef status(order_id: int):\n    return None\n\n"
                  "@router.post('/up')\ndef up(order_id: int):\n    return None\n")
        got = _flagged(_sigs({"app/main.py": main, "app/orders.py": orders}))
        self.assertEqual(got, {("PUT", "/status"), ("POST", "/up")})

    def test_get_health_still_counts_for_no_health(self):
        self.assertTrue(auth.is_health_path("/healthz"))


class I5WebhookTests(unittest.TestCase):
    def test_webhook_management_with_outgoing_hmac(self):
        js = (EXPRESS + "const crypto = require('crypto');\n"
              "function sign(body) { return crypto.createHmac('sha256', k).update(body).digest('hex'); }\n"
              "app.post('/api/webhooks', createWebhook);\n"
              "app.delete('/api/webhooks/:id', deleteWebhook);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})),
                         {("POST", "/api/webhooks"), ("DELETE", "/api/webhooks/:id")})

    def test_verified_incoming_webhook_still_public(self):
        js = (EXPRESS + "app.post('/webhooks/stripe', (req, res) => {\n"
              "  stripe.webhooks.constructEvent(req.body, req.headers['stripe-signature'], k);\n"
              "});\napp.delete('/webhooks/:id', (req, res) => {\n"
              "  stripe.webhooks.constructEvent(req.body, req.headers['stripe-signature'], k);\n"
              "});\n")
        self.assertEqual(_flagged(_sigs({"hooks.js": js})), {("DELETE", "/webhooks/:id")})

    def test_flask_webhook_verified_in_its_handler(self):
        py = ("import stripe\nfrom flask import Flask, request\napp = Flask(__name__)\n\n"
              "@app.post('/webhook')\ndef hook():\n"
              "    sig = request.headers.get('Stripe-Signature')\n"
              "    stripe.Webhook.construct_event(request.data, sig, k)\n    return ''\n\n"
              "@app.post('/api/webhooks')\ndef create():\n    return ''\n")
        self.assertEqual(_flagged(_sigs({"app.py": py})), {("POST", "/api/webhooks")})


class I6InventoryTests(unittest.TestCase):
    def test_named_routers_and_route_chains(self):
        js = (EXPRESS + "const api = express.Router();\napi.post('/orders', h);\n"
              "const userRouter = express.Router();\nuserRouter.delete('/:id', h);\n"
              "app.route('/books').post(h);\n"
              "api.route('/items/:id').put(h).delete(h);\n"
              "router\n  .route('/')\n  .post(validate(x), create)\n  .get(list);\n")
        got = _routes({"app.js": js})
        for want in (("POST", "/orders"), ("DELETE", "/:id"), ("POST", "/books"),
                     ("PUT", "/items/:id"), ("DELETE", "/items/:id"), ("POST", "/"), ("GET", "/")):
            self.assertIn(want, got)

    def test_frontend_api_client_is_not_a_route(self):
        js = "const api = axios.create();\napi.post('/orders', data);\n"
        self.assertEqual(_routes({"src/client.js": js}), set())

    def test_route_chain_auth(self):
        js = ("const express = require('express');\nconst router = express.Router();\n"
              "router\n  .route('/')\n  .post(auth('manageUsers'), validate(v.createUser), c.createUser)\n"
              "  .get(c.getUsers);\nrouter\n  .route('/:userId')\n  .delete(c.deleteUser);\n"
              "module.exports = router;\n")
        app = (EXPRESS + "const users = require('./routes/user.route');\napp.use('/users', users);\n"
               "app.listen(3000);\n")
        sigs = _sigs({"app.js": app, "routes/user.route.js": js})
        self.assertEqual(_flagged(sigs), {("DELETE", "/:userId")})


_FASTAPI_MAIN = ("from fastapi import Depends, FastAPI\nfrom app.api.routes import items\n"
                 "from app.deps import get_current_user\napp = FastAPI()\n"
                 "app.include_router(items.router%s)\n")
_FASTAPI_ITEMS = ("from fastapi import APIRouter\nrouter = APIRouter()\n\n"
                  "@router.post('/items')\ndef create(payload: dict):\n    return payload\n")
_I7_SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from dfscan.auth.core import Resolver
from dfscan.imports import _read
from dfscan.routes import find_routes
from dfscan.walker import walk
fs = walk(sys.argv[2])
res = Resolver(fs, _read)
out = sorted([r["file"], r["method"], r["path"]] + list(res.resolve(r)) for r in find_routes(fs)
             if r["method"] == "POST")
print(json.dumps(out))
"""


class I7DeterminismTests(unittest.TestCase):
    def test_fastapi_absolute_import_per_service_and_seed_independent(self):
        files = {"services/a/app/main.py": _FASTAPI_MAIN % ", dependencies=[Depends(get_current_user)]",
                 "services/a/app/api/routes/items.py": _FASTAPI_ITEMS,
                 "services/a/app/api/__init__.py": "", "services/a/app/api/routes/__init__.py": "",
                 "services/b/app/main.py": _FASTAPI_MAIN % "",
                 "services/b/app/api/routes/items.py": _FASTAPI_ITEMS,
                 "services/b/app/api/__init__.py": "", "services/b/app/api/routes/__init__.py": ""}
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                _write(root, rel, text)
            script = os.path.join(root, "probe_i7.py")
            with open(script, "w") as fh:
                fh.write(_I7_SCRIPT)
            outs = []
            for seed in ("1", "2", "3", "7"):
                env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
                p = subprocess.run([sys.executable, script, _SCRIPTS, root], env=env,
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(p.returncode, 0, p.stderr)
                outs.append(json.loads(p.stdout))
        for o in outs[1:]:
            self.assertEqual(o, outs[0])
        states = {r[0].split("/")[1]: (r[3], r[4]) for r in outs[0]}
        self.assertEqual(states["a"][0], "protected", states)
        self.assertEqual(states["b"][0], "unprotected", states)
        self.assertIn("services/b/", states["b"][1])


class I8NestDecoratorTests(unittest.TestCase):
    def test_non_auth_decorators(self):
        main = ("async function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n"
                "  await app.listen(3000);\n}\n")
        ctrl = ("@Controller('orders')\nexport class OrdersController {\n"
                "  @ApiOperation({ summary: 'Create an order for the author' })\n  @Post()\n  create() {}\n\n"
                "  @UseGuards(ThrottlerGuard)\n  @Post('t')\n  t() {}\n\n"
                "  @ApiBearerAuth()\n  @Post('b')\n  b() {}\n\n"
                "  @UseGuards(AuthGuard('jwt'))\n  @Post('p')\n  p() {}\n}\n")
        sigs = _sigs({"src/main.ts": main, "src/orders.controller.ts": ctrl})
        self.assertEqual(_flagged(sigs), {("POST", "/orders"), ("POST", "/orders/t"),
                                          ("POST", "/orders/b")})


class I1NestAppScopeTests(unittest.TestCase):
    def test_global_guard_of_another_app(self):
        main = ("async function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n"
                "  await app.listen(3000);\n}\n")
        mod = ("@Module({\n  providers: [\n    { provide: APP_GUARD, useClass: JwtAuthGuard },\n"
               "  ],\n})\nexport class AppModule {}\n")
        ctrl = "@Controller('orders')\nexport class OrdersController {\n  @Post()\n  create() {}\n}\n"
        files = {"apps/admin/src/main.ts": main, "apps/admin/src/app.module.ts": mod,
                 "apps/admin/src/users.controller.ts": ctrl.replace("orders", "users"),
                 "apps/shop/src/main.ts": main,
                 "apps/shop/src/app.module.ts": "@Module({})\nexport class AppModule {}\n",
                 "apps/shop/src/orders.controller.ts": ctrl}
        sigs = _sigs(files)
        self.assertEqual(_flagged(sigs), {("POST", "/orders")})
        self.assertFalse(sigs[0].get("uncertain_reason"))


def _go_pkg(i):
    return ('package pkg%d\n\nfunc RegisterRoutes(g *gin.RouterGroup) {\n%s}\n'
            % (i, "".join('\tg.POST("/p%d/w%d", h)\n' % (i, j) for j in range(5))))


class I10PerformanceTests(unittest.TestCase):
    N = 400
    LIMIT = 10.0  # seconds (about 1 s now); the quadratic caller search took 4-14 s at N=400

    def _timed_resolve(self, files):
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                _write(root, rel, text)
            fs = walk(root)
            start = time.perf_counter()
            res = Resolver(fs, _read)
            got = [res.resolve(r) for r in find_routes(fs) if r["method"] == "POST"]
            return time.perf_counter() - start, got

    def test_go_route_functions_linear(self):
        n = self.N
        files = {"go.mod": "module example.com/x\n"}
        imports = "".join('\t"example.com/x/pkg%d"\n' % i for i in range(n))
        calls = "".join("\tpkg%d.RegisterRoutes(api)\n" % i for i in range(n))
        files["main.go"] = ("package main\n\nimport (\n%s)\n\nfunc main() {\n\tr := gin.Default()\n"
                            "\tapi := r.Group(\"/api\")\n\tapi.Use(AuthRequired())\n%s\tr.Run()\n}\n"
                            % (imports, calls))
        for i in range(n):
            files["pkg%d/routes.go" % i] = _go_pkg(i)
        took, got = self._timed_resolve(files)
        self.assertEqual(len(got), 5 * n)
        self.assertTrue(all(v.state == "protected" for v in got), got[:3])
        self.assertLess(took, self.LIMIT)

    def test_express_require_callers_linear(self):
        n = self.N
        files = {"app.js": EXPRESS + "app.use(requireAuth);\n"
                 + "".join("require('./routes/r%d')(app);\n" % i for i in range(n))
                 + "app.listen(3000);\n"}
        for i in range(n):
            files["routes/r%d.js" % i] = ("module.exports = function (app) {\n"
                                          + "".join("  app.post('/r%d/w%d', h);\n" % (i, j)
                                                    for j in range(5)) + "};\n")
        took, got = self._timed_resolve(files)
        self.assertEqual(len(got), 5 * n)
        self.assertTrue(all(v.state == "protected" for v in got), got[:3])
        self.assertLess(took, self.LIMIT)


class MinorTests(unittest.TestCase):
    def test_m1_express_jwt_unless(self):
        js = (EXPRESS + "app.use(jwt({ secret: k }).unless({ path: ['/login', '/api/orders'] }));\n"
              "app.post('/api/orders', h);\napp.post('/api/items', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/api/orders")})

    def test_m3_go_import_alias(self):
        main = ('package main\n\nimport (\n\tp7 "example.com/x/pkg7"\n)\n\nfunc main() {\n'
                '\tr := gin.Default()\n\tapi := r.Group("/api")\n\tapi.Use(AuthRequired())\n'
                '\tp7.RegisterRoutes(api)\n\tr.Run()\n}\n')
        pkg = 'package pkg7\n\nfunc RegisterRoutes(g *gin.RouterGroup) {\n\tg.POST("/x", h)\n}\n'
        got = _verdicts({"go.mod": "module example.com/x\n", "main.go": main,
                         "pkg7/routes.go": pkg})
        self.assertEqual(got[("POST", "/x")].state, "protected", got)

    def test_m6_auth_prefixed_names(self):
        js = (EXPRESS + "app.post('/orders', validate(authValidation.create), h);\n"
              "app.post('/books', validate(authorValidation.create), h);\n"
              "app.post('/items', auth('manageItems'), h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/orders"), ("POST", "/books")})
        # node-express-boilerplate auth.route.js: no longer "auth at the route", but a
        # refresh-tokens endpoint is public by design like /refresh.
        js = (EXPRESS + "app.post('/refresh-tokens', validate(authValidation.refreshTokens), "
              "authController.refreshTokens);\napp.post('/api/devices/refresh-tokens', h);\n")
        self.assertEqual(_flagged(_sigs({"app.js": js})), {("POST", "/api/devices/refresh-tokens")})


if __name__ == "__main__":
    unittest.main()
