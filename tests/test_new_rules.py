from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Set, Tuple

from helpers import FIXTURES, REPO
from dfscan import registry
from dfscan.report import scan
from dfscan.walker import walk

EVALS = os.path.join(REPO, "evals")
CLEAN = os.path.join(FIXTURES, "clean-service")

API_RULES = ("list-route-no-pagination", "mixed-error-shapes", "no-version-prefix",
             "unvalidated-body", "openapi-drift")
INFRA_RULES = ("docker-root-user", "docker-latest-tag", "docker-no-healthcheck",
               "k8s-no-probes", "k8s-no-limits", "ci-no-test-step")
DEPS_RULES = ("missing-lockfile", "unpinned-dependency")


def _tree(root: str, files: Dict[str, str]) -> None:
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


# openapi-drift ships off by default (1.1.0 release decision); tests of the rule enable it.
OPENAPI_ON = {"openapi-drift": {"enabled": True}}


def _signals(files: Dict[str, str], rule_config: Optional[Dict[str, Any]] = None) -> List[dict]:
    with tempfile.TemporaryDirectory() as d:
        _tree(d, files)
        return scan(d, use_git=False, rule_config=rule_config)["signals"]


def _hits(files: Dict[str, str], rule: str,
          rule_config: Optional[Dict[str, Any]] = None) -> List[dict]:
    return [s for s in _signals(files, rule_config) if s["rule"] == rule]


def _drift(files: Dict[str, str]) -> List[dict]:
    """openapi-drift hits with the rule enabled explicitly in config."""
    return _hits(files, "openapi-drift", OPENAPI_ON)


def _pairs(root: str, rule_config: Optional[Dict[str, Any]] = None) -> Set[Tuple[str, str]]:
    return {(s["rule"], s["file"])
            for s in scan(root, use_git=False, rule_config=rule_config)["signals"]}


EXPRESS_HEAD = ("const express = require('express');\n"
                "const app = express();\n"
                "app.use(requireAuth);\n")
EXPRESS_TAIL = "app.get('/health', (req, res) => res.json({ ok: true }));\napp.listen(3000);\n"


def _express(body: str) -> Dict[str, str]:
    return {"server.js": EXPRESS_HEAD + body + EXPRESS_TAIL}


class RegistryCoverageTests(unittest.TestCase):
    def test_new_rules_registered_with_brief_metadata(self):
        expected = {
            "list-route-no-pagination": ("API Design", "Medium"),
            "mixed-error-shapes": ("API Design", "Medium"),
            "no-version-prefix": ("API Design", "Low"),
            "unvalidated-body": ("API Design", "High"),
            "openapi-drift": ("API Design", "Medium"),
            "docker-root-user": ("Security", "High"),
            "docker-latest-tag": ("Reliability", "Medium"),
            "docker-no-healthcheck": ("Observability", "Low"),
            "k8s-no-probes": ("Reliability", "Medium"),
            "k8s-no-limits": ("Scalability", "Medium"),
            "ci-no-test-step": ("Maintainability", "Medium"),
            "missing-lockfile": ("Maintainability", "Medium"),
            "unpinned-dependency": ("Maintainability", "Low"),
        }
        for rid, (cat, sev) in expected.items():
            meta = registry.RULES[rid]
            self.assertEqual((meta.category, meta.default_severity), (cat, sev), rid)
            self.assertTrue(meta.version.isdigit() and int(meta.version) >= 1, rid)
            self.assertTrue(meta.languages, rid)
            self.assertTrue(meta.limitations.strip() and meta.remediation.strip(), rid)


# ------------------------------------------------------------------------------------ API
class ListRouteNoPaginationTests(unittest.TestCase):
    def test_full_query_list_route_flagged(self):
        hits = _hits(_express(
            "app.get('/users', async (req, res) => {\n"
            "  const users = await User.findAll();\n"
            "  res.json(users);\n"
            "});\n"), "list-route-no-pagination")
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("server.js", 4)])
        self.assertEqual(hits[0]["category"], "API Design")

    def test_paginated_and_item_routes_not_flagged(self):
        hits = _hits(_express(
            "app.get('/orders', async (req, res) => {\n"
            "  const limit = Math.min(Number(req.query.limit) || 20, 100);\n"
            "  const offset = Number(req.query.offset) || 0;\n"
            "  const rows = await Order.findAll({ limit, offset });\n"
            "  res.json(rows);\n"
            "});\n"
            "app.get('/users/:id', async (req, res) => {\n"
            "  res.json(await User.findAll({ where: { id: req.params.id } }));\n"
            "});\n"
            "app.get('/products', listProducts);\n"), "list-route-no-pagination")
        self.assertEqual(hits, [])

    def test_fastapi_skip_limit_not_flagged(self):
        hits = _hits({"main.py": (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get('/items')\n"
            "def list_items(skip: int = 0, limit: int = 100):\n"
            "    return db.query(Item).offset(skip).limit(limit).all()\n\n"
            "@app.get('/tags')\n"
            "def list_tags():\n"
            "    return db.query(Tag).all()\n")}, "list-route-no-pagination")
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("main.py", 8)])


