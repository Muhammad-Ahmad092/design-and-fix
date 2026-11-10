from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Dict, List, Set, Tuple

from helpers import FIXTURES, REPO
from dfscan import registry
from dfscan.report import scan
from dfscan.routes import find_routes, is_health_path, route_signals
from dfscan.stack import detect_stack
from dfscan.walker import walk

EVALS = os.path.join(REPO, "evals")
FRAMEWORK_FIXTURES = ("spring", "aspnet", "rails", "laravel")


def _tree(root: str, files: Dict[str, str]) -> None:
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def _routes(files: Dict[str, str]) -> List[dict]:
    with tempfile.TemporaryDirectory() as root:
        _tree(root, files)
        return find_routes(walk(root))


def _signals(files: Dict[str, str]) -> List[dict]:
    with tempfile.TemporaryDirectory() as root:
        _tree(root, files)
        return scan(root, use_git=False)["signals"]


def _hits(files: Dict[str, str], rule: str) -> List[dict]:
    return [s for s in _signals(files) if s["rule"] == rule]


def _table(routes: List[dict]) -> Set[Tuple[str, str, str, bool]]:
    return {(r["method"], r["path"], r["framework"], r["auth_detected"]) for r in routes}


def _fixture_routes(name: str) -> List[dict]:
    return find_routes(walk(os.path.join(FIXTURES, name)))


# ------------------------------------------------------------------------------- fixtures
class FixtureRouteTests(unittest.TestCase):
    def test_spring_routes(self):
        got = _table(_fixture_routes("spring"))
        self.assertEqual(got, {
            ("GET", "/api/orders/{id}", "spring", False),
            ("POST", "/api/orders", "spring", False),
            ("DELETE", "/api/orders/{id}", "spring", True),
            ("GET", "/health", "spring", False),
        })

    def test_aspnet_routes(self):
        got = _table(_fixture_routes("aspnet"))
        self.assertEqual(got, {
            ("GET", "/api/orders/{id}", "aspnetcore", False),
            ("POST", "/api/orders", "aspnetcore", False),
            ("DELETE", "/api/orders/{id}", "aspnetcore", True),
            ("GET", "/health", "aspnetcore", False),
        })

    def test_rails_routes(self):
        got = _table(_fixture_routes("rails"))
        self.assertEqual(got, {
            ("GET", "/up", "rails", False),
            ("GET", "/orders", "rails", False),
            ("GET", "/orders/:id", "rails", False),
            ("POST", "/orders", "rails", False),
            ("PATCH", "/orders/:id", "rails", False),
            ("DELETE", "/orders/:id", "rails", False),
            ("POST", "/payments", "rails", True),
        })

    def test_laravel_routes(self):
        got = _table(_fixture_routes("laravel"))
        self.assertEqual(got, {
            ("GET", "/health", "laravel", False),
            ("GET", "/orders/{id}", "laravel", False),
            ("POST", "/orders", "laravel", False),
            ("DELETE", "/orders/{id}", "laravel", True),
        })

    def test_route_lines_and_files(self):
        r = [x for x in _fixture_routes("spring") if x["method"] == "POST"][0]
        self.assertEqual((r["file"], r["line"]),
                         ("src/main/java/com/example/shop/OrderController.java", 36))
        r = [x for x in _fixture_routes("rails") if x["path"] == "/payments"][0]
        self.assertEqual((r["file"], r["line"]), ("config/routes.rb", 6))

    def test_commented_routes_not_listed(self):
        for name, path in (("spring", "/api/orders/commented-out"), ("aspnet", "/api/orders/{id}"),
                           ("rails", "/commented"), ("laravel", "/legacy")):
            routes = _fixture_routes(name)
            if name == "aspnet":
                self.assertFalse([r for r in routes if r["method"] == "PUT"], name)
            else:
                self.assertFalse([r for r in routes if r["path"] == path], name)

    def test_health_routes_recognised(self):
        for name in FRAMEWORK_FIXTURES:
            self.assertTrue(any(is_health_path(r["path"]) for r in _fixture_routes(name)), name)
        self.assertTrue(is_health_path("/up"))
        self.assertFalse(is_health_path("/orders/up"))

    def test_frameworks_detected(self):
        expected = {"spring": "spring-boot", "aspnet": "aspnetcore", "rails": "rails",
                    "laravel": "laravel"}
        for name, fw in expected.items():
            self.assertIn(fw, detect_stack(walk(os.path.join(FIXTURES, name)))[1], name)
            self.assertIn(fw, scan(os.path.join(FIXTURES, name), use_git=False)["frameworks"], name)

    def test_expected_eval_entries(self):
        with open(os.path.join(EVALS, "scanner-expected.json"), encoding="utf-8") as fh:
            exp = json.load(fh)
        for name in FRAMEWORK_FIXTURES:
            r = scan(os.path.join(FIXTURES, name), use_git=False)
            got = {(s["rule"], s["file"]) for s in r["signals"]}
            self.assertEqual({tuple(x) for x in exp[name]["must_find"]} - got, set(), name)
            self.assertEqual({tuple(x) for x in exp[name]["must_not_find"]} & got, set(), name)
            self.assertEqual(r["summary"]["invalid_signals"], 0, name)

    def test_unauth_signal_only_for_unauthed_write(self):
        root = os.path.join(FIXTURES, "laravel")
        files = walk(root)
        sigs = [s for s in route_signals(find_routes(files), files) if s.rule == "unauth-write-route"]
        self.assertEqual([s.snippet for s in sigs], ["POST /orders"])
        # The fixture has no RouteServiceProvider / bootstrap/app.php: the group middleware
        # that loads routes/api.php is unknown, so the route is unresolved (low confidence).
        self.assertEqual(sigs[0].confidence, "low")
        self.assertIn("RouteServiceProvider", sigs[0].uncertain_reason)


