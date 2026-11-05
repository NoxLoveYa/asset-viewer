# SPDX-License-Identifier: AGPL-3.0-or-later
"""Config discovery, validation and reload.

The config is one JSON file inside the project (`asset-viewer.json`, else `.asset-viewer/config.json`, or `--config`).
Every path in it is project-relative with `/`. A wrong value fails with its key path (`model.front: "+z" or "-z"
expected`); an unknown key is a warning. `ConfigManager` re-reads the file when it changes and keeps the last good
config when the new one is bad.
"""
import hashlib
import json
import os
import re
import threading
from collections import namedtuple

from .project import PathError, split_rel

CORE_KINDS = ("model", "texture", "vfx", "sound")
DEFAULT_MAPS = ("Color", "Normal", "Metalness", "Roughness", "Emissive")
CONFIG_NAMES = ("asset-viewer.json", ".asset-viewer/config.json")
DEFAULT_PORT = 8765
MAX_CONFIG_BYTES = 1 << 20
PLUGIN_NAME = re.compile(r"[a-z][a-z0-9-]{0,40}$")
TOP_KEYS = ("title", "port", "roots", "hide", "categories", "vfxFolders", "model", "materials", "theme", "data",
            "prefsPrefix", "pluginDirs", "plugins", "$schema")

Root = namedtuple("Root", "path label")


class ConfigError(Exception):
    """A config problem; the message starts with the key path."""


def project_hash(root):
    """A short stable id of a project folder (for `prefsPrefix` and the /api/hello probe)."""
    return hashlib.sha1(os.path.normcase(os.path.realpath(root)).encode("utf-8")).hexdigest()[:8]


def _expect(ok, path, what):
    if not ok:
        raise ConfigError("%s: %s expected" % (path, what))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _rel_path(v, path, allow_root=False):
    """A project-relative path, normalised (no trailing slash; "." is the root "")."""
    _expect(isinstance(v, str), path, "string")
    s = v
    while s.startswith("./"):
        s = s[2:]
    s = s.rstrip("/") if s != "/" else s
    if s == ".":
        s = ""
    if s == "" and not allow_root:
        raise ConfigError("%s: a path inside the project expected" % path)
    try:
        split_rel(s)
    except PathError as e:
        raise ConfigError("%s: %s (use project-relative paths with /): %r" % (path, e, v))
    return s


def _string_list(v, path):
    _expect(isinstance(v, list), path, "array of strings")
    for i, s in enumerate(v):
        _expect(isinstance(s, str), "%s[%d]" % (path, i), "string")
    return list(v)


def _unknown(obj, allowed, path, warnings):
    for k in obj:
        if k not in allowed:
            warnings.append("%s: unknown key" % (path + "." + k if path else k))


def _object(v, path):
    _expect(isinstance(v, dict), path, "object")
    return v


def normalize_hide(pattern):
    """A `hide` pattern as the globs it stands for, or (None, why) when it can match nothing. Backslashes become `/`, a
    leading `./` and a trailing `/` go, and the pattern always means that path *or anything below it*: `assets/secret`,
    `assets/secret/`, `./assets/secret/*` and `assets\\secret\\*` all hide the folder's files."""
    p = pattern.replace("\\", "/").strip()
    if p.startswith("/") or re.match(r"[A-Za-z]:", p):
        return None, "an absolute path matches nothing (use a project-relative one)"
    segs = [s for s in p.split("/") if s not in ("", ".")]
    if ".." in segs:
        return None, "a pattern with '..' matches nothing (use a project-relative one)"
    if not segs:
        return None, "an empty pattern"
    while len(segs) > 1 and segs[-1] in ("*", "**"):  # `dir/*` already means everything below dir
        segs.pop()
    glob = "/".join(segs)
    return [glob, glob + "/*"], None


_JS_GROUPS = (":", "=", "!")  # (?: (?= (?! mean the same in JavaScript


