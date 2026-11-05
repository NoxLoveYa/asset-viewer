# SPDX-License-Identifier: AGPL-3.0-or-later
"""roblox-buildings, server side: tells the page which models have a JSON beside them that the client add-ons use.

Beside `<stem>.fbx` (or any model) a `<stem>.json` is one of:
  * a building report (`objects` and `files`): the page draws the model as the report says (tiled finishes, tint, the
    furnished props, storey cuts);
  * a prop sidecar (`objects`, no `files`): the model's parts, some of which slide (`slide`) or swing (`hinge`, `axis`,
    `swing`); the page turns the model front to -Z and offers Open for the parts that move.
The item gets `ext["roblox-buildings"] = {"report": {path, mtime}}` or `{"sidecar": {path, mtime, movers}}`; any other
JSON, and a model without one, gets nothing, so the page never asks for a file that is not there. README.md has the shapes.
"""
import json
import os

MAX_JSON = 8 << 20  # bytes: a bigger sidecar is not read (the catalog warns)
_cache = {}  # path -> ((mtime_ns, size), kind, movers): a JSON is parsed again only when it changed
_seen = set()  # the sidecars asked about in the scan under way; the cache keeps only these (see prune)


def classify(path, st):
    """('report' | 'sidecar' | None, movers) of a JSON file. ValueError for one over MAX_JSON."""
    _seen.add(path)
    if st.st_size > MAX_JSON:
        raise ValueError("%.1f MiB is over the %d MiB limit for a JSON file, skipped" % (st.st_size / 1048576, MAX_JSON >> 20))
    sig = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == sig:
        return hit[1], hit[2]
    kind, movers = None, 0
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    objects = data.get("objects") if isinstance(data, dict) else None
    if isinstance(objects, dict) and objects:
        if isinstance(data.get("files"), dict):
            kind = "report"
        else:
            kind = "sidecar"
            movers = sum(1 for o in objects.values() if isinstance(o, dict) and (o.get("slide") or (o.get("hinge") and o.get("swing"))))
    _cache[path] = (sig, kind, movers)
    return kind, movers


def prune():
    """Forget the cached files this scan did not meet (deleted, moved): the cache cannot grow for ever."""
    for p in [p for p in _cache if p not in _seen]:
        del _cache[p]
    _seen.clear()


def register(api):
    def enrich(item, ctx):
        stem = os.path.splitext(item["path"])[0]
        name = os.path.basename(stem) + ".json"
        if name not in ctx.sibling_set:
            return None
        rel = stem + ".json"
        try:
            path = api.project.confine(rel)
            st = os.stat(path)
        except (OSError, ValueError):
            return None
        kind, movers = classify(path, st)
        if kind == "report" and item["path"].lower().endswith(".fbx"):
            return {"report": {"path": rel, "mtime": int(st.st_mtime * 1000)}}
        if kind == "sidecar":
            return {"sidecar": {"path": rel, "mtime": int(st.st_mtime * 1000), "movers": movers}}
        return None

    api.enrich("model", enrich)
    api.after_catalog(lambda items: prune())
