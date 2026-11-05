# SPDX-License-Identifier: AGPL-3.0-or-later
"""The smoke harness without a browser: the page is mounted by the runner's own handler (and is not served by the app),
the handler's controls work, and tools/smoke.py reads a verdict correctly. The browser run itself is
`python tools/smoke.py` (here only with AV_SMOKE=1)."""
import contextlib
import io
import json
import os
import re
import shutil
import threading
import types
import unittest

import helpers
from helpers import REPO
from tools import smoke
from viewer.app import App
from viewer.http import Server
from viewer.static import csp_for


class SmokeServer(helpers.ProjectCase):
    """The sample project (a copy), served by the smoke runner's handler."""

    def setUp(self):
        super().setUp()
        self.project = os.path.join(self.tmp, "sample")
        shutil.copytree(os.path.join(REPO, "examples", "sample"), self.project, ignore=shutil.ignore_patterns("picks.json", ".viewer-token"))
        self.app = App(self.project, None, tool_dir=REPO)
        self.log = smoke.RequestLog()
        server = Server(self.app, 0)
        server.RequestHandlerClass = self.log.handler_class()
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        self.client = helpers.Client(server, self.app.token)

    def ctl(self, **body):
        r = self.client.post("/smoke/ctl", json_body=body)
        self.assertEqual(r.status, 200, r.body)
        return r.json()