class UnvalidatedBodyTests(unittest.TestCase):
    def test_raw_body_to_model_flagged(self):
        hits = _hits(_express(
            "app.post('/users', async (req, res) => {\n"
            "  const user = await User.create(req.body);\n"
            "  res.status(201).json(user);\n"
            "});\n"), "unvalidated-body")
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("server.js", 4)])
        self.assertEqual(hits[0]["severity_hint"], "High")

    def test_validated_bodies_not_flagged(self):
        hits = _hits(_express(
            "const { z } = require('zod');\n"
            "const Order = z.object({ sku: z.string() });\n"
            "app.post('/orders', async (req, res) => {\n"
            "  const data = Order.parse(req.body);\n"
            "  res.status(201).json(await save(data));\n"
            "});\n"
            "app.put('/users/:id', celebrate({ body: userSchema }), async (req, res) => {\n"
            "  res.json(await User.update(req.body));\n"
            "});\n"), "unvalidated-body")
        self.assertEqual(hits, [])

    def test_python_validation_forms_not_flagged(self):
        hits = _hits({"main.py": (
            "from fastapi import FastAPI\nfrom pydantic import BaseModel\n"
            "app = FastAPI()\n\n"
            "class Note(BaseModel):\n    text: str\n\n"
            "@app.post('/notes')\n"
            "def create(note: Note):\n"
            "    return save(note)\n"),
            "flask_app.py": (
            "from flask import Flask, request, abort\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/tasks', methods=['POST'])\n"
            "def create_task():\n"
            "    data = request.get_json()\n"
            "    if not data or 'title' not in data:\n"
            "        abort(400)\n"
            "    return save(data)\n")}, "unvalidated-body")
        self.assertEqual(hits, [])

    def test_go_bind_with_and_without_tags(self):
        tagged = ("package main\n\ntype In struct {\n  Name string `json:\"name\" binding:\"required\"`\n}\n\n"
                  "func main() {\n  r := gin.Default()\n"
                  "  r.POST(\"/things\", func(c *gin.Context) {\n"
                  "    var in In\n    if err := c.ShouldBindJSON(&in); err != nil { return }\n  })\n}\n")
        untagged = ("package main\n\ntype In struct {\n  Name string `json:\"name\"`\n}\n\n"
                    "func main() {\n  e := echo.New()\n"
                    "  e.POST(\"/things\", func(c echo.Context) error {\n"
                    "    var in In\n    c.Bind(&in)\n    return save(in)\n  })\n}\n")
        self.assertEqual(_hits({"svc/main.go": tagged}, "unvalidated-body"), [])
        hits = _hits({"svc/main.go": untagged}, "unvalidated-body")
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("svc/main.go", 9)])


class MixedErrorShapesTests(unittest.TestCase):
    def test_error_and_message_shapes_flagged_once(self):
        hits = _hits(_express(
            "app.get('/a/:id', (req, res) => res.status(404).json({ error: 'not found' }));\n"
            "app.get('/b/:id', (req, res) => res.status(404).json({ error: 'missing' }));\n"
            "app.get('/c/:id', (req, res) => res.status(400).json({ message: 'bad input' }));\n"),
            "mixed-error-shapes")
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0]["file"], hits[0]["line"]), ("server.js", 6))
        self.assertIn("error", hits[0]["snippet"])
        self.assertIn("message", hits[0]["snippet"])

    def test_consistent_shapes_not_flagged(self):
        hits = _hits(_express(
            "app.get('/a/:id', (req, res) => res.status(404).json({ error: 'not found' }));\n"
            "app.get('/b/:id', (req, res) => res.status(400).json({ error: 'bad', message: 'x' }));\n"
            "app.get('/c', (req, res) => res.status(200).json({ message: 'hello' }));\n"),
            "mixed-error-shapes")
        self.assertEqual(hits, [])


class NoVersionPrefixTests(unittest.TestCase):
    ROUTES = ("app.get('/users', h);\napp.post('/users', h);\napp.get('/orders', h);\n")

    def test_unversioned_api_flagged_once(self):
        hits = _hits(_express(self.ROUTES), "no-version-prefix")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "server.js")
        self.assertEqual(hits[0]["confidence"], "low")

    def test_mounted_version_prefix_not_flagged(self):
        files = _express(self.ROUTES.replace("app.", "router."))
        files["server.js"] = files["server.js"].replace(
            "app.listen", "app.use('/api/v1', router);\napp.listen")
        self.assertEqual(_hits(files, "no-version-prefix"), [])

    def test_small_service_not_flagged(self):
        self.assertEqual(_hits(_express("app.post('/notes', h);\n"), "no-version-prefix"), [])


SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/health": {"get": {}},
        "/users": {"get": {}, "post": {}},
        "/users/{id}": {"get": {}},
    },
}
ROUTES_OK = ("app.get('/users', h);\napp.post('/users', h);\napp.get('/users/:id', h);\n")


