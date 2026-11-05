# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalog: every file under the configured roots as an item, built per scan and cached for a second.

Core kinds are model, texture, vfx and sound (by extension; a texture under a `vfxFolders` folder is vfx). Plugins add
kinds for extensions the core does not own (`.json`), enrich items and add data (see plugins.py). A file that fails
never fails the scan: its problem becomes an entry of `warnings`.
"""
import fnmatch
import hashlib
import json
import os
import re
import threading
import time

from .filetypes import KINDS, MAP_EXTS as IMAGE_EXTS, MODEL_EXTS
from .project import PathError

REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a Windows junction or mount point (os.scandir calls it a plain dir)
SKIP_DIRS = frozenset(("node_modules", "bower_components", "site-packages"))
TTL = 1.0  # seconds a scan is reused
MAX_WARNINGS = 50


def join(*parts):
    return "/".join(p for p in parts if p)


class MapIndex:
    """The PBR maps beside the models of one folder, by name: `Stem.png` or `Stem_<Map>.png` (variant "Base"),
    `Stem_<Variant>_<Map>.png`. A file belongs to one model: `gate_left_Color.png` is the Base Color of `gate_left`, not
    the `left` variant of `gate` when both models exist; with several candidates the longest stem wins."""

    def __init__(self, folder, files, maps):
        self.found = {}
        stems = {os.path.splitext(n)[0] for n in files if os.path.splitext(n)[1].lower() in MODEL_EXTS}
        suffix = re.compile(r"(.+)_(%s)$" % "|".join(re.escape(m) for m in maps))
        for name in sorted(files):
            base, ext = os.path.splitext(name)
            if ext.lower() not in IMAGE_EXTS:
                continue
            path = join(folder, name)
            if base in stems:
                self._add(base, "Base", "Color", path)
                continue
            m = suffix.match(base)
            if not m:
                continue
            rest, which = m.groups()
            if rest in stems:
                self._add(rest, "Base", which, path)
                continue
            i = rest.rfind("_")
            while i > 0:
                stem, variant = rest[:i], rest[i + 1:]
                if stem in stems and re.fullmatch(r"\w+", variant):
                    self._add(stem, variant, which, path)
                    break
                i = rest.rfind("_", 0, i)

    def _add(self, stem, variant, which, path):
        self.found.setdefault(stem, {}).setdefault(variant, {})[which] = path

    def maps_for(self, stem):
        return self.found.get(stem, {})


def model_maps(name, folder, files, maps):
    """The maps of one model file (`name` in `folder`, among `files`); see MapIndex."""
    return MapIndex(folder, files, maps).maps_for(os.path.splitext(name)[0])


def set_maps(root, stem, cache, maps):
    """The maps of a shared map set by its path stem: Stem_<Map>.png (or .jpg / .jpeg / .webp), project paths."""
    if stem not in cache:
        found = {}
        for m in maps:
            for ext in IMAGE_EXTS:
                if os.path.isfile(os.path.join(root, *(stem + "_" + m + ext).split("/"))):
                    found[m] = stem + "_" + m + ext
                    break
        cache[stem] = found
    return cache[stem]


def model_materials(p, config, root, cache):
    """Shared-atlas looks (config `materials`, keyed by a glob of model paths): for a model matching, each mesh or
    material name of its file -> {"maps": {Color, Normal...}, "alpha"?, "doubleSided"?} ("*": any other)."""
    out = {}
    for pattern, names in config.materials.items():
        if fnmatch.fnmatchcase(p, pattern):
            for name, rule in names.items():
                out[name] = {**{k: v for k, v in rule.items() if k != "set"},
                             "maps": set_maps(root, rule["set"], cache, config.maps)}
    return out


class ClaimCtx:
    """What a plugin's claim and enrich callbacks get about the folder being scanned."""

    def __init__(self, project, folder, dir_label, siblings, cache):
        self.project = project
        self.folder = folder  # project path of the folder
        self.dir = dir_label  # the folder's display label, as an item's `dir`
        self.siblings = siblings  # file names in the folder
        self.sibling_set = frozenset(siblings)
        self.cache = cache  # one dict per scan, shared by all callbacks

    def label(self, *parts):
        """A display label under this folder's: `ctx.label("wave")` -> `<dir>/wave`."""
        return join(self.dir, *parts)


def is_link(entry):
    """A symlink, or on Windows any reparse point (a junction): a folder the walk must not enter."""
    try:
        if entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & REPARSE_POINT)
    except OSError:
        return True


def dedupe_warnings(warnings):
    seen, out = set(), []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            out.append(w)
    if len(out) > MAX_WARNINGS:
        out = out[:MAX_WARNINGS] + ["... and %d more" % (len(out) - MAX_WARNINGS)]
    return out