def js_regex_problem(s):
    """Why a pattern that Python accepts would mean something else (or nothing) to the page's JavaScript RegExp, or None.
    Only the syntax both share is allowed: groups, classes, alternation, anchors, \\d \\w \\s, greedy and lazy quantifiers,
    lookahead. Python-only: named groups `(?P`, inline flags `(?i)`, atomic groups, possessive quantifiers, conditionals,
    comments, `\\A` `\\Z`; and lookbehind, which older browsers lack."""
    i, n, in_class, quant = 0, len(s), False, False
    while i < n:
        c = s[i]
        if c == "\\":
            nxt = s[i + 1:i + 2]
            if nxt in ("A", "Z"):
                return "\\%s is Python-only (use ^ or $)" % nxt
            i, quant = i + 2, False
            continue
        if in_class:
            in_class = c != "]"
            i += 1
            continue
        if c == "[":
            in_class = True
            i += 1
            if s[i:i + 1] == "^":
                i += 1
            if s[i:i + 1] == "]":
                return "']' first in a class is a literal in Python and ends an empty class in JavaScript (write \\])"
            quant = False
            continue
        if c == "(" and s[i + 1:i + 2] == "?":
            tail = s[i + 2:i + 3]
            if tail not in _JS_GROUPS:
                return "the group (?%s is Python-only or not portable (lookbehind, named groups, inline flags...)" % tail
        if c in "*+?":
            if quant and c == "+":
                return "a possessive quantifier is Python-only"
            quant = not (quant and c == "?")  # a `?` right after a quantifier only makes it lazy
        elif c == "{":
            m = re.match(r"\{\d+(,\d*)?\}", s[i:])
            quant = bool(m)
            if m:
                i += m.end()
                continue
        else:
            quant = False
        i += 1
    return None


def _parse_roots(raw, w):
    _expect(isinstance(raw, list), "roots", "array")
    roots = []
    for i, r in enumerate(raw):
        p = "roots[%d]" % i
        if isinstance(r, str):
            path, label = _rel_path(r, p, allow_root=True), None
        else:
            _object(r, p)
            _unknown(r, ("path", "label"), p, w)
            _expect("path" in r, p + ".path", "string")
            path = _rel_path(r["path"], p + ".path", allow_root=True)
            label = r.get("label")
            _expect(label is None or isinstance(label, str), p + ".label", "string")
        if label is None:
            label = path.rsplit("/", 1)[-1]
        roots.append(Root(path, label.strip("/")))
    return roots


def _parse_hide(raw, cfg):
    cfg.hide_rules = []  # [(the pattern as written, its globs)]
    for i, pattern in enumerate(_string_list(raw, "hide")):
        globs, why = normalize_hide(pattern)
        if globs is None:
            cfg.warnings.append("hide[%d]: %s: %r" % (i, why, pattern))
        else:
            cfg.hide_rules.append((pattern, globs))
    cfg.hide = [g for _, globs in cfg.hide_rules for g in globs]


def _parse_model(raw, cfg):
    m = _object(raw, "model")
    _unknown(m, ("maps", "front", "glassNames"), "model", cfg.warnings)
    if "maps" in m:
        cfg.maps = _string_list(m["maps"], "model.maps")
        for i, s in enumerate(cfg.maps):
            if not re.fullmatch(r"\w+", s):
                raise ConfigError("model.maps[%d]: a word expected" % i)
    if "front" in m:
        if m["front"] not in ("+z", "-z"):
            raise ConfigError('model.front: "+z" or "-z" expected')
        cfg.front = m["front"]
    if "glassNames" in m:
        cfg.glass_names = _string_list(m["glassNames"], "model.glassNames")
        for i, s in enumerate(cfg.glass_names):
            try:
                re.compile(s)
            except re.error as e:
                raise ConfigError("model.glassNames[%d]: not a valid regex (%s)" % (i, e))
            why = js_regex_problem(s)
            if why:
                raise ConfigError("model.glassNames[%d]: %s (the page matches names with a JavaScript RegExp)" % (i, why))