# ------------------------------------------------------------------------------- Spring
class SpringTests(unittest.TestCase):
    def test_request_mapping_methods_and_class_auth(self):
        java = (
            "@RestController\n@Secured(\"ROLE_ADMIN\")\n@RequestMapping(value = \"/admin\")\n"
            "public class AdminController {\n"
            "    @RequestMapping(path = \"/jobs\", method = {RequestMethod.POST, RequestMethod.PUT})\n"
            "    public void jobs() {}\n"
            "    @GetMapping(value = {\"/a\", \"/b\"}, produces = \"application/json\")\n"
            "    public String ab() { return \"x\"; }\n"
            "}\n")
        got = _table(_routes({"src/main/java/Admin.java": java}))
        self.assertEqual(got, {("POST", "/admin/jobs", "spring", True),
                               ("PUT", "/admin/jobs", "spring", True),
                               ("GET", "/admin/a", "spring", True),
                               ("GET", "/admin/b", "spring", True)})

    def test_kotlin_and_feign_client(self):
        kt = (
            "@RestController\nclass ItemController(private val repo: Repo) {\n"
            "    @PostMapping(\"/items\") fun create(@RequestBody item: Item) = repo.save(item)\n"
            "}\n")
        feign = (
            "@FeignClient(name = \"billing\")\npublic interface BillingClient {\n"
            "    @PostMapping(\"/charges\")\n    Charge charge(Charge c);\n}\n")
        got = _table(_routes({"src/main/kotlin/Items.kt": kt, "src/main/java/Billing.java": feign}))
        self.assertEqual(got, {("POST", "/items", "spring", False)})

    def test_annotation_in_string_or_javadoc_ignored(self):
        java = (
            "public class Docs {\n"
            "    /** Use @PostMapping(\"/x\") on handlers. */\n"
            "    String hint = \"see @PostMapping for details\";\n"
            "}\n")
        self.assertEqual(_routes({"src/Docs.java": java}), [])

    def test_nested_type_before_handlers_keeps_controller_prefix_and_auth(self):
        java = (
            "@RestController\n@PreAuthorize(\"isAuthenticated()\")\n@RequestMapping(\"/s\")\n"
            "public class SettingsController {\n"
            "    public record Dto(String name) {}\n"
            "    static class Helper { int x; }\n"
            "    enum Mode { A, B }\n"
            "    String block = \"\"\"\n        { unbalanced\n        \"\"\";\n"
            "    @PostMapping(\"/after-record\")\n    public void save(@RequestBody Dto d) {}\n"
            "}\n"
            "@RestController\nclass Open {\n    @PostMapping(\"/open\")\n    void o() {}\n}\n")
        got = _table(_routes({"src/S.java": java}))
        self.assertEqual(got, {("POST", "/s/after-record", "spring", True),
                               ("POST", "/open", "spring", False)})

    def test_global_security_chain_resolved_by_auth_adapter(self):
        ctrl = ("@RestController\npublic class C {\n    @PostMapping(\"/orders\")\n"
                "    public void create() {}\n}\n")
        sec = ("public class Sec {\n    SecurityFilterChain chain(HttpSecurity http) {\n"
               "        return http.authorizeHttpRequests(a -> a.anyRequest().authenticated()).build();\n"
               "    }\n}\n")
        # anyRequest().authenticated() covers the route (dfscan/auth/spring.py): no signal.
        self.assertEqual(_hits({"src/C.java": ctrl, "src/Sec.java": sec}, "unauth-write-route"), [])
        alone = _hits({"src/C.java": ctrl}, "unauth-write-route")
        self.assertEqual([h["snippet"] for h in alone], ["POST /orders"])


# ------------------------------------------------------------------------------- ASP.NET
class AspNetTests(unittest.TestCase):
    def test_allow_anonymous_and_absolute_template(self):
        cs = (
            "[Authorize]\n[Route(\"api/[controller]\")]\npublic class AccountController : ControllerBase\n{\n"
            "    [HttpPost(\"refresh\")]\n    public IActionResult Refresh() => Ok();\n"
            "    [HttpPost(\"/hooks/stripe\"), AllowAnonymous]\n    public IActionResult Hook() => Ok();\n"
            "    [HttpGet(\"[action]\")]\n    public IActionResult Profile() => Ok();\n"
            "}\n")
        got = _table(_routes({"Controllers/AccountController.cs": cs}))
        self.assertEqual(got, {("POST", "/api/account/refresh", "aspnetcore", True),
                               ("POST", "/hooks/stripe", "aspnetcore", False),
                               ("GET", "/api/account/profile", "aspnetcore", True)})

    def test_minimal_api_groups(self):
        cs = (
            "var app = builder.Build();\n"
            "var api = app.MapGroup(\"/api\");\n"
            "var admin = api.MapGroup(\"/admin\").RequireAuthorization();\n"
            "api.MapPost(\"/orders\", (Order o) => Results.Created());\n"
            "admin.MapDelete(\"/orders/{id}\", (int id) => Results.NoContent());\n"
            "app.MapPut(\"/items/{id}\", Update).RequireAuthorization(\"admin\");\n"
            "// app.MapPost(\"/commented\", H);\n"
            "app.Run();\n")
        got = _table(_routes({"Program.cs": cs}))
        self.assertEqual(got, {("POST", "/api/orders", "aspnetcore", False),
                               ("DELETE", "/api/admin/orders/{id}", "aspnetcore", True),
                               ("PUT", "/items/{id}", "aspnetcore", True)})

    def test_nested_class_before_actions_keeps_controller_route_and_auth(self):
        cs = (
            "[Authorize]\n[Route(\"api/[controller]\")]\npublic class ShopController : ControllerBase\n{\n"
            "    public class Req { public string Path = @\"C:\\{dir\"; }\n"
            "    public enum Kind { A, B }\n"
            "    [HttpPost(\"buy\")]\n    public IActionResult Buy() => Ok();\n"
            "}\n")
        got = _table(_routes({"Controllers/ShopController.cs": cs}))
        self.assertEqual(got, {("POST", "/api/shop/buy", "aspnetcore", True)})

    def test_fallback_policy_resolved_by_auth_adapter(self):
        prog = ("builder.Services.AddAuthorization(o => o.FallbackPolicy = new "
                "AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build());\n"
                "app.MapPost(\"/orders\", Create);\n")
        # FallbackPolicy covers every endpoint (dfscan/auth/aspnet.py): no signal.
        self.assertEqual(_hits({"Program.cs": prog}, "unauth-write-route"), [])
        alone = _hits({"Program.cs": "app.MapPost(\"/orders\", Create);\n"}, "unauth-write-route")
        self.assertEqual([h["snippet"] for h in alone], ["POST /orders"])


