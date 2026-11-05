# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import sys
import unittest
from unittest import mock

import helpers
from viewer.check import run as run_checks
from viewer.config import ConfigError
from viewer.plugins import PluginError, deep_merge, validate_types

SERVER = '''
def register(api):
    api.add_kind("note", [".note"], claim)
    api.enrich("model", enrich)
    api.after_catalog(after)
    api.catalog_extra(lambda: {"count": api.config["count"], "wrote": api.config.get("wrote")})
    api.serve("fixture-data")
    api.writes("fixture-out")
    api.route("POST", "echo", echo, max_body=100)
    api.route("POST", "studio/<name:[A-Za-z]\\\\w{0,10}>", studio, max_body=1000, token="optional-if-no-origin",
              content_types=("application/json", "text/plain"))
    api.route("GET", "item/<name>", item)
    api.route("GET", "boom", boom)
    api.route("GET", "teapot", teapot)
    api.check(check)
    api.client_config(lambda: {"shown": api.config["count"]})
    api.route("POST", "write", write)
    api.route("POST", "badwrite", badwrite)
    API["api"] = api


API = {}


def claim(path, rel, st, ctx):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("NOTE"):
        return [{"id": rel + "#a", "name": "a", "dir": ctx.label("notes"), "kind": "note"}, {"id": rel + "#b", "name": "b"}]
    if text.startswith("BAD"):
        raise ValueError("cannot read this note")
    return None


def enrich(item, ctx):
    return {"sidecar": item["name"] + ".json" in ctx.siblings, "cache": ctx.cache.setdefault("n", 0)}


def after(items):
    for i in items:
        if i["kind"] == "note":
            i["after"] = len(items)


def echo(req):
    return req.json()


def studio(req):
    return {"name": req.params["name"], "size": len(req.body), "q": req.query}


def item(req):
    return API["api"].json_response({"name": req.params["name"]})


def write(req):
    API["api"].project.write_atomic("fixture-out/w.txt", req.body)
    return {"ok": True}


def badwrite(req):
    API["api"].project.write_atomic("elsewhere/w.txt", req.body)
    return {"ok": True}


def boom(req):
    raise RuntimeError("secret internals")


def teapot(req):
    raise API["api"].HttpError(418, "short and stout")


def check(report):
    n = len([i for i in report.items if i["kind"] == "note"])
    report.ok("%d notes" % n)
    if n > 2:
        report.fail("too many notes")
'''

MANIFEST = {"name": "fixture", "apiVersion": 1, "server": "server.py", "client": "client.js", "styles": ["style.css"],
            "files": ["view.js"], "defaults": {"count": 1, "nested": {"a": 1, "b": 2}},
            "types": {"count": "number", "nested": {"*": "number"}, "wrote": "string|null"}}


class PluginCase(helpers.ProjectCase):
    def plugin(self, name="fixture", manifest=None, server=SERVER, base="plugdir"):
        m = dict(MANIFEST if manifest is None else manifest)
        m.setdefault("name", name)
        self.write("%s/%s/plugin.json" % (base, name), m)
        for f in ("client.js", "style.css", "view.js"):
            self.write("%s/%s/%s" % (base, name, f), "// %s" % f)
        if server is not None:
            self.write("%s/%s/server.py" % (base, name), server)
        return os.path.join(self.project_dir, base, name)

    def app_with(self, plugins=None, trust=True, **extra):
        cfg = {"roots": ["assets"], "pluginDirs": ["plugdir"], "plugins": {"fixture": {}} if plugins is None else plugins}
        cfg.update(extra)
        return self.make_app(cfg, trust_project_plugins=trust)


