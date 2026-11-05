# SPDX-License-Identifier: AGPL-3.0-or-later
"""The running viewer: project, config (reloaded when it changes), plugins, catalog, picks and the per-run token."""
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import threading
import time

from . import APP_NAME, VERSION
from .catalog import Catalog
from .config import CORE_KINDS, ConfigError, ConfigManager, project_hash
from .picks import PicksStore, ReviewReader
from .plugins import PluginError, Registry
from .project import PathError, Project

POLL_INTERVAL = 0.5  # seconds between looks at the config file
STATE_DIR_ENV = "ASSET_VIEWER_STATE_DIR"  # overrides state_dir() (tests)
RESTART_NOTE = "a plugin was enabled or disabled, or pluginDirs changed, in the config: restart the viewer to apply it (plugin code is loaded once, at start)"


def state_dir_candidates():
    """Where a per-user, per-run file may go: %LOCALAPPDATA% on Windows, else $XDG_RUNTIME_DIR, $XDG_STATE_HOME or
    ~/.local/state; the temp folder last."""
    if os.environ.get(STATE_DIR_ENV):
        return [os.environ[STATE_DIR_ENV]]
    if os.name == "nt":
        bases = [os.environ.get("LOCALAPPDATA")]
    else:
        bases = [os.environ.get("XDG_RUNTIME_DIR"), os.environ.get("XDG_STATE_HOME"), os.path.join(os.path.expanduser("~"), ".local", "state")]
    return [os.path.join(b, "asset-viewer") for b in bases if b and os.path.isabs(b)] + [os.path.join(tempfile.gettempdir(), "asset-viewer")]


def check_private(st, uid):
    """Why a folder (its os.lstat result) is no place for a secret, or None: it must be a real folder (not a link) that
    belongs to `uid` and that nobody else can write to."""
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return "is not a plain folder"
    if st.st_uid != uid:
        return "belongs to another user"
    if st.st_mode & 0o022:
        return "can be written to by other users"
    return None