# ------------------------------------------------------------------------------- Rails
_APP = "class ApplicationController < ActionController::Base\nend\n"


class RailsTests(unittest.TestCase):
    def test_resources_expand_to_five_actions(self):
        routes = _routes({"config/routes.rb": "Rails.application.routes.draw do\n  resources :photos\nend\n"})
        self.assertEqual({(r["method"], r["path"]) for r in routes},
                         {("GET", "/photos"), ("GET", "/photos/:id"), ("POST", "/photos"),
                          ("PATCH", "/photos/:id"), ("DELETE", "/photos/:id")})

    def test_only_except_namespace_and_nesting(self):
        rb = (
            "Rails.application.routes.draw do\n"
            "  namespace :api do\n"
            "    resources :orders, except: [:destroy, :update] do\n"
            "      member do\n        post :refund\n      end\n"
            "      resources :items, only: :index\n"
            "    end\n"
            "  end\n"
            "  match 'search', to: 'search#run', via: [:get, :post]\n"
            "end\n")
        routes = _routes({"config/routes.rb": rb})
        self.assertEqual({(r["method"], r["path"]) for r in routes}, {
            ("GET", "/api/orders"), ("GET", "/api/orders/:id"), ("POST", "/api/orders"),
            ("POST", "/api/orders/:id/refund"), ("GET", "/api/orders/:order_id/items"),
            ("GET", "/search"), ("POST", "/search")})

    def test_parent_controller_auth_and_skip(self):
        files = {
            "config/routes.rb": ("Rails.application.routes.draw do\n"
                                 "  resources :sessions, only: [:create]\n"
                                 "  resources :notes, only: [:create, :destroy]\n"
                                 "  authenticate :user do\n    post 'export', to: 'exports#run'\n  end\n"
                                 "end\n"),
            "app/controllers/application_controller.rb": (
                "class ApplicationController < ActionController::Base\n"
                "  before_action :authenticate_user!\nend\n"),
            "app/controllers/sessions_controller.rb": (
                "class SessionsController < ApplicationController\n"
                "  skip_before_action :authenticate_user!, only: [:create]\nend\n"),
            "app/controllers/notes_controller.rb": "class NotesController < ApplicationController\nend\n",
        }
        got = {(r["method"], r["path"], r["auth_detected"]) for r in _routes(files)}
        self.assertEqual(got, {("POST", "/sessions", False), ("POST", "/notes", True),
                               ("DELETE", "/notes/:id", True), ("POST", "/export", True)})

    def test_singular_resource_uses_plural_controller(self):
        files = {
            "config/routes.rb": ("Rails.application.routes.draw do\n"
                                 "  resource :setting, only: [:update]\n"
                                 "  resource :profile, only: [:update], controller: 'me'\n"
                                 "end\n"),
            "app/controllers/settings_controller.rb": (
                "class SettingsController < ActionController::Base\n"
                "  before_action :authenticate_user!\nend\n"),
            "app/controllers/me_controller.rb": "class MeController < ActionController::Base\nend\n",
        }
        got = {(r["method"], r["path"], r["auth_detected"]) for r in _routes(files)}
        self.assertEqual(got, {("PATCH", "/setting", True), ("PATCH", "/profile", False)})

    def test_parent_controller_resolved_in_enclosing_module(self):
        files = {
            "config/routes.rb": ("Rails.application.routes.draw do\n"
                                 "  namespace :admin do\n    resources :orders, only: [:create]\n  end\n"
                                 "  resources :notes, only: [:create]\n"
                                 "end\n"),
            "app/controllers/base_controller.rb": "class BaseController < ActionController::Base\nend\n",
            "app/controllers/admin/base_controller.rb": (
                "module Admin\n  class BaseController < ActionController::Base\n"
                "    before_action :authenticate_admin!\n  end\nend\n"),
            "app/controllers/admin/orders_controller.rb": (
                "module Admin\n  class OrdersController < BaseController\n  end\nend\n"),
            "app/controllers/notes_controller.rb": "class NotesController < BaseController\nend\n",
        }
        got = {(r["method"], r["path"], r["auth_detected"]) for r in _routes(files)}
        self.assertEqual(got, {("POST", "/admin/orders", True), ("POST", "/notes", False)})

    def test_hash_rocket_controller_and_brace_collection(self):
        rb = ("Rails.application.routes.draw do\n"
              "  resources :photos, :controller => 'images', only: [:index] do\n"
              "    collection { get :search; post :bulk }\n"
              "    member { post :like }\n"
              "  end\n"
              "end\n")
        routes = _routes({"config/routes.rb": rb})
        self.assertEqual({(r["method"], r["path"]) for r in routes}, {
            ("GET", "/photos"), ("GET", "/photos/search"), ("POST", "/photos/bulk"),
            ("POST", "/photos/:id/like")})
        self.assertEqual({r["line"] for r in routes if r["path"] != "/photos"}, {3, 4})

    def test_only_routes_file_is_read(self):
        self.assertEqual(_routes({"app/models/order.rb": "get 'x'\npost 'y'\n",
                                  "app/controllers/application_controller.rb": _APP}), [])


