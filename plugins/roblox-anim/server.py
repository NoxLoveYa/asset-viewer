# SPDX-License-Identifier: AGPL-3.0-or-later
"""roblox-anim, server side: animation JSON ({Clip: {fps, looped, priority?, frames: [{Joint: [x, y, z, qx, qy, qz, qw]}]}},
what Blender bakes for Roblox rigs) as one `anim` item per clip, the clips layered beneath each (`under`), the aim
stance, and the rigs (`<rigsDir>/<name>.json`, as roblox-rig-export writes them) the page plays them on.

Config (plugins.roblox-anim): rigsDir (default <config folder>/rigs), defaultRig, bodyAim {blade, busyTwist, lockMax}, and
`files`: per animation file `{"<project path>": {rig, prop, under, up, busy, bodyAim, aimHead}}`. README.md has the shapes.
"""
import json
import math
import os
import re

RIG_NAME = re.compile(r"[A-Za-z_]\w{0,40}")
MAX_JSON = 8 << 20  # bytes: a bigger .json is not read as an animation file (the catalog warns)
_cache = {}  # path -> ((mtime_ns, size), clips | None): a file is parsed again only when it changed
_seen = set()  # the paths asked about in the scan under way; the cache keeps only these (see prune)


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def clips_of(path, st):
    """The clips of an animation JSON as {name: {fps, frames (a count), looped, priority}}; None for any other JSON (a
    config, a rig, a building report). A file that has the shape of clips but a bad one (no frames, a non-list `frames`,
    an `fps` that is not a positive number) raises ValueError, which the catalog reports as a warning."""
    _seen.add(path)
    if st.st_size > MAX_JSON:
        raise ValueError("%.1f MiB is over the %d MiB limit for a JSON file, skipped" % (st.st_size / 1048576, MAX_JSON >> 20))
    sig = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == sig:
        if isinstance(hit[1], ValueError):
            raise hit[1]
        return hit[1]
    try:
        clips = _parse(path)
    except ValueError as e:
        _cache[path] = (sig, e)
        raise
    _cache[path] = (sig, clips)
    return clips


