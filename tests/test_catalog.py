# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import time
import unittest

import helpers
from viewer.catalog import model_maps, scan

MAPS = ["Color", "Normal", "Metalness", "Roughness", "Emissive"]


class ModelMapsTest(unittest.TestCase):
    def test_naming_rule(self):
        files = ["Widget.fbx", "Widget_Color.png", "Widget_Normal.png", "Widget_Worn_Color.png", "Widget_Worn_Roughness.png", "Widgets_Color.png"]
        self.assertEqual(model_maps("Widget.fbx", "x", files, MAPS), {
            "Base": {"Color": "x/Widget_Color.png", "Normal": "x/Widget_Normal.png"},
            "Worn": {"Color": "x/Widget_Worn_Color.png", "Roughness": "x/Widget_Worn_Roughness.png"}})

    def test_plain_stem_png_is_the_base_color(self):
        self.assertEqual(model_maps("a.obj", "", ["a.obj", "a.png"], MAPS), {"Base": {"Color": "a.png"}})

    def test_prefix_collision_goes_to_the_exact_model(self):  # audit M13
        files = ["gate.fbx", "gate_left.fbx", "gate_left_Color.png", "gate_Color.png", "gate_left_Normal.png"]
        self.assertEqual(model_maps("gate.fbx", "d", files, MAPS), {"Base": {"Color": "d/gate_Color.png"}})
        self.assertEqual(model_maps("gate_left.fbx", "d", files, MAPS),
                         {"Base": {"Color": "d/gate_left_Color.png", "Normal": "d/gate_left_Normal.png"}})

    def test_variant_still_works_without_the_longer_model(self):
        files = ["gate.fbx", "gate_left_Color.png"]
        self.assertEqual(model_maps("gate.fbx", "", files, MAPS), {"left": {"Color": "gate_left_Color.png"}})

    def test_longest_stem_wins(self):
        files = ["a.fbx", "a_b.fbx", "a_b_x_Color.png"]
        self.assertEqual(model_maps("a_b.fbx", "", files, MAPS), {"x": {"Color": "a_b_x_Color.png"}})
        self.assertEqual(model_maps("a.fbx", "", files, MAPS), {})

    def test_other_extensions_and_custom_maps(self):
        files = ["m.glb", "m_Color.jpg", "m_Height.webp", "m_Color.gif", "m_AO.png"]
        self.assertEqual(model_maps("m.glb", "", files, MAPS), {"Base": {"Color": "m_Color.jpg"}})
        self.assertEqual(model_maps("m.glb", "", files, ["Height", "AO"]), {"Base": {"Height": "m_Height.webp", "AO": "m_AO.png"}})