class OpenapiDriftTests(unittest.TestCase):
    def test_spec_matches_routes(self):
        files = _express(ROUTES_OK)
        files["openapi.json"] = json.dumps(SPEC, indent=2)
        self.assertEqual(_drift(files), [])

    def test_missing_both_ways_flagged(self):
        spec = json.loads(json.dumps(SPEC))
        spec["paths"]["/legacy"] = {"get": {}}
        files = _express(ROUTES_OK + "app.delete('/users/:id', h);\n")
        files["openapi.json"] = json.dumps(spec, indent=2)
        hits = _drift(files)
        got = sorted((h["file"], h["line"]) for h in hits)
        legacy_line = files["openapi.json"].split("\n").index('    "/legacy": {') + 1
        self.assertEqual(got, [("openapi.json", legacy_line), ("server.js", 7)])

    def test_yaml_spec_with_base_path(self):
        files = _express(ROUTES_OK.replace("'/users", "'/api/users"))
        files["openapi.yaml"] = ("openapi: 3.0.0\nservers:\n  - url: /api\npaths:\n"
                                 "  /users:\n    get: {}\n    post: {}\n"
                                 "  /users/{id}:\n    get: {}\n")
        self.assertEqual(_drift(files), [])

    def test_unrelated_client_spec_ignored(self):
        files = _express(ROUTES_OK)
        files["clients/stripe/openapi.json"] = json.dumps(
            {"openapi": "3.0.0", "paths": {"/v1/charges": {"post": {}}}})
        files["clients/stripe/index.js"] = "module.exports = {};\n"
        self.assertEqual(_drift(files), [])


# ---------------------------------------------------------------------------------- infra
class WalkerInfraTests(unittest.TestCase):
    def test_infra_files_detected(self):
        files = {
            "Dockerfile": "FROM node:20\n",
            "deploy/Dockerfile.prod": "FROM node:20\n",
            "deploy/api.dockerfile": "FROM node:20\n",
            "k8s/app.yaml": "apiVersion: apps/v1\nkind: Deployment\n",
            "k8s/svc.yaml": "apiVersion: v1\nkind: Service\n",
            ".github/workflows/ci.yml": "on: push\n",
            ".github/dependabot.yml": "version: 2\n",
            ".gitlab-ci.yml": "test:\n  script: make test\n",
            ".circleci/config.yml": "version: 2.1\n",
        }
        with tempfile.TemporaryDirectory() as d:
            _tree(d, files)
            got = {f.rel: f for f in walk(d)}
        infra = {r for r, f in got.items() if f.is_infra}
        self.assertEqual(infra, {"Dockerfile", "deploy/Dockerfile.prod", "deploy/api.dockerfile",
                                 "k8s/app.yaml", ".github/workflows/ci.yml", ".gitlab-ci.yml",
                                 ".circleci/config.yml"})
        self.assertNotIn(".github/dependabot.yml", got)
        self.assertTrue(got["k8s/svc.yaml"].is_config)
        self.assertTrue(all(got[r].loc == 0 and got[r].lang is None for r in infra))


GOOD_DOCKERFILE = ("FROM node:20-alpine AS build\nWORKDIR /app\nCOPY . .\nRUN npm ci && npm run build\n\n"
                   "FROM node:20-alpine\nWORKDIR /app\nCOPY --from=build /app/dist ./dist\n"
                   "USER node\nEXPOSE 3000\n"
                   "HEALTHCHECK CMD wget -qO- http://localhost:3000/health || exit 1\n"
                   "CMD [\"node\", \"dist/index.js\"]\n")


class DockerRulesTests(unittest.TestCase):
    def test_good_dockerfile_clean(self):
        sigs = [s for s in _signals({"Dockerfile": GOOD_DOCKERFILE}) if s["rule"] in INFRA_RULES]
        self.assertEqual(sigs, [])

    def test_root_user(self):
        hits = _hits({"Dockerfile": "FROM python:3.12-slim\nCOPY . .\nCMD [\"python\", \"app.py\"]\n"},
                     "docker-root-user")
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("Dockerfile", 1)])
        hits = _hits({"Dockerfile": "FROM python:3.12-slim\nUSER app\nRUN x\nUSER root\nCMD [\"x\"]\n"},
                     "docker-root-user")
        self.assertEqual([h["line"] for h in hits], [4])

    def test_root_user_only_final_stage_and_nonroot_base(self):
        build_root = ("FROM golang:1.22 AS build\nRUN go build -o /app\n"
                      "FROM gcr.io/distroless/static:nonroot\nCOPY --from=build /app /app\n"
                      "ENTRYPOINT [\"/app\"]\n")
        self.assertEqual(_hits({"Dockerfile": build_root}, "docker-root-user"), [])

    def test_latest_and_untagged(self):
        text = ("ARG BASE=node:20\nFROM node:latest AS build\nFROM ubuntu\nFROM build\n"
                "FROM ${BASE}\nFROM alpine@sha256:abcdef0123\nFROM scratch\n"
                "FROM --platform=linux/amd64 localhost:5000/tools\n")
        hits = _hits({"Dockerfile": text}, "docker-latest-tag")
        self.assertEqual(sorted(h["line"] for h in hits), [2, 3, 8])

    def test_no_healthcheck_only_for_exposed_service_without_k8s(self):
        svc = "FROM node:20\nUSER node\nEXPOSE 3000\nCMD [\"node\", \"x.js\"]\n"
        self.assertEqual(len(_hits({"Dockerfile": svc}, "docker-no-healthcheck")), 1)
        build = "FROM node:20\nUSER node\nRUN npm ci\nCMD [\"npm\", \"run\", \"build\"]\n"
        self.assertEqual(_hits({"Dockerfile": build}, "docker-no-healthcheck"), [])
        files = {"Dockerfile": svc, "k8s/app.yaml": K8S_GOOD}
        self.assertEqual(_hits(files, "docker-no-healthcheck"), [])
        files = {"Dockerfile": svc, "docker-compose.yml":
                 "services:\n  web:\n    build: .\n    healthcheck:\n      test: [\"CMD\", \"true\"]\n"}
        self.assertEqual(_hits(files, "docker-no-healthcheck"), [])

    def test_comments_ignored(self):
        text = "# FROM node:latest\nFROM node:20\n# USER node\nCMD [\"node\"]\n"
        self.assertEqual(_hits({"Dockerfile": text}, "docker-latest-tag"), [])
        self.assertEqual(len(_hits({"Dockerfile": text}, "docker-root-user")), 1)


