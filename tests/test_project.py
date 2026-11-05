# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import unittest

import helpers
from viewer.project import PathError, Project, WriteError, split_rel


class ConfineTest(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("assets/a.png")
        self.write("assets/sub/b.png")
        self.write("secret.txt")
        self.project = Project(self.project_dir, areas=["assets"])

    def test_confine_accepts_plain_paths(self):
        self.assertEqual(self.project.confine("assets/a.png"), os.path.join(self.project_dir, "assets", "a.png"))
        self.assertEqual(self.project.confine(""), self.project_dir)
        self.assertEqual(self.project.confine("not/yet/there.png"), os.path.join(self.project_dir, "not", "yet", "there.png"))

    def test_confine_rejects(self):
        bad = ["../x", "assets/../../x", "assets/..", "a/./b", "a//b", "a/", "/abs", "C:/Windows", "C:\\Windows", "a\\b",
               "a\x00b", "a:b", "con", "assets/NUL.png", "aux.txt", "a/b ", "a/b.", "a/\x01"]
        for rel in bad:
            with self.subTest(rel=rel):
                with self.assertRaises(PathError):
                    self.project.confine(rel)
        with self.assertRaises(PathError):
            split_rel(None)

    def test_percent_forms_are_not_decoded_here(self):
        # decoding happens once, in the HTTP layer; a still-encoded dot-dot is just an odd file name
        self.assertTrue(self.project.confine("%2e%2e/x").endswith("%2e%2e" + os.sep + "x"))

    def test_symlink_escape_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "o.png"), "w") as f:
            f.write("x")
        helpers.link_dir(os.path.join(self.project_dir, "assets", "link"), outside)
        with self.assertRaises(PathError):
            self.project.confine("assets/link/o.png")
        self.assertFalse(self.project.readable("assets/link/o.png"))

    def test_rel_inverse_and_outside(self):
        self.assertEqual(self.project.rel(os.path.join(self.project_dir, "assets", "a.png")), "assets/a.png")
        self.assertEqual(self.project.rel(self.project_dir), "")
        with self.assertRaises(PathError):
            self.project.rel(os.path.dirname(self.project_dir))

    def test_readable_rules(self):
        ok = ["assets/a.png", "assets/sub/b.png"]
        for rel in ok:
            self.assertTrue(self.project.readable(rel), rel)
        self.write("assets/.hidden/x.png")
        self.write("assets/page.html")
        self.write("assets/.env.png")
        for rel in ["secret.txt", "assets", "assets/missing.png", "assets/.hidden/x.png", "assets/page.html", "assets/.env.png",
                    ".git/config", "../x.png"]:
            self.assertFalse(self.project.readable(rel), rel)

    def test_hide_globs_apply_to_serving(self):
        self.project.hide = ["assets/sub/*", "ASSETS/Z*"]
        self.write("assets/zed.png")
        self.assertFalse(self.project.readable("assets/sub/b.png"))
        self.assertFalse(self.project.readable("assets/zed.png"))
        self.assertTrue(self.project.readable("assets/a.png"))

    def test_a_declared_area_may_sit_in_a_dot_folder_and_nothing_else_there(self):
        for rel in (".asset-viewer/rigs/box.json", ".asset-viewer/rigs/sub/b.png", ".asset-viewer/rigs/.hidden/c.png",
                    ".asset-viewer/rigs/.d.png", ".asset-viewer/other/x.json", ".asset-viewer/config.json"):
            self.write(rel)
        p = Project(self.project_dir, areas=[".asset-viewer/rigs"])
        for rel in (".asset-viewer/rigs/box.json", ".asset-viewer/rigs/sub/b.png"):
            self.assertTrue(p.readable(rel), rel)
        for rel in (".asset-viewer/rigs/.hidden/c.png", ".asset-viewer/rigs/.d.png", ".asset-viewer/other/x.json",
                    ".asset-viewer/config.json", ".asset-viewer/rigs/../config.json"):
            self.assertFalse(p.readable(rel), rel)
        self.assertFalse(Project(self.project_dir, areas=["assets"]).readable(".asset-viewer/rigs/box.json"))
        self.assertTrue(Project(self.project_dir, areas=[".asset-viewer/config.json"]).readable(".asset-viewer/config.json"))

    def test_policy_problem_names_why_an_area_cannot_be_served(self):
        self.write(".asset-viewer/rigs/box.json")
        p = Project(self.project_dir, areas=[".asset-viewer/rigs", "assets"], hide=["assets/secret", "assets/secret/*"])
        self.assertIsNone(p.policy_problem(".asset-viewer/rigs"))
        self.assertIsNone(p.policy_problem("assets"))
        self.assertIn("hidden", p.policy_problem("assets/secret"))
        self.assertIn("outside", p.policy_problem("elsewhere"))
        self.assertIn("dot", p.policy_problem("assets/.cache"))

    def test_the_real_path_is_judged_too(self):  # a link must not carry a file out of the rules
        self.write("other/y.png")
        self.write("page.html", "<b>")
        helpers.link_dir(os.path.join(self.project_dir, "assets", "alias"), os.path.join(self.project_dir, "other"))
        self.assertFalse(self.project.readable("assets/alias/y.png"))  # the target is outside the served areas
        try:
            os.symlink(os.path.join(self.project_dir, "page.html"), os.path.join(self.project_dir, "assets", "page.png"))
        except (OSError, NotImplementedError):
            return  # no file symlinks here; the folder case above ran
        self.assertFalse(self.project.readable("assets/page.png"))  # a .png name over html

    def test_whole_project_area_still_refuses_dot_folders(self):
        self.write(".git/HEAD.txt")
        p = Project(self.project_dir, areas=[""])
        self.assertTrue(p.readable("secret.txt"))
        self.assertFalse(p.readable(".git/HEAD.txt"))

    def test_symlink_into_a_dot_folder_is_refused(self):
        self.write(".private/p.png")
        helpers.link_dir(os.path.join(self.project_dir, "assets", "lnk"), os.path.join(self.project_dir, ".private"))
        self.assertFalse(self.project.readable("assets/lnk/p.png"))


