# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import time
import unittest

import helpers
from viewer.config import ConfigError, ConfigManager, Root, find_config, load_file, parse
from viewer.project import Project


class ConfigTest(helpers.ProjectCase):
    def project(self):
        return Project(self.project_dir)

    def load(self, raw, file="asset-viewer.json"):
        return parse(raw, self.project(), file)

    def test_defaults_without_a_file(self):
        cfg = load_file(self.project(), None)
        self.assertEqual(cfg.roots, [Root("", "")])
        self.assertEqual(cfg.title, os.path.basename(self.project_dir))
        self.assertEqual((cfg.port, cfg.theme, cfg.front), (8765, "aero", "+z"))
        self.assertEqual((cfg.data_picks, cfg.data_review), ("picks.json", "review.json"))
        self.assertRegex(cfg.prefs_prefix, r"^av\.[0-9a-f]{8}\.$")
        self.assertEqual(cfg.vfx_folders, ["vfx"])
        self.assertEqual(cfg.maps, ["Color", "Normal", "Metalness", "Roughness", "Emissive"])

    def test_discovery_order(self):
        self.assertIsNone(find_config(self.project()))
        self.write(".asset-viewer/config.json", {})
        self.assertEqual(find_config(self.project()), ".asset-viewer/config.json")
        self.write("asset-viewer.json", {})
        self.assertEqual(find_config(self.project()), "asset-viewer.json")

    def test_explicit_config_and_data_defaults_live_in_the_config_folder(self):
        self.write("config/asset-viewer.json", {"title": "T"})
        self.assertEqual(find_config(self.project(), "config/asset-viewer.json"), "config/asset-viewer.json")
        self.assertEqual(find_config(self.project(), ".\\config\\asset-viewer.json"), "config/asset-viewer.json")
        cfg = load_file(self.project(), "config/asset-viewer.json")
        self.assertEqual((cfg.data_picks, cfg.data_review, cfg.config_dir),
                         ("config/picks.json", "config/review.json", "config"))
        with self.assertRaisesRegex(ConfigError, "not found"):
            find_config(self.project(), "nope.json")
        with self.assertRaises(ConfigError):
            find_config(self.project(), "../outside.json")

    def test_hidden_folder_config_keeps_data_beside_it(self):
        self.write(".asset-viewer/config.json", {})
        cfg = load_file(self.project(), ".asset-viewer/config.json")
        self.assertEqual(cfg.data_picks, ".asset-viewer/picks.json")

    def test_roots_and_labels(self):
        cfg = self.load({"roots": ["assets", {"path": "docs/ui-kit/icons", "label": "icons"}, {"path": "x", "label": ""}, "."]})
        self.assertEqual(cfg.roots, [Root("assets", "assets"), Root("docs/ui-kit/icons", "icons"), Root("x", ""), Root("", "")])

    def test_paths_are_normalised(self):
        cfg = self.load({"roots": ["./a/b/"], "data": {"picks": "d/p.json"}})
        self.assertEqual(cfg.roots[0].path, "a/b")
        self.assertEqual(cfg.data_picks, "d/p.json")

    def test_errors_name_the_key_path(self):
        cases = [
            ({"port": "x"}, "port: integer"),
            ({"port": True}, "port: integer"),
            ({"roots": "assets"}, "roots: array"),
            ({"roots": [{"label": "x"}]}, r"roots\[0\]\.path: string expected"),
            ({"roots": [{"path": "a", "label": 5}]}, r"roots\[0\]\.label: string expected"),
            ({"roots": ["../x"]}, r"roots\[0\]"),
            ({"roots": ["a\\b"]}, r"roots\[0\]"),
            ({"roots": ["/abs"]}, r"roots\[0\]"),
            ({"roots": ["C:/x"]}, r"roots\[0\]"),
            ({"model": {"front": "x"}}, 'model.front: "\\+z" or "-z" expected'),
            ({"model": {"glassNames": ["("]}}, r"model\.glassNames\[0\]"),
            ({"model": {"glassNames": ["(?i)x"]}}, r"model\.glassNames\[0\]"),
            ({"model": {"maps": [1]}}, r"model\.maps\[0\]: string expected"),
            ({"materials": {"a/*.fbx": {"M": {"alpha": "cutout"}}}}, r"\.set: string expected"),
            ({"materials": {"a/*.fbx": {"M": {"set": "s", "alpha": "blend"}}}}, r"\.alpha: \"cutout\" expected"),
            ({"materials": {"a/*.fbx": {"M": {"set": "s", "doubleSided": 1}}}}, r"\.doubleSided: boolean expected"),
            ({"plugins": {"Bad Name": {}}}, "plugins.Bad Name"),
            ({"plugins": {"ok": 5}}, "plugins.ok: object expected"),
            ({"data": {"picks": "a.json", "review": "a.json"}}, "different files"),
            ({"categories": ["a", "a"]}, "duplicate"),
            ({"vfxFolders": ["a/b"]}, r"vfxFolders\[0\]"),
            ({"theme": 3}, "theme"),
            ([], "config: object expected"),
        ]
        for raw, pattern in cases:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ConfigError, pattern):
                    self.load(raw)

    def test_hide_patterns_mean_the_path_or_anything_below_it(self):
        for pattern in ("assets/secret", "assets\\secret\\*", "./assets/secret/*", "assets/secret/", "assets//secret", "assets/./secret",
                        "assets/secret/**", "  assets/secret  "):
            with self.subTest(pattern=pattern):
                cfg = self.load({"hide": [pattern]})
                self.assertEqual(cfg.warnings, [])
                self.assertEqual(len(cfg.hide_rules), 1)
                project = Project(self.project_dir, hide=cfg.hide)
                for hidden in ("assets/secret", "assets/secret/a.png", "assets/secret/deep/b.png", "ASSETS/SECRET/c.png"):
                    self.assertTrue(project.is_hidden(hidden), (pattern, hidden))
                for shown in ("assets/secretive/d.png", "assets/other/secret.png", "assets/a.png"):
                    self.assertFalse(project.is_hidden(shown), (pattern, shown))

    def test_hide_globs_keep_working(self):
        project = Project(self.project_dir, hide=self.load({"hide": ["assets/sfx/*.wav", "*.psd"]}).hide)
        self.assertTrue(project.is_hidden("assets/sfx/a.wav"))
        self.assertTrue(project.is_hidden("assets/sfx/deep/a.wav"))
        self.assertTrue(project.is_hidden("art/x.psd"))
        self.assertFalse(project.is_hidden("assets/sfx/a.ogg"))

    def test_hide_patterns_that_can_match_nothing_warn_and_are_dropped(self):
        cfg = self.load({"hide": ["/etc", "C:/x", "C:\\x", "../out", "a/../../b", "", "./", "ok"]})
        self.assertEqual(len(cfg.warnings), 7)
        self.assertEqual([p for p, _ in cfg.hide_rules], ["ok"])
        self.assertTrue(all(w.startswith("hide[") for w in cfg.warnings))
        self.assertIn("absolute", cfg.warnings[0])
        self.assertIn("'..'", cfg.warnings[3])

    def test_the_redirects_key_is_gone(self):
        self.assertEqual(self.load({"redirects": ["/old/"]}).warnings, ["redirects: unknown key"])

    def test_glass_names_must_mean_the_same_to_javascript(self):
        good = ["^Glass", "Lens$", "(?:a|b)c", "x(?=y)", "a+?b", "[A-Z]\\d{2,3}", "(?!no)", "a{2}", "[^\\]]", "\\]"]
        for s in good:
            with self.subTest(good=s):
                self.assertEqual(self.load({"model": {"glassNames": [s]}}).glass_names, [s])
        bad = ["(?P<n>x)", "(?i)glass", "(?i:glass)", "(?<=a)b", "(?<!a)b", "(?>a+)b", "(?#note)a", "(?(1)a|b)", "a*+", "a++", "a?+", "a{2}+",
               "\\Aglass", "glass\\Z", "[]a]", "[^]a]"]
        for s in bad:
            with self.subTest(bad=s):
                with self.assertRaisesRegex(ConfigError, r"model\.glassNames\[0\]"):
                    self.load({"model": {"glassNames": [s]}})
        with self.assertRaisesRegex(ConfigError, "JavaScript"):
            self.load({"model": {"glassNames": ["(?i)x"]}})
        with self.assertRaisesRegex(ConfigError, "not a valid regex"):
            self.load({"model": {"glassNames": ["("]}})

    def test_unknown_keys_warn(self):
        cfg = self.load({"bogus": 1, "model": {"nope": 2}, "roots": [{"path": "a", "x": 1}]})
        self.assertEqual(sorted(cfg.warnings), ["bogus: unknown key", "model.nope: unknown key", "roots[0].x: unknown key"])

    def test_invalid_json_message_has_the_file(self):
        self.write("asset-viewer.json", "{ nope")
        with self.assertRaisesRegex(ConfigError, "asset-viewer.json: invalid JSON"):
            load_file(self.project(), "asset-viewer.json")

    def test_error_in_a_file_is_prefixed_with_it(self):
        self.write("asset-viewer.json", {"port": "x"})
        with self.assertRaisesRegex(ConfigError, "asset-viewer.json: port: integer"):
            load_file(self.project(), "asset-viewer.json")

    def test_plugin_names_skip_disabled(self):
        cfg = self.load({"plugins": {"a": {}, "b": {"enabled": False}, "c": None}})
        self.assertEqual(cfg.plugin_names(), ["a", "c"])

    def test_bad_reload_keeps_the_last_good_config(self):
        self.write("asset-viewer.json", {"title": "One"})
        mgr = ConfigManager(self.project())
        self.assertEqual(mgr.config.title, "One")
        self.assertFalse(mgr.poll())
        path = self.write("asset-viewer.json", {"title": "Two", "port": "bad"})
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertFalse(mgr.poll())
        self.assertEqual(mgr.config.title, "One")
        self.assertIn("port: integer", mgr.error)
        path = self.write("asset-viewer.json", {"title": "Three"})
        os.utime(path, (time.time() + 10, time.time() + 10))
        self.assertTrue(mgr.poll())
        self.assertEqual((mgr.config.title, mgr.error), ("Three", None))
        self.assertEqual(mgr.generation, 2)

    def test_config_that_appears_later_is_picked_up(self):
        mgr = ConfigManager(self.project())
        self.assertIsNone(mgr.config.config_file)
        self.write("asset-viewer.json", {"title": "Late"})
        self.assertTrue(mgr.poll())
        self.assertEqual(mgr.config.title, "Late")

    def test_first_load_of_a_bad_config_raises(self):
        self.write("asset-viewer.json", {"port": 0})
        with self.assertRaises(ConfigError):
            ConfigManager(self.project())


if __name__ == "__main__":
    unittest.main()
