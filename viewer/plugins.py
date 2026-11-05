# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folder plugins: a manifest (`plugin.json`), an optional `server.py` with `register(api)` and optional client files.

A project turns a plugin on by naming it under `plugins` in its config. A plugin's `server.py` is code that runs with the
user's rights: the tool's own `plugins/` always load, but a plugin from the config's `pluginDirs` (code that belongs to
the project) loads only when the viewer is started with `--trust-project-plugins`. Plugins are never fetched, and their
code is imported once, at start. A config edit that only changes a plugin's own config takes effect at once (each plugin's
`register` runs again on the same imported module with the new config); enabling or disabling a plugin, or changing
`pluginDirs`, needs a restart.
`PluginAPI` is what `register` gets; see the methods for the contract. Docs: docs/plugins.md.
"""
import copy
import importlib.util
import json
import os
import re
import sys

from . import API_VERSION
from .catalog import KINDS
from .config import CORE_KINDS, PLUGIN_NAME
from .project import PathError, split_rel

MAX_ROUTE_BODY = 32 << 20
ROUTE_METHODS = ("GET", "POST", "PUT", "DELETE")
TOKEN_MODES = (True, "optional-if-no-origin")
PARAM = re.compile(r"<(\w+)(?::([^>]+))?>")


class PluginError(Exception):
    """A plugin that cannot be loaded; the message names it."""


class HttpError(Exception):
    """Raised by a handler to answer with an error code and message."""

    def __init__(self, code, message, headers=None):
        Exception.__init__(self, message)
        self.code, self.message, self.headers = code, message, headers or {}


class Response:
    def __init__(self, code=200, body=b"", content_type="application/octet-stream", headers=None):
        self.code, self.body, self.content_type, self.headers = code, body, content_type, headers or {}


def json_response(obj, code=200):
    return Response(code, json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8")


def bytes_response(data, content_type="application/octet-stream", code=200):
    return Response(code, data, content_type)


class Request:
    """What a route handler gets: `method`, `path` (the part after the mount: `rig/hero` for a plugin route at
    /api/plugins/<name>/rig/hero), `full_path` (the whole decoded path), `params` (from the pattern), `query` (decoded,
    last value wins), `body` (bytes, already capped), `headers`."""

    def __init__(self, method, path, params, query, body, headers, full_path=None):
        self.method, self.path, self.params, self.query, self.body, self.headers = method, path, params, query, body, headers
        self.full_path = full_path if full_path is not None else path

    def json(self, expect=dict):
        try:
            data = json.loads(self.body.decode("utf-8"))
        except ValueError:
            raise HttpError(400, "body is not valid JSON")
        if not isinstance(data, expect):
            raise HttpError(400, "body must be a JSON %s" % ("object" if expect is dict else expect.__name__))
        return data


class Route:
    def __init__(self, plugin, method, pattern, handler, max_body, token, content_types):
        self.plugin, self.method, self.pattern, self.handler = plugin, method, pattern, handler
        self.max_body, self.token, self.content_types = max_body, token, content_types
        rx = ""
        pos = 0
        for m in PARAM.finditer(pattern):
            rx += re.escape(pattern[pos:m.start()]) + "(?P<%s>%s)" % (m.group(1), m.group(2) or "[^/]+")
            pos = m.end()
        self.regex = re.compile(rx + re.escape(pattern[pos:]) + r"\Z")

    def match(self, path):
        m = self.regex.match(path)
        return m.groupdict() if m else None


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else copy.deepcopy(v)
    return out


_TYPES = {
    "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict), "any": lambda v: True,
}


def validate_types(value, spec, path, warnings):
    """Check a plugin's config against its manifest `types`: a string of types joined by `|` ("number", "string|null",
    "array", "object", "any"), or an object spec {key: spec, "*": spec for any other key}. Raises PluginError."""
    if isinstance(spec, str):
        names = spec.split("|")
        if not any(_TYPES[n](value) for n in names if n in _TYPES):
            raise PluginError("%s: %s expected" % (path, " or ".join(names)))
    elif isinstance(spec, dict):
        if not isinstance(value, dict):
            raise PluginError("%s: object expected" % path)
        for k, v in value.items():
            sub = spec.get(k, spec.get("*"))
            if sub is None:
                warnings.append("%s.%s: unknown key" % (path, k))
            else:
                validate_types(v, sub, "%s.%s" % (path, k), warnings)


def _client_file(plugin_dir, name, what, plugin):
    try:
        split_rel(name)
    except PathError:
        raise PluginError("plugin %s: %s %r is not a plain relative path" % (plugin, what, name))
    if not os.path.isfile(os.path.join(plugin_dir, *name.split("/"))):
        raise PluginError("plugin %s: %s %s not found" % (plugin, what, name))
    return name


class LoadedPlugin:
    def __init__(self, name, directory, manifest, config):
        self.name, self.dir, self.manifest, self.config = name, directory, manifest, config
        self.client = manifest.get("client")
        self.styles = list(manifest.get("styles", []))
        self.client_files = {f for f in ([self.client] if self.client else []) + self.styles + list(manifest.get("files", []))}
        self.client_config_fn = None
        self.warnings = []


class PluginProject:
    """The project as a plugin sees it."""

    def __init__(self, project, writes):
        self._project, self._writes = project, writes
        self.root = project.root
        self.config_dir = project.config_dir
        self.rel = project.rel
        self.confine = project.confine
        self.read_json = project.read_json

    def write_atomic(self, rel, data):
        """Write bytes inside the areas this plugin declared with `api.writes`; anything else raises PathError."""
        self._project.write_atomic(rel, data, allowed=self._writes)


class PluginAPI:
    json_response = staticmethod(json_response)
    bytes_response = staticmethod(bytes_response)
    HttpError = HttpError
    Response = Response

    def __init__(self, registry, plugin, project):
        self._registry, self._plugin = registry, plugin
        self.name = plugin.name
        self.config = plugin.config  # manifest defaults <- project config
        self.dir = plugin.dir
        self._writes = []
        self.project = PluginProject(project, self._writes)

    def add_kind(self, kind, exts, claim):
        """Claim file extensions the core does not own as items of a new kind. `claim(abs, rel, stat, ctx)` returns a
        list of items (dicts; id is required, the core fills id/path/name/dir/mtime/bytes) or None for "not mine"."""
        r = self._registry
        if not (isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,30}", kind)) or kind in CORE_KINDS:
            raise PluginError("plugin %s: %r is not a valid new kind" % (self.name, kind))
        if r.kind_owner.setdefault(kind, self.name) != self.name:
            raise PluginError("plugin %s: kind %s is already added by %s" % (self.name, kind, r.kind_owner[kind]))
        for ext in exts:
            if not (isinstance(ext, str) and re.fullmatch(r"\.[a-z0-9]+", ext)):
                raise PluginError("plugin %s: %r is not an extension like .json" % (self.name, ext))
            if ext in KINDS:
                raise PluginError("plugin %s: %s belongs to the core kind %s" % (self.name, ext, KINDS[ext]))
            r.claimers.setdefault(ext, []).append((self.name, kind, claim))
        if kind not in r.kinds:
            r.kinds.append(kind)

    def enrich(self, kind, fn):
        """`fn(item, ctx)` for each item of `kind`; it may mutate the item, and a dict it returns is stored under
        `item["ext"][<plugin name>]`. `ctx.siblings` lists the file names beside the item, `ctx.cache` lasts one scan."""
        self._registry.enrichers.setdefault(kind, []).append((self.name, fn))

    def after_catalog(self, fn):
        """`fn(items)` once per scan, after all items exist (resolve references across files)."""
        self._registry.after.append((self.name, fn))

    def catalog_extra(self, fn):
        """`fn() -> json`, sent as `catalog["plugins"][<name>]` and part of the catalog version."""
        self._registry.extras.append((self.name, fn))

    def required_config(self, name):
        """A copy of the merged config of a plugin this one lists under `requires` (it is loaded before this one)."""
        if name not in self._plugin.manifest.get("requires", []):
            raise PluginError("plugin %s: %s is not listed under requires" % (self.name, name))
        return copy.deepcopy(self._registry.get(name).config)

    def serve(self, rel_dir):
        """Add a read-only area to /files (a project folder; the usual extension and dot-segment rules apply)."""
        self._registry.read_areas.append(self._area(rel_dir))

    def writes(self, rel_dir):
        """Declare a write area (a project folder or file); `api.project.write_atomic` refuses anything outside."""
        self._writes.append(self._area(rel_dir))

    def _area(self, rel):
        try:
            split_rel(rel)
        except PathError as e:
            raise PluginError("plugin %s: area %r: %s" % (self.name, rel, e))
        return rel

    def route(self, method, pattern, handler, max_body=65536, token=True, content_types=("application/json",)):
        """Mount `handler(req)` at /api/plugins/<name>/<pattern>; `<x>` or `<x:regex>` in the pattern is a path
        parameter. The core runs every guard first: Host, Origin, Sec-Fetch-Site, token, content type, Content-Length
        (required, at most `max_body`). `token="optional-if-no-origin"` lets a request without Origin and
        Sec-Fetch-Site (Roblox Studio's HttpService) skip the token; a browser always sends one of them. The handler
        returns `json_response(...)`, `bytes_response(...)`, a dict or list (JSON 200), and may raise HttpError."""
        if method not in ROUTE_METHODS:
            raise PluginError("plugin %s: route method %r" % (self.name, method))
        if token not in TOKEN_MODES:
            raise PluginError("plugin %s: route token must be True or 'optional-if-no-origin'" % self.name)
        if not (isinstance(max_body, int) and 0 <= max_body <= MAX_ROUTE_BODY):
            raise PluginError("plugin %s: max_body must be 0-%d" % (self.name, MAX_ROUTE_BODY))
        route = Route(self.name, method, pattern, handler, max_body, token, tuple(c.lower() for c in content_types))
        if any(r.plugin == self.name and r.method == method and r.pattern == pattern for r in self._registry.routes):
            raise PluginError("plugin %s: route %s %s twice" % (self.name, method, pattern))
        self._registry.routes.append(route)

    def check(self, fn):
        """`fn(report)` for --check: `report.ok(msg)`, `report.fail(msg)`, `report.warn(msg)`; `report.catalog` is a
        fresh scan, `report.config` the config, `report.project` the project."""
        self._registry.checks.append((self.name, fn))

    def client_config(self, fn):
        """`fn() -> json` for the page (default: the plugin's merged config)."""
        self._plugin.client_config_fn = fn


class Registry:
    """The plugins of one config, loaded."""

    def __init__(self):
        self.plugins = []
        self.kinds = []
        self.kind_owner = {}
        self.claimers = {}
        self.enrichers = {}
        self.after = []
        self.extras = []
        self.routes = []
        self.checks = []
        self.read_areas = []
        self.warnings = []
        self.base_warnings = []  # the load-time ones (skipped project plugins), kept across a reconfigure
        self.modules = {}  # name -> the imported server module
        self._by_name = {}

    def get(self, name):
        return self._by_name.get(name)

    def match_route(self, path):
        """For /api/plugins/<name>/<rest>: ([(route, params)...] matching the path, any method). Empty if none."""
        parts = path.split("/", 4)  # '', 'api', 'plugins', name, rest
        if len(parts) < 5 or parts[1:3] != ["api", "plugins"]:
            return []
        out = []
        for r in self.routes:
            if r.plugin == parts[3]:
                params = r.match(parts[4])
                if params is not None:
                    out.append((r, params))
        return out

    @staticmethod
    def _list(directory, into):
        """Add the plugin folders (those with a plugin.json) of a directory to `into`: name -> [folder]. Reads no code."""
        if not os.path.isdir(directory):
            return
        for entry in sorted(os.listdir(directory)):
            if os.path.isfile(os.path.join(directory, entry, "plugin.json")):
                into.setdefault(entry, []).append(os.path.join(directory, entry))

    @classmethod
    def load(cls, project, tool_dir, config, trust_project_plugins=False):
        """Load the enabled plugins. The tool's own `plugins/` always load. A plugin from the config's `pluginDirs` is
        Python code that belongs to the project, so it loads only when `trust_project_plugins` is set; otherwise it is
        skipped with a warning (and so is any plugin that requires it) and the rest works."""
        reg = cls()
        names = config.plugin_names()
        found, project_found = {}, {}
        cls._list(os.path.join(tool_dir, "plugins"), found)
        dirs = []
        for d in config.plugin_dirs:
            try:
                dirs.append((d, project.confine(d)))
            except PathError as e:
                raise PluginError("pluginDirs: %s: %s" % (d, e))
        for _, path in dirs:
            cls._list(path, found if trust_project_plugins else project_found)
        if dirs and not trust_project_plugins:
            there = sorted(n for n in project_found if n in names and n not in found)
            reg.warnings.append(
                "pluginDirs (%s) not loaded: a plugin there is Python code from this project and runs with your rights; "
                "start the viewer with --trust-project-plugins to load %s" %
                (", ".join(d for d, _ in dirs), ", ".join(there) if there else "its plugins"))
        order = []

        def visit(name, chain):
            """True when the plugin will load, False when it was skipped."""
            if name in order:
                return True
            if name in chain:
                raise PluginError("plugins require each other in a loop: %s" % " > ".join(chain + [name]))
            if name not in found:
                if name in project_found:
                    return False  # only in pluginDirs, and those are not trusted
                raise PluginError("plugin %s not found in plugins/ or pluginDirs" % name)
            if len(found[name]) > 1:
                raise PluginError("plugin %s found twice: %s" % (name, ", ".join(found[name])))
            manifest = reg._manifest(name, found[name][0])
            for need in manifest.get("requires", []):
                if need not in names:
                    raise PluginError("plugin %s requires %s, which is not enabled under plugins" % (name, need))
                if not visit(need, chain + [name]):
                    reg.warnings.append("plugin %s skipped: it requires %s, which was not loaded" % (name, need))
                    return False
            order.append(name)
            return True

        for n in names:
            if not visit(n, []) and n in project_found and n not in found:
                reg.warnings.append("plugin %s skipped: it is in pluginDirs (project code); start with --trust-project-plugins" % n)
        reg.base_warnings = list(reg.warnings)
        for n in order:
            reg._load_one(project, n, found[n][0], config.plugins[n])
        return reg

    @classmethod
    def reconfigure(cls, old, project, config):
        """A registry for a new config built from the plugin code `old` already loaded: the same plugins in the same order,
        each `register` called again with its config re-merged (manifest defaults <- the project's) and re-validated.
        Nothing is imported again, so editing a plugin's server.py on disk changes nothing until a restart. Plugins the
        config enabled or disabled since are not touched (the caller warns). Raises PluginError; `old` is left as it was."""
        reg = cls()
        reg.base_warnings = list(old.base_warnings)
        reg.warnings = list(old.base_warnings)
        for plugin in old.plugins:
            reg._load_one(project, plugin.name, plugin.dir, config.plugins.get(plugin.name) or {}, module=old.modules.get(plugin.name))
        return reg

    def _manifest(self, name, directory):
        path = os.path.join(directory, "plugin.json")
        try:
            with open(path, encoding="utf-8-sig") as f:
                m = json.load(f)
        except (OSError, ValueError) as e:
            raise PluginError("plugin %s: plugin.json: %s" % (name, e))
        if not isinstance(m, dict):
            raise PluginError("plugin %s: plugin.json must be an object" % name)
        if m.get("name") != name or not PLUGIN_NAME.match(name):
            raise PluginError("plugin %s: plugin.json name must be %s (the folder name)" % (name, name))
        if m.get("apiVersion") != API_VERSION:
            raise PluginError("plugin %s needs plugin API version %s; this viewer supports %s" % (name, m.get("apiVersion"), API_VERSION))
        for key in ("styles", "files", "requires"):
            v = m.get(key, [])
            if not (isinstance(v, list) and all(isinstance(s, str) for s in v)):
                raise PluginError("plugin %s: plugin.json %s must be an array of strings" % (name, key))
        for key in ("defaults", "types"):
            if not isinstance(m.get(key, {}), dict):
                raise PluginError("plugin %s: plugin.json %s must be an object" % (name, key))
        for key in ("server", "client"):
            if m.get(key) is not None and not isinstance(m[key], str):
                raise PluginError("plugin %s: plugin.json %s must be a file name" % (name, key))
        return m

    def _load_one(self, project, name, directory, user_config, module=None):
        """Merge, validate and register one plugin. `module` is its already imported server module (a reconfigure)."""
        m = self._manifest(name, directory)
        for key in ("server", "client"):
            if m.get(key):
                _client_file(directory, m[key], key, name)
        for key in ("styles", "files"):
            for f in m.get(key, []):
                _client_file(directory, f, key[:-1], name)
        user = {k: v for k, v in user_config.items() if k != "enabled"}
        cfg = deep_merge(m.get("defaults", {}), user)
        plugin = LoadedPlugin(name, directory, m, cfg)
        if m.get("types"):
            validate_types(cfg, m["types"], "plugins." + name, plugin.warnings)
        self.warnings.extend(plugin.warnings)
        self.plugins.append(plugin)
        self._by_name[name] = plugin
        if m.get("server"):
            module = module or self._import(name, directory, m["server"])
            self.modules[name] = module
            if not callable(getattr(module, "register", None)):
                raise PluginError("plugin %s: %s has no register(api)" % (name, m["server"]))
            try:
                module.register(PluginAPI(self, plugin, project))
            except PluginError:
                raise
            except Exception as e:
                raise PluginError("plugin %s: register failed: %s: %s" % (name, type(e).__name__, e))

    @staticmethod
    def _import(name, directory, server):
        mod_name = "viewer_plugin_" + name.replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, os.path.join(directory, server),
                                                      submodule_search_locations=[directory])
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            sys.modules.pop(mod_name, None)
            raise PluginError("plugin %s: %s failed to load: %s: %s" % (name, server, type(e).__name__, e))
        return module