# ------------------------------------------------------------------------------- Laravel
class LaravelTests(unittest.TestCase):
    def test_groups_prefix_and_resources(self):
        php = (
            "<?php\n"
            "Route::prefix('v1')->middleware(['auth', 'verified'])->group(function () {\n"
            "    Route::apiResource('photos', PhotoController::class)->only(['index', 'store']);\n"
            "});\n"
            "Route::group(['prefix' => 'admin', 'middleware' => 'auth:api'], function () {\n"
            "    Route::put('/users/{id}', [UserController::class, 'update']);\n"
            "});\n"
            "Route::middleware('auth')->post('/tokens/revoke', [TokenController::class, 'revoke']);\n"
            "Route::match(['get', 'post'], '/contact', [ContactController::class, 'handle']);\n"
            "Route::post('/login', [AuthController::class, 'login'])->withoutMiddleware('auth');\n"
            "# Route::post('/hashed', H::class);\n")
        got = _table(_routes({"routes/web.php": php}))
        self.assertEqual(got, {
            ("GET", "/v1/photos", "laravel", True), ("POST", "/v1/photos", "laravel", True),
            ("PUT", "/admin/users/{id}", "laravel", True),
            ("POST", "/tokens/revoke", "laravel", True),
            ("GET", "/contact", "laravel", False), ("POST", "/contact", "laravel", False),
            ("POST", "/login", "laravel", False)})

    def test_typed_closure_group_and_resource_array_options(self):
        php = (
            "<?php\n"
            "Route::middleware('auth')->group(function (): void {\n"
            "    Route::post('/secure', [S::class, 'store']);\n"
            "});\n"
            "Route::resource('photos', PhotoController::class, ['only' => ['index', 'store']]);\n"
            "Route::apiResource('tags', TagController::class, ['except' => ['update', 'destroy', 'show']]);\n")
        got = _table(_routes({"routes/web.php": php}))
        self.assertEqual(got, {
            ("POST", "/secure", "laravel", True),
            ("GET", "/photos", "laravel", False), ("POST", "/photos", "laravel", False),
            ("GET", "/tags", "laravel", False), ("POST", "/tags", "laravel", False)})

    def test_laravel11_health_option(self):
        php = ("<?php\nreturn Application::configure(basePath: dirname(__DIR__))\n"
               "    ->withRouting(api: __DIR__.'/../routes/api.php', health: '/up')\n    ->create();\n")
        routes = _routes({"bootstrap/app.php": php})
        self.assertEqual(_table(routes), {("GET", "/up", "laravel", False)})


# ------------------------------------------------------------------------------- 1.0.2 review B
class SpringReviewTests(unittest.TestCase):
    def test_i7_annotation_in_class_header_keeps_prefix_and_auth(self):
        kt = ("@RestController\n@RequestMapping(\"/api\")\n@PreAuthorize(\"isAuthenticated()\")\n"
              "class OrderController @Autowired constructor(private val s: OrderService) {\n"
              "    @PostMapping(\"/orders\") fun create() {}\n}\n")
        java = ("@RestController\n@Secured(\"ROLE_ADMIN\")\n@RequestMapping(\"/admin\")\n"
                "public class JobController extends Base<@NonNull Thing> {\n"
                "    @PostMapping(\"/jobs\")\n    public void run() {}\n}\n")
        got = _table(_routes({"src/C.kt": kt, "src/J.java": java}))
        self.assertEqual(got, {("POST", "/api/orders", "spring", True),
                               ("POST", "/admin/jobs", "spring", True)})

    def test_i8_static_import_request_method(self):
        java = ("import static org.springframework.web.bind.annotation.RequestMethod.*;\n"
                "@RestController\npublic class UsersApi {\n"
                "    @RequestMapping(path = \"/users\", method = POST)\n    public void a() {}\n"
                "    @RequestMapping(value = \"/x\", method = {GET, DELETE})\n    public void b() {}\n"
                "    @RequestMapping(\"/any\")\n    public void c() {}\n}\n")
        got = {(r["method"], r["path"]) for r in _routes({"src/U.java": java})}
        self.assertEqual(got, {("POST", "/users"), ("GET", "/x"), ("DELETE", "/x"),
                               ("ANY", "/any"), ("POST", "/any")})  # 1.1.0 review M5

    def test_m12_all_class_prefixes_constants_and_permit_all(self):
        java = ("@RestController\n@PreAuthorize(\"hasRole('USER')\")\n"
                "@RequestMapping({\"/api/orders\", \"/v2/orders\"})\n"
                "public class OrderController {\n"
                "    private static final String SUB = \"/b\";\n"
                "    @PostMapping(path = SUB + \"/c\")\n    public void b() {}\n"
                "    @PostMapping(\"/open\") @PreAuthorize(\"permitAll()\")\n    public void o() {}\n"
                "    @PostMapping(Paths.OTHER)\n    public void x() {}\n}\n"
                "@RestController\n@Secured(\"IS_AUTHENTICATED_ANONYMOUSLY\")\nclass Pub {\n"
                "    @PostMapping(\"/pub\")\n    void p() {}\n}\n")
        got = _table(_routes({"src/O.java": java}))
        self.assertEqual(got, {
            ("POST", "/api/orders/b/c", "spring", True), ("POST", "/v2/orders/b/c", "spring", True),
            ("POST", "/api/orders/open", "spring", False), ("POST", "/v2/orders/open", "spring", False),
            ("POST", "/api/orders/{OTHER}", "spring", True),
            ("POST", "/v2/orders/{OTHER}", "spring", True),
            ("POST", "/pub", "spring", False)})