class CatalogTest(helpers.ProjectCase):
    def test_core_kinds_by_extension(self):
        for name in ("a.fbx", "b.glb", "c.gltf", "d.obj", "e.png", "f.jpg", "g.webp", "h.gif", "i.svg", "j.ogg", "k.mp3",
                     "l.wav", "m.flac", "n.json", "o.txt"):
            self.write("assets/" + name)
        app = self.make_app({"roots": [{"path": "assets", "label": ""}]})
        kinds = {i["name"]: i["kind"] for i in self.catalog(app)["items"]}
        self.assertEqual(kinds, {"a": "model", "b": "model", "c": "model", "d": "model", "e": "texture", "f": "texture",
                                 "g": "texture", "h": "texture", "i": "texture", "j": "sound", "k": "sound", "l": "sound",
                                 "m": "sound"})

    def test_item_shape_and_ids_are_project_paths(self):
        self.write("assets/props/bed/bed.fbx", "12345")
        app = self.make_app({"roots": [{"path": "assets", "label": ""}]})
        (item,) = self.catalog(app)["items"]
        self.assertEqual({k: item[k] for k in ("id", "path", "kind", "name", "dir", "bytes")},
                         {"id": "assets/props/bed/bed.fbx", "path": "assets/props/bed/bed.fbx", "kind": "model",
                          "name": "bed", "dir": "props/bed", "bytes": 5})
        self.assertIsInstance(item["mtime"], int)
        self.assertEqual(item["maps"], {})

    def test_vfx_rule_and_custom_folders(self):
        self.write("assets/vfx/spark_4x4.png")
        self.write("assets/Fx/boom.png")
        self.write("assets/other/vfx.png")
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(self.ids(app, "vfx"), ["assets/vfx/spark_4x4.png"])
        app = self.make_app({"roots": ["assets"], "vfxFolders": ["fx", "vfx"]}, config_path="asset-viewer.json")
        app.refresh(force=True)
        self.assertEqual(self.ids(app, "vfx"), ["assets/Fx/boom.png", "assets/vfx/spark_4x4.png"])

    def test_root_labels_and_dir(self):
        self.write("assets/props/a.png")
        self.write("assets/top.png")
        self.write("icons/x/b.svg")
        self.write("icons/c.svg")
        self.write("plain/d.png")
        app = self.make_app({"roots": [{"path": "assets", "label": ""}, {"path": "icons", "label": "kit"}, "plain"]})
        dirs = {i["name"]: i["dir"] for i in self.catalog(app)["items"]}
        self.assertEqual(dirs, {"a": "props", "top": "", "b": "kit/x", "c": "kit", "d": "plain"})

    def test_dot_root_lists_the_project_without_dot_folders(self):
        self.write("a.png")
        self.write("sub/b.png")
        self.write(".git/c.png")
        self.write(".asset-viewer/d.png")
        app = self.make_app()
        self.assertEqual(self.ids(app), ["a.png", "sub/b.png"])
        dirs = {i["name"]: i["dir"] for i in self.catalog(app)["items"]}
        self.assertEqual(dirs, {"a": "", "b": "sub"})

    def test_skipped_folders(self):
        for d in (".hidden", "__pycache__", "node_modules", "site-packages", "ok"):
            self.write("assets/%s/x.png" % d)
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(self.ids(app), ["assets/ok/x.png"])

    def test_hide_globs(self):
        self.write("assets/sfx/pixabay/a.wav")
        self.write("assets/sfx/b.wav")
        app = self.make_app({"roots": ["assets"], "hide": ["assets/sfx/pixabay/**"]})
        self.assertEqual(self.ids(app), ["assets/sfx/b.wav"])

    def test_hide_patterns_in_every_spelling(self):  # a pattern that fails open leaks what it was meant to hide
        for pattern in ("assets/secret", "assets\\secret\\*", "./assets/secret/*", "assets/secret/", "assets/secret/**", "ASSETS/Secret"):
            with self.subTest(pattern=pattern):
                self.write("assets/secret/a.png")
                self.write("assets/secret/deep/b.png")
                self.write("assets/secretive/c.png")
                self.write("assets/d.png")
                app = self.make_app({"roots": ["assets"], "hide": [pattern]})
                self.assertEqual(self.ids(app), ["assets/d.png", "assets/secretive/c.png"])

    def test_dot_files_are_not_listed(self):  # /files/ never serves them, so the list must not offer them
        self.write("assets/.x.png")
        self.write("assets/a.png")
        self.write("assets/sub/.hidden.wav")
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(self.ids(app), ["assets/a.png"])

    def test_every_listed_file_can_be_served(self):  # the listed and the served types are one table
        from viewer.filetypes import DATA_EXTS, KINDS, SERVED_EXTS
        self.assertEqual(SERVED_EXTS - set(KINDS), set(DATA_EXTS))
        names = ["m" + ext for ext in KINDS]
        for n in names:
            self.write("assets/" + n)
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(sorted(i["name"] + os.path.splitext(i["path"])[1] for i in self.catalog(app)["items"]), sorted(names))
        for i in self.catalog(app)["items"]:
            self.assertTrue(app.project.readable(i["path"]), i["path"])

    def test_a_junction_loop_is_walked_once(self):  # audit: the walk followed Windows junctions
        self.write("assets/a/x.png")
        self.write("assets/top.png")
        helpers.link_dir(os.path.join(self.project_dir, "assets", "a", "loop"), os.path.join(self.project_dir, "assets"))
        app = self.make_app({"roots": ["assets"]})
        cat = self.catalog(app)
        self.assertEqual(self.ids(app), ["assets/a/x.png", "assets/top.png"])
        self.assertEqual(cat["warnings"], [])

    def test_a_junction_to_a_folder_elsewhere_in_the_project_is_not_entered(self):
        self.write("assets/real/x.png")
        self.write("other/y.png")
        helpers.link_dir(os.path.join(self.project_dir, "assets", "alias"), os.path.join(self.project_dir, "other"))
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(self.ids(app), ["assets/real/x.png"])

    def test_overlapping_roots_list_a_file_once(self):
        self.write("assets/a.png")
        app = self.make_app({"roots": ["assets", "."]})
        self.assertEqual(self.ids(app), ["assets/a.png"])

    def test_missing_root_is_a_warning_not_an_error(self):
        self.write("assets/a.png")
        app = self.make_app({"roots": ["assets", "gone"]})
        cat = self.catalog(app)
        self.assertEqual(self.ids(app), ["assets/a.png"])
        self.assertTrue(any("gone" in w for w in cat["warnings"]))

    def test_materials_by_glob_with_sets(self):
        self.write("assets/trees/fbx/oak.fbx")
        self.write("assets/trees/fbx/pine.fbx")
        self.write("assets/other/rock.fbx")
        self.write("assets/trees/atlas/bark_Color.png")
        self.write("assets/trees/atlas/bark_Normal.jpg")
        self.write("assets/trees/atlas/leaf_Color.png")
        app = self.make_app({"roots": ["assets"], "materials": {
            "assets/trees/fbx/*.fbx": {"Bark": {"set": "assets/trees/atlas/bark"},
                                       "Leaf": {"set": "assets/trees/atlas/leaf", "alpha": "cutout", "doubleSided": True}}}})
        items = {i["name"]: i for i in self.catalog(app)["items"] if i["kind"] == "model"}
        self.assertNotIn("materials", items["rock"])
        want = {"Bark": {"maps": {"Color": "assets/trees/atlas/bark_Color.png", "Normal": "assets/trees/atlas/bark_Normal.jpg"}},
                "Leaf": {"maps": {"Color": "assets/trees/atlas/leaf_Color.png"}, "alpha": "cutout", "doubleSided": True}}
        self.assertEqual(items["oak"]["materials"], want)
        self.assertEqual(items["pine"]["materials"], want)

    def test_models_and_maps_in_the_catalog(self):
        self.write("assets/Widget.fbx")
        self.write("assets/Widget_Color.png")
        self.write("assets/Widget_Worn_Normal.png")
        app = self.make_app({"roots": ["assets"]})
        gun = [i for i in self.catalog(app)["items"] if i["kind"] == "model"][0]
        self.assertEqual(gun["maps"], {"Base": {"Color": "assets/Widget_Color.png"}, "Worn": {"Normal": "assets/Widget_Worn_Normal.png"}})

    def test_version_is_stable_and_changes_with_files(self):
        self.write("assets/a.png")
        app = self.make_app({"roots": ["assets"]})
        v1 = app.catalog.get()[0]
        self.assertEqual(scan(app.project, app.state.config, app.state.registry)["version"], v1)
        path = self.write("assets/b.png")
        app.catalog._at = 0  # expire the 1 s cache
        v2 = app.catalog.get()[0]
        self.assertNotEqual(v1, v2)
        os.utime(path, (time.time() + 100, time.time() + 100))
        app.catalog._at = 0
        self.assertNotEqual(app.catalog.get()[0], v2)

    def test_cache_returns_the_same_scan_within_a_second(self):
        self.write("assets/a.png")
        app = self.make_app({"roots": ["assets"]})
        first = app.catalog.get()
        self.write("assets/b.png")
        self.assertIs(app.catalog.get()[2], first[2])  # cached
        app.catalog._at = 0
        self.assertEqual(len(app.catalog.get()[2]["items"]), 2)

    def test_cached_equals_uncached_scan(self):
        for n in ("a.png", "b/c.fbx", "b/c_Color.png", "d.wav"):
            self.write("assets/" + n)
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(app.catalog.get()[2], scan(app.project, app.state.config, app.state.registry))

    def test_vanished_and_unreadable_files_do_not_break_the_scan(self):
        self.write("assets/a.png")
        app = self.make_app({"roots": ["assets"]})
        os.remove(os.path.join(self.project_dir, "assets", "a.png"))
        self.assertEqual(self.catalog(app)["items"], [])

    def test_symlinked_file_leaving_the_project_is_not_listed(self):
        outside = os.path.join(self.tmp, "outside.png")
        with open(outside, "w") as f:
            f.write("x")
        self.write("assets/a.png")
        try:
            os.symlink(outside, os.path.join(self.project_dir, "assets", "link.png"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not allowed here")
        app = self.make_app({"roots": ["assets"]})
        self.assertEqual(self.ids(app), ["assets/a.png"])

    def test_large_catalog_scan_is_quick(self):
        for i in range(60):
            for j in range(10):
                self.write("assets/d%d/m%d.fbx" % (i, j))
                self.write("assets/d%d/m%d_Color.png" % (i, j))
        app = self.make_app({"roots": ["assets"]})
        t = time.time()
        cat = scan(app.project, app.state.config, app.state.registry)
        self.assertEqual(len(cat["items"]), 1200)
        self.assertLess(time.time() - t, 5)


if __name__ == "__main__":
    unittest.main()