class PluginTest(PluginCase):
    def test_claim_enrich_after_extra_and_kinds(self):
        self.plugin()
        self.write("assets/n.note", "NOTE one")
        self.write("assets/other.note", "plain")
        self.write("assets/m.fbx")
        self.write("assets/m.json", "{}")
        self.write("assets/solo.fbx")
        app = self.app_with()
        cat = self.catalog(app)
        notes = [i for i in cat["items"] if i["kind"] == "note"]
        self.assertEqual([n["id"] for n in notes], ["assets/n.note#a", "assets/n.note#b"])
        self.assertEqual(notes[0]["dir"], "assets/notes")
        self.assertEqual(notes[1]["dir"], "assets")  # the core filled in what the plugin left out
        self.assertEqual((notes[0]["path"], notes[0]["mtime"] > 0), ("assets/n.note", True))
        self.assertEqual(notes[0]["after"], len(cat["items"]))
        models = {i["name"]: i for i in cat["items"] if i["kind"] == "model"}
        self.assertEqual(models["m"]["ext"]["fixture"]["sidecar"], True)
        self.assertEqual(models["solo"]["ext"]["fixture"]["sidecar"], False)
        self.assertEqual(cat["plugins"]["fixture"], {"count": 1, "wrote": None})
        self.assertEqual(app.categories()[0], ["model", "texture", "vfx", "sound", "note"])

    def test_a_failing_claim_is_a_warning_and_the_scan_goes_on(self):  # audit H3
        self.plugin()
        self.write("assets/bad.note", "BAD")
        self.write("assets/ok.note", "NOTE")
        self.write("assets/a.png")
        cat = self.catalog(self.app_with())
        self.assertEqual(len(cat["items"]), 3)
        self.assertTrue(any("bad.note" in w and "cannot read" in w for w in cat["warnings"]))

    def test_core_kinds_claim_their_extensions_first(self):
        self.plugin(server=SERVER.replace('[".note"]', '[".note", ".png"]'))
        with self.assertRaisesRegex(PluginError, r"\.png belongs to the core kind texture"):
            self.app_with()

    def test_enrich_failure_is_a_warning(self):
        server = SERVER.replace('return {"sidecar"', 'raise KeyError("x")\n    return {"sidecar"')
        self.plugin(server=server)
        self.write("assets/m.fbx")
        cat = self.catalog(self.app_with())
        self.assertEqual(len(cat["items"]), 1)
        self.assertNotIn("ext", cat["items"][0])
        self.assertTrue(any("plugin fixture" in w for w in cat["warnings"]))

    def test_config_merges_defaults_and_is_validated(self):
        self.plugin()
        app = self.app_with({"fixture": {"count": 5, "nested": {"b": 3}}})
        p = app.state.registry.get("fixture")
        self.assertEqual(p.config, {"count": 5, "nested": {"a": 1, "b": 3}})
        with self.assertRaisesRegex(PluginError, r"plugins\.fixture\.count: number expected"):
            self.app_with({"fixture": {"count": "x"}})
        with self.assertRaisesRegex(PluginError, r"plugins\.fixture\.nested\.b: number expected"):
            self.app_with({"fixture": {"nested": {"b": "x"}}})
        app = self.app_with({"fixture": {"surprise": 1}})
        self.assertIn("plugins.fixture.surprise: unknown key", app.state.registry.warnings)

    def test_disabled_plugin_does_not_load(self):
        self.plugin()
        app = self.app_with({"fixture": {"enabled": False}})
        self.assertEqual(app.state.registry.plugins, [])
        self.assertEqual(app.categories()[0], ["model", "texture", "vfx", "sound"])

    def test_missing_plugin(self):
        with self.assertRaisesRegex(PluginError, "plugin nope not found"):
            self.app_with({"nope": {}})

    def test_wrong_api_version_is_refused(self):
        self.plugin(manifest={**MANIFEST, "apiVersion": 2})
        with self.assertRaisesRegex(PluginError, "plugin API version 2; this viewer supports 1"):
            self.app_with()

    def test_requires(self):
        self.plugin(manifest={**MANIFEST, "requires": ["base"]})
        with self.assertRaisesRegex(PluginError, "requires base, which is not enabled"):
            self.app_with()
        base = dict(MANIFEST, name="base", server=None, client=None, styles=[], files=[], requires=[])
        base.pop("server"), base.pop("client")
        self.plugin("base", manifest=base, server=None)
        app = self.app_with({"fixture": {}, "base": {}})
        self.assertEqual([p.name for p in app.state.registry.plugins], ["base", "fixture"])  # required first

    def test_required_config_is_readable_only_for_required_plugins(self):
        base = dict(MANIFEST, name="base", requires=[], defaults={"dir": "d", "n": 1})
        base.pop("server"), base.pop("client")
        self.plugin("base", manifest=base, server=None)
        reader = ("def register(api):\n    seen.append(api.required_config('base'))\n    try:\n        api.required_config('nope')\n"
                  "    except Exception as e:\n        seen.append(type(e).__name__)\n\nseen = []\n")
        self.plugin(manifest={**MANIFEST, "requires": ["base"], "types": {}}, server=reader)
        self.app_with({"base": {"n": 2}, "fixture": {}})
        import sys
        self.assertEqual(sys.modules["viewer_plugin_fixture"].seen, [{"dir": "d", "n": 2}, "PluginError"])

    def test_require_loop(self):
        a = dict(MANIFEST, name="a", requires=["b"])
        b = dict(MANIFEST, name="b", requires=["a"])
        self.plugin("a", manifest=a)
        self.plugin("b", manifest=b)
        with self.assertRaisesRegex(PluginError, "loop"):
            self.app_with({"a": {}, "b": {}})

    def test_name_clash_between_plugin_dirs(self):
        self.plugin()
        self.plugin(base="plugdir2")
        with self.assertRaisesRegex(PluginError, "found twice"):
            self.app_with(pluginDirs=["plugdir", "plugdir2"])

    def test_manifest_name_must_match_folder(self):
        self.plugin(manifest={**MANIFEST, "name": "other"})
        self.write("plugdir/fixture/plugin.json", {**MANIFEST, "name": "other"})
        with self.assertRaisesRegex(PluginError, "name must be fixture"):
            self.app_with()

    def test_manifest_problems(self):
        for bad, pattern in [({"styles": "x"}, "styles must be an array"), ({"client": "nope.js"}, "client nope.js not found"),
                             ({"files": ["../x.js"]}, "not a plain relative path"), ({"defaults": 1}, "defaults must be an object")]:
            with self.subTest(bad=bad):
                self.plugin(manifest={**MANIFEST, **bad})
                with self.assertRaisesRegex(PluginError, pattern):
                    self.app_with()

    def test_server_errors_are_plugin_errors(self):
        for server, pattern in [("raise ValueError('boom')", "failed to load: ValueError: boom"), ("x = 1", "no register"),
                                ("def register(api):\n    raise KeyError('k')", "register failed"),
                                ("def register(api):\n    api.writes('../out')", "area")]:
            with self.subTest(pattern=pattern):
                self.plugin(server=server)
                with self.assertRaisesRegex(PluginError, pattern):
                    self.app_with()

    def test_plugin_dir_must_be_inside_the_project(self):
        with self.assertRaisesRegex(ConfigError, "pluginDirs"):
            self.app_with(pluginDirs=["../elsewhere"])

    def test_duplicate_kind_and_core_kind_are_refused(self):
        self.plugin(server='def register(api):\n    api.add_kind("model", [".zzz"], None)')
        with self.assertRaisesRegex(PluginError, "not a valid new kind"):
            self.app_with()

    def test_write_confinement(self):
        self.plugin()
        app = self.app_with()
        proj = app.state.registry.plugins[0]
        api = __import__("sys").modules["viewer_plugin_fixture"].API["api"]
        api.project.write_atomic("fixture-out/x.txt", b"ok")
        with open(os.path.join(self.project_dir, "fixture-out", "x.txt"), "rb") as f:
            self.assertEqual(f.read(), b"ok")
        from viewer.project import PathError
        for rel in ("elsewhere.txt", "fixture-data/x.txt", "../x", "fixture-outer/x"):
            with self.assertRaises(PathError):
                api.project.write_atomic(rel, b"no")
        self.assertEqual(proj.name, "fixture")

    def test_plugin_sees_the_config_folder_while_registering(self):
        self.plugin(server="def register(api):\n    where = api.project.config_dir\n    api.catalog_extra(lambda: where)")
        app = self.make_app({"pluginDirs": ["plugdir"], "plugins": {"fixture": {}}}, config_path="cfg/viewer.json",
                            config_arg="cfg/viewer.json", trust_project_plugins=True)
        self.assertEqual(self.catalog(app)["plugins"]["fixture"], "cfg")

    def test_check_hook(self):
        self.plugin()
        self.write("assets/a.note", "NOTE")
        out = []
        report = run_checks(self.app_with(), out=out.append)
        self.assertEqual(report.failures, [])
        self.assertIn("ok: 2 notes", out)
        self.write("assets/b.note", "NOTE")
        out = []
        report = run_checks(self.app_with(), out=out.append)
        self.assertEqual(report.failures, ["too many notes"])
        self.assertIn("FAIL: too many notes", out)

    def test_check_that_crashes_is_a_failure(self):
        self.plugin(server='def register(api):\n    api.check(lambda r: 1 / 0)')
        report = run_checks(self.app_with(), out=lambda s: None)
        self.assertTrue(report.failures[0].startswith("plugin fixture: check crashed"))

    def test_public_config_lists_client_files_and_config(self):
        self.plugin()
        cfg = self.app_with().public_config()
        self.assertEqual(cfg["plugins"], [{"name": "fixture", "client": "/plugins/fixture/client.js",
                                           "styles": ["/plugins/fixture/style.css"], "config": {"shown": 1}}])
        self.assertEqual(cfg["categories"][-1], "note")

    def edit_config(self, **changes):
        cfg = {"roots": ["assets"], "pluginDirs": ["plugdir"], "plugins": {"fixture": {}}, **changes}
        path = self.write("asset-viewer.json", cfg)
        os.utime(path, (os.path.getmtime(path) + 5 + len(self.edits),) * 2)
        self.edits.append(cfg)

    def setUp(self):
        super().setUp()
        self.edits = []

    def test_a_config_reload_never_loads_plugin_code_again(self):
        self.plugin(server=SERVER + "\nimport os as _os\nwith open(_os.environ['LOADS'], 'a') as _f:\n    _f.write('x')\n")
        loads = os.path.join(self.tmp, "loads.txt")
        with mock.patch.dict(os.environ, {"LOADS": loads}):
            app = self.app_with()
            self.assertEqual(self.catalog(app)["plugins"]["fixture"]["count"], 1)
            self.edit_config(title="Renamed", roots=["assets", "more"])
            app.refresh(force=True)
            self.edit_config(title="Again", plugins={"fixture": {}}, hide=["assets/x"])
            app.refresh(force=True)
        with open(loads) as f:
            self.assertEqual(f.read(), "x")  # the module ran once, at start
        self.assertEqual((app.state.config.title, app.notices), ("Again", []))

    def test_other_config_changes_apply_at_once(self):
        self.plugin()
        self.write("assets/a.png")
        self.write("more/b.png")
        app = self.app_with()
        self.assertEqual(self.ids(app), ["assets/a.png"])
        self.edit_config(roots=["assets", "more"], hide=["assets/a.png"])
        app.refresh(force=True)
        self.assertEqual(self.ids(app), ["more/b.png"])
        self.assertIsNone(app.error)

    def test_a_plugins_own_config_is_applied_at_once(self):
        self.plugin()
        app = self.app_with()
        self.assertEqual(self.catalog(app)["plugins"]["fixture"]["count"], 1)
        self.edit_config(plugins={"fixture": {"count": 9, "nested": {"b": 7}}})
        app.refresh(force=True)
        self.assertEqual(self.catalog(app)["plugins"]["fixture"]["count"], 9)  # no restart
        self.assertEqual(app.state.registry.get("fixture").config, {"count": 9, "nested": {"a": 1, "b": 7}})
        self.assertEqual(app.public_config()["plugins"][0]["config"], {"shown": 9})
        self.assertEqual((app.notices, app.error), ([], None))
        self.assertNotIn("restart", " ".join(app.public_config()["warnings"]))

    def test_state_a_plugin_derives_from_its_config_is_rebuilt(self):  # register runs again, on the module already imported
        self.plugin(server="def register(api):\n    n = api.config['count'] * 10\n    api.catalog_extra(lambda: n)\n")
        app = self.app_with()
        self.assertEqual(self.catalog(app)["plugins"]["fixture"], 10)
        self.edit_config(plugins={"fixture": {"count": 4}})
        app.refresh(force=True)
        self.assertEqual(self.catalog(app)["plugins"]["fixture"], 40)

    def test_routes_and_areas_follow_the_new_config(self):
        server = ("def register(api):\n    d = api.config['dir']\n    api.serve(d)\n    api.writes(d)\n"
                  "    api.route('GET', 'where', lambda req: {'dir': d})\n")
        self.plugin(manifest={**MANIFEST, "defaults": {"dir": "one"}, "types": {}}, server=server)
        self.write("one/a.json", "{}")
        self.write("two/b.json", "{}")
        app = self.app_with()
        client = self.serve(app)
        self.assertEqual((client.get("/files/one/a.json").status, client.get("/files/two/b.json").status), (200, 404))
        self.edit_config(plugins={"fixture": {"dir": "two"}})
        app.refresh(force=True)
        self.assertEqual((client.get("/files/one/a.json").status, client.get("/files/two/b.json").status), (404, 200))
        self.assertEqual(client.get("/api/plugins/fixture/where").json(), {"dir": "two"})

    def test_a_bad_plugin_config_is_reported_and_the_plugin_keeps_its_old_one(self):
        self.plugin()
        app = self.app_with()
        self.edit_config(plugins={"fixture": {"count": "bad"}}, title="Other")
        app.refresh(force=True)
        self.assertIn("plugins.fixture.count: number expected", app.error)
        self.assertEqual(self.catalog(app)["plugins"]["fixture"]["count"], 1)
        self.assertEqual(app.state.config.title, "Other")  # the rest of the edit still applied
        self.assertEqual(app.public_config()["error"], app.error)
        self.edit_config(plugins={"fixture": {"count": 5}})
        app.refresh(force=True)
        self.assertEqual((app.error, self.catalog(app)["plugins"]["fixture"]["count"]), (None, 5))

    def test_enabling_or_disabling_a_plugin_or_changing_plugin_dirs_needs_a_restart_and_says_so(self):
        self.plugin()
        self.plugin("late", base="plugdir")
        app = self.app_with()
        for change in ({"plugins": {"fixture": {}, "late": {}}}, {"plugins": {}}, {"plugins": {"fixture": {"enabled": False}}},
                       {"pluginDirs": ["plugdir", "elsewhere"]}):
            with self.subTest(change=change):
                self.edit_config(**change)
                app.refresh(force=True)
                self.assertEqual(len(app.notices), 1)
                self.assertIn("restart", app.public_config()["warnings"][-1])
                self.assertEqual([p.name for p in app.state.registry.plugins], ["fixture"])  # nothing was loaded or dropped
        self.edit_config()  # put back: no notice
        app.refresh(force=True)
        self.assertEqual(app.notices, [])

    def test_a_config_edit_never_imports_a_plugin_again(self):
        self.plugin(server=SERVER + "\nBORN = object()\n")
        app = self.app_with()
        module = sys.modules["viewer_plugin_fixture"]
        born = module.BORN
        self.edit_config(plugins={"fixture": {"count": 2}})
        app.refresh(force=True)
        self.assertIs(sys.modules["viewer_plugin_fixture"], module)
        self.assertIs(sys.modules["viewer_plugin_fixture"].BORN, born)
        self.write("plugdir/fixture/server.py", SERVER + "\nBORN = 'edited on disk'\n")  # a change on disk is not picked up
        self.edit_config(plugins={"fixture": {"count": 3}})
        app.refresh(force=True)
        self.assertIs(sys.modules["viewer_plugin_fixture"].BORN, born)
        self.assertEqual(self.catalog(app)["plugins"]["fixture"]["count"], 3)

    def test_a_new_plugin_dir_in_the_config_is_not_loaded_on_reload(self):
        self.plugin()
        self.plugin("late", base="plugdir2")
        app = self.app_with()
        self.edit_config(pluginDirs=["plugdir", "plugdir2"], plugins={"fixture": {}, "late": {}})
        app.refresh(force=True)
        self.assertEqual([p.name for p in app.state.registry.plugins], ["fixture"])
        self.assertTrue(app.notices)

    def test_a_bad_config_edit_keeps_the_old_state(self):
        self.plugin()
        app = self.app_with()
        self.edit_config(plugins={"fixture": {"count": "bad"}}, port="x")
        app.refresh(force=True)
        self.assertIn("port: integer", app.error)
        self.assertEqual(app.state.config.plugins["fixture"], {})
        self.assertEqual(app.public_config()["error"], app.error)


