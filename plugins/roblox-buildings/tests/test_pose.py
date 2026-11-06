# SPDX-License-Identifier: AGPL-3.0-or-later
"""pose.js, the building add-on's placement and pose maths, run under node (skipped when node is not installed)."""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class PoseMaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([NODE, os.path.join(HERE, "pose_probe.mjs"), REPO], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if run.returncode:
            raise AssertionError("pose_probe.mjs failed:\n" + run.stderr)
        cls.out = json.loads(run.stdout)

    def test_placement(self):
        o = self.out
        self.assertEqual(o["yawOrigin"], [-3, 1, -2])  # (x, y, z) -> (-x, y, -z)
        self.assertTrue(o["yawEqualsCf"])  # a cf of the same yaw places like pos and yaw
        self.assertTrue(o["badCfFallsBack"])  # a cf that is not 12 numbers is ignored

    def test_tipped_cf(self):
        # a cf whose up column is Roblox +X: in the FBX frame (turned 180 degrees about Y) up points along -X
        self.assertEqual(self.out["tippedUp"], [-1, 0, 0])
        self.assertEqual(self.out["tippedFront"], [0, 0, -1])

    def test_furniture_state(self):
        o = self.out
        self.assertEqual(o["furnitureNames"], ["Drawer_1", "Glass_D", "Door_L", "Glass_Door_L"])  # the pane with the same motion follows
        self.assertEqual(o["drawerMove"], [0, 0, 0.5])  # half of a slide of (0, 0, -1), in the FBX frame
        self.assertEqual(o["hingeEnd"], [-1, 0, -1])  # a leaf turned 90 degrees counter-clockwise about its hinge

    def test_opening_state(self):
        o = self.out
        self.assertEqual(o["ajarPose"], ["Leaf_A", "Glass_A"])  # only the lead leaf (first by name) turns, with its pane
        self.assertEqual(o["ajarHide"], [])
        self.assertEqual(o["removedHide"], ["Glass_A", "Leaf_A", "Leaf_B"])  # a removed door: the leaves and panes go, the slider stays
        self.assertEqual(o["shutPose"], {"hide": [], "pose": []})
        self.assertEqual(o["zeroPose"], 0)

    def test_apply_restores(self):
        self.assertEqual(self.out["applied"], [0, 0, -1.75])
        self.assertEqual(self.out["restored"], [0, 0, 0.25])


if __name__ == "__main__":
    unittest.main()
