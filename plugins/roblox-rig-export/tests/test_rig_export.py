# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import http.server
import json
import os
import shutil
import sys
import threading
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PLUGIN))
sys.path.insert(0, os.path.join(REPO, "tests"))
import helpers  # noqa: E402

BASE = "/api/plugins/roblox-rig-export/"
RIG = {"root": "Root", "parts": {"Root": {"size": [1, 1, 1], "color": [1, 2, 3], "cf": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]}},
       "joints": {}}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def module():
    return sys.modules["viewer_plugin_roblox_rig_export"]


def b64(raw):
    return base64.b64encode(raw)


def decode_png(data):
    """(w, h, rgba) of a PNG this plugin wrote (RGBA, unfiltered rows)."""
    assert data.startswith(PNG_MAGIC)
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    n = int.from_bytes(data[33:37], "big")
    raw = zlib.decompress(data[41:41 + n])
    rows = [raw[y * (w * 4 + 1) + 1:(y + 1) * (w * 4 + 1)] for y in range(h)]
    return w, h, b"".join(rows)


class RigExportCase(helpers.ProjectCase):
    anim_dir = None

    def setUp(self):
        super().setUp()
        shutil.copytree(PLUGIN, os.path.join(self.tool_dir, "plugins", "roblox-rig-export"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        # a stand-in for roblox-anim: only what this plugin reads of it
        self.put(self.tool_dir, "plugins/roblox-anim/plugin.json",
                 {"name": "roblox-anim", "apiVersion": 1, "requires": [], "defaults": {"rigsDir": None}})
        self.client = self.serve(self.app())

    def app(self, cfg=None, **kw):
        cfg = cfg or {"roots": ["assets"], "plugins": {"roblox-anim": {"rigsDir": "rigs"}, "roblox-rig-export": {}}}
        return self.make_app(cfg, **kw)

    def rigs(self, *rel):
        return os.path.join(self.project_dir, "rigs", *rel)

    def rig(self, name, body, **kw):
        return self.client.post(BASE + "rig/" + name, body=json.dumps(body).encode(), token=None, **kw)

    def chunk(self, name, file, raw, w, h, part, parts, **kw):
        q = "w=%d&h=%d&part=%d&parts=%d" % (w, h, part, parts)
        return self.client.post("%srig/%s/%s.png?%s" % (BASE, name, file, q), body=b64(raw), token=None, content_type="text/plain", **kw)

    def read(self, *rel):
        with open(self.rigs(*rel), "rb") as f:
            return f.read()


class PngTest(unittest.TestCase):
    def test_png_round_trip(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("rig_server_standalone", os.path.join(PLUGIN, "server.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rgba = bytes((i * 7) % 256 for i in range(3 * 2 * 4))
        w, h, back = decode_png(mod.png(3, 2, rgba))
        self.assertEqual((w, h, back), (3, 2, rgba))


class RigJsonTest(RigExportCase):
    def test_rig_is_written_compact_and_atomically(self):
        r = self.rig("hero", RIG)
        self.assertEqual((r.status, r.json()), (200, {"wrote": "rigs/hero.json", "bytes": len(self.read("hero.json"))}))
        self.assertEqual(json.loads(self.read("hero.json")), RIG)
        self.assertNotIn(b" ", self.read("hero.json"))
        self.assertEqual([n for n in os.listdir(self.rigs()) if n.endswith(".tmp")], [])
        self.rig("hero", {**RIG, "root": "Root", "extra": 1})  # a second export replaces it
        self.assertEqual(json.loads(self.read("hero.json"))["extra"], 1)

    def test_studio_needs_no_token_but_a_browser_does(self):
        body = json.dumps(RIG).encode()
        port = self.client.port
        self.assertEqual(self.client.post(BASE + "rig/a", body=body, token=None).status, 200)
        self.assertEqual(self.client.post(BASE + "rig/a", body=body, token=None, headers={"Origin": "http://127.0.0.1:%d" % port}).status, 403)
        self.assertEqual(self.client.post(BASE + "rig/a", body=body, token=None, headers={"Origin": "http://evil.example"}).status, 403)
        self.assertEqual(self.client.post(BASE + "rig/a", body=body, token=None, headers={"Sec-Fetch-Site": "cross-site"}).status, 403)
        self.assertEqual(self.client.post(BASE + "rig/a", body=body, host="evil.example").status, 421)
        self.assertEqual(self.client.post(BASE + "rig/a", body=body).status, 200)  # the page, with its token

    def test_bad_rigs(self):
        bad = [[], "x", {}, {"parts": {}}, {"parts": [], "joints": {}}, {"parts": {}, "joints": []}, {"parts": {"a": 1}, "joints": {}},
               {"parts": {"a": {}}, "joints": {"b": 5}}, {"root": "Nope", "parts": {"a": {}}, "joints": {}}]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.rig("r", body).status, 400)
        self.assertEqual(self.client.post(BASE + "rig/r", body=b"{", token=None).status, 400)
        self.assertFalse(os.path.exists(self.rigs("r.json")))

    def test_nan_and_infinity_are_a_400_not_a_broken_file(self):
        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(literal=literal):
                body = b'{"root": "a", "parts": {"a": {"size": [%s, 1, 1]}}, "joints": {}}' % literal
                r = self.client.post(BASE + "rig/r", body=body, token=None)
                self.assertEqual(r.status, 400)
                self.assertIn("NaN", r.json()["error"])
        self.assertFalse(os.path.exists(self.rigs("r.json")))

    def test_content_type_and_size_caps(self):
        self.assertEqual(self.client.post(BASE + "rig/r", body=json.dumps(RIG).encode(), token=None, content_type="text/plain").status, 415)
        head = ("POST %srig/r HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                % (BASE, self.client.port, (8 << 20) + 1))
        self.assertEqual(self.client.raw_status(head.encode()), 413)
        big = {"root": "a", "parts": {"a": {"pad": "x" * (1 << 20)}}, "joints": {}}
        self.assertEqual(self.rig("big", big).status, 200)  # 1 MiB is fine

    def test_names_are_bare_names(self):
        for name in ("1bad", "a-b", "a.b", "%2e%2e", "..%2fx", "a%2fb", "x" * 42, "a%5cb", "%C3%A9"):
            with self.subTest(name=name):
                r = self.client.post(BASE + "rig/" + name, body=json.dumps(RIG).encode(), token=None)
                self.assertEqual(r.status, 404)
        self.assertEqual(self.rig("x" * 41, RIG).status, 200)
        self.assertEqual(sorted(os.listdir(self.rigs())), ["x" * 41 + ".json"])

    def test_names_windows_reserves_are_a_400(self):
        for name in ("con", "CON", "nul", "aux", "prn", "com1", "lpt9"):
            with self.subTest(name=name):
                self.assertEqual(self.rig(name, RIG).status, 400)
                self.assertEqual(self.chunk(name, "img", b"\x00" * 4, 1, 1, 0, 1).status, 400)
        for file in ("con", "nul", "aux"):
            with self.subTest(file=file):
                self.assertEqual(self.chunk("hero", file, b"\x00" * 4, 1, 1, 0, 1).status, 400)
        self.assertEqual(self.rig("console", RIG).status, 200)  # only the reserved words themselves
        self.assertFalse(os.path.exists(self.rigs("hero")))

    def test_write_error_is_a_clear_500(self):
        with mock.patch("os.replace", side_effect=PermissionError(13, "Access is denied")):
            r = self.rig("r", RIG)
        self.assertEqual(r.status, 500)
        self.assertIn("rigs/r.json", r.json()["error"])
        self.assertEqual([n for n in os.listdir(self.rigs()) if n.endswith(".tmp")], [])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(BASE + "rig/r").status, 405)

    def test_default_rigs_folder_is_beside_the_config(self):
        cfg = {"roots": ["assets"], "plugins": {"roblox-anim": {}, "roblox-rig-export": {}}}
        client = self.serve(self.make_app(cfg, config_path="config/asset-viewer.json", config_arg="config/asset-viewer.json"))
        r = client.post(BASE + "rig/surv", body=json.dumps(RIG).encode(), token=None)
        self.assertEqual(r.json()["wrote"], "config/rigs/surv.json")
        self.assertTrue(os.path.isfile(os.path.join(self.project_dir, "config", "rigs", "surv.json")))

    def test_a_new_rigs_folder_in_roblox_anim_s_config_applies_without_a_restart(self):
        self.assertEqual(self.rig("a", RIG).json()["wrote"], "rigs/a.json")
        path = self.write("asset-viewer.json", {"roots": ["assets"], "plugins": {"roblox-anim": {"rigsDir": "elsewhere/rigs"},
                                                                                "roblox-rig-export": {}}})
        os.utime(path, (os.path.getmtime(path) + 9,) * 2)
        import time
        time.sleep(0.6)
        self.client.get("/api/config")
        self.assertEqual(self.rig("b", RIG).json()["wrote"], "elsewhere/rigs/b.json")
        self.assertTrue(os.path.isfile(os.path.join(self.project_dir, "elsewhere", "rigs", "b.json")))
        self.assertFalse(os.path.exists(self.rigs("b.json")))
        # and the old folder is no longer a write area
        self.assertEqual(self.client.post(BASE + "rig/c", body=json.dumps(RIG).encode(), token=None).json()["wrote"], "elsewhere/rigs/c.json")

    def test_partial_uploads_survive_a_config_reload(self):  # plugin code is never reloaded, so the uploads stay
        rgba = bytes(range(32))
        self.chunk("hero", "img", rgba[0:12], 4, 2, 0, 2)
        path = self.write("asset-viewer.json", {"roots": ["assets"], "title": "Renamed",
                                                "plugins": {"roblox-anim": {"rigsDir": "rigs"}, "roblox-rig-export": {}}})
        os.utime(path, (os.path.getmtime(path) + 9,) * 2)
        self.client.get("/api/config")  # looks at the config
        import time
        time.sleep(0.6)
        self.assertEqual(self.client.get("/api/config").json()["title"], "Renamed")
        r = self.chunk("hero", "img", rgba[12:32], 4, 2, 1, 2)
        self.assertEqual((r.status, decode_png(self.read("hero", "img.png"))[2]), (200, rgba))


class ChunkTest(RigExportCase):
    RGBA = bytes(range(32))  # a 4 x 2 image

    def test_chunks_in_any_order_assemble_an_exact_png(self):
        r = self.chunk("hero", "img", self.RGBA[24:32], 4, 2, 2, 3)
        self.assertEqual((r.status, r.json()), (200, {"part": 2, "of": 3}))
        self.assertFalse(os.path.exists(self.rigs("hero", "img.png")))
        self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 3)
        r = self.chunk("hero", "img", self.RGBA[12:24], 4, 2, 1, 3)
        self.assertEqual((r.status, r.json()["wrote"]), (200, "rigs/hero/img.png"))
        self.assertEqual(decode_png(self.read("hero", "img.png")), (4, 2, self.RGBA))

    def test_single_part_default(self):
        r = self.client.post(BASE + "rig/s/one.png?w=4&h=2", body=b64(self.RGBA), token=None, content_type="text/plain")
        self.assertEqual(r.status, 200)
        self.assertEqual(decode_png(self.read("s", "one.png"))[2], self.RGBA)

    def test_a_resent_part_replaces_the_first(self):
        self.chunk("hero", "img", b"\xff" * 12, 4, 2, 0, 3)
        self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 3)
        self.chunk("hero", "img", self.RGBA[12:24], 4, 2, 1, 3)
        r = self.chunk("hero", "img", self.RGBA[24:32], 4, 2, 2, 3)
        self.assertEqual(r.status, 200)
        self.assertEqual(decode_png(self.read("hero", "img.png"))[2], self.RGBA)

    def test_parameters_are_pinned_by_the_first_chunk(self):
        self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 3)
        for w, h, parts in ((8, 1, 3), (4, 2, 2), (2, 4, 3)):
            self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 3)  # (re)start
            r = self.chunk("hero", "img", self.RGBA[12:24], w, h, 1, parts)
            self.assertEqual(r.status, 400, (w, h, parts))
        r = self.chunk("hero", "img", self.RGBA[12:24], 4, 2, 1, 3)  # the dropped upload starts over
        self.assertEqual(r.json(), {"part": 1, "of": 3})
        self.assertFalse(os.path.exists(self.rigs("hero", "img.png")))

    def test_wrong_total_size_is_refused_and_writes_nothing(self):
        self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 2)
        r = self.chunk("hero", "img", self.RGBA[12:30], 4, 2, 1, 2)  # 30 bytes of 32
        self.assertEqual(r.status, 400)
        self.assertFalse(os.path.exists(self.rigs("hero", "img.png")))

    def test_more_bytes_than_the_image_holds_drops_the_upload(self):
        self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 2)
        r = self.chunk("hero", "img", b"\x00" * 24, 4, 2, 1, 2)
        self.assertEqual(r.status, 400)
        r = self.chunk("hero", "img", self.RGBA[12:32], 4, 2, 1, 2)  # the first chunk was dropped with it
        self.assertEqual(r.json(), {"part": 1, "of": 2})

    def test_parameter_limits(self):
        cases = [("w=0&h=1", 400), ("w=1&h=0", 400), ("w=4097&h=1", 400), ("w=1&h=4097", 400), ("w=4096&h=4097", 400), ("w=x&h=1", 400),
                 ("w=1", 400), ("h=1", 400), ("w=1&h=1&parts=65", 400), ("w=1&h=1&parts=0", 400),
                 ("w=1&h=1&parts=2&part=2", 400), ("w=1&h=1&part=-1", 400), ("w=1.5&h=1", 400), ("w=%20&h=1", 400)]
        for query, want in cases:
            with self.subTest(query=query):
                r = self.client.post("%srig/s/x.png?%s" % (BASE, query), body=b64(b"\x00" * 4), token=None, content_type="text/plain")
                self.assertEqual(r.status, want)
        r = self.client.post(BASE + "rig/s/x.png?w=4096&h=1", body=b64(b"\x00" * 16), token=None, content_type="text/plain")
        self.assertEqual(r.status, 400)  # a 4096 x 1 image is allowed, but 16 bytes are not enough for it

    def test_bad_bodies(self):
        for body in (b"", b"not base64!!", b"AAA", b"=" * 4):
            with self.subTest(body=body):
                r = self.client.post(BASE + "rig/s/x.png?w=1&h=1", body=body, token=None, content_type="text/plain")
                self.assertEqual(r.status, 400)

    def test_chunk_size_cap(self):
        head = ("POST %srig/s/x.png?w=4096&h=4096&parts=64&part=0 HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: text/plain\r\n"
                "Content-Length: %d\r\n\r\n" % (BASE, self.client.port, (1 << 20) + 1))
        self.assertEqual(self.client.raw_status(head.encode()), 413)
        raw = b"\x00" * (module().MAX_CHUNK_RAW - 3)  # under the cap: accepted
        r = self.chunk("s", "big", raw, 4096, 4096, 0, 64)
        self.assertEqual(r.json(), {"part": 0, "of": 64})

    def test_file_names_and_extension(self):
        for tail in ("../x.png", "a-b.png", "a.b.png", "%2e%2e%2fx.png", "x.PNG", "x.jpg", "x", "a%2fb.png", "y" * 61 + ".png"):
            with self.subTest(tail=tail):
                r = self.client.post("%srig/s/%s?w=1&h=1" % (BASE, tail), body=b64(b"\x00" * 4), token=None, content_type="text/plain")
                self.assertEqual(r.status, 404)
        self.assertEqual(os.listdir(self.project_dir), ["asset-viewer.json"])

    def test_content_types(self):
        for ct, want in (("text/plain", 200), ("application/octet-stream", 200), ("application/json", 200), ("text/html", 415)):
            with self.subTest(ct=ct):
                r = self.client.post(BASE + "rig/s/ct.png?w=1&h=1", body=b64(b"\x00" * 4), token=None, content_type=ct)
                self.assertEqual(r.status, want)

    def test_partial_uploads_expire(self):
        now = [1000.0]
        with mock.patch.object(module(), "clock", lambda: now[0]):
            self.chunk("hero", "img", self.RGBA[0:12], 4, 2, 0, 2)
            now[0] += 299
            r = self.chunk("hero", "img", self.RGBA[12:32], 4, 2, 1, 2)
            self.assertEqual(r.status, 200)  # in time: complete
            self.chunk("hero", "late", self.RGBA[0:12], 4, 2, 0, 2)
            now[0] += 301
            r = self.chunk("hero", "late", self.RGBA[12:32], 4, 2, 1, 2)
            self.assertEqual(r.json(), {"part": 1, "of": 2})  # the first chunk expired: starts over, not complete
            self.assertFalse(os.path.exists(self.rigs("hero", "late.png")))

    def test_each_chunk_keeps_the_upload_alive(self):
        now = [0.0]
        with mock.patch.object(module(), "clock", lambda: now[0]):
            for p, cut in enumerate((0, 8, 16, 24)):
                now[0] += 250
                r = self.chunk("hero", "slow", self.RGBA[cut:cut + 8], 4, 2, p, 4)
            self.assertEqual(r.json()["wrote"], "rigs/hero/slow.png")

    def test_at_most_eight_pending_uploads(self):
        now = [0.0]
        with mock.patch.object(module(), "clock", lambda: now[0]):
            for i in range(8):
                self.assertEqual(self.chunk("s", "p%d" % i, self.RGBA[0:12], 4, 2, 0, 2).status, 200)
            self.assertEqual(self.chunk("s", "p8", self.RGBA[0:12], 4, 2, 0, 2).status, 429)
            self.assertEqual(self.chunk("s", "p0", self.RGBA[12:32], 4, 2, 1, 2).status, 200)  # finishing one frees a place
            self.assertEqual(self.chunk("s", "p8", self.RGBA[0:12], 4, 2, 0, 2).status, 200)
            now[0] += 301
            self.assertEqual(self.chunk("s", "p9", self.RGBA[0:12], 4, 2, 0, 2).status, 200)  # the rest expired

    def test_pending_bytes_are_capped(self):
        with mock.patch.object(module(), "MAX_PENDING_BYTES", 20):
            self.assertEqual(self.chunk("s", "a", self.RGBA[0:12], 4, 2, 0, 2).status, 200)
            self.assertEqual(self.chunk("s", "b", self.RGBA[0:12], 4, 2, 0, 2).status, 400)

    def test_two_images_upload_side_by_side(self):
        self.chunk("s", "one", self.RGBA[0:12], 4, 2, 0, 2)
        self.chunk("s", "two", self.RGBA[0:12], 4, 2, 0, 2)
        self.chunk("s", "two", self.RGBA[12:32], 4, 2, 1, 2)
        self.chunk("s", "one", self.RGBA[12:32], 4, 2, 1, 2)
        self.assertEqual(sorted(os.listdir(self.rigs("s"))), ["one.png", "two.png"])

    def test_concurrent_chunks_of_one_image(self):
        results = []

        def send(p):
            results.append(self.chunk("s", "par", self.RGBA[p * 8:p * 8 + 8], 4, 2, p, 4).status)
        threads = [threading.Thread(target=send, args=(p,)) for p in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, [200] * 4)
        self.assertEqual(decode_png(self.read("s", "par.png"))[2], self.RGBA)