def _parse_materials(raw, cfg):
    out = {}
    for pattern, names in _object(raw, "materials").items():
        pp = "materials[%s]" % json.dumps(pattern)
        _object(names, pp)
        out[pattern] = {}
        for name, rule in names.items():
            rp = "%s.%s" % (pp, name)
            _object(rule, rp)
            _unknown(rule, ("set", "alpha", "doubleSided"), rp, cfg.warnings)
            _expect("set" in rule, rp + ".set", "string")
            clean = {"set": _rel_path(rule["set"], rp + ".set")}
            if rule.get("alpha") is not None:
                if rule["alpha"] != "cutout":
                    raise ConfigError('%s.alpha: "cutout" expected' % rp)
                clean["alpha"] = "cutout"
            if "doubleSided" in rule:
                _expect(isinstance(rule["doubleSided"], bool), rp + ".doubleSided", "boolean")
                clean["doubleSided"] = rule["doubleSided"]
            out[pattern][name] = clean
    cfg.materials = out


def _parse_theme(t, cfg):
    _expect(isinstance(t, str) and t, "theme", "built-in theme name or path to a .css file")
    cfg.theme = _rel_path(t, "theme") if (t.endswith(".css") or "/" in t) else t
    if not (cfg.theme.endswith(".css") or re.fullmatch(r"[\w-]+", cfg.theme)):
        raise ConfigError("theme: built-in theme name or path to a .css file expected")


def _parse_data(raw, cfg):
    d = _object(raw, "data")
    _unknown(d, ("picks", "review"), "data", cfg.warnings)
    if "picks" in d:
        cfg.data_picks = _rel_path(d["picks"], "data.picks")
    if "review" in d:
        cfg.data_review = _rel_path(d["review"], "data.review")


def _parse_plugins(raw, cfg):
    for name, value in _object(raw, "plugins").items():
        if not PLUGIN_NAME.match(name):
            raise ConfigError("plugins.%s: a plugin name (lower case letters, digits, dashes) expected" % name)
        _expect(value is None or isinstance(value, dict), "plugins." + name, "object")
        cfg.plugins[name] = value or {}
    # a plugin's own keys are validated against its manifest when it loads


class Config:
    """A validated config. Attributes mirror the keys; paths are normalised project paths."""

    def __init__(self, **kw):
        self.title = ""
        self.port = DEFAULT_PORT
        self.roots = [Root("", "")]
        self.hide = []  # the globs of every `hide` pattern (see normalize_hide)
        self.hide_rules = []  # [(pattern as written, its globs)]
        self.categories = None  # None: core kinds, then plugin kinds
        self.vfx_folders = ["vfx"]
        self.maps = list(DEFAULT_MAPS)
        self.front = "+z"
        self.glass_names = []
        self.materials = {}
        self.theme = "aero"
        self.data_picks = "picks.json"
        self.data_review = "review.json"
        self.prefs_prefix = ""
        self.plugin_dirs = []
        self.plugins = {}
        self.config_file = None  # project path of the config file, or None for the defaults
        self.config_dir = ""
        self.warnings = []
        self.__dict__.update(kw)

    def plugin_names(self):
        """The enabled plugins, in config order."""
        return [n for n, v in self.plugins.items() if not (isinstance(v, dict) and v.get("enabled") is False)]


def find_config(project, config_arg=None):
    """The project path of the config file, or None when there is none. An explicit one must exist."""
    if config_arg:
        arg = config_arg.replace("\\", "/")
        if os.path.isabs(config_arg):
            try:
                arg = project.rel(config_arg)
            except PathError:
                raise ConfigError("--config: %s is outside the project" % config_arg)
        rel = _rel_path(arg, "--config")
        try:
            found = os.path.isfile(project.confine(rel))
        except PathError as e:
            raise ConfigError("--config: %s" % e)
        if not found:
            raise ConfigError("--config: file not found: %s" % rel)
        return rel
    for name in CONFIG_NAMES:
        if os.path.isfile(os.path.join(project.root, *name.split("/"))):
            return name
    return None


