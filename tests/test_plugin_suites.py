# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runs the tests that live in each plugin (plugins/<name>/tests/test_*.py) as part of `unittest discover -s tests`.

A plugin folder name has dashes, so its tests cannot be found as a package; this loads them by folder. Test file names
must be unique across plugins (they share one module namespace): name them after the plugin, e.g. test_rig_export.py.
"""
import glob
import os
import unittest

from helpers import REPO


def plugin_test_dirs():
    return sorted(glob.glob(os.path.join(REPO, "plugins", "*", "tests")))


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for d in plugin_test_dirs():
        suite.addTests(loader.discover(d, pattern="test_*.py", top_level_dir=d))
    suite.addTests(loader.loadTestsFromTestCase(SuitesTest))
    return suite


class SuitesTest(unittest.TestCase):
    def test_plugin_test_file_names_are_unique(self):
        names = [os.path.basename(p) for d in plugin_test_dirs() for p in glob.glob(os.path.join(d, "test_*.py"))]
        self.assertEqual(sorted(names), sorted(set(names)))

    def test_every_plugin_with_a_server_has_tests(self):
        missing = [os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(REPO, "plugins", "*", "server.py"))
                   if not glob.glob(os.path.join(os.path.dirname(p), "tests", "test_*.py"))]
        self.assertEqual(missing, [])
