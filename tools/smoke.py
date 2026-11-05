# SPDX-License-Identifier: AGPL-3.0-or-later
"""Client smoke test: serve a project, open the smoke page in headless Chrome or Edge and read its verdict.

    python tools/smoke.py [--no-webgl] [--browser PATH] [--verbose]

The page (tools/smoke/smoke.html and smoke.js) mounts every view of the project, drives the review board and runs the real
app (app/app.js) in frames to check what the audit fixed. It writes PASS / FAIL / SKIP / ERROR lines into #result. The
exit code is 0 only when there is no FAIL, no console error, no uncaught error or CSP violation on the page, no 4xx or 5xx
answer from the server (but a favicon, and the failures this runner injects on purpose), and the page ran to its end.
`--no-webgl` turns every step that needs WebGL into SKIP (for runners without a software GL). It always runs on a temp copy
of examples/sample, never on another project, so no picks are written into the repository or into anyone's data.

The smoke page is not part of the app: this runner mounts it, with a few controls the tests need, through a handler
subclass of its own (`/smoke/smoke.html`, `/smoke/smoke.js`, `/smoke/ctl`). The viewer itself serves none of that.
"""
import argparse
import glob
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from viewer.app import App  # noqa: E402
from viewer.filetypes import CONTENT_TYPES, TOOL_EXTS  # noqa: E402
from viewer.http import JSON_TYPE, Handler, Server  # noqa: E402
from viewer.plugins import HttpError  # noqa: E402
from viewer.static import csp_for  # noqa: E402

SAMPLE = os.path.join(REPO, "examples", "sample")
SMOKE_DIR = os.path.join(REPO, "tools", "smoke")
SMOKE_FILES = {"/smoke/smoke.html": "smoke.html", "/smoke/smoke.js": "smoke.js", "/smoke/probe.js": "probe.js"}
EXTRA_PREFIX = "smoke_extra_"  # files the page adds to the project's textures to change the catalog
CANDIDATES = {
    "nt": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
           r"%LocalAppData%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
           r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles(x86)%\Microsoft\EdgeCore\*\msedge.exe"],
    "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/Applications/Chromium.app/Contents/MacOS/Chromium"],
    "posix": [],
}
NAMES = ["google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge"]
RESULT = re.compile(r'<pre id="result"[^>]*>(.*?)</pre>', re.S)
CONSOLE_ERROR = re.compile(r":ERROR:CONSOLE[:(]\d+\)?\] (.*)")  # the line's shape differs between Chrome and Edge builds
FAILED_LOAD = re.compile(r"Failed to load resource: the server responded with a status of (\d{3})")


def find_browser(explicit=None):
    if explicit:
        return explicit if os.path.isfile(explicit) else shutil.which(explicit)
    env = os.environ.get("CHROME") or os.environ.get("BROWSER_BIN")
    if env and os.path.isfile(env):
        return env
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    for pattern in CANDIDATES.get(os.name if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "posix"), []):
        found = sorted(glob.glob(os.path.expandvars(pattern)))  # a version folder in the path: the newest last
        if found and os.path.isfile(found[-1]):
            return found[-1]
    return None