def private_dir(folder):
    """Make `folder` (mode 0700 where it is new) and, on POSIX, check that it can safely hold the token: the temp folder
    is shared, so a folder someone else made there first must not be used. Raises OSError with the reason."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if os.name == "nt":
        return
    why = check_private(os.lstat(folder), os.getuid())
    if why:
        raise OSError("%s %s" % (folder, why))


class State:
    """Everything a request needs from one config, replaced as a whole when the config changes."""

    def __init__(self, config, registry, generation):
        self.config, self.registry, self.generation = config, registry, generation


class App:
    def __init__(self, project_dir, config_arg=None, tool_dir=None, debug=False, trust_project_plugins=False):
        self.tool_dir = os.path.realpath(tool_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.debug = debug
        self.trust_project_plugins = trust_project_plugins
        self.project = Project(project_dir)
        if not os.path.isdir(self.project.root):
            raise ConfigError("project folder not found: %s" % project_dir)
        self.configs = ConfigManager(self.project, config_arg)  # raises ConfigError on a bad first config
        self.token = secrets.token_urlsafe(24)
        self.port = None
        self.error = None  # the last failed config reload, shown by the page
        self.notices = []  # warnings of this run (a plugin change that needs a restart)
        self._plugin_error = None  # why the last reload of the plugins' config failed
        self._token_path = None
        self._gen = 0
        self._last_poll = time.monotonic()
        self._lock = threading.Lock()
        self._stores = {}
        self._readers = {}
        config = self.configs.config
        self.project.config_dir = config.config_dir  # plugins read it while registering (data defaults)
        self._plugin_sig = self._signature(config)
        self.state = self._build_state(config, Registry.load(self.project, self.tool_dir, config, trust_project_plugins))
        self.project_hash = hashlib.sha1(("%s|%s" % (project_hash(self.project.root), config.config_file or "")).encode()).hexdigest()[:12]
        self.catalog = Catalog(self)

    @staticmethod
    def _signature(config):
        """Which plugins are on and where they may come from: a change needs a restart (their config does not)."""
        return json.dumps([sorted(config.plugin_names()), config.plugin_dirs])

    def _build_state(self, config, registry):
        self._gen += 1
        self.project.hide = list(config.hide)
        self.project.areas = [r.path for r in config.roots] + [config.data_picks, config.data_review] + registry.read_areas
        return State(config, registry, self._gen)

    def refresh(self, force=False):
        """Look at the config file (at most every POLL_INTERVAL) and apply a change: roots, hide, materials, theme and
        the rest take effect at once, and so does each running plugin's own config (re-merged, re-validated, `register`
        run again on the code imported at start). Plugin code is never imported again: enabling or disabling a plugin or
        changing `pluginDirs` only raises a notice (restart to apply). A bad edit keeps the old state and sets `error`."""
        now = time.monotonic()
        if not force and now - self._last_poll < POLL_INTERVAL:
            return
        with self._lock:
            self._last_poll = now
            if self.configs.poll():
                config = self.configs.config
                self.notices = [RESTART_NOTE] if self._signature(config) != self._plugin_sig else []
                registry, self._plugin_error = self.state.registry, None
                try:
                    registry = Registry.reconfigure(self.state.registry, self.project, config)
                except PluginError as e:
                    self._plugin_error = str(e)
                self.state = self._build_state(config, registry)  # on an error the plugins keep their old config
            self.error = self.configs.error or self._plugin_error

    def attach(self, server):
        self.port = server.server_address[1]

    # data files -------------------------------------------------------------------------------------------------
    def picks(self):
        rel = self.state.config.data_picks
        if rel not in self._stores:
            self._stores[rel] = PicksStore(self.project, rel)
        return self._stores[rel]

    def review(self):
        rel = self.state.config.data_review
        if rel not in self._readers:
            self._readers[rel] = ReviewReader(self.project, rel)
        return self._readers[rel]

    # theme ------------------------------------------------------------------------------------------------------
    def theme_files(self):
        """(css file, its folder, built-in?) of the chosen theme; the paths are None when it cannot be found."""
        t = self.state.config.theme
        if t.endswith(".css") or "/" in t:
            try:
                css = self.project.confine(t)
            except PathError:
                return None, None, False
            return (css, os.path.dirname(css), False) if os.path.isfile(css) else (None, None, False)
        css = os.path.join(self.tool_dir, "themes", t, "theme.css")
        return (css, os.path.dirname(css), True) if os.path.isfile(css) else (None, None, True)

    def theme_version(self):
        css = self.theme_files()[0]
        try:
            return os.stat(css).st_mtime_ns // 1000000 if css else 0
        except OSError:
            return 0

    # page -------------------------------------------------------------------------------------------------------
    def categories(self):
        st = self.state
        known = list(CORE_KINDS) + st.registry.kinds
        if st.config.categories is None:
            return known, []
        warnings = ["categories: unknown kind %r" % c for c in st.config.categories if c not in known]
        return [c for c in st.config.categories if c in known], warnings

    def public_config(self):
        st = self.state
        categories, warnings = self.categories()
        warnings = st.config.warnings + st.registry.warnings + self.notices + warnings
        if self.theme_files()[0] is None:
            warnings.append("theme %s not found" % st.config.theme)
        plugins = []
        for p in st.registry.plugins:
            try:
                cfg = p.client_config_fn() if p.client_config_fn else p.config
            except Exception as e:
                cfg = {}
                warnings.append("plugin %s: client_config failed: %s" % (p.name, e))
            plugins.append({"name": p.name, "client": "/plugins/%s/%s" % (p.name, p.client) if p.client else None,
                            "styles": ["/plugins/%s/%s" % (p.name, s) for s in p.styles], "config": cfg})
        return {"app": APP_NAME, "version": VERSION, "title": st.config.title, "categories": categories,
                "themeVersion": self.theme_version(), "prefsPrefix": st.config.prefs_prefix,
                "model": {"front": st.config.front, "glassNames": list(st.config.glass_names)}, "plugins": plugins,
                "data": {"picks": st.config.data_picks, "review": st.config.data_review},
                "error": self.error, "warnings": warnings}

    # per-run token ----------------------------------------------------------------------------------------------
    def token_file(self):
        """Where the token goes: a per-user state folder, named by the project (never inside the project)."""
        return os.path.join(state_dir_candidates()[0], self.project_hash + ".token")

    def write_token_file(self):
        """Leave the token where a local tool of this user may read it. A stale file of an earlier run is replaced.
        Returns the path, or None when no state folder could be written."""
        refused = []
        for folder in state_dir_candidates():
            path = os.path.join(folder, self.project_hash + ".token")
            try:
                private_dir(folder)
                if os.path.lexists(path):
                    os.remove(path)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "w", encoding="ascii") as f:
                    f.write(self.token)
                self._token_path = path
                return path
            except OSError as e:
                refused.append(str(e))
        sys.stderr.write("warning: no token file written (local tools cannot read the token): %s\n" % "; ".join(refused))
        return None

    def remove_token_file(self):
        """Delete the token file if it is still this run's."""
        path, self._token_path = self._token_path, None
        if not path:
            return
        try:
            with open(path, encoding="ascii") as f:
                mine = f.read() == self.token
            if mine:
                os.remove(path)
        except (OSError, ValueError):
            pass