class AspNetReviewTests(unittest.TestCase):
    _CTRL = ("[Route(\"api/v1/[controller]\")]\n"
             "public class OrdersController(IOrderService svc) : BaseApiController\n{\n"
             "    [HttpPost]\n    public IActionResult Create() => Ok();\n"
             "    [HttpDelete(\"{id}\")]\n    public IActionResult Delete(int id) => Ok();\n}\n")
    _BASE = ("[Authorize]\n[ApiController]\npublic abstract class BaseApiController : ControllerBase\n"
             "{\n}\n")

    def test_i12_authorize_on_base_controller_inherited(self):
        expected = {("POST", "/api/v1/orders", "aspnetcore", True),
                    ("DELETE", "/api/v1/orders/{id}", "aspnetcore", True)}
        same = _table(_routes({"Controllers/OrdersController.cs": self._BASE + self._CTRL}))
        self.assertEqual(same, expected)
        other = _table(_routes({"Controllers/OrdersController.cs": self._CTRL,
                                "Controllers/BaseApiController.cs": self._BASE}))
        self.assertEqual(other, expected)
        # Two levels, and the route template inherited from the base.
        mid = ("public class SecureBase : BaseApiController { }\n"
               "[Route(\"api/[controller]\")]\n" + self._BASE.replace("[Authorize]\n", ""))
        leaf = ("public class ItemsController : SecureBase\n{\n"
                "    [HttpPost(\"x\")]\n    public IActionResult X() => Ok();\n}\n")
        got = _table(_routes({"Controllers/ItemsController.cs": leaf,
                              "Controllers/Base.cs": mid.replace("public abstract class",
                                                                 "[Authorize]\npublic abstract class")}))
        self.assertEqual(got, {("POST", "/api/items/x", "aspnetcore", True)})

    def test_i12_ignored_base_controller_not_read(self):
        got = _table(_routes({".gitignore": "Controllers/Base/\n",
                              "Controllers/OrdersController.cs": self._CTRL,
                              "Controllers/Base/BaseApiController.cs": self._BASE}))
        self.assertEqual({a for _, _, _, a in got}, {False})

    def test_m11_inline_group_accept_verbs_map_methods(self):
        prog = ("var app = builder.Build();\n"
                "app.MapGroup(\"/pub\").MapPost(\"/inline\", H);\n"
                "app.MapGroup(\"/sec\").RequireAuthorization().MapPut(\"/x\", H);\n"
                "app.MapMethods(\"/m\", new[] { \"POST\", \"PUT\" }, H);\n"
                "app.MapMethods(\"/n\", new[] { HttpMethods.Delete }, H).RequireAuthorization();\n")
        ctrl = ("[Route(\"api/bulk\")]\npublic class BulkController : ControllerBase\n{\n"
                "    [AcceptVerbs(\"POST\", \"PUT\")]\n    [Route(\"go\")]\n"
                "    public IActionResult Go() => Ok();\n}\n")
        got = _table(_routes({"Program.cs": prog, "Controllers/BulkController.cs": ctrl}))
        self.assertEqual(got, {
            ("POST", "/pub/inline", "aspnetcore", False), ("PUT", "/sec/x", "aspnetcore", True),
            ("POST", "/m", "aspnetcore", False), ("PUT", "/m", "aspnetcore", False),
            ("DELETE", "/n", "aspnetcore", True),
            ("POST", "/api/bulk/go", "aspnetcore", False), ("PUT", "/api/bulk/go", "aspnetcore", False)})


_DRAW = "Rails.application.routes.draw do\n%send\n"