class RequestLog:
    """Every answer the server gives, so a 404 for a file the page wanted is caught (the browser only logs it). It also
    holds what the page may ask of the handler (`/smoke/ctl`): a slow catalog, failing saves, a new token, a counter of
    the files served, a file added to the catalog. A failure injected on purpose is not a bad answer."""

    def __init__(self):
        self.bad = []
        self.count = 0
        self.lock = threading.Lock()
        self.catalog_delay = 0.0
        self.fail_picks = 0
        self.expect_403 = 0
        self.injected = []  # the statuses the page was made to see: its console says so, which is no error
        self.files = []  # paths of /files/ answered, for the page to count
        self.extra = 0
        self.config_text = None  # the project's config as it was, before the page edited a plugin's part of it

    def handler_class(self):
        log = self

        class Smoke(Handler):
            _smoke_injected = False

            def send_header(self, keyword, value):  # the page shows the app in frames: same origin only
                if keyword == "X-Frame-Options":
                    value = "SAMEORIGIN"
                elif keyword == "Content-Security-Policy":
                    value = value.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
                Handler.send_header(self, keyword, value)

            def log_request(self, code="-", size="-"):
                try:
                    status = int(code)
                except (TypeError, ValueError):
                    return
                with log.lock:
                    log.count += 1
                    if status >= 400 and not self.path.startswith("/favicon.ico") and not self._expected(status):
                        log.bad.append("%d %s %s" % (status, self.command, self.path))

            def _expected(self, status):
                if self._smoke_injected:
                    return True
                if status == 403 and self.path.startswith("/api/picks") and log.expect_403 > 0:
                    log.expect_403 -= 1
                    log.injected.append(403)
                    return True
                return False

            def _dispatch(self, app):
                self._smoke_injected = False  # a connection serves several requests: decided afresh for each
                path = self.path.split("#", 1)[0].partition("?")[0]
                if path.startswith("/smoke/"):
                    return self._smoke(app, path)
                fail = False
                with log.lock:
                    delay = log.catalog_delay
                    if self.command == "GET" and path.startswith("/files/"):
                        log.files.append(unquote(path))
                    if self.command == "POST" and path == "/api/picks" and log.fail_picks > 0:
                        log.fail_picks -= 1
                        log.injected.append(503)
                        fail = True
                if fail:
                    self._smoke_injected = True
                    return self._error(503, "smoke: a save failed on purpose")
                if delay and self.command == "GET" and path == "/api/catalog":
                    time.sleep(delay)
                return Handler._dispatch(self, app)

            def _smoke(self, app, path):
                self._check_host(app)
                if self.command == "GET" and path in SMOKE_FILES:
                    name = SMOKE_FILES[path]
                    if name.endswith(".html"):
                        with open(os.path.join(SMOKE_DIR, name), encoding="utf-8") as f:
                            page = f.read().replace("__VIEWER_TOKEN__", app.token)
                        return self._reply(200, page.encode("utf-8"), CONTENT_TYPES[".html"], csp=csp_for(page))
                    return self._tool_file(SMOKE_DIR, name, TOOL_EXTS - {".html"})
                if self.command == "POST" and path == "/smoke/ctl":
                    n = self._content_length()
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                    self._body_read = True
                    return self._reply(200, json.dumps(log.control(app, body)).encode("utf-8"), JSON_TYPE)
                raise HttpError(404, "not found")

        return Smoke

    def control(self, app, body):
        """What the page asked for through /smoke/ctl; the answer is JSON."""
        out = {"ok": True}
        with self.lock:
            if "catalog_delay" in body:
                self.catalog_delay = float(body["catalog_delay"]) / 1000.0
            if "fail_picks" in body:
                self.fail_picks = int(body["fail_picks"])
            if body.get("rotate_token"):
                app.token = secrets.token_urlsafe(24)  # as after a restart: the token in an open page is stale
                self.expect_403 += 1
            if "count" in body:
                out["count"] = sum(1 for p in self.files if p.startswith(body["count"]))
            if body.get("add_texture"):
                self.extra += 1
                folder = os.path.join(app.project.root, "assets", "textures")
                shutil.copyfile(os.path.join(folder, "checker.png"), os.path.join(folder, "%s%d.png" % (EXTRA_PREFIX, self.extra)))
            if body.get("remove_extra"):
                for path in glob.glob(os.path.join(app.project.root, "assets", "textures", EXTRA_PREFIX + "*.png")):
                    os.remove(path)
            if "plugin_config" in body or body.get("restore_config"):
                path = os.path.join(app.project.root, "asset-viewer.json")
                if self.config_text is None:
                    with open(path, encoding="utf-8") as f:
                        self.config_text = f.read()
                cfg = json.loads(self.config_text)
                for name, extra in (body.get("plugin_config") or {}).items():
                    cfg["plugins"][name].update(extra)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.config_text if body.get("restore_config") else json.dumps(cfg, indent=2))
                app._last_poll = 0  # the page runs on virtual time: the server's look-at-the-file interval would not pass
            if body.get("add_texture") or body.get("remove_extra"):
                app.catalog._at = 0  # the page runs on virtual time: the catalog's one-second cache would never expire for it
        return out