def parse(raw, project, config_file=None):
    """Validate a decoded config (a dict) and return a Config. Raises ConfigError with a key path."""
    cfg = Config(config_file=config_file)
    cfg.config_dir = os.path.dirname(config_file).replace("\\", "/") if config_file else ""
    cfg.title = os.path.basename(project.root) or "Asset viewer"
    cfg.prefs_prefix = "av.%s." % project_hash(project.root)
    cfg.data_picks = "/".join(p for p in (cfg.config_dir, "picks.json") if p)
    cfg.data_review = "/".join(p for p in (cfg.config_dir, "review.json") if p)
    _expect(isinstance(raw, dict), "config", "object")
    _unknown(raw, TOP_KEYS, "", cfg.warnings)

    if "title" in raw:
        _expect(isinstance(raw["title"], str) and raw["title"].strip(), "title", "non-empty string")
        cfg.title = raw["title"]
    if "port" in raw:
        _expect(_is_int(raw["port"]) and 1 <= raw["port"] <= 65535, "port", "integer 1-65535")
        cfg.port = raw["port"]
    if "roots" in raw:
        cfg.roots = _parse_roots(raw["roots"], cfg.warnings)
    if "hide" in raw:
        _parse_hide(raw["hide"], cfg)
    if "categories" in raw:
        cfg.categories = _string_list(raw["categories"], "categories")
        if len(set(cfg.categories)) != len(cfg.categories):
            raise ConfigError("categories: duplicate entry")
    if "vfxFolders" in raw:
        cfg.vfx_folders = _string_list(raw["vfxFolders"], "vfxFolders")
        for i, s in enumerate(cfg.vfx_folders):
            if not s or "/" in s:
                raise ConfigError("vfxFolders[%d]: a folder name expected" % i)
    if "model" in raw:
        _parse_model(raw["model"], cfg)
    if "materials" in raw:
        _parse_materials(raw["materials"], cfg)
    if "theme" in raw:
        _parse_theme(raw["theme"], cfg)
    if "data" in raw:
        _parse_data(raw["data"], cfg)
    if cfg.data_picks == cfg.data_review:
        raise ConfigError("data: picks and review need different files")
    if "prefsPrefix" in raw:
        _expect(isinstance(raw["prefsPrefix"], str) and len(raw["prefsPrefix"]) <= 64, "prefsPrefix", "string (64 characters at most)")
        cfg.prefs_prefix = raw["prefsPrefix"]
    if "pluginDirs" in raw:
        _expect(isinstance(raw["pluginDirs"], list), "pluginDirs", "array")
        cfg.plugin_dirs = [_rel_path(s, "pluginDirs[%d]" % i) for i, s in enumerate(raw["pluginDirs"])]
    if "plugins" in raw:
        _parse_plugins(raw["plugins"], cfg)
    return cfg


def load_file(project, config_file):
    """Read and validate the config at a project path (None: the defaults)."""
    if config_file is None:
        return parse({}, project, None)
    path = project.confine(config_file)
    try:
        if os.path.getsize(path) > MAX_CONFIG_BYTES:
            raise ConfigError("%s: file too large" % config_file)
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except ValueError as e:
        raise ConfigError("%s: invalid JSON: %s" % (config_file, e))
    except OSError as e:
        raise ConfigError("%s: cannot read: %s" % (config_file, e.strerror or e))
    try:
        return parse(raw, project, config_file)
    except ConfigError as e:
        raise ConfigError("%s: %s" % (config_file, e))


class ConfigManager:
    """Holds the current config, re-reading it when the file changes. A bad edit keeps the last good config and sets
    `error` (shown by the page as a banner); the first load raises."""

    def __init__(self, project, config_arg=None):
        self.project = project
        self.config_arg = config_arg
        self.error = None
        self.generation = 0
        self._lock = threading.Lock()
        self._sig = None
        self.config = None
        file = find_config(project, config_arg)
        self.config = load_file(project, file)
        self._sig = self._signature(file)
        self.generation = 1

    def _signature(self, file):
        if file is None:
            return None
        try:
            st = os.stat(self.project.confine(file))
            return (file, st.st_mtime_ns, st.st_size)
        except (OSError, PathError):
            return (file, None, None)

    def poll(self):
        """Re-read the config if it changed. Returns True when `config` is a new object."""
        with self._lock:
            try:
                file = find_config(self.project, self.config_arg)
            except ConfigError as e:
                file = self.config.config_file  # the explicit file vanished: keep what we have
                sig = (file, None, None)
                if sig != self._sig:
                    self._sig, self.error = sig, str(e)
                return False
            sig = self._signature(file)
            if sig == self._sig:
                return False
            self._sig = sig
            try:
                self.config = load_file(self.project, file)
            except ConfigError as e:
                self.error = str(e)
                return False
            self.error = None
            self.generation += 1
            return True
