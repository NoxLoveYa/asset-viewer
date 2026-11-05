# SPDX-License-Identifier: AGPL-3.0-or-later
"""Limits of the animation claimer: a huge .json is skipped with a warning, and the parse cache forgets files that are gone."""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "tests"))
import helpers  # noqa: E402

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIP = {"Idle": {"fps": 30, "looped": True, "frames": [{"Root": [0, 0, 0, 0, 0, 0, 1]}]}}


def module():
    return sys.modules["viewer_plugin_roblox_anim"]


class LimitsCase(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(PLUGIN, os.path.join(self.tool_dir, "plugins", "roblox-anim"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def app(self):
        return self.make_app({"roots": ["assets"], "plugins": {"roblox-anim": {}}})

    def rescan(self, app):
        app.catalog._at = 0  # expire the one-second cache
        return self.catalog(app)


class SizeLimitTest(LimitsCase):
    def test_a_json_over_8_mib_is_skipped_with_a_warning_and_the_rest_is_listed(self):
        self.write("assets/ok.json", CLIP)
        self.write("assets/huge.json", b"{" + b" " * ((8 << 20) + 1) + b"}")
        self.write("assets/a.png")
        cat = self.catalog(self.app())
        self.assertEqual(sorted(i["id"] for i in cat["items"]), ["assets/a.png", "assets/ok.json#Idle"])
        (warning,) = [w for w in cat["warnings"] if "huge.json" in w]
        self.assertIn("plugin roblox-anim", warning)
        self.assertIn("8 MiB", warning)

    def test_exactly_8_mib_is_still_read(self):
        pad = (8 << 20) - len(b'{"Idle": {"fps": 30, "frames": [{}], "pad": ""}}')
        path = self.write("assets/edge.json", b'{"Idle": {"fps": 30, "frames": [{}], "pad": "' + b"x" * pad + b'"}}')
        self.assertEqual(os.path.getsize(path), 8 << 20)
        self.assertEqual([i["id"] for i in self.catalog(self.app())["items"]], ["assets/edge.json#Idle"])

    def test_the_limit_is_a_constant_of_the_plugin(self):
        self.catalog(self.app())
        self.assertEqual(module().MAX_JSON, 8 << 20)


class CachePruneTest(LimitsCase):
    def test_the_cache_keeps_only_files_the_last_scan_met(self):
        app = self.app()
        a = self.write("assets/a.json", CLIP)
        b = self.write("assets/b.json", CLIP)
        self.write("assets/data.json", {"not": "an animation"})
        self.rescan(app)
        self.assertEqual(sorted(os.path.basename(p) for p in module()._cache), ["a.json", "b.json", "data.json"])
        os.remove(b)
        os.remove(os.path.join(self.project_dir, "assets", "data.json"))
        self.rescan(app)
        self.assertEqual([os.path.basename(p) for p in module()._cache], ["a.json"])
        os.remove(a)
        self.rescan(app)
        self.assertEqual(module()._cache, {})

    def test_a_file_that_comes_back_is_parsed_again(self):
        app = self.app()
        path = self.write("assets/a.json", CLIP)
        self.assertEqual(len(self.rescan(app)["items"]), 1)
        os.remove(path)
        self.assertEqual(self.rescan(app)["items"], [])
        self.write("assets/a.json", CLIP)
        self.assertEqual(len(self.rescan(app)["items"]), 1)

    def test_a_folder_hidden_later_drops_out_of_the_cache(self):
        app = self.app()
        self.write("assets/sub/a.json", CLIP)
        self.rescan(app)
        self.assertEqual(len(module()._cache), 1)
        self.write("asset-viewer.json", {"roots": ["assets"], "hide": ["assets/sub"], "plugins": {"roblox-anim": {}}})
        os.utime(os.path.join(self.project_dir, "asset-viewer.json"), (os.path.getmtime(os.path.join(self.project_dir, "asset-viewer.json")) + 9,) * 2)
        app.refresh(force=True)
        self.rescan(app)
        self.assertEqual(module()._cache, {})


class ConfigReloadTest(LimitsCase):
    """A project edits plugins.roblox-anim.files often: it applies without a restart, and the plugin code is not imported again."""

    def edit(self, plugin):
        path = self.write("asset-viewer.json", {"roots": ["assets"], "plugins": {"roblox-anim": plugin}})
        self.edits = getattr(self, "edits", 0) + 1
        os.utime(path, (os.path.getmtime(path) + 5 * self.edits,) * 2)

    def clip(self, app, name="Idle"):
        return [i for i in self.catalog(app)["items"] if i["kind"] == "anim" and i["name"] == name][0]

    def test_files_bodyaim_defaultrig_and_rigsdir_follow_the_config(self):
        self.write("assets/guns.json", {"Idle": {"fps": 30, "frames": [{}]}, "Aim": {"fps": 30, "frames": [{}]}})
        self.write("rigs/one.json", {"parts": {}, "joints": {}})
        self.write("other/two.json", {"parts": {}, "joints": {}})
        self.write("asset-viewer.json", {"roots": ["assets"], "plugins": {"roblox-anim": {
            "rigsDir": "rigs", "defaultRig": "one", "bodyAim": {"blade": 30},
            "files": {"assets/guns.json": {"prop": "m1", "up": ["Aim"]}}}}})
        app = self.make_app()
        mod = module()
        aim = self.clip(app, "Aim")
        self.assertEqual((aim["rig"], aim["prop"], aim["stance"], aim["bodyAim"]), ("one", "m1", "up", {"blade": 30}))
        self.assertEqual([r["name"] for r in self.catalog(app)["plugins"]["roblox-anim"]["rigs"]], ["one"])

        self.edit({"rigsDir": "other", "defaultRig": "two", "bodyAim": {"blade": 45, "lockMax": 9},
                   "files": {"assets/guns.json": {"prop": "m2", "busy": ["Aim"], "under": {"*": ["Idle"]}}}})
        app.refresh(force=True)
        aim = self.clip(app, "Aim")
        self.assertEqual((aim["rig"], aim["prop"], aim["stance"], aim["bodyAim"], aim["under"][0]["name"]),
                         ("two", "m2", "busy", {"blade": 45, "lockMax": 9}, "Idle"))
        self.assertEqual([r["name"] for r in self.catalog(app)["plugins"]["roblox-anim"]["rigs"]], ["two"])
        self.assertEqual(app.public_config()["plugins"][0]["config"]["rigsDir"], "other")
        self.assertEqual((app.notices, app.error), ([], None))
        self.assertIs(module(), mod)  # the same imported module

    def test_a_typo_in_the_files_block_is_reported_and_the_old_config_keeps_working(self):
        self.write("assets/guns.json", {"Idle": {"fps": 30, "frames": [{}]}})
        self.write("asset-viewer.json", {"roots": ["assets"], "plugins": {"roblox-anim": {"files": {"assets/guns.json": {"prop": "m1"}}}}})
        app = self.make_app()
        self.edit({"files": {"assets/guns.json": {"prop": 5}}})
        app.refresh(force=True)
        self.assertIn("plugins.roblox-anim.files", app.error)
        self.assertEqual(self.clip(app)["prop"], "m1")
        self.edit({"files": {"assets/guns.json": {"prop": "m3"}}})
        app.refresh(force=True)
        self.assertEqual((app.error, self.clip(app)["prop"]), (None, "m3"))


if __name__ == "__main__":
    unittest.main()