class RailsReviewTests(unittest.TestCase):
    def test_i9_percent_and_hash_rocket_option_lists(self):
        rb = _DRAW % ("  resources :tags, only: %w[index create]\n"
                      "  resources :votes, only: %i(create)\n"
                      "  resources(:books, :only => [:create])\n"
                      "  resources :pages, except: %i[index show update destroy]\n")
        files = {"config/routes.rb": rb,
                 "app/controllers/votes_controller.rb": (
                     "class VotesController < ActionController::Base\n"
                     "  before_action :authenticate_user!, only: %i(update)\nend\n")}
        got = {(r["method"], r["path"], r["auth_detected"]) for r in _routes(files)}
        self.assertEqual(got, {("GET", "/tags", False), ("POST", "/tags", False),
                               ("POST", "/votes", False), ("POST", "/books", False),
                               ("POST", "/pages", False)})

    def test_i10_module_symbol(self):
        rb = _DRAW % ("  scope module: :admin do\n    resources :reports, only: [:create]\n  end\n"
                      "  resources :audits, only: [:create], module: :admin\n"
                      "  namespace :staff, module: :admin do\n    resources :notes, only: [:create]\n"
                      "  end\n")
        auth = ("module Admin\n  class %sController < ApplicationController\n"
                "    before_action :authenticate_admin!\n  end\nend\n")
        files = {"config/routes.rb": rb,
                 "app/controllers/admin/reports_controller.rb": auth % "Reports",
                 "app/controllers/admin/audits_controller.rb": auth % "Audits",
                 "app/controllers/admin/notes_controller.rb": auth % "Notes",
                 "app/controllers/reports_controller.rb": "class ReportsController < ActionController::Base\nend\n"}
        got = {(r["method"], r["path"], r["auth_detected"]) for r in _routes(files)}
        self.assertEqual(got, {("POST", "/reports", True), ("POST", "/audits", True),
                               ("POST", "/staff/notes", True)})

    def test_i11_concerns_replayed_not_emitted(self):
        rb = _DRAW % ("  concern :commentable do\n    resources :comments, only: [:create]\n  end\n"
                      "  concern :likeable do\n    post :like, on: :member\n  end\n"
                      "  resources :posts, concerns: :commentable, only: []\n"
                      "  resources :photos, only: [], concerns: [:commentable, :likeable] do\n"
                      "    get :raw, on: :member\n  end\n"
                      "  resources :videos, only: [] do\n    concerns :likeable\n  end\n")
        routes = _routes({"config/routes.rb": rb})
        self.assertEqual({(r["method"], r["path"]) for r in routes}, {
            ("POST", "/posts/:post_id/comments"), ("POST", "/photos/:photo_id/comments"),
            ("POST", "/photos/:id/like"), ("GET", "/photos/:id/raw"), ("POST", "/videos/:id/like")})
        self.assertEqual([r["line"] for r in routes if r["path"] == "/posts/:post_id/comments"], [8])

    def test_m9_route_shapes(self):
        rb = _DRAW % ("  resources :orders, only: [] do\n"
                      "    post :refund, on: :member\n    post :bulk, on: :collection\n  end\n"
                      "  match 'hook' => 'hooks#receive', :via => :post\n"
                      "  resources :articles, path: 'blog', only: [:create]\n"
                      "  resources :users, path: '', only: [:update]\n")
        got = {(r["method"], r["path"]) for r in _routes({"config/routes.rb": rb})}
        self.assertEqual(got, {("POST", "/orders/:id/refund"), ("POST", "/orders/bulk"),
                               ("POST", "/hook"), ("POST", "/blog"), ("PATCH", "/:id")})

    def test_m9_auth_markers(self):
        rb = _DRAW % "  resources :a, :b, :c, :d, only: [:create]\n"
        files = {
            "config/routes.rb": rb,
            "app/controllers/a_controller.rb": ("class AController < ActionController::Base\n"
                                                "  before_action :set_locale, :authenticate_user!\nend\n"),
            "app/controllers/b_controller.rb": ("class BController < ActionController::API\n"
                                                "  before_action :doorkeeper_authorize!\nend\n"),
            "app/controllers/application_controller.rb": (
                "class ApplicationController < ActionController::Base\n  include Authentication\nend\n"),
            "app/controllers/concerns/authentication.rb": (
                "module Authentication\n  extend ActiveSupport::Concern\n  included do\n"
                "    before_action :require_authentication\n  end\nend\n"),
            "app/controllers/c_controller.rb": "class CController < ApplicationController\nend\n",
            "app/controllers/d_controller.rb": (
                "class DController < ApplicationController\n"
                "  allow_unauthenticated_access only: %i[ new create ]\nend\n"),
        }
        got = {(r["path"], r["auth_detected"]) for r in _routes(files)}
        self.assertEqual(got, {("/a", True), ("/b", True), ("/c", True), ("/d", False)})

    def test_m13_ignored_controllers_not_read(self):
        files = {".gitignore": "app/controllers/\n",
                 "config/routes.rb": _DRAW % "  resources :orders, only: [:create]\n",
                 "app/controllers/orders_controller.rb": (
                     "class OrdersController < ActionController::Base\n"
                     "  before_action :authenticate_user!\nend\n")}
        self.assertEqual(_table(_routes(files)), {("POST", "/orders", "rails", False)})
        with tempfile.TemporaryDirectory() as root:  # the scan's own file list is honoured too
            _tree(root, files)
            walked = walk(root)
            self.assertEqual({r["auth_detected"] for r in find_routes(walked)}, {False})

    def test_m1_auth_regexes_linear_on_blank_runs(self):
        import time
        from dfscan.frameworks import _RailsAuth
        text = "\n" * 100000 + " \n" * 50000 + "class A < B\n  before_action :authenticate_user!\nend\n"
        t0 = time.time()
        self.assertEqual(len(_RailsAuth._BEFORE_RE.findall(text)), 1)
        self.assertEqual(len(_RailsAuth._SKIP_RE.findall(text)), 0)
        self.assertEqual(len(_RailsAuth._CLASS_RE.findall(text)), 1)
        self.assertLess(time.time() - t0, 2.0)


