from __future__ import annotations

import time
import unittest

import helpers  # noqa: F401  (puts the scanner package on sys.path)
from dfscan import taint
from dfscan.text import strip_comments


def _flows(src, lang):
    return taint.flows(strip_comments(src, lang), lang)


class PythonFlowTests(unittest.TestCase):
    def test_flask_arg_concatenated_into_execute(self):
        src = ("from flask import request\n"
               "def search():\n"
               "    name = request.args.get(\"name\")\n"
               "    cur.execute(\"SELECT * FROM users WHERE name = '\" + name + \"'\")\n")
        self.assertEqual(_flows(src, "Python"), [(3, 4, "name")])

    def test_parameterised_query_is_not_a_flow(self):
        src = ("def search():\n    name = request.args[\"name\"]\n"
               "    cur.execute(\"SELECT * FROM users WHERE name = %s\", (name,))\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_assignment_chain_reaches_sink(self):
        src = ("def search():\n    name = request.form[\"name\"]\n"
               "    q = f\"SELECT * FROM users WHERE name = '{name}'\"\n"
               "    cur.execute(q)\n")
        self.assertEqual(_flows(src, "Python"), [(2, 4, "name")])

    def test_reassignment_clears_taint(self):
        src = ("def search():\n    name = request.args[\"name\"]\n    name = \"bob\"\n"
               "    cur.execute(\"SELECT * FROM users WHERE name = '%s'\" % name)\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_taint_does_not_cross_functions(self):
        src = ("def a():\n    name = request.args[\"name\"]\n\n"
               "def b(name):\n    cur.execute(\"SELECT * FROM t WHERE name = '%s'\" % name)\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_module_level_code_after_function_is_a_new_scope(self):
        src = ("def a():\n    name = request.args[\"name\"]\n\n"
               "cur.execute(\"SELECT * FROM t WHERE name = '%s'\" % name)\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_django_view_url_parameter_is_a_source(self):
        src = ("def detail(request, slug):\n"
               "    cursor.execute(\"SELECT * FROM t WHERE slug = '%s'\" % slug)\n")
        self.assertEqual(_flows(src, "Python"), [(1, 2, "slug")])

    def test_fastapi_typed_parameters(self):
        src = ("@app.get(\"/items\")\n"
               "async def items(q: str, limit: int = 10, db: Session = Depends(get_db)):\n"
               "    db.execute(text(f\"SELECT * FROM items WHERE name = '{q}' LIMIT {limit}\"))\n")
        self.assertEqual(_flows(src, "Python"), [(2, 3, "q")])
        src = ("@app.get(\"/items\")\n"
               "async def items(limit: int = 10):\n"
               "    db.execute(text(f\"SELECT * FROM items LIMIT {limit}\"))\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_flask_int_converter_is_not_a_source(self):
        src = ("@app.route(\"/items/<int:item_id>\")\ndef get_item(item_id):\n"
               "    cur.execute(\"SELECT * FROM items WHERE id = %d\" % item_id)\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_cli_arguments_are_sources(self):
        src = ("import sys\n\ndef main():\n    name = sys.argv[1]\n    other = input(\"? \")\n"
               "    cur.execute(\"DELETE FROM t WHERE name = '\" + name + \"'\")\n"
               "    cur.execute(\"DELETE FROM t WHERE name = '\" + other + \"'\")\n")
        self.assertEqual(_flows(src, "Python"), [(4, 6, "name"), (5, 7, "other")])

    def test_multiline_triple_quoted_sql(self):
        src = ("def f():\n    uid = request.args[\"id\"]\n    cur.execute(f\"\"\"\n"
               "        SELECT * FROM users\n        WHERE id = {uid}\n    \"\"\")\n")
        self.assertEqual(_flows(src, "Python"), [(2, 3, "uid")])

    def test_variable_name_inside_plain_string_is_not_a_use(self):
        src = ("def f():\n    name = request.args[\"n\"]\n"
               "    cur.execute(\"SELECT name FROM users WHERE id = 1\")\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_attribute_with_same_name_is_not_the_variable(self):
        src = ("def f(user):\n    name = request.args[\"n\"]\n"
               "    cur.execute(\"SELECT * FROM t WHERE n = '%s'\" % user.name)\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_list_append_then_join(self):
        src = ("def f():\n    parts = []\n    name = request.GET[\"n\"]\n"
               "    parts.append(\"name = '%s'\" % name)\n"
               "    cur.execute(\"SELECT * FROM t WHERE \" + \" AND \".join(parts))\n")
        self.assertEqual(_flows(src, "Python"), [(3, 5, "name")])

    def test_crlf_and_multiline_signature_match_lf_single_line(self):
        one = ("@router.get(\"/items\")\ndef items(q: str, db: SessionDep):\n"
               "    db.execute(text(\"SELECT * FROM items WHERE name = '\" + q + \"'\"))\n")
        multi = ("@router.get(\"/items\")\ndef items(\n    q: str,\n    db: SessionDep,\n):\n"
                 "    db.execute(text(\"SELECT * FROM items WHERE name = '\" + q + \"'\"))\n")
        self.assertEqual(_flows(one, "Python"), [(2, 3, "q")])
        self.assertEqual(_flows(one.replace("\n", "\r\n"), "Python"), [(2, 3, "q")])
        self.assertEqual(_flows(multi, "Python"), [(2, 6, "q")])

    def test_identifier_position_is_marked(self):
        src = ("def f():\n    table = request.args[\"t\"]\n"
               "    cur.execute(f\"SELECT * FROM {table} WHERE id = %s\", (1,))\n")
        got = taint.find_flows(src, "Python")
        self.assertEqual([(f.source_line, f.sink_line, f.variable, f.identifier) for f in got],
                         [(2, 3, "table", True)])
        src = ("def f():\n    sort = request.args[\"s\"]\n"
               "    cur.execute(\"SELECT * FROM t ORDER BY \" + sort)\n")
        self.assertTrue(taint.find_flows(src, "Python")[0].identifier)
        src = ("def f():\n    v = request.args[\"v\"]\n"
               "    cur.execute(\"SELECT * FROM t WHERE v = '\" + v + \"'\")\n")
        self.assertFalse(taint.find_flows(src, "Python")[0].identifier)


class JavaScriptFlowTests(unittest.TestCase):
    def test_express_destructured_query(self):
        src = ("app.get(\"/u\", async (req, res) => {\n  const { id } = req.query;\n"
               "  const rows = await pool.query(\"SELECT * FROM users WHERE id = \" + id);\n});\n")
        self.assertEqual(_flows(src, "JavaScript"), [(2, 3, "id")])

    def test_direct_source_in_template_literal(self):
        src = ("router.get('/u/:id', (req, res) => {\n"
               "  db.query(`SELECT * FROM users WHERE id = ${req.params.id}`);\n});\n")
        self.assertEqual(_flows(src, "TypeScript"), [(2, 2, "req.params.id")])

    def test_tagged_template_and_placeholders_are_safe(self):
        src = ("router.get('/u/:id', async (req, res) => {\n"
               "  await db.query(sql`SELECT * FROM users WHERE id = ${req.params.id}`);\n"
               "  await pool.query(\"SELECT * FROM users WHERE id = $1\", [req.params.id]);\n"
               "  await prisma.$queryRaw`SELECT * FROM users WHERE id = ${req.params.id}`;\n});\n")
        self.assertEqual(_flows(src, "TypeScript"), [])

    def test_number_cast_sanitises(self):
        src = ("app.get('/u', (req, res) => {\n  const id = Number(req.query.id);\n"
               "  db.query(\"SELECT * FROM users WHERE id = \" + id);\n});\n")
        self.assertEqual(_flows(src, "JavaScript"), [])

    def test_query_builder_is_not_raw_sql(self):
        src = ("app.get('/u', (req, res) => {\n"
               "  knex.select('*').from('users').where('id', req.query.id);\n});\n")
        self.assertEqual(_flows(src, "JavaScript"), [])

    def test_knex_raw_fragment(self):
        src = ("app.get('/u', (req, res) => {\n"
               "  knex('users').whereRaw(\"name = '\" + req.query.name + \"'\");\n});\n")
        self.assertEqual(len(_flows(src, "JavaScript")), 1)


class GoFlowTests(unittest.TestCase):
    def test_sprintf_query_into_db_query(self):
        src = ("func h(w http.ResponseWriter, r *http.Request) {\n"
               "\tid := r.URL.Query().Get(\"id\")\n"
               "\tq := fmt.Sprintf(\"SELECT * FROM users WHERE id = '%s'\", id)\n"
               "\trows, err := db.Query(q)\n}\n")
        self.assertEqual(_flows(src, "Go"), [(2, 4, "id")])

    def test_gin_param_into_context_query(self):
        src = ("func get(c *gin.Context) {\n\tname := c.Param(\"name\")\n"
               "\trow := db.QueryRowContext(ctx, \"SELECT * FROM t WHERE name = '\"+name+\"'\")\n}\n")
        self.assertEqual(_flows(src, "Go"), [(2, 3, "name")])

    def test_placeholder_and_atoi_are_safe(self):
        src = ("func get(c *gin.Context) {\n\tname := c.Query(\"name\")\n"
               "\tid, err := strconv.Atoi(c.Param(\"id\"))\n"
               "\tdb.QueryRow(\"SELECT * FROM t WHERE name = $1\", name)\n"
               "\tdb.Exec(fmt.Sprintf(\"DELETE FROM t WHERE id = %d\", id))\n}\n")
        self.assertEqual(_flows(src, "Go"), [])

    def test_bind_target_is_tainted(self):
        src = ("func create(c *gin.Context) {\n\tvar in Input\n\tc.ShouldBindJSON(&in)\n"
               "\tdb.Exec(\"INSERT INTO t (n) VALUES ('\" + in.Name + \"')\")\n}\n")
        self.assertEqual(_flows(src, "Go"), [(3, 4, "in")])


class JvmFlowTests(unittest.TestCase):
    def test_spring_path_variable(self):
        src = ("@GetMapping(\"/{id}\")\n"
               "public List<Order> find(@PathVariable String id) {\n"
               "    return jdbc.queryForList(\"SELECT * FROM orders WHERE id = '\" + id + \"'\");\n}\n")
        self.assertEqual(_flows(src, "Java"), [(2, 3, "id")])

    def test_spring_numeric_parameter_is_safe(self):
        src = ("@GetMapping(\"/{id}\")\n"
               "public List<Order> find(@PathVariable(\"id\") Long id) {\n"
               "    return jdbc.queryForList(\"SELECT * FROM orders WHERE id = \" + id);\n}\n")
        self.assertEqual(_flows(src, "Java"), [])

    def test_string_builder_and_get_parameter(self):
        src = ("void doGet(HttpServletRequest request) {\n"
               "    String name = request.getParameter(\"name\");\n"
               "    StringBuilder sb = new StringBuilder(\"SELECT * FROM t WHERE name = '\");\n"
               "    sb.append(name).append(\"'\");\n"
               "    stmt.executeQuery(sb.toString());\n}\n")
        self.assertEqual(_flows(src, "Java"), [(2, 5, "name")])

    def test_kotlin_request_param_template(self):
        src = ("fun find(@RequestParam name: String): List<Row> {\n"
               "    return jdbc.queryForList(\"SELECT * FROM t WHERE name = '$name'\")\n}\n")
        self.assertEqual(_flows(src, "Kotlin"), [(1, 2, "name")])


class CSharpFlowTests(unittest.TestCase):
    def test_controller_action_parameter_allman(self):
        src = ("    [HttpGet(\"{id}\")]\n    public IActionResult Get(string id)\n    {\n"
               "        var cmd = new SqlCommand(\"SELECT * FROM Orders WHERE Id = '\" + id + \"'\", _db);\n"
               "        return Ok(cmd.ExecuteScalar());\n    }\n")
        self.assertEqual(_flows(src, "C#"), [(2, 4, "id")])

    def test_from_query_into_execute_sql_raw(self):
        src = ("public IActionResult Delete([FromQuery] string name)\n{\n"
               "    _db.Database.ExecuteSqlRaw($\"DELETE FROM t WHERE name = '{name}'\");\n"
               "    return Ok();\n}\n")
        self.assertEqual(_flows(src, "C#"), [(1, 3, "name")])

    def test_request_query_and_command_text(self):
        src = ("public void Run()\n{\n    var q = Request.Query[\"q\"];\n"
               "    cmd.CommandText = \"SELECT * FROM t WHERE q = '\" + q + \"'\";\n}\n")
        self.assertEqual(_flows(src, "C#"), [(3, 4, "q")])

    def test_int_parameter_and_parameters_are_safe(self):
        src = ("[HttpGet(\"{id}\")]\npublic IActionResult Get(int id, string name)\n{\n"
               "    var a = new SqlCommand(\"SELECT * FROM t WHERE Id = \" + id, _db);\n"
               "    var b = _db.Query<Order>(\"SELECT * FROM t WHERE Name = @name\", new { name });\n}\n")
        self.assertEqual(_flows(src, "C#"), [])


class PhpFlowTests(unittest.TestCase):
    def test_laravel_request_input_interpolated(self):
        src = ("<?php\nclass C extends Controller {\n"
               "    public function index(Request $request) {\n"
               "        $name = $request->input('name');\n"
               "        return DB::select(\"SELECT * FROM users WHERE name = '$name'\");\n    }\n}\n")
        self.assertEqual(_flows(src, "PHP"), [(4, 5, "$name")])

    def test_controller_route_parameter(self):
        src = ("<?php\nclass OrderController extends Controller\n{\n"
               "    public function show(string $id)\n    {\n"
               "        return DB::select(\"SELECT * FROM orders WHERE id = \" . $id);\n    }\n}\n")
        self.assertEqual(_flows(src, "PHP"), [(4, 6, "$id")])

    def test_superglobal_into_wpdb(self):
        src = "<?php\nfunction f() {\n    $wpdb->query(\"DELETE FROM t WHERE id = \" . $_GET['id']);\n}\n"
        self.assertEqual(_flows(src, "PHP"), [(3, 3, "$_GET['id']")])

    def test_bindings_and_intval_are_safe(self):
        src = ("<?php\nfunction f() {\n    $name = request('name');\n    $id = intval($_GET['id']);\n"
               "    DB::select(\"SELECT * FROM users WHERE name = ?\", [$name]);\n"
               "    $wpdb->query(\"DELETE FROM t WHERE id = \" . $id);\n}\n")
        self.assertEqual(_flows(src, "PHP"), [])


class RubyFlowTests(unittest.TestCase):
    def test_params_interpolated_into_exec_query(self):
        src = ("class OrdersController < ApplicationController\n  def show\n"
               "    rows = ActiveRecord::Base.connection.exec_query(\"SELECT * FROM orders WHERE id = #{params[:id]}\")\n"
               "  end\nend\n")
        self.assertEqual(_flows(src, "Ruby"), [(3, 3, "params[:id]")])

    def test_where_fragment_vs_hash_and_placeholder(self):
        bad = "def index\n  User.where(\"name = '#{params[:name]}'\")\nend\n"
        self.assertEqual(len(_flows(bad, "Ruby")), 1)
        good = ("def index\n  User.where(name: params[:name])\n"
                "  User.where(\"name = ?\", params[:name])\n  User.where(id: params[:id].to_i)\nend\n")
        self.assertEqual(_flows(good, "Ruby"), [])

    def test_heredoc_sql(self):
        src = ("def search\n  term = params[:q]\n  sql = <<~SQL\n"
               "    SELECT * FROM posts WHERE title LIKE '%#{term}%'\n  SQL\n"
               "  Post.find_by_sql(sql)\nend\n")
        self.assertEqual(_flows(src, "Ruby"), [(2, 6, "term")])

    def test_server_value_in_migration_is_not_a_source(self):
        src = ("class AddConfirmable < ActiveRecord::Migration[7.0]\n  def up\n"
               "    execute(\"UPDATE users SET confirmed_at = '#{DateTime.now}'\")\n  end\nend\n")
        self.assertEqual(_flows(src, "Ruby"), [])


class FixRound1Tests(unittest.TestCase):
    """Task 7 review, fix round 1."""

    def test_module_constant_sql_text(self):
        src = ("QUERY = \"SELECT * FROM users WHERE name = '%s'\"\nBASE = \"SELECT * FROM t WHERE id = \"\n\n\n"
               "def a():\n    cursor.execute(QUERY % request.args[\"n\"])\n\n\n"
               "def b():\n    cursor.execute(BASE + request.args[\"id\"])\n")
        self.assertEqual(_flows(src, "Python"), [(6, 6, "request.args[\"n\"]"),
                                                 (10, 10, "request.args[\"id\"]")])

    def test_local_non_sql_assignment_shadows_module_constant(self):
        src = ("BASE = \"SELECT * FROM t WHERE id = \"\n\n\ndef b():\n    BASE = \"x\"\n"
               "    cursor.execute(BASE + request.args[\"id\"])\n")
        self.assertEqual(_flows(src, "Python"), [])

    def test_java_class_constant_sql_text(self):
        src = ("public class Repo {\n"
               "    private static final String SQL = \"SELECT * FROM orders WHERE id = '\";\n\n"
               "    @GetMapping(\"/o\")\n    public List<Order> find(@RequestParam String id) {\n"
               "        return jdbc.queryForList(SQL + id + \"'\");\n    }\n}\n")
        self.assertEqual(_flows(src, "Java"), [(5, 6, "id")])

    def test_reassignment_in_branch_keeps_taint(self):
        src = ("def search():\n    q = request.args.get(\"q\")\n    if q is None:\n        q = \"\"\n"
               "    cursor.execute(\"SELECT * FROM t WHERE q = '\" + q + \"'\")\n")
        self.assertEqual(_flows(src, "Python"), [(2, 5, "q")])
        one_line = ("def search():\n    q = request.args.get(\"q\")\n    if q is None: q = \"\"\n"
                    "    cursor.execute(\"SELECT * FROM t WHERE q = '\" + q + \"'\")\n")
        self.assertEqual(_flows(one_line, "Python"), [(2, 4, "q")])
        rb = ("def s\n  q = params[:q]\n  q = \"\" if q.nil?\n"
              "  Post.find_by_sql(\"SELECT * FROM posts WHERE t = '#{q}'\")\nend\n")
        self.assertEqual(_flows(rb, "Ruby"), [(2, 4, "q")])

    def test_hash_and_map_conditions_are_not_sql(self):
        rb = ("def s\n  User.find_by(\"email\" => params[:email])\n"
              "  User.where(\"email\" => params[:email], \"active\" => true)\n"
              "  User.where(email: params[:email])\nend\n")
        self.assertEqual(_flows(rb, "Ruby"), [])
        go = ("func h(c *gin.Context) {\n\tname := c.Query(\"name\")\n"
              "\tdb.Where(map[string]interface{}{\"name\": name}).Find(&users)\n}\n")
        self.assertEqual(_flows(go, "Go"), [])

    def test_format_placeholder_table_is_identifier(self):
        for lang, src in {
            "Python": ("def f():\n    t = request.args[\"t\"]\n"
                       "    cursor.execute(\"SELECT * FROM %s\" % t)\n"
                       "    cursor.execute(\"SELECT * FROM {}\".format(t))\n"),
            "Java": ("void f(@RequestParam String t) {\n"
                     "    jdbc.queryForList(String.format(\"SELECT * FROM %s\", t));\n}\n"),
            "Go": ("func h(c *gin.Context) {\n\tt := c.Query(\"t\")\n"
                   "\tdb.Query(fmt.Sprintf(\"SELECT * FROM %s\", t))\n}\n"),
        }.items():
            got = taint.find_flows(src, lang)
            self.assertTrue(got, lang)
            self.assertTrue(all(f.identifier for f in got), lang)

    def test_string_join_is_not_sql_join(self):
        src = ("def f():\n    parts = []\n    name = request.GET[\"n\"]\n"
               "    parts.append(\"name = '%s'\" % name)\n"
               "    cur.execute(\"SELECT * FROM t WHERE \" + \"\".join(parts))\n")
        self.assertFalse(taint.find_flows(src, "Python")[0].identifier)

    def test_go_label_includes_call_argument(self):
        src = ("func get(c *gin.Context) {\n"
               "\tdb.Exec(\"DELETE FROM t WHERE name = '\" + c.Param(\"name\") + \"'\")\n}\n")
        self.assertEqual(_flows(src, "Go"), [(2, 2, "c.Param(\"name\")")])

    def test_unclosed_attribute_and_annotation_lists_are_linear(self):
        bodies = {
            "C#": "public void F(" + "[FromQuery " * 16000 + "string x)\n{\n}\n",
            "Java": "void f(" + "@RequestParam( " * 16000 + "String x) {\n}\n",
            "Kotlin": "fun f(" + "@PathVariable( " * 16000 + "x: String) {\n}\n",
            "TypeScript": "find(" + "@Param( " * 16000 + "id: string) {\n}\n",
        }
        for lang, src in bodies.items():
            sink = "_db.Query(" if lang == "C#" else "db.query("
            src += "q = " + sink + "\"SELECT * FROM t WHERE a = \" + x)\n"  # pass the file pre-check
            self.assertTrue(taint._worth_analysing(src, taint._lang_key(lang)), lang)
            t0 = time.perf_counter()
            taint.flows(src, lang)
            self.assertLess(time.perf_counter() - t0, 3.0, lang)


class LinearTimeTests(unittest.TestCase):
    def test_long_lines_are_fast(self):
        bodies = {
            "Python": ("def f(request, a):\n    q = \"SELECT * FROM t WHERE a = '\" + a"
                       + " + a" * 30000 + "\n    cur.execute(" + "(" * 30000 + "q\n"
                       + "    x = " + "int(" * 20000 + "a\n" + "    y = \"" + "{" * 50000 + "\n"),
            "JavaScript": ("app.get('/', (req, res) => {\n  db.query(`SELECT * FROM t WHERE a = "
                           + "${req.query.a}" * 20000 + "`);\n  const s = '" + "\\'" * 50000 + "\n});\n"),
            "Ruby": "def f\n  x = " + "params[:a].to_i." * 20000 + "to_i\n  User.where(\"#{x}\")\nend\n",
            "PHP": "<?php\nfunction f() {\n  $q = \"" + "$a->" * 30000 + "\";\n}\n",
            "Go": ("func h(w http.ResponseWriter, r *http.Request) {\n\tid := r.FormValue(\"id\")\n\t"
                   + "db.Query(" * 20000 + "\"SELECT * FROM t WHERE id = \" + id\n}\n"),
        }
        for lang, src in bodies.items():
            t0 = time.perf_counter()
            taint.flows(src, lang)
            self.assertLess(time.perf_counter() - t0, 3.0, lang)


if __name__ == "__main__":
    unittest.main()
