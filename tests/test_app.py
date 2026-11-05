# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import subprocess
import sys
import unittest
from unittest import mock

import helpers
from helpers import REPO
from viewer import app as app_module
from viewer import launch
from viewer.http import Server


class TokenFileTest(helpers.ProjectCase):
    """The per-run token goes to a per-user state folder named by the project, never into the project."""

    def make(self):
        self.write("assets/a.png")
        return self.make_app({"roots": ["assets"]})

    def test_written_outside_the_project_and_named_by_it(self):
        app = self.make()
        path = app.write_token_file()
        self.assertEqual(path, os.path.join(self.state_dir, app.project_hash + ".token"))
        with open(path) as f:
            self.assertEqual(f.read(), app.token)
        self.assertFalse(os.path.realpath(path).startswith(self.project_dir))
        self.assertEqual([n for n in os.listdir(self.project_dir) if "token" in n], [])
        if os.name != "nt":
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(self.state_dir).st_mode & 0o077, 0)

    def test_two_projects_two_files(self):
        a = self.make()
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        b = app_module.App(other, None, tool_dir=self.tool_dir)
        self.assertNotEqual(a.write_token_file(), b.write_token_file())
        self.assertEqual(len(os.listdir(self.state_dir)), 2)

    def test_a_stale_file_is_replaced_at_start(self):
        app = self.make()
        os.makedirs(self.state_dir)
        stale = os.path.join(self.state_dir, app.project_hash + ".token")
        with open(stale, "w") as f:
            f.write("old-token-of-a-dead-run")
        app.write_token_file()
        with open(stale) as f:
            self.assertEqual(f.read(), app.token)

    def test_removed_on_exit_but_only_when_it_is_still_ours(self):
        app = self.make()
        path = app.write_token_file()
        app.remove_token_file()
        self.assertFalse(os.path.exists(path))
        path = app.write_token_file()
        with open(path, "w") as f:
            f.write("somebody else's run")  # another instance of the same project started since
        app.remove_token_file()
        self.assertTrue(os.path.exists(path))

    def test_the_candidates_when_nothing_is_set(self):
        env = {k: v for k, v in os.environ.items() if k != app_module.STATE_DIR_ENV}
        base = os.path.join(self.tmp, "base")
        key = "LOCALAPPDATA" if os.name == "nt" else "XDG_RUNTIME_DIR"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(os.environ, {key: base}):
            got = app_module.state_dir_candidates()
        self.assertEqual(got[0], os.path.join(base, "asset-viewer"))
        self.assertTrue(got[-1].endswith("asset-viewer"))  # the temp folder is the last resort

    def test_falls_back_when_the_first_folder_cannot_be_made(self):
        app = self.make()
        blocked = os.path.join(self.tmp, "blocked")
        with open(blocked, "w") as f:
            f.write("a file where a folder should go")
        good = os.path.join(self.tmp, "good")
        with mock.patch.object(app_module, "state_dir_candidates", lambda: [os.path.join(blocked, "x"), good]):
            path = app.write_token_file()
        self.assertEqual(path, os.path.join(good, app.project_hash + ".token"))

    def test_check_private_names_why_a_folder_is_no_place_for_a_secret(self):
        import stat
        from types import SimpleNamespace as St
        mine = app_module.check_private
        self.assertIsNone(mine(St(st_mode=stat.S_IFDIR | 0o700, st_uid=7), 7))
        self.assertIsNone(mine(St(st_mode=stat.S_IFDIR | 0o755, st_uid=7), 7))  # readable is fine, the file is 0600
        self.assertIn("another user", mine(St(st_mode=stat.S_IFDIR | 0o700, st_uid=8), 7))
        for mode in (0o777, 0o770, 0o707, 0o722, 0o702):
            self.assertIn("written to", mine(St(st_mode=stat.S_IFDIR | mode, st_uid=7), 7), oct(mode))
        self.assertIn("plain folder", mine(St(st_mode=stat.S_IFLNK | 0o777, st_uid=7), 7))
        self.assertIn("plain folder", mine(St(st_mode=stat.S_IFREG | 0o600, st_uid=7), 7))

    def test_nothing_is_written_when_every_folder_is_refused(self):
        app = self.make()
        with mock.patch.object(app_module, "state_dir_candidates", lambda: [os.path.join(self.tmp, "a")]), \
                mock.patch.object(app_module, "private_dir", side_effect=OSError("a belongs to another user")), \
                mock.patch("sys.stderr") as err:
            self.assertIsNone(app.write_token_file())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "a")))
        self.assertIn("belongs to another user", "".join(c.args[0] for c in err.write.call_args_list))
        app.remove_token_file()  # nothing to remove, nothing raised

    @unittest.skipIf(os.name == "nt", "folder modes and owners are POSIX")
    def test_a_shared_folder_made_by_someone_else_is_not_used(self):
        app = self.make()
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(shared)
        os.chmod(shared, 0o777)  # like a /tmp/asset-viewer another user made first
        good = os.path.join(self.tmp, "good")
        with mock.patch.object(app_module, "state_dir_candidates", lambda: [shared, good]):
            path = app.write_token_file()
        self.assertEqual(path, os.path.join(good, app.project_hash + ".token"))
        self.assertEqual(os.listdir(shared), [])
        self.assertEqual(os.stat(good).st_mode & 0o077, 0)  # new folders are private (0700)
        with mock.patch.object(app_module.os, "getuid", lambda: os.stat(good).st_uid + 1):
            with self.assertRaisesRegex(OSError, "another user"):
                app_module.private_dir(good)

    @unittest.skipIf(os.name == "nt", "folder modes and owners are POSIX")
    def test_a_symlink_in_place_of_the_folder_is_refused(self):
        target = os.path.join(self.tmp, "target")
        os.makedirs(target)
        link = os.path.join(self.tmp, "link")
        os.symlink(target, link)
        with self.assertRaisesRegex(OSError, "plain folder"):
            app_module.private_dir(link)

    def test_run_prints_the_path_and_cleans_up(self):
        app = self.make()
        lines = []
        seen = []

        def out(line):
            lines.append(line)
            if line.startswith("Token file"):
                seen.append(os.path.exists(line.split(": ", 1)[1]))
        with mock.patch.object(Server, "serve_forever", lambda self, *a, **k: None):
            self.assertEqual(launch.run(app, 0, explicit=True, open_browser=False, out=out), 0)
        self.assertEqual(seen, [True])
        self.assertTrue(any(l.startswith("Asset viewer at http://127.0.0.1:") for l in lines))
        self.assertEqual(os.listdir(self.state_dir), [])