class LaravelReviewTests(unittest.TestCase):
    def test_m10_class_middleware_arrow_group_and_resources_array(self):
        php = ("<?php\n"
               "Route::middleware(Authenticate::class)->group(function () {\n"
               "    Route::post('/a', [A::class, 'store']);\n});\n"
               "Route::middleware('auth:sanctum')->group(fn () => Route::post('/b', [B::class, 'x']));\n"
               "Route::post('/c', [C::class, 'x']);\n"
               "Route::apiResources(['photos' => PhotoController::class, 'posts' => PostController::class]);\n")
        got = _table(_routes({"routes/api.php": php}))
        expected = {("POST", "/a", "laravel", True), ("POST", "/b", "laravel", True),
                    ("POST", "/c", "laravel", False)}
        for res in ("photos", "posts"):
            expected |= {("GET", "/" + res, "laravel", False), ("POST", "/" + res, "laravel", False),
                         ("GET", "/%s/{id}" % res, "laravel", False),
                         ("PUT", "/%s/{id}" % res, "laravel", False),
                         ("DELETE", "/%s/{id}" % res, "laravel", False)}
        self.assertEqual(got, expected)


class FrameworkHealthEndToEndTests(unittest.TestCase):
    """I14: with a Dockerfile each fixture is a service, so no-health-endpoint really runs."""

    _REMOVE = {
        "spring": ("src/main/java/com/example/shop/HealthController.java", None),
        "aspnet": ("Program.cs", "app.MapHealthChecks(\"/health\");"),
        "rails": ("config/routes.rb", "get \"up\" => \"rails/health#show\", as: :rails_health_check"),
        "laravel": ("routes/api.php", "Route::get('/health', fn () => ['status' => 'ok']);"),
    }

    def test_health_route_recognised_and_missing_one_flagged(self):
        import shutil
        for name in FRAMEWORK_FIXTURES:
            with tempfile.TemporaryDirectory() as tmp:
                root = os.path.join(tmp, name)
                shutil.copytree(os.path.join(FIXTURES, name), root)
                _tree(root, {"Dockerfile": "FROM base\nEXPOSE 8080\nCMD [\"run\"]\n"})
                rule = "no-health-endpoint"
                sigs = scan(root, use_git=False)["signals"]
                self.assertFalse([s for s in sigs if s["rule"] == rule], name)
                rel, line = self._REMOVE[name]
                path = os.path.join(root, *rel.split("/"))
                if line is None:
                    os.remove(path)
                else:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                    self.assertIn(line, text, name)
                    _tree(root, {rel: text.replace(line, "")})
                sigs = scan(root, use_git=False)["signals"]
                self.assertTrue([s for s in sigs if s["rule"] == rule], name)


# ------------------------------------------------------------------------------- scaling
class ScalingTests(unittest.TestCase):
    def test_unbalanced_large_files_stay_fast_and_lines_exact(self):
        import time
        php = "<?php\nfoo((\n" + "".join(
            "Route::post('/r%d', [C::class, 'x']);\n" % i for i in range(3000))
        cs = "var api = app.MapGroup(\"/api\");\n" + "".join(
            "var g%d = api.MapGroup(\"/g%d\");\ng%d.MapPost(\"/x\", H);\n" % (i, i, i)
            for i in range(1500)) + "g7.RequireAuthorization();\n{{{\n"
        t0 = time.time()
        php_routes = _routes({"routes/web.php": php})
        cs_routes = _routes({"Program.cs": cs})
        self.assertLess(time.time() - t0, 10.0)
        self.assertEqual(len(php_routes), 3000)
        self.assertEqual({r["line"] for r in php_routes if r["path"] == "/r2999"}, {3002})
        authed = {r["path"] for r in cs_routes if r["auth_detected"]}
        self.assertEqual(authed, {"/api/g7/x"})
        self.assertEqual([r["line"] for r in cs_routes if r["path"] == "/api/g10/x"], [23])