class SmokePageTest(SmokeServer):
    def test_the_runner_mounts_the_smoke_page_with_its_token_and_a_csp_it_can_satisfy(self):
        r = self.client.get("/smoke/smoke.html")
        self.assertEqual(r.status, 200)
        page = r.body.decode("utf-8")
        self.assertIn('content="%s"' % self.client.token, page)
        self.assertEqual(r.header("content-security-policy"), csp_for(page).replace("frame-ancestors 'none'", "frame-ancestors 'self'"))
        self.assertEqual(len(re.findall(r"'sha256-", csp_for(page))), 1, "only the import map may be inline")
        self.assertNotRegex(page, r"<script(?![^>]*\bsrc=)[^>]*>(?!\s*\{)")  # no inline script but the import map
        self.assertNotRegex(page, r'\sstyle="')  # no style attribute: style-src 'self'
        js = self.client.get("/smoke/smoke.js")
        self.assertEqual((js.status, js.header("content-type")), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(self.client.get("/smoke/probe.js").status, 200)
        self.assertEqual(self.client.get("/smoke/nothing.js").status, 404)
        self.assertEqual(self.client.get("/smoke/../tools/smoke.py").status, 404)
        self.assertEqual(self.client.get("/smoke/ctl").status, 404)

    def test_the_app_may_be_framed_by_the_smoke_page_only_through_the_runner(self):
        r = self.client.get("/")
        self.assertEqual(r.header("x-frame-options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", r.header("content-security-policy"))

    def test_the_smoke_page_is_not_part_of_the_app(self):
        """Production serves neither the page nor its controls (the page clears storage and drives the server)."""
        for name in ("smoke.html", "smoke.js"):
            self.assertFalse(os.path.exists(os.path.join(REPO, "app", name)), "app/%s must not exist" % name)
        client = self.serve(App(os.path.join(REPO, "examples", "sample"), None, tool_dir=REPO))
        for path in ("/app/smoke.html", "/app/smoke.js", "/smoke/smoke.html", "/smoke/smoke.js", "/smoke/probe.js", "/smoke/ctl"):
            self.assertEqual(client.get(path).status, 404, path)
        self.assertEqual(client.post("/smoke/ctl", json_body={"fail_picks": 1}).status, 404)
        self.assertEqual(client.get("/").header("x-frame-options"), "DENY")

    def test_smoke_js_walks_the_sample_and_clears_only_its_own_prefs(self):
        with open(os.path.join(REPO, "tools", "smoke", "smoke.js"), encoding="utf-8") as f:
            js = f.read()
        from tools import make_sample
        ids = set(make_sample.build())
        for rel in re.findall(r'"((?:assets)/[\w/.]+\.\w+)', js):
            self.assertIn(rel, ids, rel + " is not in the sample")
        self.assertNotIn("localStorage.clear", js)
        self.assertIn("startsWith(prefix)", js)

    def test_the_steps_for_the_audit_fixes_are_there(self):
        with open(os.path.join(REPO, "tools", "smoke", "smoke.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("spills past its card", js)
        self.assertIn("reaches its viewer, with no reload banner", js)
        self.assertIn("a texture that loads after the model is shown is drawn without any input", js)
        for tag in ("(H4)", "(H5)", "(M3)", "(H6)", "(M12)", "(M4)"):
            self.assertIn(tag, js)


class ControlsTest(SmokeServer):
    def ids(self):
        return {i["id"] for i in self.client.get("/api/catalog").json()["items"]}

    def test_a_texture_comes_and_goes_and_the_catalog_cache_follows(self):
        before = self.ids()
        self.ctl(add_texture=True)
        self.assertEqual(self.ids() - before, {"assets/textures/smoke_extra_1.png"})
        self.ctl(remove_extra=True)
        self.assertEqual(self.ids(), before)

    def test_the_failure_it_injects_is_not_a_bad_answer_but_a_real_one_is(self):
        self.ctl(fail_picks=1)
        r = self.client.post("/api/picks", json_body={"id": "assets/sounds/beep.wav", "note": "x"})
        self.assertEqual(r.status, 503)
        self.assertEqual(self.client.post("/api/picks", json_body={"id": "assets/sounds/beep.wav", "note": "x"}).status, 200)
        self.assertEqual(self.log.bad, [])
        self.assertEqual(self.log.injected, [503])
        self.client.get("/files/nope.png")
        self.assertEqual(self.log.bad, ["404 GET /files/nope.png"])

    def test_a_new_token_makes_the_old_one_403_once_as_expected(self):
        old = self.client.token
        self.ctl(rotate_token=True)
        self.assertNotEqual(self.app.token, old)
        body = {"id": "assets/sounds/beep.wav", "note": "x"}
        self.assertEqual(self.client.post("/api/picks", json_body=body).status, 403)
        self.assertEqual(self.log.bad, [])
        self.assertEqual(self.log.injected, [403])
        self.assertEqual(self.client.post("/api/picks", json_body=body, token=self.app.token).status, 200)
        self.assertEqual(self.client.post("/api/picks", json_body=body).status, 403)  # a second one is not expected
        self.assertEqual(len(self.log.bad), 1)

    def test_a_plugins_config_is_edited_and_put_back(self):
        path = os.path.join(self.project, "asset-viewer.json")
        with open(path, encoding="utf-8") as f:
            original = f.read()
        self.ctl(plugin_config={"roblox-anim": {"bodyAim": {"blade": 37}}})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["plugins"]["roblox-anim"]["bodyAim"], {"blade": 37})
        self.ctl(restore_config=True)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), original)

    def test_the_files_served_are_counted_by_prefix(self):
        self.client.get("/files/assets/sounds/beep.wav")
        self.client.get("/files/assets/sounds/beep.wav")
        self.client.get("/files/assets/textures/checker.png")
        self.assertEqual(self.ctl(count="/files/assets/sounds/")["count"], 2)
        self.assertEqual(self.ctl(count="/files/assets/")["count"], 3)

    def test_the_catalog_can_be_made_slow(self):
        import time
        self.ctl(catalog_delay=300)
        t = time.monotonic()
        self.client.get("/api/catalog")
        self.assertGreaterEqual(time.monotonic() - t, 0.28)
        self.ctl(catalog_delay=0)
        t = time.monotonic()
        self.client.get("/api/catalog")
        self.assertLess(time.monotonic() - t, 0.25)


def proc(stdout, stderr="", code=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def page(*lines):
    return '<html><body><pre id="result" hidden="">' + "\n".join(lines).replace("<", "&lt;") + "\n</pre></body></html>"


class Args:
    verbose = False


class JudgeTest(unittest.TestCase):
    def judge(self, p, bad=(), injected=()):
        log = smoke.RequestLog()
        log.bad = list(bad)
        log.injected = list(injected)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = smoke.judge(p, log, Args())
        return code, out.getvalue()

    def test_all_pass(self):
        code, out = self.judge(proc(page("STEP a", "PASS a", "SKIP b: needs WebGL", "WARN careful", "DONE 1 pass, 0 fail, 1 skip, 0 errors")))
        self.assertEqual(code, 0)
        self.assertIn("smoke: ok (1 steps passed, 1 skipped", out)

    def test_a_failed_step_fails(self):
        code, out = self.judge(proc(page("PASS a", "FAIL b: broke &", "DONE 1 pass, 1 fail, 0 skip, 0 errors")))
        self.assertEqual(code, 1)
        self.assertIn("FAIL b: broke &", out)

    def test_a_page_error_fails_even_when_every_step_passed(self):
        code, _ = self.judge(proc(page("PASS a", "ERROR console.error: x", "DONE 1 pass, 0 fail, 0 skip, 1 errors")))
        self.assertEqual(code, 1)

    def test_a_page_that_did_not_finish_names_the_last_step(self):
        code, out = self.judge(proc(page("STEP a", "PASS a", "STEP b")))
        self.assertEqual(code, 1)
        self.assertIn("'b'", out)

    def test_no_result_at_all(self):
        code, out = self.judge(proc("<html></html>", code=1))
        self.assertEqual(code, 1)
        self.assertIn("no #result", out)

    def test_a_4xx_from_the_server_fails(self):
        code, out = self.judge(proc(page("PASS a", "DONE 1 pass, 0 fail, 0 skip, 0 errors")), bad=["404 GET /files/x.png"])
        self.assertEqual(code, 1)
        self.assertIn("404 GET /files/x.png", out)

    def test_a_browser_console_error_fails(self):
        for line in ('[1:2:1105/060120.430:ERROR:CONSOLE(12)] "Failed to load resource: 404", source: x (12)',
                     '[1:2:1105/060120.430:ERROR:CONSOLE:12] "boom", source: y (12)'):
            code, _ = self.judge(proc(page("DONE 0 pass, 0 fail, 0 skip, 0 errors"), stderr=line))
            self.assertEqual(code, 1, line)
        code, _ = self.judge(proc(page("DONE 0 pass, 0 fail, 0 skip, 0 errors"), stderr="[1:2:3:ERROR:chrome\\installer\\x.cc:1] unrelated"))
        self.assertEqual(code, 0)

    def test_the_console_line_of_a_failure_made_on_purpose_is_no_error_but_only_that_many(self):
        line = '[1:2:1105/060120.430:ERROR:CONSOLE(0)] "Failed to load resource: the server responded with a status of 503 (Service Unavailable)", source: x (0)'
        done = page("DONE 0 pass, 0 fail, 0 skip, 0 errors")
        self.assertEqual(self.judge(proc(done, stderr=line), injected=[503])[0], 0)
        self.assertEqual(self.judge(proc(done, stderr=line + "\n" + line), injected=[503])[0], 1)
        self.assertEqual(self.judge(proc(done, stderr=line), injected=[403])[0], 1)
        self.assertEqual(self.judge(proc(done, stderr=line))[0], 1)

    def test_the_request_log_catches_404s_but_not_the_favicon(self):
        log = smoke.RequestLog()
        app = App(os.path.join(REPO, "examples", "sample"), None, tool_dir=REPO)
        server = Server(app, 0)
        server.RequestHandlerClass = log.handler_class()
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        client = helpers.Client(server, app.token)
        client.get("/files/nope.png")
        client.get("/favicon.ico")
        client.get("/api/hello")
        self.assertEqual(log.bad, ["404 GET /files/nope.png"])
        self.assertEqual(log.count, 3)


@unittest.skipUnless(os.environ.get("AV_SMOKE") and smoke.find_browser(), "set AV_SMOKE=1 (and have Chrome or Edge) to run the browser smoke test")
class BrowserTest(unittest.TestCase):
    def test_sample_in_a_browser_without_webgl(self):
        self.assertEqual(smoke.main(["--no-webgl"]), 0)


if __name__ == "__main__":
    unittest.main()
