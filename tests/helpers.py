# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test helpers: a temp project, a temp tool folder, a real server on port 0 and a raw HTTP client."""
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from viewer.app import App  # noqa: E402
from viewer.http import Server  # noqa: E402

INDEX_HTML = ('<!doctype html><html><head><meta name="viewer-token" content="__VIEWER_TOKEN__">'
              '<script type="importmap">{"imports":{"viewer/":"/app/"}}</script></head><body></body></html>')


def link_dir(link, target):
    """A directory symlink, or on Windows without the privilege a junction. Raises unittest.SkipTest if neither works."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        if r.returncode == 0:
            return
    raise unittest.SkipTest("symlinks not allowed here")


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def header(self, name):
        return self.headers.get(name.lower())


class Client:
    def __init__(self, server, token):
        self.port = server.server_address[1]
        self.token = token

    def request(self, method, path, body=None, headers=None, host="default", json_body=None, token="default",
                content_type="default"):
        h = {}
        if host == "default":
            host = "127.0.0.1:%d" % self.port
        if host is not None:
            h["Host"] = host
        data = body
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
        if method in ("POST", "PUT", "DELETE"):
            data = data if data is not None else b""
            if content_type == "default":
                content_type = "application/json"
            if content_type:
                h["Content-Type"] = content_type
            if token == "default":
                token = self.token
            if token:
                h["X-Viewer-Token"] = token
            h["Content-Length"] = str(len(data))
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                if v is not None:
                    conn.putheader(k, v)
            conn.endheaders(data if isinstance(data, (bytes, bytearray)) else None)
            r = conn.getresponse()
            return Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, json_body=None, **kw):
        return self.request("POST", path, json_body=json_body, **kw)

    def raw(self, data):
        """Send raw bytes, return everything the server answers until it closes (or goes quiet)."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall(data)
            out = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    out += chunk
                    head, sep, rest = out.partition(b"\r\n\r\n")
                    if sep:  # a whole response: headers plus Content-Length bytes
                        length = [int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length:")]
                        if len(rest) >= (length[0] if length else 0):
                            break
            except (socket.timeout, ConnectionError):
                pass
            return out
        finally:
            s.close()

    def raw_status(self, data):
        out = self.raw(data)
        return int(out.split(b" ", 2)[1]) if out.startswith(b"HTTP/") else 0

    def raw_headers(self, **extra):
        """A minimal valid request head with the right Host; `extra` are more header lines."""
        lines = ["Host: 127.0.0.1:%d" % self.port] + ["%s: %s" % kv for kv in extra.items()]
        return "\r\n".join(lines)


class ProjectCase(unittest.TestCase):
    """A temp project folder and a temp tool folder (app/index.html, themes/aero, vendor), cleaned up after."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="av-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state_dir = os.path.join(self.tmp, "state")  # the per-user folder of the token file, kept out of the real one
        env = mock.patch.dict(os.environ, {"ASSET_VIEWER_STATE_DIR": self.state_dir})
        env.start()
        self.addCleanup(env.stop)
        self.project_dir = os.path.join(self.tmp, "project")
        self.tool_dir = os.path.join(self.tmp, "tool")
        os.makedirs(self.project_dir)
        self.put(self.tool_dir, "app/index.html", INDEX_HTML)
        self.put(self.tool_dir, "app/app.js", "export const x = 1;")
        self.put(self.tool_dir, "themes/aero/theme.css", ":root{--ink:#000}")
        self.put(self.tool_dir, "vendor/lib.js", "export default 1;")
        self.put(self.tool_dir, "app/secret.py", "SECRET = 1")

    @staticmethod
    def put(base, rel, data="x"):
        path = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(data, (dict, list)):
            data = json.dumps(data)
        with open(path, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def write(self, rel, data="x"):
        return self.put(self.project_dir, rel, data)

    def make_app(self, config=None, config_path="asset-viewer.json", config_arg=None, **kw):
        if config is not None:
            self.write(config_path, config)
        return App(self.project_dir, config_arg, tool_dir=self.tool_dir, **kw)

    def serve(self, app=None, **kw):
        app = app or self.make_app(**kw)
        server = Server(app, 0)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
        self.addCleanup(stop)
        return Client(server, app.token)

    def catalog(self, app):
        return app.catalog.get()[2]

    def ids(self, app, kind=None):
        return sorted(i["id"] for i in self.catalog(app)["items"] if kind in (None, i["kind"]))
