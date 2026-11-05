# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import socket
import unittest
from unittest import mock

import helpers


class SecurityCase(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("assets/a.png", "PNG")
        self.write("assets/page.html", "<script>1</script>")
        self.write("assets/sub/b.png", "B")
        self.write("assets/secret/hidden.png", "H")
        self.write("top.txt", "T")
        self.write(".git/config", "[core]")
        self.write(".env", "SECRET=1")
        self.write("assets/.env.png", "dot")
        self.cfg = {"roots": [{"path": "assets", "label": ""}], "hide": ["assets/secret/*"]}
        self.app = self.make_app(self.cfg)
        self.client = self.serve(self.app)
        self.port = self.client.port

    def raw(self, request_line, *headers, body=b"", host=True):
        lines = [request_line] + (["Host: 127.0.0.1:%d" % self.port] if host else []) + list(headers)
        return self.client.raw_status(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


class HostTest(SecurityCase):
    def test_bad_host_is_421(self):  # audit C2 (DNS rebinding)
        for host in ("evil.example", "evil.example:%d" % self.port, "127.0.0.1", "localhost", "127.0.0.1:1", "0.0.0.0:%d" % self.port,
                     "127.0.0.1:%d.evil.example" % self.port, "", "[::2]:%d" % self.port):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/api/hello", host=host).status, 421)
                self.assertEqual(self.client.get("/", host=host).status, 421)
                self.assertEqual(self.client.get("/files/assets/a.png", host=host).status, 421)

    def test_good_hosts(self):
        for host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port, "LOCALHOST:%d" % self.port, "[::1]:%d" % self.port):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/api/hello", host=host).status, 200)

    def test_missing_or_double_host(self):
        self.assertEqual(self.raw("GET /api/hello HTTP/1.1", host=False), 421)
        self.assertEqual(self.raw("GET /api/hello HTTP/1.1", "Host: 127.0.0.1:%d" % self.port), 421)


class OriginTokenTest(SecurityCase):
    def test_cross_origin_post_is_refused_even_with_the_token(self):  # audit C2: text/plain cross-origin POST
        for origin in ("http://evil.example", "null", "http://127.0.0.1:1", "https://127.0.0.1:%d" % self.port,
                       "http://localhost.evil.example:%d" % self.port):
            with self.subTest(origin=origin):
                r = self.client.post("/api/picks", {"id": "a", "picked": True}, headers={"Origin": origin})
                self.assertEqual(r.status, 403)
                r = self.client.post("/api/picks", body=b'{"id": "a", "picked": true}', content_type="text/plain",
                                     headers={"Origin": origin}, token=None)
                self.assertEqual(r.status, 403)
        self.assertFalse(os.path.exists(os.path.join(self.project_dir, "picks.json")))

    def test_simple_cross_origin_form_post_cannot_write(self):
        r = self.client.post("/api/picks", body=b'{"id": "a", "picked": true}', content_type="text/plain", token=None)
        self.assertIn(r.status, (403, 415))
        r = self.client.post("/api/picks", body=b"id=a&picked=1", content_type="application/x-www-form-urlencoded", token=None)
        self.assertIn(r.status, (403, 415))
        self.assertFalse(os.path.exists(os.path.join(self.project_dir, "picks.json")))

    def test_same_origin_with_token_passes(self):
        for origin in ("http://127.0.0.1:%d" % self.port, "http://localhost:%d" % self.port):
            r = self.client.post("/api/picks", {"id": "a", "picked": True}, headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(r.status, 200, origin)

    def test_sec_fetch_site_must_be_same_origin_or_none(self):
        for site, want in (("cross-site", 403), ("same-site", 403), ("same-origin", 200), ("none", 200)):
            with self.subTest(site=site):
                r = self.client.post("/api/picks", {"id": "a", "picked": True}, headers={"Sec-Fetch-Site": site})
                self.assertEqual(r.status, want)

    def test_token_required_and_checked(self):
        self.assertEqual(self.client.post("/api/picks", {"id": "a"}, token=None).status, 403)
        self.assertEqual(self.client.post("/api/picks", {"id": "a"}, token="nope").status, 403)
        self.assertEqual(self.client.post("/api/picks", {"id": "a"}, token=self.client.token + "x").status, 403)
        r = self.client.post("/api/picks?token=" + self.client.token, {"id": "a"}, token=None)
        self.assertEqual(r.status, 403)  # a query string is not a token
        self.assertEqual(self.client.post("/api/picks", {"id": "a", "picked": True}).status, 200)

    def test_json_content_type_required(self):
        for ct in ("text/plain", "", None, "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(ct=ct):
                self.assertEqual(self.client.post("/api/picks", body=b'{"id": "a"}', content_type=ct).status, 415)
        self.assertEqual(self.client.post("/api/picks", body=b'{"id": "a", "picked": true}', content_type="application/json; charset=utf-8").status, 200)

    def test_get_api_refuses_foreign_origin_and_site(self):
        self.assertEqual(self.client.get("/api/catalog", headers={"Origin": "http://evil.example"}).status, 403)
        self.assertEqual(self.client.get("/api/picks", headers={"Sec-Fetch-Site": "cross-site"}).status, 403)
        self.assertEqual(self.client.get("/api/picks", headers={"Sec-Fetch-Site": "same-origin"}).status, 200)
        # following a link to the page from elsewhere is a navigation: allowed
        self.assertEqual(self.client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status, 200)

    def test_token_is_per_run_and_not_in_api_responses(self):
        self.assertNotIn(self.client.token.encode(), self.client.get("/api/config").body)
        self.assertNotIn(self.client.token.encode(), self.client.get("/api/catalog").body)
        other = self.serve(self.make_app(self.cfg))
        self.assertNotEqual(other.token, self.client.token)
        self.assertEqual(other.post("/api/picks", {"id": "a"}, token=self.client.token).status, 403)


class BodyTest(SecurityCase):
    def post_head(self, *headers, body=b""):
        return self.raw("POST /api/picks HTTP/1.1", "Content-Type: application/json", "X-Viewer-Token: " + self.client.token,
                        *headers, body=body)

    def test_content_length_required(self):
        self.assertEqual(self.post_head(), 411)

    def test_chunked_refused(self):
        self.assertEqual(self.post_head("Transfer-Encoding: chunked", body=b"0\r\n\r\n"), 411)
        self.assertEqual(self.post_head("Transfer-Encoding: chunked", "Content-Length: 2", body=b"{}"), 411)

    def test_bad_content_length_never_hangs(self):  # audit H1
        for value in ("-1", "abc", "1e3", "+5", "", "99999999999999", "1 2"):
            with self.subTest(value=value):
                self.assertIn(self.post_head("Content-Length: " + value), (400, 411, 413))
        self.assertEqual(self.post_head("Content-Length: 2", "Content-Length: 2", body=b"{}"), 400)

    def test_oversize_is_413_before_reading(self):
        self.assertEqual(self.post_head("Content-Length: 65537"), 413)
        self.assertEqual(self.post_head("Content-Length: 4294967296"), 413)
        body = b"{" + b" " * 65534 + b"}"
        self.assertEqual(len(body), 65536)
        self.assertEqual(self.post_head("Content-Length: 65536", body=body), 400)  # the cap passes, then `{}` has no id

    def test_a_body_over_the_old_silent_truncation_limit_is_rejected_not_cut(self):
        r = self.client.post("/api/picks", body=b'{"id": "a", "note": "' + b"x" * 70000 + b'"}')
        self.assertEqual(r.status, 413)

    def test_short_body_is_a_400_not_a_hang(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall(("POST /api/picks HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nX-Viewer-Token: %s\r\n"
                       "Content-Length: 50\r\n\r\n{\"id\"" % (self.port, self.client.token)).encode())
            s.shutdown(socket.SHUT_WR)
            out = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                out += chunk
            self.assertTrue(out.startswith(b"HTTP/1.1 400"), out[:40])
        finally:
            s.close()

    def test_json_must_be_an_object(self):
        for body in (b"[]", b"1", b'"x"', b"null", b"\xff\xfe", b""):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/picks", body=body).status, 400)

    def test_rejected_post_closes_the_connection_so_the_body_is_not_reused(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            smuggled = b"GET /api/hello HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port
            head = ("POST /api/picks HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n"
                    % (self.port, len(smuggled))).encode()
            s.sendall(head + smuggled)
            out = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                out += chunk
            self.assertEqual(out.count(b"HTTP/1.1 "), 1, out)
        finally:
            s.close()


class PathTest(SecurityCase):
    def test_traversal_and_odd_paths_through_http(self):
        paths = [
            "/files/../top.txt", "/files/assets/../top.txt", "/files/%2e%2e/top.txt", "/files/assets/%2e%2e/top.txt",
            "/files/..%2ftop.txt", "/files/assets%2f..%2f..%2ftop.txt", "/files/assets/..%5ctop.txt", "/files/assets%5c..%5ctop.txt",
            "/files/C:/Windows/win.ini", "/files/C%3A/Windows/win.ini", "/files//etc/passwd", "/files/%2Fetc/passwd",
            "/files/assets/./a.png", "/files/assets//a.png", "/files/assets/a.png/", "/files/assets/a.png.", "/files/assets/a.png%20",
            "/files/.git/config", "/files/assets/../.git/config", "/files/.env", "/files/assets/.env.png",
            "/files/assets/page.html", "/files/assets/secret/hidden.png", "/files/ASSETS/SECRET/hidden.png",
            "/files/top.txt", "/files/assets", "/files/assets/", "/files/", "/files", "/files/nul", "/files/assets/con.png",
            "/app/../top.txt", "/app/%2e%2e/top.txt", "/app/..%2f..%2ftop.txt", "/vendor/..\\x", "/themes/..%5c..%5ctop.txt",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.raw_status(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % (path, self.port)).encode()), 404)

    def test_bad_encodings_are_400(self):
        for path in ("/files/assets/a.png%00", "/files/assets/%00a.png", "/files/%ff%fe", "/api/hello%00"):
            with self.subTest(path=path):
                self.assertEqual(self.client.raw_status(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % (path, self.port)).encode()), 400)

    def test_only_served_files_of_known_types_come_out(self):
        self.assertEqual(self.client.get("/files/assets/a.png").status, 200)
        self.assertEqual(self.client.get("/files/assets/sub/b.png").status, 200)

    def test_absolute_form_target_is_refused(self):
        self.assertEqual(self.raw("GET http://evil.example/api/hello HTTP/1.1"), 400)

    def test_no_directory_listing_anywhere(self):
        for path in ("/files/assets/", "/files/assets/sub", "/app/", "/vendor/", "/themes/", "/themes/aero/", "/plugins/", "/theme/"):
            r = self.client.get(path)
            self.assertEqual(r.status, 404, path)
            self.assertNotIn(b"<li>", r.body)

    def test_whole_project_root_still_hides_dot_folders_and_data_not_in_an_area(self):
        client = self.serve(self.make_app({"roots": ["."]}))
        self.assertEqual(client.get("/files/top.txt").status, 200)
        self.assertEqual(client.get("/files/.git/config").status, 404)
        self.assertEqual(client.get("/files/.env").status, 404)
        self.assertEqual(client.get("/files/assets/page.html").status, 404)  # html is never served from the project

    def test_symlink_out_of_the_project_is_not_served(self):
        outside = os.path.join(self.tmp, "outside.png")
        with open(outside, "w") as f:
            f.write("x")
        try:
            os.symlink(outside, os.path.join(self.project_dir, "assets", "link.png"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not allowed here")
        self.assertEqual(self.client.get("/files/assets/link.png").status, 404)

    def test_config_hide_not_served_but_data_files_are_areas(self):
        self.assertEqual(self.client.get("/files/assets/secret/hidden.png").status, 404)
        self.write("picks.json", "{}")
        self.assertEqual(self.client.get("/files/picks.json").status, 200)


class FailureTest(SecurityCase):
    def test_internal_errors_are_json_without_tracebacks(self):
        with mock.patch.object(type(self.app.catalog), "get", side_effect=RuntimeError("/private/path/boom")):
            r = self.client.get("/api/catalog")
        self.assertEqual((r.status, r.json()), (500, {"error": "internal error"}))
        self.assertNotIn(b"boom", r.body)
        self.assertNotIn(b"Traceback", r.body)

    def test_unwritable_picks_is_a_clear_500(self):
        self.write("picks.json", "{}")
        with mock.patch("os.replace", side_effect=PermissionError(13, "Access is denied")):
            r = self.client.post("/api/picks", {"id": "a", "picked": True})
        self.assertEqual(r.status, 500)
        self.assertIn("picks.json", r.json()["error"])
        self.assertEqual([n for n in os.listdir(self.project_dir) if n.endswith(".tmp")], [])

    def test_a_dead_client_does_not_kill_the_server(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /api/catalog HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port)
        s.close()
        self.assertEqual(self.client.get("/api/hello").status, 200)

    def test_garbage_request_line(self):
        self.assertEqual(self.client.raw_status(b"GARBAGE LINE HERE\r\n\r\n"), 400)
        self.assertEqual(self.client.get("/api/hello").status, 200)

    def test_unknown_method_is_json(self):
        out = self.client.raw(("FOO /api/hello HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % self.port).encode())
        self.assertTrue(out.startswith(b"HTTP/1.1 501"))
        self.assertIn(b'"error"', out)


if __name__ == "__main__":
    unittest.main()