def scan(project, config, registry):
    """One uncached pass over the roots. Returns the catalog dict: version, items, plugins, warnings."""
    warnings = []
    items = []
    seen = set()
    cache = {}
    vfx = {s.lower() for s in config.vfx_folders}

    def add(item, ctx):
        """Append an item and enrich it; False (with a warning) when it has no id or the id is taken."""
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            warnings.append("%s: a plugin item needs an id" % (ctx.folder or "."))
            return False
        if item["id"] in seen:
            warnings.append("duplicate id %s" % item["id"])
            return False
        seen.add(item["id"])
        items.append(item)
        enrich(item, ctx)
        return True

    def enrich(item, ctx):
        for plugin, fn in registry.enrichers.get(item.get("kind"), ()):
            try:
                extra = fn(item, ctx)
            except Exception as e:  # a plugin must not fail the scan
                warnings.append("%s: plugin %s: %s" % (item.get("path"), plugin, e))
                continue
            if isinstance(extra, dict):
                item.setdefault("ext", {})[plugin] = extra

    visited = set()  # real paths of the folders walked, so no folder is listed twice

    def visit(root, folder_abs, folder_rel, below):
        real = os.path.normcase(os.path.realpath(folder_abs))
        if real in visited:
            return
        visited.add(real)
        try:
            entries = sorted(os.scandir(folder_abs), key=lambda e: e.name)
        except OSError as e:
            warnings.append("%s: cannot read folder (%s)" % (folder_rel or ".", e.strerror or e))
            return
        files, dirs = [], []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not e.name.startswith((".", "__")) and e.name not in SKIP_DIRS and not is_link(e):
                        dirs.append(e)
                elif e.is_file() and not e.name.startswith("."):  # a dot file is never served, so never listed
                    files.append(e)
            except OSError:
                continue
        names = [e.name for e in files]
        dir_label = join(root.label, *below)
        ctx = ClaimCtx(project, folder_rel, dir_label, names, cache)
        index = None
        for e in files:
            name = e.name
            p = join(folder_rel, name)
            if project.is_hidden(p):
                continue
            try:
                if e.is_symlink():
                    project.confine(p)  # a link leaving the project is not listed
                st = e.stat()
            except (OSError, PathError):
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = KINDS.get(ext)
            base = {"id": p, "path": p, "kind": kind, "name": os.path.splitext(name)[0], "dir": dir_label,
                    "mtime": int(st.st_mtime * 1000), "bytes": st.st_size}
            if kind is None:
                for plugin, plugin_kind, claim in registry.claimers.get(ext, ()):
                    try:
                        claimed = claim(e.path, p, st, ctx)
                    except Exception as err:
                        warnings.append("%s: plugin %s: %s" % (p, plugin, err))
                        continue
                    if claimed is None:
                        continue
                    for it in claimed:
                        add({**base, "kind": plugin_kind, **it} if isinstance(it, dict) else it, ctx)
                    break
                continue
            if kind == "texture" and vfx.intersection(s.lower() for s in folder_rel.split("/")):
                kind = "vfx"
            item = {**base, "kind": kind}
            if kind == "model":
                if index is None:
                    index = MapIndex(folder_rel, names, config.maps)
                item["maps"] = index.maps_for(base["name"])
                materials = model_materials(p, config, project.root, cache.setdefault("core.sets", {}))
                if materials:
                    item["materials"] = materials
            add(item, ctx)
        for d in dirs:
            visit(root, d.path, join(folder_rel, d.name), below + [d.name])

    for root in config.roots:
        try:
            base = project.confine(root.path)
        except PathError as e:
            warnings.append("root %s: %s" % (root.path or ".", e))
            continue
        if not os.path.isdir(base):
            warnings.append("root %s: folder not found" % (root.path or "."))
            continue
        visit(root, base, root.path, [])

    extras = {}
    for plugin, fn in registry.after:
        try:
            fn(items)
        except Exception as e:
            warnings.append("plugin %s: %s" % (plugin, e))
    for plugin, fn in registry.extras:
        try:
            extras[plugin] = fn()
        except Exception as e:
            warnings.append("plugin %s: %s" % (plugin, e))
    warnings = dedupe_warnings(warnings)
    version = hashlib.sha1(json.dumps([items, extras, warnings], sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return {"version": version, "items": items, "plugins": extras, "warnings": warnings}


class Catalog:
    """The scan, shared by all requests: one scan at a time, reused for `TTL` seconds and until the config changes."""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._at = 0.0
        self._key = None
        self._data = None
        self._body = b""

    def get(self):
        """(version, body bytes, data dict) of a fresh-enough scan."""
        state = self.app.state
        key = (state.generation, id(state.registry))
        if self._key == key and time.monotonic() - self._at < TTL:
            return self._data["version"], self._body, self._data
        with self._lock:
            if self._key == key and time.monotonic() - self._at < TTL:
                return self._data["version"], self._body, self._data
            data = scan(self.app.project, state.config, state.registry)
            self._body = json.dumps(data, separators=(",", ":")).encode("utf-8")
            self._data, self._key, self._at = data, key, time.monotonic()
            return data["version"], self._body, data
