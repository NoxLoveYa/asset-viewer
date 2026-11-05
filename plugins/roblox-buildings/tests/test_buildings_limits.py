# SPDX-License-Identifier: AGPL-3.0-or-later
"""Limits of the buildings enricher: a huge sidecar is skipped with a warning, and the parse cache forgets files that are gone."""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "tests"))
import helpers  # noqa: E402

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "roblox-buildings"
SIDECAR = {"name": "Box", "objects": {"Body": {"tris": 10}}}


def module():
    return sys.modules["viewer_plugin_roblox_buildings"]


class LimitsCase(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(PLUGIN, os.path.join(self.tool_dir, "plugins", NAME), ignore=shutil.ignore_patterns("tests", "__pycache__"))
        self.app = self.make_app({"roots": ["assets"], "plugins": {NAME: {}}})

    def rescan(self):
        self.app.catalog._at = 0
        return self.catalog(self.app)

    def model(self, cat, name):
        return [i for i in cat["items"] if i["path"] == "assets/" + name][0]


class SizeLimitTest(LimitsCase):
    def test_a_sidecar_over_8_mib_is_skipped_with_a_warning(self):
        self.write("assets/big.fbx")
        self.write("assets/big.json", b'{"objects": {"a": {}}, "pad": "' + b"x" * ((8 << 20) + 1) + b'"}')
        self.write("assets/ok.fbx")
        self.write("assets/ok.json", SIDECAR)
        cat = self.rescan()
        self.assertNotIn("ext", self.model(cat, "big.fbx"))
        self.assertIn(NAME, self.model(cat, "ok.fbx")["ext"])
        (warning,) = [w for w in cat["warnings"] if "big.fbx" in w]
        self.assertIn("plugin " + NAME, warning)
        self.assertIn("8 MiB", warning)

    def test_the_limit_is_a_constant_of_the_plugin(self):
        self.assertEqual(module().MAX_JSON, 8 << 20)


class CachePruneTest(LimitsCase):
    def test_the_cache_keeps_only_sidecars_the_last_scan_met(self):
        for n in ("a", "b"):
            self.write("assets/%s.fbx" % n)
            self.write("assets/%s.json" % n, SIDECAR)
        self.rescan()
        self.assertEqual(sorted(os.path.basename(p) for p in module()._cache), ["a.json", "b.json"])
        os.remove(os.path.join(self.project_dir, "assets", "b.fbx"))
        os.remove(os.path.join(self.project_dir, "assets", "b.json"))
        self.rescan()
        self.assertEqual([os.path.basename(p) for p in module()._cache], ["a.json"])
        os.remove(os.path.join(self.project_dir, "assets", "a.fbx"))
        self.rescan()
        self.assertEqual(module()._cache, {})


if __name__ == "__main__":
    unittest.main()