class ThumbTest(RigExportCase):
    PNG = PNG_MAGIC + b"fake image bytes"

    def fake(self, state="Completed", image="https://t0.rbxcdn.com/abc.png", data=None, fail=None):
        calls = []

        def fetch(url, limit, check):
            calls.append((url, limit))
            check(url)
            if fail:
                raise module().HttpError(502, fail)
            if url.startswith("https://thumbnails.roblox.com/"):
                return json.dumps({"data": [{"targetId": 1, "state": state, "imageUrl": image}]}).encode()
            return self.PNG if data is None else data
        return mock.patch.object(module(), "fetch_bytes", fetch), calls

    def post(self, query="thumb=123456"):
        return self.client.post("%srig/s/123456.png?%s" % (BASE, query), body=b"", token=None, content_type="text/plain")

    def test_a_completed_thumbnail_is_saved(self):
        patch, calls = self.fake()
        with patch:
            r = self.post()
        self.assertEqual((r.status, r.json()["wrote"]), (200, "rigs/s/123456.png"))
        self.assertEqual(self.read("s", "123456.png"), self.PNG)
        self.assertEqual(calls[0][0], module().THUMBS % "123456")
        self.assertEqual([c[1] for c in calls], [module().MAX_THUMB_INFO, module().MAX_THUMB])

    def test_the_placeholder_is_not_saved(self):
        for state in ("Pending", "Blocked", "Error", "InReview", ""):
            with self.subTest(state=state):
                patch, _ = self.fake(state=state)
                with patch:
                    r = self.post()
                self.assertEqual(r.status, 409)
        self.assertFalse(os.path.exists(self.rigs("s")))

    def test_only_https_rbxcdn_hosts_are_fetched(self):
        bad = ["http://t0.rbxcdn.com/a.png", "https://evil.example/a.png", "https://rbxcdn.com/a.png", "https://t0.rbxcdn.com.evil.example/a.png",
               "https://evilrbxcdn.com/a.png", "ftp://t0.rbxcdn.com/a.png", "file:///etc/passwd", "https://user:pw" + "@t0.rbxcdn.com/a.png",
               "https://t0.rbxcdn.com:8443/a.png", "//t0.rbxcdn.com/a.png", "https://127.0.0.1/a.png", "", None]
        for url in bad:
            with self.subTest(url=url):
                patch, calls = self.fake(image=url)
                with patch:
                    r = self.post()
                self.assertIn(r.status, (409, 502))
                self.assertEqual(len(calls), 1, "only the thumbnails service was asked")
        good, calls = self.fake(image="https://t5.rbxcdn.com/x/y.png?z=1")
        with good:
            self.assertEqual(self.post().status, 200)

    def test_not_a_png_is_refused(self):
        patch, _ = self.fake(data=b"<html>nope</html>")
        with patch:
            r = self.post()
        self.assertEqual(r.status, 502)
        self.assertFalse(os.path.exists(self.rigs("s")))

    def test_fetch_failure_and_odd_answers(self):
        patch, _ = self.fake(fail="thumbnail fetch failed: timed out")
        with patch:
            r = self.post()
        self.assertEqual((r.status, r.json()["error"]), (502, "thumbnail fetch failed: timed out"))

        def garbage(url, limit, check):
            return b"not json" if url.startswith("https://thumbnails") else b""
        with mock.patch.object(module(), "fetch_bytes", garbage):
            self.assertEqual(self.post().status, 502)

        def empty(url, limit, check):
            return b'{"data": []}'
        with mock.patch.object(module(), "fetch_bytes", empty):
            self.assertEqual(self.post().status, 502)

    def test_thumb_must_be_an_asset_id(self):
        for q in ("thumb=abc", "thumb=1;2", "thumb=", "thumb=" + "1" * 21, "thumb=12%20", "thumb=-1", "thumb=1/../2"):
            with self.subTest(q=q):
                patch, calls = self.fake()
                with patch:
                    self.assertEqual(self.post(q).status, 400)
                self.assertEqual(calls, [])


