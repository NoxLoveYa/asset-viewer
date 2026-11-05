# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "tests"))
import helpers  # noqa: E402  (the server tests' temp-project helpers)
from viewer.check import run as run_checks  # noqa: E402
from viewer.plugins import PluginError  # noqa: E402

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def clip(frames=3, fps=30, looped=False, priority=None):
    c = {"fps": fps, "looped": looped, "frames": [{"Root": [0, i, 0, 0, 0, 0, 1]} for i in range(frames)]}
    if priority:
        c["priority"] = priority
    return c


def load_server():
    spec = importlib.util.spec_from_file_location("roblox_anim_server_under_test", os.path.join(PLUGIN, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AnimCase(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(PLUGIN, os.path.join(self.tool_dir, "plugins", "roblox-anim"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def app_with(self, plugin=None, **extra):
        cfg = {"roots": ["assets"], "plugins": {"roblox-anim": plugin or {}}}
        cfg.update(extra)
        return self.make_app(cfg)

    def clips(self, app):
        return [i for i in self.catalog(app)["items"] if i["kind"] == "anim"]


class ClipsOfTest(AnimCase):
    def setUp(self):
        super().setUp()
        self.mod = load_server()

    def clips_of(self, data, name="a.json"):
        path = self.write("assets/" + name, data)
        return self.mod.clips_of(path, os.stat(path))

    def test_clip_shapes(self):
        got = self.clips_of({"Idle": clip(5, looped=True, priority="Idle"), "Walk": clip(2, fps=24)})
        self.assertEqual(got, {"Idle": {"fps": 30, "frames": 5, "looped": True, "priority": "Idle"},
                               "Walk": {"fps": 24, "frames": 2, "looped": False, "priority": None}})

    def test_other_json_is_not_an_animation_file(self):
        for data in ({}, [], {"a": 1}, {"objects": {"x": {}}, "files": []}, {"Idle": {"fps": 30}}, {"parts": {}, "joints": {}}):
            with self.subTest(data=data):
                self.assertIsNone(self.clips_of(data))
        path = self.write("assets/broken.json", "{not json")
        self.assertIsNone(self.mod.clips_of(path, os.stat(path)))

    def test_bad_clips_are_refused_with_a_reason(self):  # audit H3
        bad = [({"Idle": {"fps": 30, "frames": []}}, "frames must be a non-empty list"),
               ({"Idle": {"fps": 30, "frames": 5}}, "frames must be a non-empty list"),
               ({"Idle": {"fps": 30, "frames": {"a": 1}}}, "frames must be a non-empty list"),
               ({"Idle": {"fps": 0, "frames": [{}]}}, "fps must be a positive number"),
               ({"Idle": {"fps": "30", "frames": [{}]}}, "fps must be a positive number"),
               ({"Idle": {"fps": True, "frames": [{}]}}, "fps must be a positive number"),
               ({"Idle": {"fps": 30, "frames": [1, 2]}}, "every frame must be an object")]
        for n, (data, message) in enumerate(bad):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, message):
                    self.clips_of(data, "bad%d.json" % n)

    def test_a_file_is_parsed_again_only_when_it_changed(self):
        path = self.write("assets/c.json", {"A": clip(2)})
        st = os.stat(path)
        first = self.mod.clips_of(path, st)
        self.assertIs(self.mod.clips_of(path, st), first)  # the cache answers
        self.write("assets/c.json", {"A": clip(4), "B": clip(1)})
        self.assertEqual(sorted(self.mod.clips_of(path, os.stat(path))), ["A", "B"])

    def test_aim_layers(self):
        opts = {"under": {"*": ["Idle", "Hold"], "Hold": ["Idle"]}, "up": ["Aim"], "busy": ["Reload"], "aimHead": [40, 35],
                "bodyAim": {"blade": 45}}
        base = {"blade": 30, "busyTwist": 35, "lockMax": 45}
        g = self.mod.aim_layers
        self.assertEqual(g(opts, "Hold", base), {"under": ["Idle"]})
        self.assertEqual(g(opts, "Walk", base), {"under": ["Idle", "Hold"]})
        self.assertEqual(g(opts, "Aim", base), {"under": ["Idle", "Hold"], "stance": "up", "aimHead": [40, 35],
                                                "bodyAim": {"blade": 45, "busyTwist": 35, "lockMax": 45}})
        self.assertEqual(g(opts, "Reload", base)["stance"], "busy")
        self.assertEqual(g({}, "Walk", base), {})
        self.assertEqual(g({"up": ["Aim"]}, "Aim", base), {"stance": "up", "bodyAim": base})  # no under, no aimHead


class CatalogTest(AnimCase):
    def test_one_item_per_clip_with_layers_and_resolved_under(self):
        self.write("assets/animations/loco.json", {"Idle": clip(4, looped=True), "Run": clip(3)})
        self.write("assets/animations/held.json", {"Hold": clip(3, looped=True), "Aim": clip(2), "Reload": clip(6)})
        self.write("assets/animations/notes.json", {"hello": 1})
        app = self.app_with({"defaultRig": "hero", "bodyAim": {"blade": 30, "busyTwist": 35, "lockMax": 45},
                             "files": {"assets/animations/held.json": {
                                 "prop": "pistol", "under": {"*": ["Idle"], "Aim": ["Idle", "Hold"]}, "up": ["Aim"], "busy": ["Reload"]}}})
        items = {i["id"]: i for i in self.clips(app)}
        self.assertEqual(sorted(items), ["assets/animations/held.json#Aim", "assets/animations/held.json#Hold",
                                         "assets/animations/held.json#Reload", "assets/animations/loco.json#Idle",
                                         "assets/animations/loco.json#Run"])
        aim = items["assets/animations/held.json#Aim"]
        self.assertEqual((aim["kind"], aim["name"], aim["dir"], aim["rig"], aim["prop"]), ("anim", "Aim", "assets/animations/held", "hero", "pistol"))
        self.assertEqual((aim["fps"], aim["frames"], aim["looped"], aim["priority"]), (30, 2, False, None))
        self.assertEqual(aim["under"], [{"path": "assets/animations/loco.json", "name": "Idle"},
                                        {"path": "assets/animations/held.json", "name": "Hold"}])  # first file that has the clip
        self.assertEqual((aim["stance"], aim["bodyAim"]), ("up", {"blade": 30, "busyTwist": 35, "lockMax": 45}))
        self.assertEqual(items["assets/animations/held.json#Reload"]["stance"], "busy")
        loco = items["assets/animations/loco.json#Run"]
        self.assertNotIn("under", loco)
        self.assertNotIn("stance", loco)
        self.assertEqual((loco["rig"], loco["prop"]), ("hero", None))

    def test_an_unresolved_under_has_no_path(self):
        self.write("assets/a.json", {"Walk": clip()})
        app = self.app_with({"files": {"assets/a.json": {"under": {"*": ["Missing"]}}}})
        self.assertEqual(self.clips(app)[0]["under"], [{"path": None, "name": "Missing"}])

    def test_a_bad_file_is_a_warning_and_the_scan_goes_on(self):  # audit H3
        self.write("assets/bad.json", {"Idle": {"fps": 30, "frames": []}})
        self.write("assets/ok.json", {"Idle": clip()})
        self.write("assets/a.png")
        cat = self.catalog(self.app_with())
        self.assertEqual(sorted(i["id"] for i in cat["items"]), ["assets/a.png", "assets/ok.json#Idle"])
        self.assertTrue(any("bad.json" in w and "frames must be a non-empty list" in w for w in cat["warnings"]), cat["warnings"])

    def test_category_is_listed_and_sort_keeps_core_first(self):
        self.write("assets/a.json", {"Idle": clip()})
        self.assertEqual(self.app_with().categories()[0], ["model", "texture", "vfx", "sound", "anim"])


class RigsTest(AnimCase):
    def test_rigs_listed_with_mtimes_and_a_missing_rigs_dir_does_not_break_the_catalog(self):  # audit H3, M8
        self.write("assets/a.json", {"Idle": clip()})
        app = self.app_with()
        cat = self.catalog(app)
        self.assertEqual(cat["plugins"]["roblox-anim"], {"rigsDir": "rigs", "rigs": []})
        self.assertEqual(len(self.clips(app)), 1)
        self.assertEqual(cat["warnings"], [])

    def test_rigs_dir_defaults_to_the_config_folder_and_can_be_set(self):
        self.write("cfg/rigs/box.json", {"parts": {}, "joints": {}})
        self.write("assets/a.json", {"Idle": clip()})
        app = self.make_app({"roots": ["assets"], "plugins": {"roblox-anim": {}}}, config_path="cfg/viewer.json", config_arg="cfg/viewer.json")
        self.assertEqual(self.catalog(app)["plugins"]["roblox-anim"]["rigsDir"], "cfg/rigs")
        self.assertEqual([r["name"] for r in self.catalog(app)["plugins"]["roblox-anim"]["rigs"]], ["box"])
        self.write("other/hero.json", {"parts": {}, "joints": {}})
        app = self.app_with({"rigsDir": "other"})
        self.assertEqual([r["name"] for r in self.catalog(app)["plugins"]["roblox-anim"]["rigs"]], ["hero"])

    def test_rig_mtime_moves_with_its_images_and_the_catalog_version_follows(self):  # audit M8
        self.write("rigs/box.json", {"parts": {}, "joints": {}})
        self.write("rigs/box/1.png", b"png")
        app = self.app_with()
        a = self.catalog(app)
        os.utime(os.path.join(self.project_dir, "rigs", "box", "1.png"), (2_000_000_000, 2_000_000_000))
        app.catalog._at = 0  # drop the one-second cache
        b = self.catalog(app)
        self.assertEqual(b["plugins"]["roblox-anim"]["rigs"][0]["mtime"], 2_000_000_000_000)
        self.assertNotEqual(a["version"], b["version"])

    def test_only_plain_rig_names_are_listed(self):
        for name in ("box", "my-rig", "9x", "ok_1"):
            self.write("rigs/%s.json" % name, {"parts": {}, "joints": {}})
        self.write("rigs/readme.txt")
        names = [r["name"] for r in self.catalog(self.app_with())["plugins"]["roblox-anim"]["rigs"]]
        self.assertEqual(names, ["box", "ok_1"])

    def test_rigs_are_served_read_only_via_files(self):
        self.write("rigs/box.json", {"parts": {}, "joints": {}})
        self.write("rigs/box/1.png", b"png")
        self.write("assets/a.json", {"Idle": clip()})
        client = self.serve(self.app_with())
        self.assertEqual(client.get("/files/rigs/box.json").status, 200)
        self.assertEqual(client.get("/files/rigs/box/1.png").status, 200)
        self.assertEqual(client.get("/files/rigs/../asset-viewer.json").status, 404)
        self.assertEqual(client.get("/api/catalog").json()["plugins"]["roblox-anim"]["rigs"][0]["name"], "box")
        cfg = client.get("/api/config").json()
        entry = [p for p in cfg["plugins"] if p["name"] == "roblox-anim"][0]
        self.assertEqual(entry["client"], "/plugins/roblox-anim/client.js")
        self.assertEqual(client.get("/plugins/roblox-anim/view.js").status, 200)
        self.assertEqual(client.get("/plugins/roblox-anim/server.py").status, 404)
        self.assertEqual(entry["config"], {"rigsDir": "rigs", "defaultRig": None, "bodyAim": {}})


class ConfigTest(AnimCase):
    def test_config_is_validated(self):
        for bad, pattern in [({"rigsDir": 5}, r"rigsDir: string or null expected"), ({"bodyAim": {"blade": "x"}}, r"bodyAim\.blade: number expected"),
                             ({"files": {"a.json": {"under": {"*": "Idle"}}}}, r"under\.\*: array expected"),
                             ({"files": {"a.json": {"up": "Aim"}}}, r"up: array expected")]:
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(PluginError, pattern):
                    self.app_with(bad)

    def test_unknown_keys_warn(self):
        app = self.app_with({"surprise": 1, "files": {"a.json": {"sideways": 1}}})
        self.assertIn("plugins.roblox-anim.surprise: unknown key", app.state.registry.warnings)
        self.assertIn("plugins.roblox-anim.files.a.json.sideways: unknown key", app.state.registry.warnings)


class CheckTest(AnimCase):
    def run_check(self, plugin=None):
        out = []
        report = run_checks(self.app_with(plugin), out=out.append)
        return report, out

    def rig(self, name, **extra):
        self.write("rigs/%s.json" % name, {"parts": {}, "joints": {}, **extra})

    def test_a_good_project_passes(self):
        self.rig("box")
        self.rig("tool", grip={})
        self.write("assets/a.json", {"Idle": clip(), "Aim": clip()})
        report, out = self.run_check({"defaultRig": "box", "bodyAim": {"blade": 30}, "files": {"assets/a.json": {
            "prop": "tool", "under": {"*": ["Idle"]}, "up": ["Aim"]}}})
        self.assertEqual(report.failures, [])
        self.assertIn("ok: 2 clips in 1 animation files, 2 rigs", out)

    def test_missing_rig_prop_under_and_bodyaim_fail(self):
        self.rig("box")
        self.write("assets/a.json", {"Idle": clip(), "Aim": clip()})
        report, _ = self.run_check({"defaultRig": "ghost", "files": {"assets/a.json": {
            "prop": "nogun", "under": {"Aim": ["Nothing"]}, "up": ["Aim"]}}})
        joined = "\n".join(report.failures)
        self.assertIn("assets/a.json#Idle wants rig ghost, not in rigs", joined)
        self.assertIn("assets/a.json#Aim wants rig nogun, not in rigs", joined)
        self.assertIn("plays Nothing under it: no clip of that name", joined)
        self.assertIn("assets/a.json#Aim has a stance but plugins.roblox-anim.bodyAim is empty", joined)

    def test_a_file_entry_without_a_file_and_a_broken_rig_fail(self):
        self.write("rigs/broken.json", "[1]")
        self.write("assets/a.json", {"Idle": clip()})
        report, _ = self.run_check({"files": {"assets/nope.json": {}}})
        joined = "\n".join(report.failures)
        self.assertIn("assets/nope.json is not an animation file", joined)
        self.assertIn("rig broken: rigs/broken.json is not a rig", joined)


if __name__ == "__main__":
    unittest.main()
