# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picks (`picks.json`) and the review file (`review.json`).

picks.json is `{id: {picked, note, at, cut?}}`, written atomically under a lock. A corrupt file is never overwritten.
`rev` counts writes (and outside edits) so a client can tell a stale answer from a fresh one.
"""
import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timezone

from .project import PathError

MAX_BODY = 65536
MAX_NOTE = 2000
MAX_ID = 500
MAX_GROUP = 500
MAX_CUT_END = 3600


class BadPick(ValueError):
    """A POST body that is not a valid pick update (HTTP 400)."""


class CorruptFile(Exception):
    """picks.json exists but cannot be read as picks (HTTP 409); the file is left as it is."""


def parse_cut(value):
    """A pick's cut (the part of a sound to keep, from the review board): [start, end] in seconds, rounded to 0.01, or
    None to clear it. Raises ValueError on anything else."""
    if value is None:
        return None
    if not (isinstance(value, list) and len(value) == 2 and all(type(v) in (int, float) and math.isfinite(v) for v in value)):
        raise ValueError("cut: [start, end]")
    cut = [round(float(value[0]), 2), round(float(value[1]), 2)]
    if not 0 <= cut[0] < cut[1] <= MAX_CUT_END:
        raise ValueError("cut: 0 <= start < end")
    return cut


def _id(value):
    if not (isinstance(value, str) and 0 < len(value) < MAX_ID):
        raise BadPick("id: a string of 1-%d characters expected" % (MAX_ID - 1))
    return value


class PicksStore:
    def __init__(self, project, rel):
        self.project = project
        self.rel = rel
        self.lock = threading.Lock()
        self._rev = int(time.time() * 1000)  # unique across restarts
        self._sig = None

    def _read(self):
        """The picks dict; {} for no file; CorruptFile for one that is not a picks object. Call under the lock."""
        try:
            path = self.project.confine(self.rel)
            st = os.stat(path)
            with open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            self._note_signature(None)
            return {}
        except (OSError, PathError) as e:
            raise CorruptFile("%s cannot be read: %s" % (self.rel, getattr(e, "strerror", None) or e))
        self._note_signature((st.st_mtime_ns, len(raw)))
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except ValueError as e:
            raise CorruptFile("%s is not valid JSON (%s); fix or delete it, the viewer will not overwrite it" % (self.rel, e))
        if not isinstance(data, dict):
            raise CorruptFile("%s must be an object of picks; fix or delete it" % self.rel)
        for k, v in data.items():
            if not isinstance(v, dict):
                raise CorruptFile("%s: the entry %r is not an object; fix or delete it" % (self.rel, k))
        return data

    def _note_signature(self, sig):
        if self._sig is not None and sig != self._sig:
            self._rev += 1  # edited outside the viewer
        self._sig = sig

    def read(self):
        """(rev, picks) or CorruptFile."""
        with self.lock:
            picks = self._read()
            return self._rev, picks

    def _write(self, picks):
        data = json.dumps(dict(sorted(picks.items())), indent=2).encode("utf-8")
        self.project.write_atomic(self.rel, data, allowed=[self.rel])
        self._rev += 1
        st = os.stat(self.project.confine(self.rel))
        self._sig = (st.st_mtime_ns, len(data))

    @staticmethod
    def _entry(old, picked=None, note=None, cut=Ellipsis):
        """The merged entry, or None when nothing is left to keep. Absent fields keep their old value."""
        old = old or {}
        picked = bool(old.get("picked")) if picked is None else picked
        note = str(old.get("note", "")) if note is None else note
        cut = old.get("cut") if cut is Ellipsis else cut
        if not (picked or note or cut):
            return None
        out = {"picked": picked, "note": note, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if cut:
            out["cut"] = cut
        return out

    def update(self, body):
        """Apply a POST body: `{id, picked?, note?, cut?}` or `{group: [ids], picked: id|null}`. Returns (rev, picks)."""
        if not isinstance(body, dict):
            raise BadPick("a JSON object expected")
        if "group" in body:
            return self._update_group(body)
        extra = set(body) - {"id", "picked", "note", "cut"}
        if extra:
            raise BadPick("unknown field: %s" % sorted(extra)[0])
        pid = _id(body.get("id"))
        picked = body.get("picked")
        if picked is not None and not isinstance(picked, bool):
            raise BadPick("picked: true or false expected")
        note = body.get("note")
        if note is not None:
            if not isinstance(note, str) or len(note) > MAX_NOTE:
                raise BadPick("note: a string of at most %d characters expected" % MAX_NOTE)
        try:
            cut = parse_cut(body["cut"]) if "cut" in body else Ellipsis  # null clears it, absent keeps it
        except ValueError as e:
            raise BadPick(str(e))
        with self.lock:
            picks = self._read()
            entry = self._entry(picks.get(pid), picked, note, cut)
            if entry is None:
                picks.pop(pid, None)
            else:
                picks[pid] = entry
            self._write(picks)
            return self._rev, picks

    def _update_group(self, body):
        extra = set(body) - {"group", "picked"}
        if extra:
            raise BadPick("unknown field: %s" % sorted(extra)[0])
        group = body["group"]
        if not (isinstance(group, list) and 0 < len(group) <= MAX_GROUP):
            raise BadPick("group: a list of 1-%d ids expected" % MAX_GROUP)
        ids = [_id(i) for i in group]
        chosen = body.get("picked")
        if chosen is not None and chosen not in ids:
            raise BadPick("picked: null or one of the group's ids expected")
        with self.lock:
            picks = self._read()
            for i in ids:
                entry = self._entry(picks.get(i), picked=(i == chosen))
                if entry is None:
                    picks.pop(i, None)
                else:
                    picks[i] = entry
            self._write(picks)
            return self._rev, picks


def _check_review(data):
    """The first problem of a review file as a message with a key path, or None."""
    def need(ok, path, what):
        if not ok:
            return "%s: %s expected" % (path, what)

    err = need(isinstance(data, dict) and isinstance(data.get("reviews"), list), "reviews", "array")
    if err:
        return err
    for i, r in enumerate(data["reviews"]):
        p = "reviews[%d]" % i
        err = need(isinstance(r, dict), p, "object") or need(isinstance(r.get("title"), str), p + ".title", "string") \
            or need(isinstance(r.get("groups"), list), p + ".groups", "array")
        if err:
            return err
        for j, g in enumerate(r["groups"]):
            gp = "%s.groups[%d]" % (p, j)
            err = need(isinstance(g, dict), gp, "object") or need(isinstance(g.get("id"), str), gp + ".id", "string") \
                or need(isinstance(g.get("items"), list), gp + ".items", "array")
            if err:
                return err
            for k, it in enumerate(g["items"]):
                ip = "%s.items[%d]" % (gp, k)
                err = need(isinstance(it, dict), ip, "object") or need(isinstance(it.get("id"), str), ip + ".id", "string") \
                    or need(it.get("ref") is None or isinstance(it.get("ref"), bool), ip + ".ref", "boolean")
                if err:
                    return err
    return None


class ReviewReader:
    """review.json for the page: `{rev, reviews, error}`. A missing file is no reviews; a broken one is reported."""

    def __init__(self, project, rel):
        self.project = project
        self.rel = rel
        self._rev = 0
        self._hash = None

    def read(self):
        """(etag, payload dict)."""
        try:
            with open(self.project.confine(self.rel), "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            raw = None
        except (OSError, PathError) as e:
            return self._payload(b"?", [], "%s cannot be read: %s" % (self.rel, getattr(e, "strerror", None) or e))
        if raw is None:
            return self._payload(b"", [], None)
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except ValueError as e:
            return self._payload(raw, [], "%s is not valid JSON: %s" % (self.rel, e))
        err = _check_review(data)
        if err:
            return self._payload(raw, [], "%s: %s" % (self.rel, err))
        return self._payload(raw, data["reviews"], None)

    def _payload(self, raw, reviews, error):
        h = hashlib.sha1(raw).hexdigest()[:12]
        if h != self._hash:
            self._hash = h
            self._rev += 1
        return h, {"rev": self._rev, "reviews": reviews, "error": error}
