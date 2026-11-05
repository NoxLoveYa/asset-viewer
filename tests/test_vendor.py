# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vendored three.js and fonts are the files their manifests pin (no network: it only hashes what is on disk)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import REPO


def verify(root):
    return subprocess.run([sys.executable, "-B", os.path.join(root, "tools", "vendor_three.py"), "--verify"],
                          capture_output=True, text=True, timeout=120, cwd=root)


class VendorTest(unittest.TestCase):
    def test_verify_passes(self):
        r = verify(REPO)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_verify_notices_a_changed_file(self):  # on a copy: the repo's own files are never touched
        root = tempfile.mkdtemp(prefix="av-vendor-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "tools"))
        shutil.copy(os.path.join(REPO, "tools", "vendor_three.py"), os.path.join(root, "tools"))
        shutil.copytree(os.path.join(REPO, "vendor"), os.path.join(root, "vendor"))
        shutil.copytree(os.path.join(REPO, "themes", "aero", "fonts"), os.path.join(root, "themes", "aero", "fonts"))
        self.assertEqual(verify(root).returncode, 0, "the copy should verify before it is changed")
        with open(os.path.join(root, "vendor", "three", "LICENSE"), "ab") as f:
            f.write(b"\ntampered\n")
        r = verify(root)
        self.assertNotEqual(r.returncode, 0, "a changed vendored file must fail --verify")
        self.assertIn("LICENSE", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
