# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runs the tests that live in each plugin (plugins/<name>/tests/test_*.py) as part of `unittest discover -s tests`.

A plugin folder name has dashes, so its tests cannot be found as a package, and `loader.discover` refuses (Python 3.9
asserts) a start folder outside the top-level one. So each test file is imported by path and handed to
`loader.loadTestsFromModule`. Test file names must be unique across plugins (they become module names): name them after the
plugin, e.g. test_rig_export.py.
"""
import glob
import importlib.util
import os
import sys
import unittest

from helpers import REPO


def plugin_test_dirs():
    return sorted(glob.glob(os.path.join(REPO, "plugins", "*", "tests")))


def plugin_test_files():
    return [p for d in plugin_test_dirs() for p in sorted(glob.glob(os.path.join(d, "test_*.py")))]


def import_by_path(path):
    """The module of a test file, named after the file (so it is loaded once however many times discovery asks)."""
    name = os.path.splitext(os.path.basename(path))[0]
    if name in sys.modules and getattr(sys.modules[name], "__file__", None) == path:
        return sys.modules[name]
    folder = os.path.dirname(path)
    if folder not in sys.path:
        sys.path.insert(0, folder)  # a plugin's tests may import a helper beside them
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for path in plugin_test_files():
        suite.addTests(loader.loadTestsFromModule(import_by_path(path)))
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
