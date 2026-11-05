# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import threading
import time
import unittest

import helpers
from viewer import VERSION
from viewer.launch import StartError, open_server, probe
from viewer.static import FILE_CSP, csp_for


class HttpTest(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("assets/a.png", "PNGDATA")
        self.write("assets/sub/m.obj", "o cube")
        self.write("assets/sub/s.wav", "RIFF")
        self.write("assets/page.html", "<script>alert(1)</script>")
        self.write("assets/vector.svg", "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
        self.write("assets/data.json", '{"k": 1}')
        self.write("theme/custom.css", ":root{--ink:#111}")
        self.write("theme/icon.svg", "<svg/>")
        self.write("theme/notes.py", "no")
        self.write("outside.png", "no")
        self.write(".git/config", "[core]")
        self.write(".env", "SECRET=1")
        self.cfg = {"roots": [{"path": "assets", "label": ""}], "title": "Sample"}
        self.client = self.serve(self.make_app(self.cfg))

    def test_index_has_token_and_csp_with_the_importmap_hash(self):
        r = self.client.get("/")
        self.assertEqual(r.status, 200)
        html = r.body.decode("utf-8")
        self.assertIn('content="%s"' % self.client.token, html)
        self.assertNotIn("__VIEWER_TOKEN__", html)
        csp = r.header("content-security-policy")
        self.assertEqual(csp, csp_for(html))
        self.assertRegex(csp, r"script-src 'self' 'sha256-[A-Za-z0-9+/=]+'")
        for part in ("default-src 'self'", "style-src 'self'", "img-src 'self' data: blob:", "media-src 'self' blob:",
                     "frame-ancestors 'none'"):
            self.assertIn(part, csp)
        self.assertEqual(self.client.get("/index.html").body, r.body)
        self.assertEqual(self.client.get("/app/index.html").body, r.body)

    def test_csp_hash_is_of_the_exact_script_text(self):
        import base64
        import hashlib
        body = '{"imports":{"viewer/":"/app/"}}'
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        self.assertIn("'sha256-%s'" % digest, csp_for('<script type="importmap">%s</script><script src="x.js"></script>' % body))
        self.assertNotIn("x.js", csp_for('<script src="x.js"></script>'))

    def test_common_headers_everywhere(self):
        for path in ("/", "/api/hello", "/api/catalog", "/files/assets/a.png", "/app/app.js", "/nothing"):
            r = self.client.get(path)
            with self.subTest(path=path):
                self.assertEqual(r.header("x-content-type-options"), "nosniff")
                self.assertEqual(r.header("referrer-policy"), "no-referrer")
                self.assertEqual(r.header("cross-origin-resource-policy"), "same-origin")

    def test_hello(self):
        r = self.client.get("/api/hello")
        data = r.json()
        self.assertEqual((data["app"], data["version"]), ("asset-viewer", VERSION))
        self.assertRegex(data["project"], r"^[0-9a-f]{12}$")

    def test_config_shape(self):
        data = self.client.get("/api/config").json()
        self.assertEqual(set(data), {"app", "version", "title", "categories", "themeVersion", "prefsPrefix", "model", "plugins",
                                     "data", "error", "warnings"})
        self.assertEqual(data["model"], {"front": "+z", "glassNames": []})
        self.assertEqual((data["title"], data["categories"], data["plugins"], data["error"]), ("Sample", ["model", "texture", "vfx", "sound"], [], None))
        self.assertEqual(data["data"], {"picks": "picks.json", "review": "review.json"})
        self.assertGreater(data["themeVersion"], 0)
        self.assertEqual(self.client.get("/api/config").header("cache-control"), "no-store")

    def test_catalog_etag_and_304(self):
        r = self.client.get("/api/catalog")
        data = r.json()
        self.assertEqual(sorted(data), ["items", "plugins", "version", "warnings"])
        self.assertEqual([i["id"] for i in data["items"]],
                         ["assets/a.png", "assets/vector.svg", "assets/sub/m.obj", "assets/sub/s.wav"])
        etag = r.header("etag")
        self.assertEqual(etag, '"%s"' % data["version"])
        r2 = self.client.get("/api/catalog", headers={"If-None-Match": etag})
        self.assertEqual((r2.status, r2.body), (304, b""))
        self.assertEqual(self.client.get("/api/catalog", headers={"If-None-Match": '"other"'}).status, 200)
        self.assertEqual(self.client.get("/api/catalog", headers={"If-None-Match": 'W/"x", ' + etag}).status, 304)

    def test_catalog_changes_when_files_change_after_the_cache_second(self):
        v1 = self.client.get("/api/catalog").json()["version"]
        self.write("assets/new.png")
        time.sleep(1.1)
        v2 = self.client.get("/api/catalog").json()["version"]
        self.assertNotEqual(v1, v2)

    def test_files_served_with_sandbox_csp_and_validators(self):
        r = self.client.get("/files/assets/a.png")
        self.assertEqual((r.status, r.body, r.header("content-type")), (200, b"PNGDATA", "image/png"))
        self.assertEqual(r.header("content-security-policy"), "sandbox; default-src 'none'")
        self.assertEqual(r.header("cache-control"), "no-cache")
        self.assertEqual(r.header("content-length"), "7")
        self.assertTrue(r.header("etag") and r.header("last-modified"))
        r2 = self.client.get("/files/assets/a.png", headers={"If-None-Match": r.header("etag")})
        self.assertEqual((r2.status, r2.body), (304, b""))
        svg = self.client.get("/files/assets/vector.svg")
        self.assertEqual((svg.status, svg.header("content-type")), (200, "image/svg+xml"))
        self.assertIn("sandbox", svg.header("content-security-policy"))
        self.assertEqual(self.client.get("/files/assets/sub/s.wav").header("content-type"), "audio/wav")

    def test_files_url_decoding(self):
        self.write("assets/sp ace/é.png", "u")
        r = self.client.get("/files/assets/sp%20ace/%C3%A9.png")
        self.assertEqual((r.status, r.body), (200, b"u"))
        self.assertEqual(self.client.get("/files/assets/a.png?v=123").status, 200)

    def test_head(self):
        r = self.client.request("HEAD", "/files/assets/a.png")
        self.assertEqual((r.status, r.body, r.header("content-length")), (200, b"", "7"))
        r = self.client.request("HEAD", "/api/hello")
        self.assertEqual((r.status, r.body), (200, b""))

    def test_static_tool_folders(self):
        self.assertEqual(self.client.get("/app/app.js").body, b"export const x = 1;")
        self.assertEqual(self.client.get("/app/app.js").header("content-type"), "text/javascript; charset=utf-8")
        self.assertEqual(self.client.get("/vendor/lib.js").status, 200)
        self.assertEqual(self.client.get("/themes/aero/theme.css").header("content-type"), "text/css; charset=utf-8")
        for path in ("/app/secret.py", "/app/", "/app/missing.js", "/vendor/", "/themes/aero/", "/app/../secret.py",
                     "/app/%2e%2e/app/secret.py", "/themes/aero/%2e%2e/%2e%2e/app/app.js"):
            self.assertEqual(self.client.get(path).status, 404, path)

    def test_theme_route_serves_the_aero_theme_by_default(self):
        self.assertEqual(self.client.get("/theme/theme.css").body, b":root{--ink:#000}")

    def test_project_theme_maps_to_its_folder(self):
        client = self.serve(self.make_app({**self.cfg, "theme": "theme/custom.css"}))
        self.assertEqual(client.get("/theme/theme.css").body, b":root{--ink:#111}")
        self.assertEqual(client.get("/theme/icon.svg").status, 200)
        self.assertEqual(client.get("/theme/notes.py").status, 404)  # only theme file types
        self.assertEqual(client.get("/theme/custom.css").status, 200)
        self.assertEqual(client.get("/theme/../outside.png").status, 404)
        data = client.get("/api/config").json()
        self.assertGreater(data["themeVersion"], 0)
        self.assertEqual(data["warnings"], [])

    def test_model_settings_reach_the_client(self):
        client = self.serve(self.make_app({**self.cfg, "model": {"front": "-z", "glassNames": ["^Glass", "^Lens"]}}))
        self.assertEqual(client.get("/api/config").json()["model"], {"front": "-z", "glassNames": ["^Glass", "^Lens"]})

    def test_missing_theme_is_a_warning_and_404(self):
        client = self.serve(self.make_app({**self.cfg, "theme": "nope"}))
        self.assertEqual(client.get("/theme/theme.css").status, 404)
        self.assertIn("theme nope not found", client.get("/api/config").json()["warnings"])

    def test_the_sandbox_csp_is_on_everything_that_is_not_the_tools_own(self):
        project_theme = self.serve(self.make_app({**self.cfg, "theme": "theme/custom.css"}))
        project_files = ["/files/assets/a.png", "/files/assets/vector.svg", "/theme/theme.css", "/theme/icon.svg"]
        for path in project_files:
            self.assertEqual(project_theme.get(path).header("content-security-policy"), FILE_CSP, path)
        for path in ("/api/hello", "/api/catalog", "/api/picks", "/api/config", "/nothing", "/files/nope.png"):
            self.assertEqual(self.client.get(path).header("content-security-policy"), FILE_CSP, path)
        self.assertEqual(self.client.get("/files/assets/a.png", headers={"If-None-Match": '"x"'}).header("content-security-policy"), FILE_CSP)
        # the tool's own files and the app page carry none / their own
        for path in ("/app/app.js", "/vendor/lib.js", "/themes/aero/theme.css", "/theme/theme.css"):
            self.assertIsNone(self.client.get(path).header("content-security-policy"), path)
        self.assertNotEqual(self.client.get("/").header("content-security-policy"), FILE_CSP)

    def test_there_are_no_redirects(self):
        for path in ("/old/viewer/", "/old/"):
            self.assertEqual(self.client.get(path).status, 404, path)


    def test_picks_flow(self):
        r = self.client.get("/api/picks").json()
        self.assertEqual(r["picks"], {})
        rev0 = r["rev"]
        r = self.client.post("/api/picks", {"id": "assets/a.png", "picked": True, "note": "yes"})
        self.assertEqual(r.status, 200)
        body = r.json()
        self.assertGreater(body["rev"], rev0)
        self.assertEqual(body["picks"]["assets/a.png"]["note"], "yes")
        self.assertEqual(self.client.get("/api/picks").json(), body)
        r = self.client.post("/api/picks", {"id": "assets/a.png", "note": "changed"}).json()
        self.assertEqual((r["picks"]["assets/a.png"]["picked"], r["picks"]["assets/a.png"]["note"]), (True, "changed"))
        r = self.client.post("/api/picks", {"group": ["assets/a.png", "assets/x.png"], "picked": "assets/x.png"}).json()
        self.assertEqual((r["picks"]["assets/a.png"]["picked"], r["picks"]["assets/x.png"]["picked"]), (False, True))
        self.assertEqual(self.client.post("/api/picks", {"id": "a", "cut": [2, 1]}).status, 400)
        self.assertEqual(self.client.post("/api/picks", body=b"not json").status, 400)

    def test_corrupt_picks_answer_409_and_stay_untouched(self):
        self.write("picks.json", "<<<<<<< merge")
        self.assertEqual(self.client.get("/api/picks").status, 409)
        r = self.client.post("/api/picks", {"id": "a", "picked": True})
        self.assertEqual(r.status, 409)
        self.assertIn("picks.json", r.json()["error"])
        with open(os.path.join(self.project_dir, "picks.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "<<<<<<< merge")

    def test_review_endpoint(self):
        r = self.client.get("/api/review")
        self.assertEqual(r.json(), {"rev": r.json()["rev"], "reviews": [], "error": None})
        self.write("review.json", {"reviews": [{"title": "T", "groups": []}]})
        r = self.client.get("/api/review")
        self.assertEqual(r.json()["reviews"][0]["title"], "T")
        self.assertEqual(self.client.get("/api/review", headers={"If-None-Match": r.header("etag")}).status, 304)

    def test_unknown_and_wrong_method_answer_json(self):
        r = self.client.get("/api/nothing")
        self.assertEqual((r.status, r.header("content-type")), (404, "application/json; charset=utf-8"))
        self.assertEqual(r.json(), {"error": "not found"})
        r = self.client.post("/api/hello", {})
        self.assertEqual((r.status, r.header("allow")), (405, "GET"))
        self.assertEqual(self.client.request("PATCH", "/api/picks").status, 405)
        self.assertEqual(self.client.request("OPTIONS", "/api/picks").status, 405)
        self.assertEqual(self.client.post("/files/assets/a.png", {}).status, 405)
        self.assertEqual(self.client.get("/random").status, 404)

    def test_config_reload_shows_in_api_config(self):
        path = self.write("asset-viewer.json", {**self.cfg, "title": "Renamed"})
        os.utime(path, (time.time() + 5,) * 2)
        time.sleep(0.6)
        self.assertEqual(self.client.get("/api/config").json()["title"], "Renamed")
        path = self.write("asset-viewer.json", "{ broken")
        os.utime(path, (time.time() + 10,) * 2)
        time.sleep(0.6)
        data = self.client.get("/api/config").json()
        self.assertEqual(data["title"], "Renamed")
        self.assertIn("invalid JSON", data["error"])

    def test_keep_alive_survives_many_requests(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=5)
        for _ in range(5):
            conn.request("GET", "/api/hello")
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 200)
        conn.close()


class StartTest(helpers.ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("assets/a.png")

    def app(self, project=None):
        return self.make_app({"roots": ["assets"]})

    def start(self, app, port):
        status, what = open_server(app, port)
        if status == "started":
            threading.Thread(target=what.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

            def stop():
                what.shutdown()
                what.server_close()
            self.addCleanup(stop)
        return status, what

    def test_second_start_of_the_same_project_only_opens(self):  # audit M10
        a = self.app()
        status, server = self.start(a, 0)
        self.assertEqual(status, "started")
        port = server.server_address[1]
        status, what = open_server(self.app(), port)
        self.assertEqual((status, what), ("running", port))

    def test_another_project_gets_the_next_port_or_an_error(self):
        a = self.app()
        _, server = self.start(a, 0)
        port = server.server_address[1]
        other_dir = os.path.join(self.tmp, "other")
        os.makedirs(os.path.join(other_dir, "assets"))
        from viewer.app import App
        other = App(other_dir, None, tool_dir=self.tool_dir)
        self.assertNotEqual(other.project_hash, a.project_hash)
        with self.assertRaisesRegex(StartError, "another asset viewer"):
            open_server(other, port, explicit=True)
        try:
            status, what = self.start(other, port)
        except StartError:
            self.skipTest("the next ports are busy")
        self.assertEqual(status, "started")
        self.assertGreater(what.server_address[1], port)

    def test_a_port_held_by_another_program_is_named(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.addCleanup(s.close)
        with self.assertRaisesRegex(StartError, "another program"):
            open_server(self.app(), s.getsockname()[1], explicit=True)
        self.assertIsNone(probe(s.getsockname()[1], timeout=0.5))

    def test_restart_right_after_a_stop_binds_again(self):  # audit M11
        a = self.app()
        status, server = open_server(a, 0)
        port = server.server_address[1]
        server.server_close()
        status, server2 = open_server(self.app(), port, explicit=True)
        self.assertEqual(status, "started")
        server2.server_close()

    def test_binds_loopback_only(self):
        _, server = self.start(self.app(), 0)
        self.assertEqual(server.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
