# SPDX-License-Identifier: AGPL-3.0-or-later
"""The HTTP server: routes, guards and headers. Files are served by static.py, starting is in launch.py.

Every request passes the same guards before any handler runs (core or plugin): Host check against DNS rebinding;
for unsafe methods a same-origin Origin, the per-run token and a JSON content type; a required, capped Content-Length.
"""
import hmac
import json
import os
import shutil
import socket
import sys
import traceback
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote

from . import APP_NAME, VERSION
from .filetypes import content_type
from .picks import MAX_BODY as PICKS_MAX_BODY, BadPick, CorruptFile
from .plugins import HttpError, Request, Response, Route, json_response
from .project import PathError, WriteError
from .static import FILE_CSP, StaticMixin, csp_for, is_static_path  # noqa: F401  (csp_for: kept importable from here)

JSON_TYPE = "application/json; charset=utf-8"
UNSAFE = ("POST", "PUT", "DELETE", "PATCH")
DRAIN_MAX = 65536  # bytes of an unread request body read before a refusal closes the connection
DRAIN_SECONDS = 2  # and how long to wait for them


def core_routes(app):
    """The /api routes of the core, as Routes with the same guards as a plugin's."""
    def hello(req):
        return json_response({"app": APP_NAME, "version": VERSION, "project": app.project_hash})

    def config(req):
        return json_response(app.public_config())

    def catalog(req):
        version, body, _data = app.catalog.get()
        return Response(200, body, JSON_TYPE, {"ETag": '"%s"' % version})

    def picks_get(req):
        try:
            rev, picks = app.picks().read()
        except CorruptFile as e:
            raise HttpError(409, str(e))
        return json_response({"rev": rev, "picks": picks})

    def picks_post(req):
        try:
            rev, picks = app.picks().update(req.json(dict))
        except BadPick as e:
            raise HttpError(400, str(e))
        except CorruptFile as e:
            raise HttpError(409, str(e))
        except (WriteError, PathError) as e:
            raise HttpError(500, str(e))
        return json_response({"rev": rev, "picks": picks})

    def review(req):
        etag, payload = app.review().read()
        resp = json_response(payload)
        resp.headers["ETag"] = '"%s"' % etag
        return resp

    return [
        Route("core", "GET", "hello", hello, 0, True, ()),
        Route("core", "GET", "config", config, 0, True, ()),
        Route("core", "GET", "catalog", catalog, 0, True, ()),
        Route("core", "GET", "picks", picks_get, 0, True, ()),
        Route("core", "POST", "picks", picks_post, PICKS_MAX_BODY, True, ("application/json",)),
        Route("core", "GET", "review", review, 0, True, ()),
    ]


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    allow_reuse_address = os.name != "nt"  # on Windows reuse would let a second server share the port silently

    def __init__(self, app, port):
        self.app = app
        self.routes = core_routes(app)
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), Handler)  # loopback only, there is no --host
        app.attach(self)

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        ThreadingHTTPServer.server_bind(self)

    def handle_error(self, request, client_address):
        if self.app.debug:
            traceback.print_exc()


