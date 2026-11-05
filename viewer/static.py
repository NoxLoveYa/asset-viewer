# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the server serves as files: the app page (with its token and CSP), the tool's own folders, the chosen theme's
folder and the project's served areas.

Files come from three places only: the tool folder (`/app/`, `/vendor/`, `/themes/`, a plugin's manifest-listed client
files), the chosen theme's folder (`/theme/`) and the project (`/files/`). Everything that is not the tool's own goes
out with the sandbox CSP (`FILE_CSP`), so an opened SVG or HTML file cannot run script on our origin. No directory
listings. `StaticMixin` is the part of the request handler that routes these; it uses the handler's `_file` and `_reply`.
"""
import base64
import hashlib
import os
import re

from .filetypes import CONTENT_TYPES, THEME_EXTS, TOOL_EXTS
from .plugins import HttpError
from .project import PathError, inside, split_rel

FILE_CSP = "sandbox; default-src 'none'"
STATIC_PREFIXES = ("/app/", "/vendor/", "/themes/", "/theme/", "/files/", "/plugins/")
SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.S | re.I)
NO_HTML = TOOL_EXTS - {".html"}


def csp_for(html):
    """The CSP of the app page: scripts from this origin and the page's own inline scripts (the importmap) by hash."""
    hashes = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(m.encode("utf-8")).digest()).decode("ascii")
                      for m in SCRIPT.findall(html))
    return ("default-src 'self'; script-src 'self' %s; style-src 'self'; img-src 'self' data: blob:; "
            "media-src 'self' blob:; connect-src 'self' data: blob:; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % hashes).replace("  ", " ")


def is_static_path(path):
    return path in ("/", "/index.html") or path.startswith(STATIC_PREFIXES)


class StaticMixin:
    def _static(self, app, path):
        if path in ("/", "/index.html"):
            return self._app_html(app, "index.html")
        if path.startswith("/app/"):
            rel = path[len("/app/"):]
            if rel.lower().endswith(".html"):
                return self._app_html(app, rel)
            return self._tool_file(os.path.join(app.tool_dir, "app"), rel, NO_HTML)
        if path.startswith("/vendor/"):
            return self._tool_file(os.path.join(app.tool_dir, "vendor"), path[len("/vendor/"):], NO_HTML)
        if path.startswith("/themes/"):
            return self._tool_file(os.path.join(app.tool_dir, "themes"), path[len("/themes/"):], THEME_EXTS)
        if path.startswith("/theme/"):
            return self._theme(app, path[len("/theme/"):])
        if path.startswith("/files/"):
            try:
                real = app.project.resolve_readable(path[len("/files/"):])
            except PathError:
                raise HttpError(404, "not found")
            return self._file(real)
        if path.startswith("/plugins/"):
            name, _, rest = path[len("/plugins/"):].partition("/")
            plugin = app.state.registry.get(name)
            if plugin is None or rest not in plugin.client_files:
                raise HttpError(404, "not found")  # only files the manifest lists; never server.py
            return self._tool_file(plugin.dir, rest, NO_HTML)
        raise HttpError(404, "not found")

    def _tool_file(self, base, rest, exts, tool=True):
        """A file of a folder (`base`) by its path below it. `tool`: the tool's own file, else a project file."""
        try:
            segs = split_rel(rest)
        except PathError:
            raise HttpError(404, "not found")
        if not segs or any(s.startswith(".") for s in segs) or os.path.splitext(rest)[1].lower() not in exts:
            raise HttpError(404, "not found")
        real_base = os.path.realpath(base)
        path = os.path.realpath(os.path.join(real_base, *segs))
        if not inside(path, real_base) or not os.path.isfile(path):
            raise HttpError(404, "not found")
        return self._file(path, tool=tool)

    def _theme(self, app, rest):
        css, folder, builtin = app.theme_files()
        if css is None:
            raise HttpError(404, "theme not found")
        if rest == "theme.css":
            return self._file(css, tool=builtin)
        if not builtin:  # a project folder: the same hide and dot-segment rules as /files/
            try:
                rel = app.project.rel(os.path.join(folder, *split_rel(rest)))
            except PathError:
                raise HttpError(404, "not found")
            if app.project.is_hidden(rel):
                raise HttpError(404, "not found")
        return self._tool_file(folder, rest, THEME_EXTS, tool=builtin)

    def _app_html(self, app, name):
        """A page of app/ with the per-run token filled in and the CSP computed from its inline scripts."""
        try:
            segs = split_rel(name)
            base = os.path.realpath(os.path.join(app.tool_dir, "app"))
            path = os.path.realpath(os.path.join(base, *segs))
            if not segs or not inside(path, base) or not os.path.isfile(path):
                raise PathError("missing")
            with open(path, encoding="utf-8") as f:
                html = f.read()
        except (PathError, OSError):
            raise HttpError(404, "app page not found")
        html = html.replace("__VIEWER_TOKEN__", app.token)
        self._reply(200, html.encode("utf-8"), CONTENT_TYPES[".html"], csp=csp_for(html))
