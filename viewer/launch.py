# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starting the server: find a port (or the viewer already running on it), serve until Ctrl+C, clean up."""
import json
import threading
import urllib.request
import webbrowser

from . import APP_NAME
from .http import Server


class StartError(Exception):
    """The server cannot start; the message says why and what to do."""


def probe(port, timeout=2):
    """/api/hello of whatever listens on this port if it is an asset viewer, else None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d/api/hello" % port, timeout=timeout) as r:
            data = json.loads(r.read(4096).decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == APP_NAME else None


def open_server(app, port, explicit=False, tries=20):
    """Bind the first free port from `port`. Returns ("started", server) or ("running", port) when this project's viewer
    already listens. A port taken by another project or program is skipped unless `explicit` (then StartError)."""
    ports = [port] if explicit or port == 0 else list(range(port, min(port + tries, 65536)))
    for p in ports:
        try:
            return "started", Server(app, p)
        except OSError:
            info = probe(p)
            if info and info.get("project") == app.project_hash:
                return "running", p
            if explicit:
                who = "another asset viewer (a different project)" if info else "another program"
                raise StartError("port %d is taken by %s: pass a free --port" % (p, who))
    raise StartError("no free port from %d to %d: pass --port" % (ports[0], ports[-1]))


def run(app, port, explicit=False, open_browser=True, out=print):
    """Serve until Ctrl+C. Returns an exit code."""
    status, what = open_server(app, port, explicit)
    if status == "running":
        url = "http://127.0.0.1:%d/" % what
        out("Asset viewer already running at " + url)
        if open_browser:
            webbrowser.open(url)
        return 0
    server = what
    url = "http://127.0.0.1:%d/" % app.port
    if app.port != port:
        out("port %d is busy: using %d" % (port, app.port))
    out("Asset viewer at %s (Ctrl+C stops it)" % url)
    token_path = app.write_token_file()
    if token_path:
        out("Token file (for local tools): %s" % token_path)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.remove_token_file()
    return 0