K8S_GOOD = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      initContainers:
        - name: migrate
          image: api:1.2.3
      containers:
        - name: api
          image: api:1.2.3
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: {path: /ready, port: 8080}
          livenessProbe:
            httpGet: {path: /health, port: 8080}
          resources:
            limits:
              memory: 512Mi
"""

K8S_BAD = """apiVersion: v1
kind: Service
metadata:
  name: api
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: db
spec:
  template:
    spec:
      containers:
        - name: db
          image: postgres:16
          ports:
            - containerPort: 5432
        - name: metrics-sidecar
          image: exporter:1.0
"""


class K8sRulesTests(unittest.TestCase):
    def test_good_deployment_clean(self):
        sigs = [s for s in _signals({"k8s/app.yaml": K8S_GOOD}) if s["rule"] in INFRA_RULES]
        self.assertEqual(sigs, [])

    def test_no_probes_only_for_containers_with_ports(self):
        hits = _hits({"k8s/db.yaml": K8S_BAD}, "k8s-no-probes")
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("k8s/db.yaml", 14)])

    def test_no_limits_per_container(self):
        hits = _hits({"k8s/db.yaml": K8S_BAD}, "k8s-no-limits")
        self.assertEqual(sorted(h["line"] for h in hits), [14, 18])

    def test_limitrange_and_helm_templates_skip_limits(self):
        files = {"k8s/db.yaml": K8S_BAD,
                 "k8s/limits.yaml": "apiVersion: v1\nkind: LimitRange\nmetadata:\n  name: d\n"}
        self.assertEqual(_hits(files, "k8s-no-limits"), [])
        helm = K8S_BAD + "          resources: {{- toYaml .Values.resources | nindent 12 }}\n"
        self.assertEqual(_hits({"chart/templates/db.yaml": helm}, "k8s-no-limits"), [])


class CiRulesTests(unittest.TestCase):
    def test_workflow_without_tests_flagged(self):
        wf = ("name: deploy\non: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n"
              "      - uses: actions/checkout@v4\n      - run: npm ci\n      - run: npm run build\n"
              "      - run: ./deploy.sh\n")
        hits = _hits({".github/workflows/deploy.yml": wf}, "ci-no-test-step")
        self.assertEqual([h["file"] for h in hits], [".github/workflows/deploy.yml"])

    def test_workflow_running_tests_not_flagged(self):
        wf = ("name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
              "      - uses: actions/checkout@v4\n      - run: npm ci\n      - run: npm test\n")
        self.assertEqual(_hits({".github/workflows/ci.yml": wf}, "ci-no-test-step"), [])

    def test_any_workflow_with_tests_covers_the_repo(self):
        files = {
            ".github/workflows/release.yml": "on: release\njobs:\n  r:\n    steps:\n      - run: npm publish\n",
            ".github/workflows/py.yml": "on: push\njobs:\n  t:\n    steps:\n      - run: python -m pytest -q\n",
        }
        self.assertEqual(_hits(files, "ci-no-test-step"), [])

    def test_reusable_workflow_and_gitlab_include_skipped(self):
        files = {".github/workflows/ci.yml":
                 "on: push\njobs:\n  ci:\n    uses: org/shared/.github/workflows/node.yml@v1\n",
                 ".gitlab-ci.yml": "include:\n  - project: org/templates\n    file: node.yml\n"}
        self.assertEqual(_hits(files, "ci-no-test-step"), [])

    def test_gitlab_without_tests_flagged(self):
        files = {".gitlab-ci.yml": "build:\n  script:\n    - docker build .\n"}
        self.assertEqual([h["file"] for h in _hits(files, "ci-no-test-step")], [".gitlab-ci.yml"])


# ----------------------------------------------------------------------------------- deps
PKG = {"name": "web", "private": True, "dependencies": {"express": "^4.18.2", "pg": "~8.11.0"}}


class MissingLockfileTests(unittest.TestCase):
    def test_package_json_without_lock_flagged(self):
        hits = _hits({"web/package.json": json.dumps(PKG, indent=2)}, "missing-lockfile")
        self.assertEqual([(h["file"], h["confidence"]) for h in hits], [("web/package.json", "medium")])

    def test_lockfile_present_or_at_workspace_root(self):
        files = {"web/package.json": json.dumps(PKG), "web/package-lock.json": "{}"}
        self.assertEqual(_hits(files, "missing-lockfile"), [])
        files = {"package.json": json.dumps({"private": True, "workspaces": ["packages/*"]}),
                 "pnpm-lock.yaml": "lockfileVersion: 9\n",
                 "packages/a/package.json": json.dumps(PKG)}
        self.assertEqual(_hits(files, "missing-lockfile"), [])

    def test_no_dependencies_and_python_requirements_not_flagged(self):
        files = {"tools/package.json": json.dumps({"name": "t", "scripts": {"x": "y"}}),
                 "api/requirements.txt": "flask==3.0.0\n"}
        self.assertEqual(_hits(files, "missing-lockfile"), [])

    def test_pipfile_and_gemfile(self):
        files = {"a/Pipfile": "[packages]\nflask = \"*\"\n",
                 "b/Gemfile": "source 'https://rubygems.org'\ngem 'rails'\n",
                 "c/Gemfile": "gemspec\n", "c/c.gemspec": "",
                 "d/Pipfile": "[packages]\n", "d/Pipfile.lock": "{}"}
        hits = _hits(files, "missing-lockfile")
        self.assertEqual(sorted(h["file"] for h in hits), ["a/Pipfile", "b/Gemfile"])


class UnpinnedDependencyTests(unittest.TestCase):
    def test_requirements_unbounded(self):
        req = ("# comment >= ignored\nflask>=2.0\nrequests==2.31.0\ncelery\n"
               "django>=4.2,<5\nnumpy~=1.26\n-r base.txt\n-e .\n"
               "pkg @ https://example.test/pkg.whl\nboto3==1.*\nuvicorn[standard]>=0.29; python_version>'3.8'\n")
        hits = _hits({"requirements.txt": req}, "unpinned-dependency")
        self.assertEqual(sorted(h["line"] for h in hits), [2, 4, 11])
        self.assertTrue(all(h["category"] == "Maintainability" for h in hits))

    def test_requirements_pinned_not_flagged(self):
        req = "fastapi==0.110.0\nhttpx==0.27.0 --hash=sha256:abc\n"
        self.assertEqual(_hits({"requirements.txt": req}, "unpinned-dependency"), [])

    def test_package_json_star_is_security(self):
        pkg = {"dependencies": {"a": "*", "b": "latest", "c": "^1.0.0", "d": ">=2.0.0",
                                "e": ">=1 <2", "f": "workspace:*", "g": "file:../g"},
               "peerDependencies": {"react": "*"}}
        text = json.dumps(pkg, indent=2)
        hits = _hits({"package.json": text, "package-lock.json": "{}"}, "unpinned-dependency")
        got = sorted((text.split("\n")[h["line"] - 1].strip().split(":")[0], h["category"]) for h in hits)
        self.assertEqual(got, [('"a"', "Security"), ('"b"', "Security"), ('"d"', "Maintainability")])

    def test_pyproject_poetry_and_pep621(self):
        py = ("[project]\nname = \"x\"\ndependencies = [\n  \"httpx>=0.27\",\n  \"attrs==23.2.0\",\n]\n\n"
              "[tool.poetry.dependencies]\npython = \"^3.11\"\nrich = \"*\"\nclick = \"^8.1\"\n")
        hits = _hits({"pyproject.toml": py, "poetry.lock": ""}, "unpinned-dependency")
        got = sorted((h["line"], h["category"], h["confidence"]) for h in hits)
        self.assertEqual(got, [(4, "Maintainability", "low"), (10, "Security", "low")])

    def test_pyproject_library_lower_bounds_not_flagged(self):
        py = ("[build-system]\nrequires = [\"flit_core<4\"]\n\n[project]\nname = \"lib\"\n"
              "dependencies = [\"click>=8.1.3\", \"jinja2>=3.1.2\"]\n")
        self.assertEqual(_hits({"pyproject.toml": py}, "unpinned-dependency"), [])


# ------------------------------------------------------------------------------- fixtures
class FixtureTests(unittest.TestCase):
    def test_new_fixtures_expected(self):
        with open(os.path.join(EVALS, "scanner-expected.json"), encoding="utf-8") as fh:
            exp = json.load(fh)
        for key in ("api-service", "infra", "deps"):
            got = _pairs(os.path.join(FIXTURES, key))
            must = {tuple(x) for x in exp[key]["must_find"]}
            self.assertEqual(must - got, set(), key)
            self.assertEqual({tuple(x) for x in exp[key]["must_not_find"]} & got, set(), key)
            if "must_find_when_enabled" in exp[key]:  # default-off rules, enabled in config
                got_on = _pairs(os.path.join(FIXTURES, key), exp[key]["enabled_rules"])
                on = {tuple(x) for x in exp[key]["must_find_when_enabled"]}
                self.assertEqual((on | must) - got_on, set(), key)
        rules = {r for key in ("api-service", "infra", "deps") for r, _ in
                 (tuple(x) for x in exp[key]["must_find"] + exp[key].get("must_find_when_enabled", []))}
        self.assertTrue(set(API_RULES + INFRA_RULES + DEPS_RULES) <= rules)

    def test_clean_service_still_zero(self):
        self.assertEqual(scan(CLEAN, use_git=False)["signals"], [])

    def test_signals_validate_and_mask(self):
        r = scan(os.path.join(FIXTURES, "infra"), use_git=False)
        self.assertEqual(r["summary"]["invalid_signals"], 0)
        for s in r["signals"]:
            self.assertNotIn("hunter2-secret-value", s["snippet"])

# ------------------------------------------------------------- release review 1.0.2 (area B)
def _walked(files: Dict[str, str]):
    """(tmpdir handle, walked files) for calling a rule runner directly."""
    d = tempfile.TemporaryDirectory()
    _tree(d.name, files)
    return d, walk(d.name)


class MalformedInputTests(unittest.TestCase):
    SERVER = ("const app = require('express')();\napp.get('/users', h);\n"
              "app.post('/users', auth, h);\napp.listen(3);\n")

    def test_openapi_servers_not_a_list_does_not_crash(self):  # C1
        for servers in ("1", "true", '"x"', "{}"):
            files = {"server.js": self.SERVER, "openapi.json":
                     '{"openapi":"3.0.0","servers":%s,"paths":{"/users":{"get":{}}}}' % servers}
            hits = _drift(files)  # scan() must not raise
            self.assertEqual([h["snippet"] for h in hits], ["POST /users not in openapi.json"])

    def test_deeply_nested_json_is_skipped_with_note(self):  # I13, C1
        from dfscan.api_rules import run_api_rules
        from dfscan.deps_rules import run_deps_rules
        from dfscan.routes import find_routes
        deep = "[" * 100000 + "]" * 100000
        files = {"server.js": self.SERVER,
                 "package.json": '{"dependencies": %s}' % deep,
                 "web/composer.json": '{"require": %s}' % deep,
                 "openapi.json": '{"paths": %s}' % deep,
                 "bad/package.json": '{"dependencies": {"a": "*"',
                 "ok/package.json": '{"private": true, "dependencies": {"a": "*"}}'}
        sigs = _signals(files, OPENAPI_ON)  # scan() with openapi-drift on must not raise
        self.assertIn(("unpinned-dependency", "ok/package.json"),
                      {(s["rule"], s["file"]) for s in sigs})
        d, walked = _walked(files)
        with d:
            notes: List[str] = []
            run_deps_rules(walked, notes)
            run_api_rules(walked, find_routes(walked), notes)
        self.assertEqual(sorted(notes), [
            "deps rules skipped bad/package.json: malformed JSON (JSONDecodeError)",
            "deps rules skipped package.json: malformed JSON (RecursionError)",
            "deps rules skipped web/composer.json: malformed JSON (RecursionError)",
            "openapi-drift skipped openapi.json: malformed spec (RecursionError)"])

    def test_disabled_openapi_drift_writes_no_note(self):  # final review M1
        files = {"server.js": self.SERVER, "openapi.json": '{"paths": {"/users": '}
        note = "openapi-drift skipped openapi.json: malformed spec (JSONDecodeError)"
        with tempfile.TemporaryDirectory() as d:
            _tree(d, files)
            default = scan(d, use_git=False)["notes"]
            on = scan(d, use_git=False, rule_config=OPENAPI_ON)["notes"]
        self.assertNotIn(note, default)
        self.assertIn("rules off by default (enable in config): openapi-drift", default)
        self.assertIn(note, on)


class OpenapiDriftReviewTests(unittest.TestCase):
    def test_prefix_mounted_in_another_file(self):  # I1
        files = {"src/app.js": ("const express = require('express');\nconst app = express();\n"
                                "app.use('/api', users);\napp.listen(3000);\n"),
                 "src/routes/users.js": ("router.get('/users', list);\n"
                                         "router.post('/users', requireAuth, create);\n"
                                         "router.get('/users/:id', show);\n"
                                         "router.get('/me', me);\n"),
                 "openapi.yaml": ("openapi: 3.0.0\npaths:\n  /api/users:\n    get: {}\n"
                                  "    post: {}\n  /api/users/{id}:\n    get: {}\n"
                                  "  /me:\n    get: {}\n")}
        self.assertEqual(_drift(files), [])
        # A spec path that is only a parameter never matches as a suffix.
        files["openapi.yaml"] += "  /{id}:\n    get: {}\n"
        hits = _drift(files)
        self.assertEqual([h["snippet"] for h in hits], ["spec path /{id} has no route in code"])

    def test_spec_in_docs_folder_is_used(self):  # I2
        files = {"src/server.js": ("const app = require('express')();\napp.get('/users', h);\n"
                                   "app.post('/users', requireAuth, h);\n"
                                   "app.delete('/users/:id', requireAuth, h);\napp.listen(3);\n"),
                 "docs/openapi.yaml": ("openapi: 3.0.0\npaths:\n  /users:\n    get: {}\n"
                                       "    post: {}\n  /legacy:\n    get: {}\n")}
        hits = _drift(files)
        self.assertEqual(sorted(h["snippet"] for h in hits),
                         ["DELETE /users/:id not in docs/openapi.yaml",
                          "spec path /legacy has no route in code"])

    def test_spec_of_another_service_not_used(self):  # I2 guard
        files = {"svc/a/server.js": ("const app = require('express')();\napp.get('/a', h);\n"
                                     "app.listen(3);\n"),
                 "svc/b/server.js": ("const app = require('express')();\napp.get('/b', h);\n"
                                     "app.get('/c', h);\napp.listen(3);\n"),
                 "svc/a/openapi.json": json.dumps({"paths": {"/a": {"get": {}}, "/b": {"get": {}}}})}
        hits = _drift(files)
        self.assertEqual([h["snippet"] for h in hits], ["spec path /b has no route in code"])

    def test_aspnet_controller_case_insensitive(self):  # I3
        files = {"Controllers/OrdersController.cs": (
            "using Microsoft.AspNetCore.Mvc;\n[ApiController]\n[Route(\"api/[controller]\")]\n"
            "public class OrdersController : ControllerBase\n{\n"
            "    [HttpGet]\n    public IActionResult List() => Ok();\n"
            "    [HttpGet(\"{id}\")]\n    public IActionResult Get(int id) => Ok();\n"
            "    [HttpPost(\"/api/import\")]\n    public IActionResult Import() => Ok();\n}\n"),
            "swagger.json": json.dumps({"swagger": "2.0", "paths": {
                "/api/Orders": {"get": {}}, "/api/Orders/{id}": {"get": {}},
                "/api/import": {"post": {}}}}, indent=2)}
        self.assertEqual(_drift(files), [])

    def test_yaml_license_url_is_not_a_base(self):  # M3
        files = _express("app.get('/v1/users', h);\napp.post('/v1/users', h);\n"
                         "app.get('/v1/orders', h);\n")
        files["openapi.yaml"] = ("openapi: 3.0.0\ninfo:\n  title: t\n  license:\n"
                                 "    name: MIT\n    url: https://example.com/v1\n"
                                 "paths:\n  /v1/users:\n    get: {}\n    post: {}\n"
                                 "  /v1/orders:\n    get: {}\n  /v1/legacy:\n    get: {}\n")
        hits = _drift(files)
        self.assertEqual([h["snippet"] for h in hits], ["spec path /v1/legacy has no route in code"])

    def test_large_json_spec_lines_are_linear(self):  # M2
        import time
        from dfscan.api_rules import _parse_spec
        spec = {"paths": {"/p%d" % i: {"get": {}} for i in range(20000)}}
        text = json.dumps(spec, indent=2)
        t0 = time.perf_counter()
        paths, _ = _parse_spec(text, True)
        self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(paths["/p19999"][0], text.split("\n").index('    "/p19999": {') + 1)

    def test_registry_versions_and_languages(self):  # I4
        # no-version-prefix 3: Task 16 (RSpec *_spec.rb files are tests and no longer count).
        for rid, version in (("openapi-drift", "2"), ("no-version-prefix", "3")):
            meta = registry.RULES[rid]
            self.assertEqual(meta.version, version, rid)
            self.assertTrue({"Java", "Kotlin", "C#", "Ruby", "PHP"} <= set(meta.languages), rid)
        for rid in ("list-route-no-pagination", "mixed-error-shapes", "unvalidated-body"):
            self.assertEqual(registry.RULES[rid].languages,
                             ["Python", "JavaScript", "TypeScript", "Go"], rid)


class NoVersionPrefixReviewTests(unittest.TestCase):
    def test_aspnet_api_versioning_seen(self):  # I4
        files = {"Controllers/OrdersController.cs": (
            "using Microsoft.AspNetCore.Mvc;\n[ApiController]\n[ApiVersion(\"1.0\")]\n"
            "[Route(\"api/v{version:apiVersion}/[controller]\")]\n"
            "public class OrdersController : ControllerBase\n{\n"
            "    [HttpGet]\n    public IActionResult List() => Ok();\n"
            "    [HttpPost]\n    public IActionResult Create() => Ok();\n"
            "    [HttpDelete(\"{id}\")]\n    public IActionResult Delete(int id) => Ok();\n}\n"),
            "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:8.0\nEXPOSE 8080\n"}
        self.assertEqual(_hits(files, "no-version-prefix"), [])
        # The v{...} route segment alone is enough (no [ApiVersion] attribute).
        files["Controllers/OrdersController.cs"] = files["Controllers/OrdersController.cs"].replace(
            "[ApiVersion(\"1.0\")]\n", "")
        self.assertEqual(_hits(files, "no-version-prefix"), [])

    def test_framework_versioning_in_code_seen(self):  # I4
        java = ("package com.example.api;\n@RestController\n@RequestMapping(\"/orders\")\n"
                "public class OrderController {\n"
                "  @GetMapping(headers = \"X-API-VERSION=1\") public List<O> a() { return null; }\n"
                "  @PostMapping public O b() { return null; }\n"
                "  @DeleteMapping(\"/{id}\") public void c() {}\n}\n")
        files = {"src/main/java/com/example/api/OrderController.java": java,
                 "Dockerfile": "FROM eclipse-temurin:21.0.2_13-jre\nEXPOSE 8080\n"}
        self.assertEqual(_hits(files, "no-version-prefix"), [])
        files["src/main/java/com/example/api/OrderController.java"] = java.replace(
            "headers = \"X-API-VERSION=1\"", "")
        self.assertEqual(len(_hits(files, "no-version-prefix")), 1)


class DepsReviewTests(unittest.TestCase):
    def test_pyproject_extras_do_not_end_list(self):  # M4
        py = ('[project]\nname = "x"\ndependencies = [\n  "uvicorn[standard]>=0.29",\n'
              '  "httpx>=0.27",\n]\n')
        hits = _hits({"pyproject.toml": py}, "unpinned-dependency")
        self.assertEqual([h["line"] for h in hits], [4, 5])

    def test_pipfile_quoted_keys(self):  # M5
        hits = _hits({"Pipfile": '[packages]\n"requests" = "*"\n'}, "missing-lockfile")
        self.assertEqual([h["file"] for h in hits], ["Pipfile"])

    def test_npm_alias_without_version(self):  # M6
        from dfscan.deps_rules import _npm_unpinned
        self.assertEqual(_npm_unpinned("npm:@scope/foo"), "Security")
        self.assertEqual(_npm_unpinned("npm:foo"), "Security")
        self.assertIsNone(_npm_unpinned("npm:@scope/foo@^1.2.0"))
        self.assertEqual(_npm_unpinned("npm:foo@*"), "Security")

    def test_package_json_lines_and_speed(self):  # M2
        import time
        deps = {"dep%d" % i: "*" for i in range(20000)}
        text = json.dumps({"private": True, "devDependencies": {"dep5": "*"},
                           "dependencies": deps}, indent=2)
        from dfscan.deps_rules import rule_unpinned_dependency
        d, walked = _walked({"package.json": text})
        with d:
            t0 = time.perf_counter()
            hits = rule_unpinned_dependency(walked[0], text)
            self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(len(hits), 20001)
        lines = text.split("\n")
        by = {(h.snippet, h.line) for h in hits}
        self.assertIn(('"dep19999": "*"', lines.index('    "dep19999": "*"') + 1), by)
        self.assertIn(('"dep5": "*"', lines.index('    "dep5": "*"') + 1), by)  # devDependencies
        self.assertIn(('"dep5": "*"', lines.index('    "dep5": "*",') + 1), by)  # dependencies

    def test_rule_versions(self):
        self.assertEqual(registry.RULES["missing-lockfile"].version, "2")
        self.assertEqual(registry.RULES["unpinned-dependency"].version, "4")


class UnpinnedDocsToolingTests(unittest.TestCase):  # 1.1.0 Task 9
    DOCS_REQ = "pyenchant\nSphinx>=4.5.0\nsphinxcontrib-spelling\nblacken-docs\n"

    def test_unpinned_docs_requirements_in_library_not_flagged(self):
        # django: docs/requirements.txt in a library (pyproject [project]) only builds the docs.
        lib = '[project]\nname = "Django"\nrequires-python = ">=3.12"\n'
        for marker in ({"pyproject.toml": lib}, {"setup.py": "from setuptools import setup\nsetup()\n"},
                       {"setup.cfg": "[metadata]\nname = lib\n"}):
            files = dict(marker, **{"docs/requirements.txt": self.DOCS_REQ})
            self.assertEqual(_hits(files, "unpinned-dependency"), [], sorted(marker))
            files = dict(marker, **{"doc/source/requirements.txt": self.DOCS_REQ})
            self.assertEqual(_hits(files, "unpinned-dependency"), [], sorted(marker))
        # The library's other requirement files are still read.
        files = {"pyproject.toml": lib, "docs/requirements.txt": self.DOCS_REQ,
                 "requirements.txt": "requests\n"}
        self.assertEqual([(h["file"], h["line"]) for h in _hits(files, "unpinned-dependency")],
                         [("requirements.txt", 1)])

    def test_docs_requirements_in_application_still_flagged(self):
        # No library marker (an app, example-voting-app style): docs requirements still reported.
        hits = _hits({"docs/requirements.txt": self.DOCS_REQ}, "unpinned-dependency")
        self.assertEqual(sorted(h["line"] for h in hits), [1, 2, 3, 4])
        # A pyproject without [project] / [build-system] (tool config only) is not a library.
        hits = _hits({"pyproject.toml": "[tool.black]\nline-length = 100\n",
                      "docs/requirements.txt": self.DOCS_REQ}, "unpinned-dependency")
        self.assertEqual(len(hits), 4)
        # App service requirements in a repo with a library marker elsewhere are still read.
        hits = _hits({"vote/requirements.txt": "Flask\nRedis\n",
                      "pyproject.toml": '[project]\nname = "x"\n'}, "unpinned-dependency")
        self.assertEqual(sorted(h["line"] for h in hits), [1, 2])


class InfraReviewTests(unittest.TestCase):
    def test_final_stage_inherits_user_from_earlier_stage(self):  # M7
        ok = ("FROM node:20.11 AS base\nRUN useradd -m app\nUSER app\nFROM base\n"
              "COPY . .\nCMD [\"node\", \"s.js\"]\n")
        self.assertEqual(_hits({"Dockerfile": ok}, "docker-root-user"), [])
        chain = ok.replace("FROM base\n", "FROM base AS mid\nRUN true\nFROM mid\n")
        self.assertEqual(_hits({"Dockerfile": chain}, "docker-root-user"), [])
        root = "FROM node:20.11 AS base\nUSER root\nFROM base\nCMD [\"node\", \"s.js\"]\n"
        hits = _hits({"Dockerfile": root}, "docker-root-user")
        self.assertEqual([(h["line"], h["confidence"]) for h in hits], [(3, "medium")])
        none = "FROM node:20.11 AS base\nRUN true\nFROM base\nCMD [\"node\"]\n"
        self.assertEqual([h["line"] for h in _hits({"Dockerfile": none}, "docker-root-user")], [3])

    def test_k8s_kind_with_trailing_comment(self):  # M8
        dep = ("apiVersion: apps/v1\nkind: Deployment # web tier\nmetadata:\n  name: web\n"
               "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n"
               "          image: r.example.test/web:1.0\n          ports:\n"
               "            - containerPort: 80\n")
        rules = {s["rule"] for s in _signals({"k8s/web.yaml": dep})}
        self.assertTrue({"k8s-no-probes", "k8s-no-limits"} <= rules)

    def test_rule_versions(self):
        for rid in ("docker-root-user", "docker-no-healthcheck", "k8s-no-probes", "k8s-no-limits"):
            self.assertEqual(registry.RULES[rid].version, "2", rid)


if __name__ == "__main__":
    unittest.main()