def run(args):
    browser = find_browser(args.browser)
    if not browser:
        print("smoke: no Chrome, Edge or Chromium found; pass --browser PATH or set CHROME", file=sys.stderr)
        return 2
    tmp = tempfile.mkdtemp(prefix="av-smoke-")
    try:
        project = os.path.join(tmp, "project")
        shutil.copytree(SAMPLE, project, ignore=shutil.ignore_patterns("picks.json"))
        app = App(project, None, tool_dir=REPO)
        server = Server(app, 0)
        requests = RequestLog()
        server.RequestHandlerClass = requests.handler_class()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        page = "http://127.0.0.1:%d/smoke/smoke.html%s" % (app.port, "?webgl=0" if args.no_webgl else "")
        cmd = [browser, "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--disable-sync", "--mute-audio", "--hide-scrollbars", "--window-size=1280,900", "--force-device-scale-factor=1",
               "--autoplay-policy=no-user-gesture-required", "--user-data-dir=" + os.path.join(tmp, "profile"),
               "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
               "--enable-logging=stderr", "--v=0", "--virtual-time-budget=%d" % args.budget, "--dump-dom"]
        if args.no_sandbox or os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
            cmd.append("--no-sandbox")
        cmd.append(page)
        if args.verbose:
            print("smoke: " + " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
        except subprocess.TimeoutExpired:
            print("smoke: FAIL the browser did not finish in %d s" % args.timeout)
            return 1
        finally:
            server.shutdown()
            server.server_close()
        return judge(proc, requests, args)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def judge(proc, requests, args):
    match = RESULT.search(proc.stdout)
    lines = html.unescape(match.group(1)).splitlines() if match else []
    fails = [l for l in lines if l.startswith(("FAIL ", "ERROR "))]
    steps = [l[5:] for l in lines if l.startswith("STEP ")]
    done = any(l.startswith("DONE") for l in lines)
    expected = list(requests.injected)  # a browser logs a failed load of a request that was made to fail
    console = []
    for l in proc.stderr.splitlines():
        m = CONSOLE_ERROR.search(l)
        if not m:
            continue
        failed = FAILED_LOAD.search(m.group(1))
        if failed and int(failed.group(1)) in expected:
            expected.remove(int(failed.group(1)))
            continue
        console.append(m.group(1))
    for l in lines:
        if args.verbose or not l.startswith(("STEP ", "WARN ")):
            print("  " + l)
    problems = []
    if not match:
        problems.append("the page wrote no #result (did it load? exit code %s)" % proc.returncode)
    elif not done:
        problems.append("the page did not finish; the last step started was %r" % (steps[-1] if steps else None))
    problems += fails
    problems += ["browser console error: " + c for c in console]
    problems += ["server answered " + b for b in requests.bad]
    if args.verbose and proc.stderr:
        print(proc.stderr[-3000:])
    if problems:
        print("smoke: FAILED")
        for p in problems:
            if p not in fails:
                print("  " + p)
        if not match or not done:
            print(proc.stderr[-2000:])
        return 1
    passed = sum(1 for l in lines if l.startswith("PASS "))
    skipped = sum(1 for l in lines if l.startswith("SKIP "))
    print("smoke: ok (%d steps passed, %d skipped, %d requests)" % (passed, skipped, requests.count))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the client smoke test in headless Chrome or Edge.")
    ap.add_argument("--browser", help="path or name of Chrome, Edge or Chromium (default: look for one; $CHROME)")
    ap.add_argument("--no-webgl", action="store_true", help="skip every step that needs WebGL")
    ap.add_argument("--no-sandbox", action="store_true", help="pass --no-sandbox (set automatically on CI)")
    ap.add_argument("--budget", type=int, default=120000, help="virtual time the page gets, in ms (default 120000)")
    ap.add_argument("--timeout", type=int, default=300, help="wall-clock limit for the browser, in s (default 300)")
    ap.add_argument("--verbose", action="store_true", help="print every step and the browser's log")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