class FetchBytesTest(unittest.TestCase):
    """The real fetch_bytes against a local server: the size cap and the redirect check."""

    @classmethod
    def setUpClass(cls):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/redir":
                    self.send_response(302)
                    self.send_header("Location", "/elsewhere")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = b"x" * (1000 if self.path == "/big" else 10)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        import importlib.util
        spec = importlib.util.spec_from_file_location("rig_server_fetch", os.path.join(PLUGIN, "server.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, REPO)
        spec.loader.exec_module(cls.mod)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_reads_up_to_the_limit(self):
        self.assertEqual(self.mod.fetch_bytes(self.base + "/ok", 10, lambda u: u), b"x" * 10)
        with self.assertRaises(self.mod.HttpError) as cm:
            self.mod.fetch_bytes(self.base + "/big", 999, lambda u: u)
        self.assertEqual(cm.exception.code, 502)
        self.assertEqual(len(self.mod.fetch_bytes(self.base + "/big", 1000, lambda u: u)), 1000)

    def test_every_redirect_goes_through_the_check(self):
        seen = []

        def check(u):
            seen.append(u)
            if u.endswith("/elsewhere"):
                raise self.mod.HttpError(502, "no")
            return u
        with self.assertRaises(self.mod.HttpError):
            self.mod.fetch_bytes(self.base + "/redir", 100, check)
        self.assertEqual(seen, [self.base + "/redir", self.base + "/elsewhere"])

    def test_connection_errors_are_502(self):
        with self.assertRaises(self.mod.HttpError) as cm:
            self.mod.fetch_bytes("http://127.0.0.1:1/x", 10, lambda u: u)
        self.assertEqual(cm.exception.code, 502)

    def test_the_host_checks(self):
        m = self.mod
        for url in ("https://thumbnails.roblox.com/v1/assets?x=1",):
            self.assertEqual(m.check_info_url(url), url)
        for url in ("http://thumbnails.roblox.com/", "https://evil.example/", "https://thumbnails.roblox.com.evil.example/"):
            with self.assertRaises(m.HttpError):
                m.check_info_url(url)


class WiringTest(RigExportCase):
    def test_needs_roblox_anim(self):
        from viewer.plugins import PluginError
        with self.assertRaisesRegex(PluginError, "requires roblox-anim"):
            self.app({"roots": ["assets"], "plugins": {"roblox-rig-export": {}}})

    @unittest.skipUnless(os.path.isfile(os.path.join(REPO, "plugins", "roblox-anim", "plugin.json")), "roblox-anim is not in the repo yet")
    def test_with_the_real_roblox_anim_the_rig_shows_up_in_the_catalog(self):
        self.put(self.project_dir, "asset-viewer.json", {"roots": ["assets"], "plugins": {
            "roblox-anim": {"rigsDir": "rigs"}, "roblox-rig-export": {}}})
        app = helpers.App(self.project_dir, None, tool_dir=REPO)
        client = self.serve(app)
        r = client.post(BASE + "rig/box", body=json.dumps(RIG).encode(), token=None)
        self.assertEqual(r.status, 200)
        app.catalog._at = 0
        rigs = client.get("/api/catalog").json()["plugins"]["roblox-anim"]["rigs"]
        self.assertEqual([x["name"] for x in rigs], ["box"])
        self.assertEqual(client.get("/files/rigs/box.json").json(), RIG)

    def test_check_hook_reports_the_folder(self):
        from viewer.check import run
        out = []
        run(self.app(), out=out.append)
        self.assertIn("ok: rig export writes to rigs", out)


if __name__ == "__main__":
    unittest.main()