class Handler(StaticMixin, BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = APP_NAME
    sys_version = ""
    timeout = 30  # a stalled client frees its thread

    def log_message(self, fmt, *args):
        if self.server.app.debug:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # entry points -----------------------------------------------------------------------------------------------
    def do_GET(self):
        self._handle()

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_GET

    def version_string(self):
        return APP_NAME

    def send_error(self, code, message=None, explain=None):  # errors of the base class (bad request line...)
        self.close_connection = True
        if self.request_version == "HTTP/0.9":  # no version understood: still answer with headers
            self.request_version = "HTTP/1.0"
        self._error(code, message or HTTPStatus(code).phrase)

    def _handle(self):
        app = self.server.app
        self._body_read = False
        try:
            self._dispatch(app)
        except HttpError as e:
            self._error(e.code, e.message, e.headers)
        except (ConnectionError, TimeoutError, socket.timeout):
            self.close_connection = True
        except Exception:
            if app.debug:
                traceback.print_exc()
            self._error(500, "internal error")

    # sending ----------------------------------------------------------------------------------------------------
    def _start(self, code, ctype=None, length=None, headers=None, cache="no-store", csp=FILE_CSP):
        """Status and headers. Every response carries the sandbox CSP unless it is the tool's own file (`csp=None`) or
        brings its own (the app page)."""
        self.send_response(code)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", cache)
        if csp:
            self.send_header("Content-Security-Policy", csp)
        if ctype:
            self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def _reply(self, code, body=b"", ctype=None, headers=None, cache="no-store", csp=FILE_CSP):
        bodyless = code in (204, 304)
        self._start(code, ctype, None if bodyless else len(body), headers, cache, csp)
        if body and not bodyless and self.command != "HEAD":
            self.wfile.write(body)

    def _drain(self):
        """Read a small unread body before closing, so the close is clean: on Windows a close with unread data is a reset
        that can eat the reply the client is waiting for. A big or odd body is not read (the connection just closes)."""
        try:
            n = (self.headers.get("Content-Length") or "").strip()
            if self.headers.get("Transfer-Encoding") is None and n.isascii() and n.isdigit() and 0 < int(n) <= DRAIN_MAX:
                self.connection.settimeout(DRAIN_SECONDS)
                self.rfile.read(int(n))
        except (AttributeError, ValueError, OSError):
            pass

    def _error(self, code, message, headers=None):
        drain = not getattr(self, "_body_read", False) and self.command in UNSAFE
        if drain:
            self.close_connection = True  # an unread body would be taken for the next request
        try:
            self._reply(code, json.dumps({"error": message}).encode("utf-8"), JSON_TYPE, headers)
        except OSError:
            self.close_connection = True
            return
        if drain:
            self._drain()  # after the reply: the client has its answer whether or not the rest of its body ever comes

    def _not_modified(self, etag):
        sent = self.headers.get("If-None-Match")
        if not sent:
            return False
        return any(t.strip().removeprefix("W/") in (etag, "*") for t in sent.split(","))

    def _file(self, path, ctype=None, headers=None, cache="no-cache", tool=False):
        """A file with validators. `tool`: the tool's own file; anything else is project data and gets the sandbox CSP."""
        csp = None if tool else FILE_CSP
        try:
            f = open(path, "rb")
        except OSError:
            raise HttpError(404, "not found")
        with f:
            st = os.fstat(f.fileno())
            etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
            h = {"ETag": etag, "Last-Modified": formatdate(st.st_mtime, usegmt=True)}
            h.update(headers or {})
            if self._not_modified(etag):
                return self._reply(304, headers=h, cache=cache, csp=csp)
            self._start(200, ctype or content_type(path), st.st_size, h, cache, csp)
            if self.command != "HEAD":
                shutil.copyfileobj(f, self.wfile, 65536)

    # guards -----------------------------------------------------------------------------------------------------
    def _allowed_origins(self, app):
        return ("http://127.0.0.1:%d" % app.port, "http://localhost:%d" % app.port, "http://[::1]:%d" % app.port)

    def _check_host(self, app):
        hosts = self.headers.get_all("Host") or []
        allowed = ("127.0.0.1:%d" % app.port, "localhost:%d" % app.port, "[::1]:%d" % app.port)
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            raise HttpError(421, "bad Host header (use 127.0.0.1:%d)" % app.port)

    def _check_origin(self, app, route=None):
        """Same-origin only. For an unsafe method also the token, unless the route lets a request that is not from a
        browser (no Origin, no Sec-Fetch-Site) go without."""
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site")
        if origin is not None and origin.lower() not in self._allowed_origins(app):
            raise HttpError(403, "cross-origin request refused")
        if site is not None and site.lower() not in ("same-origin", "none"):
            raise HttpError(403, "cross-site request refused")
        if route is None:
            return
        if route.token == "optional-if-no-origin" and origin is None and site is None:
            return
        sent = self.headers.get("X-Viewer-Token", "")
        if not hmac.compare_digest(sent.encode("utf-8"), app.token.encode("utf-8")):
            raise HttpError(403, "missing or wrong X-Viewer-Token (reload the page)")

    def _content_length(self):
        values = self.headers.get_all("Content-Length") or []
        if self.headers.get("Transfer-Encoding") is not None:
            raise HttpError(411, "chunked bodies are not accepted: send Content-Length")
        if not values:
            raise HttpError(411, "Content-Length required")
        v = values[0].strip()
        if len(values) > 1 or not (v.isascii() and v.isdigit()) or len(v) > 12:
            raise HttpError(400, "bad Content-Length")
        return int(v)

    # dispatch ---------------------------------------------------------------------------------------------------
    def _dispatch(self, app):
        method = self.command
        if not self.path.startswith("/"):
            raise HttpError(400, "bad request target")
        raw_path, _, qs = self.path.split("#", 1)[0].partition("?")
        self._check_host(app)
        if method not in UNSAFE and (self.headers.get("Transfer-Encoding") is not None
                                     or self.headers.get("Content-Length", "0").strip() not in ("", "0")):
            self.close_connection = True  # a body on a GET is never read
        try:
            path = unquote(raw_path, errors="strict")  # decoded once; every check below sees this string
        except UnicodeDecodeError:
            raise HttpError(400, "bad path encoding")
        if "\x00" in path:
            raise HttpError(400, "bad path")
        app.refresh()
        if is_static_path(path):
            if method not in ("GET", "HEAD"):
                raise HttpError(405, "method not allowed", {"Allow": "GET, HEAD"})
            return self._static(app, path)
        if path.startswith("/api/"):
            return self._api(app, method, path, qs)
        raise HttpError(404, "not found")

    def _api(self, app, method, path, qs):
        if path.startswith("/api/plugins/"):
            matches = app.state.registry.match_route(path)
            mount = path.split("/", 4)[4]  # what follows /api/plugins/<name>/
        else:
            mount = path[len("/api/"):]
            matches = [(r, p) for r in self.server.routes for p in [r.match(mount)] if p is not None]
        if not matches:
            raise HttpError(404, "not found")
        want = "GET" if method == "HEAD" else method
        chosen = [(r, p) for r, p in matches if r.method == want]
        if not chosen:
            raise HttpError(405, "method not allowed", {"Allow": ", ".join(sorted({r.method for r, _ in matches}))})
        route, params = chosen[0]
        unsafe = route.method != "GET"
        self._check_origin(app, route if unsafe else None)
        body = b""
        if unsafe:
            media = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if route.content_types and media not in route.content_types:
                raise HttpError(415, "Content-Type must be %s" % " or ".join(route.content_types))
            n = self._content_length()
            if n > route.max_body:
                raise HttpError(413, "body too large (at most %d bytes)" % route.max_body)
            body = self.rfile.read(n)
            if len(body) != n:
                self.close_connection = True
                raise HttpError(400, "incomplete body")
            self._body_read = True
        try:
            query = {k: v[-1] for k, v in parse_qs(qs, keep_blank_values=True, max_num_fields=64).items()}
        except ValueError:
            raise HttpError(400, "bad query string")
        req = Request(method, mount, params, query, body, self.headers, full_path=path)
        try:
            result = route.handler(req)
        except HttpError:
            raise
        except Exception:
            if app.debug:
                traceback.print_exc()
            raise HttpError(500, "plugin %s failed" % route.plugin if route.plugin != "core" else "internal error")
        resp = self._as_response(result)
        etag = resp.headers.get("ETag")
        if etag and method in ("GET", "HEAD") and self._not_modified(etag):
            return self._reply(304, headers={"ETag": etag})
        self._reply(resp.code, resp.body, resp.content_type, resp.headers)

    @staticmethod
    def _as_response(result):
        if isinstance(result, Response):
            return result
        if result is None:
            return Response(204)
        if isinstance(result, (bytes, bytearray)):
            return Response(200, bytes(result))
        return json_response(result)