# ------------------------------------------------------------------------------- language rules
class LanguageRuleTests(unittest.TestCase):
    def test_swallowed_error_per_language(self):
        files = {
            "src/A.java": "class A { void f() { try { g(); } catch (Exception e) {} } }\n",
            "src/B.kt": "fun f() { try { g() } catch (e: Exception) {} }\n",
            "src/C.cs": "class C { void F() { try { G(); } catch { } } }\n",
            "src/d.php": "<?php\ntry { g(); } catch (\\Exception $e) {}\n",
            "lib/e.rb": "def f\n  begin\n    g\n  rescue => e\n  end\nend\n",
            "lib/f.rb": "def f\n  g\nrescue ActiveRecord::RecordNotFound; end\n",
        }
        hits = {s["file"]: s for s in _hits(files, "swallowed-error")}
        self.assertEqual(set(hits), set(files))
        # swallowed-error is capped at "low" (measured precision).
        self.assertEqual(hits["lib/e.rb"]["confidence"], "low")
        self.assertEqual(hits["lib/f.rb"]["confidence"], "low")

    def test_ruby_rescue_with_body_not_flagged(self):
        rb = "def f\n  g\nrescue StandardError => e\n  logger.error(e)\n  raise\nend\n"
        self.assertEqual(_hits({"lib/g.rb": rb}, "swallowed-error"), [])

    def test_http_no_timeout_per_language(self):
        flagged = {
            "src/A.java": "class A { RestTemplate t = new RestTemplate(); }\n",
            "src/B.kt": "val client = OkHttpClient.Builder().build()\n",
            "src/C.cs": "class C { HttpClient h = new HttpClient(); }\n",
            "lib/d.rb": "Net::HTTP.get(URI('https://api.internal/x'))\n",
            "src/e.php": "<?php\n$r = file_get_contents('https://api.internal/x');\n",
        }
        quiet = {
            "src/F.java": ("class F { HttpClient c = HttpClient.newBuilder()"
                           ".connectTimeout(Duration.ofSeconds(2)).build(); }\n"),
            "src/G.kt": ("val client = OkHttpClient.Builder()\n"
                         "    .callTimeout(5, TimeUnit.SECONDS)\n    .build()\n"),
            "src/H.cs": "class H { HttpClient h = new HttpClient { Timeout = TimeSpan.FromSeconds(5) }; }\n",
            "lib/i.rb": "http = Net::HTTP.new(host, 443)\nhttp.read_timeout = 5\n",
            "src/j.php": ("<?php\n$ch = curl_init($u);\ncurl_setopt($ch, CURLOPT_TIMEOUT, 5);\n"
                          "curl_exec($ch);\n"),
        }
        hits = {s["file"] for s in _hits(dict(flagged, **quiet), "http-no-timeout")}
        self.assertEqual(hits, set(flagged))

    def test_sql_string_concat_per_language(self):
        # 1.1.0: request input must reach the SQL call; concatenation alone gives no signal.
        flagged = {
            "src/A.kt": ("fun f(@RequestParam name: String) {\n"
                         "    val q = \"SELECT * FROM users WHERE name = $name\"\n"
                         "    jdbc.queryForList(q)\n}\n"),
            "src/B.cs": ("public void F([FromQuery] string name)\n{\n"
                         "    _db.Query($\"SELECT * FROM users WHERE name = '{name}'\");\n}\n"),
            "src/c.php": ("<?php\n$name = $_GET['name'];\n"
                          "DB::select(\"SELECT * FROM users WHERE name = '$name'\");\n"),
            "lib/d.rb": ("def f\n  Order.find_by_sql(\"SELECT * FROM users WHERE name = "
                         "'#{params[:name]}'\")\nend\n"),
            "src/E.java": ("void f(@RequestParam String name) {\n    jdbc.queryForList(String.format("
                           "\"SELECT * FROM users WHERE name = '%s'\", name));\n}\n"),
        }
        quiet = {
            # concatenation without a traced source, one per language
            "src/L.kt": ("fun f(name: String) {\n"
                         "    jdbc.queryForList(\"SELECT * FROM users WHERE name = '$name'\")\n}\n"),
            "src/M.cs": ("public void F(string name)\n{\n"
                         "    _db.Query($\"SELECT * FROM users WHERE name = '{name}'\");\n}\n"),
            "src/n.php": ("<?php\nfunction f($name) {\n"
                          "    DB::select(\"SELECT * FROM users WHERE name = '$name'\");\n}\n"),
            "lib/o.rb": ("def f(name)\n  Order.find_by_sql(\"SELECT * FROM users WHERE name = "
                         "'#{name}'\")\nend\n"),
            "src/P.java": ("void f(String name) {\n    jdbc.queryForList(String.format("
                           "\"SELECT * FROM users WHERE name = '%s'\", name));\n}\n"),
            "src/F.java": "String q = \"SELECT * FROM users WHERE name = ?\";\n",
            "src/g.php": "<?php\n$q = 'SELECT * FROM users WHERE name = :name';\n",
            "src/k.php": "<?php\n$r = DB::select(\"SELECT * FROM users WHERE id = ?\", [$id]);\n",
            "lib/h.rb": "User.where(\"name = ?\", name)\n",
            "src/I.java": "System.out.println(\"SELECT * FROM users WHERE id = \" + id);\n",
        }
        hits = {s["file"] for s in _hits(dict(flagged, **quiet), "sql-string-concat")}
        self.assertEqual(hits, set(flagged))

    def test_php_and_ruby_hash_secrets_masked(self):
        files = {
            "config/db.php": "<?php\nreturn ['password' => 'fixture-pass-4471'];\n",
            "lib/client.rb": "CONFIG = { :api_key => 'fixture-key-8812' }\n",
        }
        hits = _hits(files, "hardcoded-secret")
        self.assertEqual({h["file"] for h in hits}, set(files))
        for h in hits:
            self.assertNotIn("fixture-pass-4471", h["snippet"])
            self.assertNotIn("fixture-key-8812", h["snippet"])

    def test_ruby_skips_loop_rules(self):
        rb = "orders.each do |o|\n  db.query(\"SELECT * FROM items\")\nend\n"
        sigs = _signals({"lib/x.rb": rb})
        self.assertFalse([s for s in sigs if s["rule"] in ("query-in-loop", "unbounded-query")])


# ------------------------------------------------------------------------------- registry
class RegistryTests(unittest.TestCase):
    def test_languages_extended_without_new_ids(self):
        new_langs = {"Java", "Kotlin", "C#", "Ruby", "PHP"}
        for rule in ("http-no-timeout", "sql-string-concat", "hardcoded-secret", "swallowed-error",
                     "unauth-write-route", "no-health-endpoint"):
            self.assertTrue(new_langs <= set(registry.RULES[rule].languages), rule)

    def test_rule_versions_bumped(self):
        expected = {"http-no-timeout": "2", "sql-string-concat": "4", "hardcoded-secret": "6",
                    "swallowed-error": "4", "unauth-write-route": "6", "no-health-endpoint": "4",
                    "console-logging-only": "3"}
        # Each rule must be at least the version that added these languages (unauth-write-route:
        # at least the 1.0.2 framework-detection fixes).
        for rule, version in expected.items():
            self.assertGreaterEqual(int(registry.RULES[rule].version), int(version), rule)

    def test_registry_file_has_lf_endings(self):
        path = os.path.join(REPO, "skills", "design-and-fix", "scripts", "dfscan", "registry.py")
        with open(path, "rb") as fh:
            self.assertNotIn(b"\r\n", fh.read())


if __name__ == "__main__":
    unittest.main()