class CommandLineTest(unittest.TestCase):
    def serve(self, *args):
        return subprocess.run([sys.executable, "-B", os.path.join(REPO, "serve.py"), *args], capture_output=True, text=True, timeout=60)

    def test_port_is_range_checked_by_argparse(self):
        for bad in ("0", "-1", "65536", "99999", "abc", "1.5", ""):
            r = self.serve("--port", bad, "--check")
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("not a port (1-65535)", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_a_good_port_is_taken(self):
        r = self.serve("--project", os.path.join(REPO, "examples", "sample"), "--port", "8000", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_smoke_runner_has_no_project_option(self):  # it always runs on a temp copy of the sample
        r = subprocess.run([sys.executable, "-B", os.path.join(REPO, "tools", "smoke.py"), "--project", REPO],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unrecognized arguments", r.stderr)
        self.assertNotIn("--project", subprocess.run([sys.executable, "-B", os.path.join(REPO, "tools", "smoke.py"), "--help"],
                                                     capture_output=True, text=True, timeout=60).stdout)

    def test_help_names_the_trust_flag(self):
        self.assertIn("--trust-project-plugins", self.serve("--help").stdout)

    def test_a_missing_project_is_a_clean_error(self):
        r = self.serve("--project", os.path.join(REPO, "no-such-folder"), "--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("error: project folder not found", r.stderr)


class CheckHideTest(helpers.ProjectCase):
    def check(self, cfg):
        from viewer.check import run
        out = []
        return run(self.make_app(cfg), out=out.append), out

    def test_a_hide_pattern_that_matches_nothing_fails(self):
        self.write("assets/secret/a.png")
        self.write("assets/b.png")
        report, out = self.check({"roots": ["assets"], "hide": ["assets/secret", "assets/typo/*", "*.psd"]})
        self.assertEqual(len(report.failures), 2)
        self.assertIn("hide pattern 'assets/typo/*' matches nothing", report.failures[0])
        self.assertIn("hide pattern '*.psd' matches nothing", report.failures[1])
        self.assertTrue(any(l.startswith("FAIL: hide pattern") for l in out))

    def test_patterns_that_match_pass_in_every_spelling(self):
        self.write("assets/secret/a.png")
        self.write("assets/b.psd")
        for pattern in ("assets/secret", "assets\\secret\\*", "./assets/secret/*", "assets/secret/", "*.psd", "ASSETS/SECRET"):
            with self.subTest(pattern=pattern):
                report, _ = self.check({"roots": ["assets"], "hide": [pattern]})
                self.assertEqual(report.failures, [])

    def test_a_pattern_for_a_hidden_dot_file_or_folder_still_counts_as_matching(self):
        self.write("assets/.cache/x.bin")
        report, _ = self.check({"roots": ["assets"], "hide": ["assets/.cache"]})
        self.assertEqual(report.failures, [])

    def test_the_walk_does_not_follow_a_junction_loop(self):
        self.write("assets/a/x.png")
        helpers.link_dir(os.path.join(self.project_dir, "assets", "a", "loop"), os.path.join(self.project_dir, "assets"))
        report, _ = self.check({"roots": ["assets"], "hide": ["assets/a/x.png", "assets/a/loop"]})
        self.assertEqual(report.failures, [])  # the link itself can be named...
        report, _ = self.check({"roots": ["assets"], "hide": ["assets/a/loop/a/x.png"]})
        self.assertEqual(len(report.failures), 1)  # ...but nothing is found through it
        self.assertEqual(report.warnings, [])

    def test_absolute_and_dotdot_patterns_warn_and_do_not_fail(self):
        self.write("assets/a.png")
        report, out = self.check({"roots": ["assets"], "hide": ["/etc", "../x"]})
        self.assertEqual(report.failures, [])
        self.assertEqual(len([l for l in out if l.startswith("warning: hide[")]), 2)


if __name__ == "__main__":
    unittest.main()
