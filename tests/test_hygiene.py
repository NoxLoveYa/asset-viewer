# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repo is public: no personal data, an AGPL license and an SPDX header on every source file.

Personal strings are not written down here: the test asks the machine it runs on (the user name, the git e-mail, the home
folder) and looks for those, plus generic shapes (a drive path into a user folder, an e-mail address).
"""
import getpass
import os
import re
import subprocess
import unittest

from helpers import REPO

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".smoke"}
UNPUBLISHED = {"PLAN.md", "AUDIT.md"}  # gitignored planning notes
TEXT_EXTS = {".py", ".js", ".mjs", ".css", ".md", ".json", ".yml", ".yaml", ".luau", ".html", ".txt", ".cmd", ".sh", ".gitignore"}
SOURCE_EXTS = {".py", ".js", ".mjs", ".css", ".luau"}
MAX_BYTES = 256 * 1024
USER_PATH = re.compile(r"\b[A-Za-z]:[\\/](?i:Users|Documents and Settings)[\\/]|(?<![\w.])/Users/[\w.-]+/")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
EMAIL_OK = re.compile(r"(?:^noreply@|@users\.noreply\.github\.com$|@example\.(?:com|org|net)$)", re.I)
COMMON_USERS = {"runner", "runneradmin", "root", "user", "admin", "ubuntu", "vsts", "docker", "builder", "jenkins", "build",
                "default", "guest", "test", "tester", "nobody", "system"}


def files(exclude_vendor=True):
    for folder, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_folder = os.path.relpath(folder, REPO).replace(os.sep, "/")
        if exclude_vendor and (rel_folder == "vendor" or rel_folder.startswith("vendor/")):
            continue
        for name in names:
            if name in UNPUBLISHED and rel_folder == ".":
                continue
            if name.endswith(".pyc"):
                continue
            yield os.path.join(folder, name), (name if rel_folder == "." else rel_folder + "/" + name)


def git_emails():
    out = set()
    for scope in ([], ["--global"]):
        try:
            r = subprocess.run(["git", "config"] + scope + ["user.email"], capture_output=True, text=True, timeout=10, cwd=REPO)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            out.add(r.stdout.strip())
    return out


def personal_strings():
    """What identifies the person running this test: the user name, the git e-mail address(es), the home folder."""
    found = {}
    try:
        user = getpass.getuser()
    except Exception:  # no user name to ask for
        user = ""
    if len(user) >= 4 and user.lower() not in COMMON_USERS:
        found[user] = "the user name"
    for mail in git_emails():
        found[mail] = "the git e-mail address"
    home = os.path.expanduser("~")
    if len(home) >= 8 and home not in ("~",):
        found[home] = "the home folder"
        found[home.replace("\\", "/")] = "the home folder"
    return found


class HygieneTest(unittest.TestCase):
    def test_license_is_agpl_3(self):
        with open(os.path.join(REPO, "LICENSE"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 19 November 2007", text)
        self.assertGreater(len(text), 30000)  # the full text, not a notice

    def test_no_large_files_outside_vendor_and_fonts(self):
        big = [rel for path, rel in files() if os.path.getsize(path) > MAX_BYTES and "/fonts/" not in rel]
        self.assertEqual(big, [])

    def test_no_personal_strings_user_paths_or_addresses(self):
        mine = personal_strings()
        hits = []
        for path, rel in files():
            if os.path.splitext(rel)[1].lower() not in TEXT_EXTS and not rel.endswith(".gitignore"):
                continue
            if rel.endswith("LICENSE") or rel.startswith("examples/sample/assets/") or rel == "tests/test_hygiene.py":
                continue  # (this file holds the examples that prove the patterns work)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            low = text.lower()
            for s, what in mine.items():
                if s.lower() in low:
                    hits.append("%s contains %s" % (rel, what))
            for m in USER_PATH.finditer(text):
                hits.append("%s contains a path into a user folder: %r" % (rel, m.group(0)))
            for m in EMAIL.findall(text):
                if not EMAIL_OK.search(m):
                    hits.append("%s contains the e-mail address %r" % (rel, m))
        self.assertEqual(hits, [])

    def test_the_checks_themselves_work(self):
        self.assertTrue(USER_PATH.search("see C:\\Users\\someone\\x"))
        self.assertTrue(USER_PATH.search("d:/users/someone/x"))  # Windows paths ignore case
        self.assertTrue(USER_PATH.search("opened /Users/someone/Documents"))
        self.assertIsNone(USER_PATH.search("pip install --user; /usr/local; C:/Windows/win.ini"))
        for ok in ("noreply@github.com", "123+name@users.noreply.github.com", "a@example.com"):
            self.assertTrue(EMAIL_OK.search(ok), ok)
        self.assertFalse(EMAIL_OK.search("someone@gmail.com"))
        self.assertTrue(EMAIL.findall("mail a.b+c@host.example.org now"))
        self.assertIsInstance(personal_strings(), dict)

    def test_every_source_file_has_an_spdx_header(self):
        missing = []
        for path, rel in files():
            if os.path.splitext(rel)[1].lower() not in SOURCE_EXTS or rel.startswith("examples/"):
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                head = "".join(f.readline() for _ in range(3))
            if "SPDX-License-Identifier: AGPL-3.0-or-later" not in head:
                missing.append(rel)
        self.assertEqual(missing, [])

    def test_python_files_need_nothing_newer_than_3_9(self):
        bad = []
        for path, rel in files():
            if not rel.endswith(".py"):
                continue
            with open(path, encoding="utf-8") as f:
                src = f.read()
            try:
                compile(src, rel, "exec", dont_inherit=True, flags=0)
            except SyntaxError as e:  # pragma: no cover
                bad.append("%s: %s" % (rel, e))
            if re.search(r"^\s*match\s+\S.*:\s*$", src, re.M) and "case " in src:
                bad.append(rel + ": match statement")
            if re.search(r"\bstrict\s*=\s*(True|False)\)", src) and "zip(" in src:
                bad.append(rel + ": zip(strict=)")
        self.assertEqual(bad, [])

    def test_gitignore_keeps_the_planning_notes_and_editor_litter_out(self):
        with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
            lines = f.read().split()
        for name in ("PLAN.md", "AUDIT.md", "__pycache__/", "examples/sample/picks.json", ".DS_Store", "Thumbs.db", ".idea/", ".vscode/",
                     ".pytest_cache/"):
            self.assertIn(name, lines)

    def test_gitattributes_leave_vendored_and_binary_files_alone(self):
        with open(os.path.join(REPO, ".gitattributes"), encoding="utf-8") as f:
            lines = [l.split() for l in f.read().splitlines() if l.strip()]
        rules = {l[0]: l[1:] for l in lines}
        self.assertEqual(rules.get("vendor/**"), ["-text"])
        self.assertEqual(rules.get("*.woff2"), ["binary"])
        self.assertEqual(rules.get("examples/sample/**"), ["-text"])


if __name__ == "__main__":
    unittest.main()