class TrustTest(PluginCase):
    """Plugins from the project's pluginDirs are code the project brings along: they load only when the user says so."""

    MARKER = "import os\nwith open(os.environ['AV_MARKER'], 'a') as f:\n    f.write('ran')\n"

    def setUp(self):
        super().setUp()
        self.marker = os.path.join(self.tmp, "marker.txt")
        self.plugin(server=self.MARKER + SERVER)
        self.write("assets/a.png")
        env = mock.patch.dict(os.environ, {"AV_MARKER": self.marker})
        env.start()
        self.addCleanup(env.stop)

    def ran(self):
        return os.path.exists(self.marker)

    def test_project_plugins_do_not_run_without_the_flag(self):
        app = self.app_with(trust=False)
        self.assertFalse(self.ran())
        self.assertEqual(app.state.registry.plugins, [])
        self.assertEqual(self.ids(app), ["assets/a.png"])  # the rest works
        warning = " ".join(app.state.registry.warnings)
        self.assertIn("--trust-project-plugins", warning)
        self.assertIn("plugdir", warning)
        self.assertIn("fixture", warning)
        self.assertEqual(app.public_config()["plugins"], [])
        self.assertTrue(any("--trust-project-plugins" in w for w in app.public_config()["warnings"]))

    def test_check_does_not_run_them_either(self):
        out = []
        report = run_checks(self.app_with(trust=False), out=out.append)
        self.assertFalse(self.ran())
        self.assertEqual(report.failures, [])
        self.assertTrue(any(l.startswith("warning:") and "--trust-project-plugins" in l for l in out), out)

    def test_with_the_flag_they_load(self):
        app = self.app_with(trust=True)
        self.assertTrue(self.ran())
        self.assertEqual([p.name for p in app.state.registry.plugins], ["fixture"])
        self.assertEqual(app.state.registry.warnings, [])

    def test_a_project_dir_without_enabled_plugins_still_warns_and_loads_nothing(self):
        app = self.app_with({}, trust=False)
        self.assertFalse(self.ran())
        self.assertTrue(app.state.registry.warnings)

    def test_bundled_plugins_always_load(self):
        self.put(self.tool_dir, "plugins/bundled/plugin.json", {"name": "bundled", "apiVersion": 1, "server": "server.py"})
        self.put(self.tool_dir, "plugins/bundled/server.py", "def register(api):\n    api.catalog_extra(lambda: 'hi')\n")
        app = self.app_with({"bundled": {}, "fixture": {}}, trust=False)
        self.assertEqual([p.name for p in app.state.registry.plugins], ["bundled"])
        self.assertEqual(self.catalog(app)["plugins"], {"bundled": "hi"})
        self.assertFalse(self.ran())

    def test_a_bundled_plugin_that_needs_a_skipped_one_is_skipped_too(self):
        self.put(self.tool_dir, "plugins/needy/plugin.json", {"name": "needy", "apiVersion": 1, "requires": ["fixture"]})
        app = self.app_with({"needy": {}, "fixture": {}}, trust=False)
        self.assertEqual(app.state.registry.plugins, [])
        self.assertTrue(any("needy skipped" in w for w in app.state.registry.warnings))

    def test_a_project_plugin_cannot_shadow_a_bundled_one(self):
        self.put(self.tool_dir, "plugins/fixture/plugin.json", {"name": "fixture", "apiVersion": 1})
        app = self.app_with({"fixture": {}}, trust=False)
        self.assertEqual([p.name for p in app.state.registry.plugins], ["fixture"])
        self.assertFalse(self.ran())

    def test_a_name_that_exists_nowhere_is_still_an_error(self):
        with self.assertRaisesRegex(PluginError, "plugin nope not found"):
            self.app_with({"nope": {}}, trust=False)

    def test_the_command_line_flag(self):
        import subprocess
        import sys
        cfg = {"roots": ["assets"], "pluginDirs": ["plugdir"], "plugins": {"fixture": {}}}
        self.write("asset-viewer.json", cfg)
        base = [sys.executable, "-B", os.path.join(helpers.REPO, "serve.py"), "--project", self.project_dir, "--check"]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run(base, capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--trust-project-plugins", r.stdout)
        self.assertFalse(self.ran())
        r = subprocess.run(base + ["--trust-project-plugins"], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.ran())


class DotFolderTest(PluginCase):
    """The config in .asset-viewer/ puts the plugins' folders in a dot folder; they are served, nothing else there is."""

    SERVER = ("def register(api):\n    d = api.project.config_dir + '/rigs'\n    api.serve(d)\n    api.writes(d)\n"
              "    api.catalog_extra(lambda: d)\n")

    def setUp(self):
        super().setUp()
        self.plugin(server=self.SERVER)
        self.write(".asset-viewer/config.json", {"roots": ["assets"], "pluginDirs": ["plugdir"], "plugins": {"fixture": {}}})
        self.write(".asset-viewer/rigs/box.json", '{"parts": {}}')
        self.write(".asset-viewer/rigs/sub/p.png", "png")
        self.write(".asset-viewer/rigs/.private/x.png", "no")
        self.write(".asset-viewer/other.json", "{}")
        self.write("assets/a.png")

    def test_the_declared_area_is_served_and_nothing_else_in_the_dot_folder(self):
        app = self.make_app(trust_project_plugins=True)
        client = self.serve(app)
        self.assertEqual(self.catalog(app)["plugins"]["fixture"], ".asset-viewer/rigs")
        self.assertEqual(client.get("/files/.asset-viewer/rigs/box.json").status, 200)
        self.assertEqual(client.get("/files/.asset-viewer/rigs/sub/p.png").status, 200)
        for path in ("/files/.asset-viewer/rigs/.private/x.png", "/files/.asset-viewer/other.json", "/files/.asset-viewer/config.json",
                     "/files/.git/config"):
            self.assertEqual(client.get(path).status, 404, path)

    def test_check_passes_for_a_servable_area(self):
        report = run_checks(self.make_app(trust_project_plugins=True), out=lambda s: None)
        self.assertEqual(report.failures, [])

    def test_check_fails_for_an_area_that_cannot_be_served(self):
        self.write(".asset-viewer/config.json", {"roots": ["."], "pluginDirs": ["plugdir"], "plugins": {"fixture": {}},
                                                 "hide": [".asset-viewer/rigs"]})
        out = []
        report = run_checks(self.make_app(trust_project_plugins=True), out=out.append)
        self.assertEqual(len(report.failures), 1)
        self.assertIn("served area .asset-viewer/rigs cannot be served", report.failures[0])
        self.assertIn("hidden", report.failures[0])


class RequestTest(PluginCase):
    def test_request_path_is_the_part_after_the_mount(self):
        server = ("def register(api):\n    api.route('GET', 'a/<x>/b', lambda req: {'path': req.path, 'full': req.full_path, 'x': req.params['x']})\n")
        self.plugin(server=server)
        client = self.serve(self.app_with())
        self.assertEqual(client.get("/api/plugins/fixture/a/one/b").json(),
                         {"path": "a/one/b", "full": "/api/plugins/fixture/a/one/b", "x": "one"})

    def test_core_routes_get_the_part_after_api(self):
        from viewer.plugins import Request
        r = Request("GET", "picks", {}, {}, b"", {}, full_path="/api/picks")
        self.assertEqual((r.path, r.full_path), ("picks", "/api/picks"))
        self.assertEqual(Request("GET", "x", {}, {}, b"", {}).full_path, "x")


class PluginHttpTest(PluginCase):
    def setUp(self):
        super().setUp()
        self.plugin()
        self.write("fixture-data/d.json", '{"d": 1}')
        self.write("fixture-data/page.html", "<b>x</b>")
        self.client = self.serve(self.app_with())

    def test_client_files_are_served_only_when_listed(self):
        for name in ("client.js", "style.css", "view.js"):
            r = self.client.get("/plugins/fixture/" + name)
            self.assertEqual(r.status, 200, name)
        self.assertEqual(self.client.get("/plugins/fixture/client.js").header("content-type"), "text/javascript; charset=utf-8")
        for path in ("/plugins/fixture/server.py", "/plugins/fixture/plugin.json", "/plugins/fixture/", "/plugins/nope/client.js",
                     "/plugins/fixture/../fixture/server.py", "/plugins/fixture/%2e%2e/fixture/server.py"):
            self.assertEqual(self.client.get(path).status, 404, path)

    def test_serve_area_is_readable_via_files(self):
        self.assertEqual(self.client.get("/files/fixture-data/d.json").json(), {"d": 1})
        self.assertEqual(self.client.get("/files/fixture-data/page.html").status, 404)

    def test_route_runs_after_the_guards(self):
        r = self.client.post("/api/plugins/fixture/echo", json_body={"a": 1})
        self.assertEqual((r.status, r.json()), (200, {"a": 1}))
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", json_body={"a": 1}, token=None).status, 403)
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", json_body={"a": 1}, token="wrong").status, 403)
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", json_body={"a": 1}, host="evil.example").status, 421)
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", body=b"x" * 101).status, 413)
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", body=b"[1]").status, 400)
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", body=b"{", ).status, 400)
        self.assertEqual(self.client.post("/api/plugins/fixture/echo", body=b"{}", content_type="text/plain").status, 415)
        self.assertEqual(self.client.get("/api/plugins/fixture/echo").status, 405)

    def test_optional_token_route_for_studio(self):
        # no Origin, no Sec-Fetch-Site (Roblox Studio's HttpService): no token needed
        r = self.client.post("/api/plugins/fixture/studio/Rig?w=3&w=4", body=b"hello", token=None, content_type="text/plain")
        self.assertEqual((r.status, r.json()), (200, {"name": "Rig", "size": 5, "q": {"w": "4"}}))
        # a browser always sends Origin or Sec-Fetch-Site, so the token is required again
        port = self.client.port
        r = self.client.post("/api/plugins/fixture/studio/Rig", body=b"x", token=None, headers={"Origin": "http://127.0.0.1:%d" % port})
        self.assertEqual(r.status, 403)
        r = self.client.post("/api/plugins/fixture/studio/Rig", body=b"x", token=None, headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status, 403)
        r = self.client.post("/api/plugins/fixture/studio/Rig", body=b"x", headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)
        # the param regex is enforced
        self.assertEqual(self.client.post("/api/plugins/fixture/studio/9bad", body=b"x", token=None, content_type="text/plain").status, 404)
        self.assertEqual(self.client.post("/api/plugins/fixture/studio/Rig", body=b"x" * 1001, token=None, content_type="text/plain").status, 413)

    def test_path_params_and_unknown_routes(self):
        self.assertEqual(self.client.get("/api/plugins/fixture/item/abc").json(), {"name": "abc"})
        self.assertEqual(self.client.get("/api/plugins/fixture/item/a%2Fb").status, 404)
        self.assertEqual(self.client.get("/api/plugins/fixture/nothing").status, 404)
        self.assertEqual(self.client.get("/api/plugins/other/item/abc").status, 404)

    def test_handler_errors_become_json(self):
        r = self.client.get("/api/plugins/fixture/boom")
        self.assertEqual(r.status, 500)
        self.assertEqual(r.json(), {"error": "plugin fixture failed"})
        self.assertNotIn(b"secret", r.body)
        r = self.client.get("/api/plugins/fixture/teapot")
        self.assertEqual((r.status, r.json()), (418, {"error": "short and stout"}))

    def test_plugin_writes_stay_in_their_areas(self):
        r = self.client.post("/api/plugins/fixture/write", body=b"data", content_type="application/json")
        self.assertEqual((r.status, r.json()), (200, {"ok": True}))
        with open(os.path.join(self.project_dir, "fixture-out", "w.txt"), "rb") as f:
            self.assertEqual(f.read(), b"data")
        r = self.client.post("/api/plugins/fixture/badwrite", body=b"data")
        self.assertEqual(r.status, 500)
        self.assertFalse(os.path.exists(os.path.join(self.project_dir, "elsewhere")))

    def test_catalog_carries_plugin_extra(self):
        self.assertEqual(self.client.get("/api/catalog").json()["plugins"]["fixture"]["count"], 1)


class HelpersTest(unittest.TestCase):
    def test_deep_merge_copies(self):
        base = {"a": {"b": 1, "c": [1]}, "d": 2}
        out = deep_merge(base, {"a": {"b": 5}, "e": 3})
        self.assertEqual(out, {"a": {"b": 5, "c": [1]}, "d": 2, "e": 3})
        out["a"]["c"].append(2)
        self.assertEqual(base["a"]["c"], [1])

    def test_validate_types(self):
        w = []
        spec = {"rigsDir": "string|null", "bodyAim": {"*": "number"}, "files": "object", "x": "any"}
        validate_types({"rigsDir": None, "bodyAim": {"blade": 30}, "files": {}, "x": [1]}, spec, "plugins.p", w)
        self.assertEqual(w, [])
        for bad, pattern in [({"rigsDir": 5}, r"plugins\.p\.rigsDir: string or null expected"),
                             ({"bodyAim": {"blade": "x"}}, r"plugins\.p\.bodyAim\.blade: number expected"),
                             ({"bodyAim": []}, r"plugins\.p\.bodyAim: object expected"),
                             ({"files": []}, r"plugins\.p\.files: object expected"),
                             ({"bodyAim": {"blade": True}}, "number expected")]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(PluginError, pattern):
                    validate_types(bad, spec, "plugins.p", [])
        validate_types({"zzz": 1}, spec, "plugins.p", w)
        self.assertEqual(w, ["plugins.p.zzz: unknown key"])


if __name__ == "__main__":
    unittest.main()
