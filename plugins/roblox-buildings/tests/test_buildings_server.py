# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "tests"))
import helpers  # noqa: E402  (the server tests' temp-project helpers)
from viewer.plugins import PluginError  # noqa: E402

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_NAME = "roblox-buildings"

REPORT = {"name": "house", "tris": 100, "objects": {"plaster": {"finish": "plaster", "tint": "e2ded4", "glass": False, "maps": {"Color": "m/c.jpg"}}},
          "files": {"fbx": "assets/buildings/house.fbx"}, "storeys": [{"index": 0, "name": "g", "y": 1.0, "height": 15.0, "clear": 13.5}],
          "props": [], "openings": []}
SIDECAR = {"name": "Cabinet", "objects": {"Body": {"tris": 10, "glass": False},
                                          "Door": {"hinge": [0, 1, 0], "axis": "y", "swing": 100},
                                          "Drawer": {"slide": [0, 0, -0.6]},
                                          "Still": {"hinge": [0, 1, 0]}}}  # a hinge without a swing does not move
PLAIN_PROP = {"name": "Box", "objects": {"Body": {"tris": 52, "glass": False}}}


class BuildingsCase(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(PLUGIN, os.path.join(self.tool_dir, "plugins", PLUGIN_NAME),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def app_with(self, plugin=None):
        return self.make_app({"roots": ["assets"], "plugins": {PLUGIN_NAME: plugin or {}}})

    def models(self, app):
        return {i["path"]: i for i in self.catalog(app)["items"] if i["kind"] == "model"}


class EnrichTest(BuildingsCase):
    def test_a_building_report_beside_an_fbx(self):
        self.write("assets/b/house.fbx")
        self.write("assets/b/house.json", REPORT)
        item = self.models(self.app_with())["assets/b/house.fbx"]
        ext = item["ext"][PLUGIN_NAME]
        self.assertEqual(list(ext), ["report"])
        self.assertEqual(ext["report"]["path"], "assets/b/house.json")
        self.assertGreater(ext["report"]["mtime"], 0)

    def test_a_prop_sidecar_counts_the_parts_that_move(self):
        self.write("assets/p/Cabinet.fbx")
        self.write("assets/p/Cabinet.json", SIDECAR)
        ext = self.models(self.app_with())["assets/p/Cabinet.fbx"]["ext"][PLUGIN_NAME]
        self.assertEqual(ext, {"sidecar": {"path": "assets/p/Cabinet.json", "mtime": ext["sidecar"]["mtime"], "movers": 2}})

    def test_a_prop_without_moving_parts_still_has_a_sidecar(self):  # it is turned front to -Z all the same
        self.write("assets/p/Box.glb")
        self.write("assets/p/Box.json", PLAIN_PROP)
        self.assertEqual(self.models(self.app_with())["assets/p/Box.glb"]["ext"][PLUGIN_NAME]["sidecar"]["movers"], 0)

    def test_no_enrichment_for_a_plain_model_or_other_json(self):  # the page never asks for a file that is not there
        self.write("assets/plain.fbx")
        self.write("assets/crate.obj")
        self.write("assets/crate.json", {"hello": 1})                # a JSON that is neither
        self.write("assets/empty.fbx")
        self.write("assets/empty.json", {"objects": {}})
        self.write("assets/arr.fbx")
        self.write("assets/arr.json", [1, 2])
        self.write("assets/broken.fbx")
        self.write("assets/broken.json", "{nope")
        self.write("assets/colliders.fbx")
        self.write("assets/colliders.colliders.json", {"parts": []})  # another file's name: not <stem>.json
        for path, item in self.models(self.app_with()).items():
            with self.subTest(path=path):
                self.assertNotIn("ext", item)

    def test_a_report_beside_a_non_fbx_model_is_not_used(self):
        self.write("assets/house.glb")
        self.write("assets/house.json", REPORT)
        self.assertNotIn("ext", self.models(self.app_with())["assets/house.glb"])

    def test_a_changed_json_is_read_again(self):
        self.write("assets/h.fbx")
        self.write("assets/h.json", SIDECAR)
        app = self.app_with()
        self.assertIn("sidecar", self.models(app)["assets/h.fbx"]["ext"][PLUGIN_NAME])
        path = self.write("assets/h.json", REPORT)
        os.utime(path, (os.path.getmtime(path) + 5,) * 2)
        app.catalog._at = 0
        self.assertIn("report", self.models(app)["assets/h.fbx"]["ext"][PLUGIN_NAME])

    def test_the_json_stays_a_catalog_non_item(self):
        self.write("assets/h.fbx")
        self.write("assets/h.json", REPORT)
        self.assertEqual(self.ids(self.app_with()), ["assets/h.fbx"])  # the sidecar is not listed as anything

    def test_files_are_served_to_the_page(self):
        self.write("assets/h.fbx")
        self.write("assets/h.json", REPORT)
        client = self.serve(self.app_with({"openingProps": "assets/props/{dir}/{prop}.fbx"}))
        self.assertEqual(client.get("/files/assets/h.json").status, 200)
        entry = [p for p in client.get("/api/config").json()["plugins"] if p["name"] == PLUGIN_NAME][0]
        self.assertEqual(entry["config"], {"openingProps": "assets/props/{dir}/{prop}.fbx"})
        self.assertEqual(entry["styles"], ["/plugins/%s/style.css" % PLUGIN_NAME])
        self.assertEqual(client.get("/plugins/%s/client.js" % PLUGIN_NAME).status, 200)


class ConfigTest(BuildingsCase):
    def test_opening_props_is_validated(self):
        with self.assertRaisesRegex(PluginError, r"openingProps: string or null expected"):
            self.app_with({"openingProps": 5})
        self.assertEqual(self.app_with().state.registry.get(PLUGIN_NAME).config, {"openingProps": None})


if __name__ == "__main__":
    unittest.main()