def _parse(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None  # not JSON, or unreadable: not an animation file
    if not (isinstance(data, dict) and data and all(isinstance(v, dict) and "fps" in v and "frames" in v for v in data.values())):
        return None
    clips = {}
    for name, v in data.items():
        if not (_number(v["fps"]) and v["fps"] > 0):
            raise ValueError("clip %s: fps must be a positive number" % name)
        if not (isinstance(v["frames"], list) and v["frames"]):
            raise ValueError("clip %s: frames must be a non-empty list" % name)
        if not all(isinstance(f, dict) for f in v["frames"]):
            raise ValueError("clip %s: every frame must be an object of joints" % name)
        priority = v.get("priority")
        clips[name] = {"fps": v["fps"], "frames": len(v["frames"]), "looped": bool(v.get("looped")),
                       "priority": priority if isinstance(priority, str) else None}
    return clips


def aim_layers(opts, clip, defaults):
    """The layers of a clip (the file's options): the clips under it (`under`, by clip name; "*" for the rest) and the
    aim stance: `up` (a raised held item: the body bladed, the barrel locked onto the look, the head turned `aimHead`
    onto the stock) or `busy` (worked during the aim: bladed and twisted). A file's `bodyAim` overrides the plugin's,
    key by key."""
    under = opts.get("under") or {}
    out = {"under": list(under.get(clip, under.get("*", [])))} if under else {}
    stance = "up" if clip in opts.get("up", []) else "busy" if clip in opts.get("busy", []) else None
    if stance:
        out.update(stance=stance, bodyAim={**defaults, **(opts.get("bodyAim") or {})})
        if opts.get("aimHead"):
            out["aimHead"] = list(opts["aimHead"])
    return out


def prune():
    """Forget the cached files that this scan did not meet (deleted, moved, hidden): the cache cannot grow for ever."""
    for p in [p for p in _cache if p not in _seen]:
        del _cache[p]
    _seen.clear()


def resolve_under(items):
    """`under` names to {path, name}: the first file that has a clip of that name (path None when none does)."""
    prune()
    where = {}
    for i in items:
        if i["kind"] == "anim":
            where.setdefault(i["name"], i["path"])
    for i in items:
        if i["kind"] == "anim" and "under" in i:
            i["under"] = [{"path": where.get(n), "name": n} for n in i["under"]]


def register(api):
    cfg = api.config
    rigs_dir = cfg.get("rigsDir") or "/".join(p for p in (api.project.config_dir, "rigs") if p)
    default_rig = cfg.get("defaultRig")
    body_aim = cfg.get("bodyAim") or {}
    files = cfg.get("files") or {}

    def claim(path, rel, st, ctx):
        clips = clips_of(path, st)
        if clips is None:
            return None
        opts = files.get(rel) or {}
        stem = os.path.splitext(os.path.basename(rel))[0]
        return [{**info, "id": rel + "#" + clip, "kind": "anim", "name": clip, "dir": ctx.label(stem),
                 "rig": opts.get("rig") or default_rig, "prop": opts.get("prop"), **aim_layers(opts, clip, body_aim)}
                for clip, info in clips.items()]

    def rig_files():
        """[(name, path, mtime ms)] of the rigs in rigsDir; none when the folder is missing. A rig's mtime also moves
        when one of its images (<rigsDir>/<name>/*) is exported again."""
        try:
            base = api.project.confine(rigs_dir)
            entries = sorted(os.scandir(base), key=lambda e: e.name)
        except (OSError, ValueError):
            return []
        out = []
        for e in entries:
            name, ext = os.path.splitext(e.name)
            if ext.lower() != ".json" or not RIG_NAME.fullmatch(name) or not e.is_file():
                continue
            mtime = int(e.stat().st_mtime * 1000)
            try:
                for img in os.scandir(os.path.join(base, name)):
                    mtime = max(mtime, int(img.stat().st_mtime * 1000))
            except OSError:
                pass
            out.append((name, e.path, mtime))
        return out

    def extra():
        return {"rigsDir": rigs_dir, "rigs": [{"name": n, "mtime": m} for n, _, m in rig_files()]}

    def check(report):
        clips = [i for i in report.items if i["kind"] == "anim"]
        names = {n for n, _, _ in rig_files()}
        for name, path, _ in rig_files():
            try:
                with open(path, encoding="utf-8-sig") as f:
                    rig = json.load(f)
                ok = isinstance(rig, dict) and isinstance(rig.get("parts"), dict) and isinstance(rig.get("joints"), dict)
            except (OSError, ValueError):
                ok = False
            if not ok:
                report.fail("rig %s: %s/%s.json is not a rig ({parts, joints})" % (name, rigs_dir, name))
        for i in clips:
            for rig in (i.get("rig"), i.get("prop")):
                if rig and rig not in names:
                    report.fail("%s wants rig %s, not in %s" % (i["id"], rig, rigs_dir))
            for u in i.get("under", []):
                if not u["path"]:
                    report.fail("%s plays %s under it: no clip of that name" % (i["id"], u["name"]))
            if i.get("stance") and not i.get("bodyAim"):
                report.fail("%s has a stance but plugins.roblox-anim.bodyAim is empty" % i["id"])
        seen = {i["path"] for i in clips}
        for path in files:
            if path not in seen:
                report.fail("plugins.roblox-anim.files: %s is not an animation file under the roots" % path)
        report.ok("%d clips in %d animation files, %d rigs" % (len(clips), len(seen), len(names)))

    api.add_kind("anim", [".json"], claim)
    api.after_catalog(resolve_under)
    api.catalog_extra(extra)
    api.serve(rigs_dir)
    api.check(check)
    api.client_config(lambda: {"rigsDir": rigs_dir, "defaultRig": default_rig, "bodyAim": body_aim})
