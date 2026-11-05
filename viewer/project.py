# SPDX-License-Identifier: AGPL-3.0-or-later
"""The project folder: every path the viewer reads or writes goes through here.

A path is a project-relative string with `/` separators. `confine` checks its syntax and that its real path stays inside
the project; `resolve_readable` adds the rules for serving it (an allowed area, an allowed extension, no dot-segment, not
hidden); `write_atomic` writes only inside areas the caller names.
"""
import fnmatch
import json
import os
import secrets
import time

from .filetypes import SERVED_EXTS

_DEVICE_NAMES = frozenset(["con", "prn", "aux", "nul"] + ["com%d" % i for i in range(1, 10)] + ["lpt%d" % i for i in range(1, 10)])


class PathError(ValueError):
    """A path that is malformed, outside the project, or not allowed here."""


class WriteError(OSError):
    """A write that failed; the message names the file."""


def split_rel(rel):
    """The segments of a project-relative path, or PathError. "" is the project root (no segments)."""
    if not isinstance(rel, str):
        raise PathError("path must be a string")
    if rel == "":
        return []
    if "\x00" in rel or "\\" in rel or ":" in rel:
        raise PathError("bad character in path")
    if rel.startswith("/"):
        raise PathError("absolute path")
    segs = rel.split("/")
    for s in segs:
        if s in ("", ".", ".."):
            raise PathError("bad path segment")
        if any(ord(c) < 32 for c in s):
            raise PathError("control character in path")
        if s[-1] in ". ":
            raise PathError("bad path segment")
        if s.split(".", 1)[0].strip().lower() in _DEVICE_NAMES:
            raise PathError("reserved name")
    return segs


def _fold(rel):
    """Compare project paths the way the file system does (case-insensitive on Windows); keeps the `/`."""
    return rel.lower() if os.name == "nt" else rel


def in_area(rel, area):
    """Whether `rel` is the area (a file) or inside it (a folder); the area "" is the whole project."""
    if area == "":
        return True
    a, r = _fold(area), _fold(rel)
    return r == a or r.startswith(a + "/")


def inside(path, base):
    """Whether an absolute path is `base` or below it (case-insensitive where the file system is)."""
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(base)]) == os.path.normcase(base)
    except ValueError:  # another drive
        return False


class Project:
    def __init__(self, root, areas=None, hide=(), config_dir=""):
        self.root = os.path.realpath(root)
        self.areas = [""] if areas is None else list(areas)  # what /files may serve (project paths)
        self.hide = list(hide)  # globs of project paths: not listed, not served
        self.config_dir = config_dir  # the folder holding the config file; data defaults live there

    def rel(self, path):
        """The project path of an absolute path, or PathError when it is outside (or on another drive)."""
        try:
            r = os.path.relpath(path, self.root)
        except ValueError:
            raise PathError("path is on another drive")
        r = r.replace(os.sep, "/")
        if r == ".":
            return ""
        if r == ".." or r.startswith("../"):
            raise PathError("path is outside the project")
        return r

    def confine(self, rel):
        """The real absolute path of `rel`, which must stay inside the project (symlinks resolved)."""
        segs = split_rel(rel)
        real = os.path.realpath(os.path.join(self.root, *segs)) if segs else self.root
        if not inside(real, self.root):
            raise PathError("path leaves the project")
        return real

    def is_hidden(self, rel):
        low = rel.lower()
        return any(fnmatch.fnmatchcase(low, g.lower()) for g in self.hide)

    def _policy(self, rel):
        """The serving rules for one project path (not whether the file exists): a served extension, inside a served
        area, not hidden, and no dot-segment below the area. An area the config itself declares may sit in a dot
        folder (`.asset-viewer/rigs`): its own segments are allowed, the ones below it are not. PathError if refused."""
        if os.path.splitext(rel)[1].lower() not in SERVED_EXTS:
            raise PathError("not served")
        if self.is_hidden(rel):
            raise PathError("hidden")
        best = None  # the longest area that holds rel
        for a in self.areas:
            if in_area(rel, a) and (best is None or len(a) > len(best)):
                best = a
        if best is None:
            raise PathError("outside the served areas")
        below = rel[len(best):].strip("/") if best else rel
        if any(s.startswith(".") for s in below.split("/") if s):
            raise PathError("dot path")

    def resolve_readable(self, rel):
        """The absolute path of a file `/files/` may serve, or PathError (every refusal looks the same to a client).
        The rules are judged on the path as asked and on the real path it resolves to, so a link cannot lead out of
        them."""
        real = self.confine(rel)
        if not rel or not os.path.isfile(real):
            raise PathError("not a file")
        self._policy(rel)
        self._policy(self.rel(real))
        return real

    def policy_problem(self, rel_dir):
        """Why a file in the folder `rel_dir` could not be served, or None (for --check on a declared area)."""
        try:
            self.confine(rel_dir)
            self._policy((rel_dir + "/" if rel_dir else "") + "probe.json")
        except PathError as e:
            return str(e)
        return None

    def readable(self, rel):
        try:
            self.resolve_readable(rel)
            return True
        except PathError:
            return False

    def read_json(self, rel):
        with open(self.confine(rel), encoding="utf-8-sig") as f:
            return json.load(f)

    def write_atomic(self, rel, data, allowed=()):
        """Write bytes to `rel` through a temp file and os.replace. Refuses a path outside `allowed` (project paths of
        files or folders) or outside the project."""
        segs = split_rel(rel)
        if not segs:
            raise PathError("cannot write the project root")
        if not any(in_area(rel, a) for a in allowed):
            raise PathError("write outside the allowed areas: %s" % rel)
        target = os.path.join(self.root, *segs)
        parent = os.path.realpath(os.path.dirname(target))
        if not inside(parent, self.root) or (os.path.lexists(target) and not inside(os.path.realpath(target), self.root)):
            raise PathError("path leaves the project")
        try:
            os.makedirs(parent, exist_ok=True)
            tmp = os.path.join(parent, ".%s.%s.tmp" % (os.path.basename(target), secrets.token_hex(4)))
            try:
                with open(tmp, "xb") as f:
                    f.write(data)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                try:
                    os.replace(tmp, target)
                except PermissionError:  # Windows: a reader (antivirus, indexer) holds the file; once more
                    time.sleep(0.1)
                    os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except OSError as e:
            raise WriteError("cannot write %s: %s" % (rel, e.strerror or e))