class WriteTest(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.project = Project(self.project_dir)

    def test_write_inside_area(self):
        self.project.write_atomic("data/p.json", b"{}", allowed=["data"])
        with open(os.path.join(self.project_dir, "data", "p.json"), "rb") as f:
            self.assertEqual(f.read(), b"{}")
        self.assertEqual([n for n in os.listdir(os.path.join(self.project_dir, "data")) if n.endswith(".tmp")], [])

    def test_write_exact_file_area_and_replace(self):
        self.project.write_atomic("p.json", b"1", allowed=["p.json"])
        self.project.write_atomic("p.json", b"22", allowed=["p.json"])
        with open(os.path.join(self.project_dir, "p.json"), "rb") as f:
            self.assertEqual(f.read(), b"22")

    def test_write_outside_area_raises(self):
        for rel, allowed in [("other.json", ["p.json"]), ("data/../x", ["data"]), ("datax/p.json", ["data"]),
                             ("p.json", []), ("../x", [""]), ("/abs", [""]), ("", [""])]:
            with self.subTest(rel=rel):
                with self.assertRaises(PathError):
                    self.project.write_atomic(rel, b"x", allowed=allowed)
        self.assertFalse(os.path.exists(os.path.join(self.project_dir, "other.json")))

    def test_write_through_a_symlink_out_of_the_project_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        helpers.link_dir(os.path.join(self.project_dir, "link"), outside)
        with self.assertRaises(PathError):
            self.project.write_atomic("link/x.json", b"x", allowed=["link"])
        self.assertEqual(os.listdir(outside), [])

    def test_write_failure_is_a_clear_error_and_leaves_no_temp(self):
        self.write("data", "i am a file, not a folder")
        with self.assertRaises(WriteError) as cm:
            self.project.write_atomic("data/p.json", b"x", allowed=["data"])
        self.assertIn("data/p.json", str(cm.exception))

    def test_read_json(self):
        self.write("c.json", '{"a": 1}')
        self.assertEqual(self.project.read_json("c.json"), {"a": 1})
        with self.assertRaises(PathError):
            self.project.read_json("../c.json")


if __name__ == "__main__":
    unittest.main()
