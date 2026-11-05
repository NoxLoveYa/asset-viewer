# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

import helpers
from helpers import REPO
from tools import make_sample

SAMPLE = os.path.join(REPO, "examples", "sample")
GENERATED_AT_RUN = {"picks.json", ".viewer-token"}


def tree(base):
    out = {}
    for folder, _dirs, files in os.walk(base):
        for name in files:
            if name in GENERATED_AT_RUN or name.endswith(".tmp"):
                continue
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, base).replace(os.sep, "/")] = f.read()
    return out


class SampleTest(helpers.ProjectCase):
    def test_make_sample_regenerates_the_committed_sample_byte_for_byte(self):
        out = tempfile.mkdtemp(prefix="av-sample-")
        self.addCleanup(shutil.rmtree, out, True)
        make_sample.write(out)
        committed, fresh = tree(SAMPLE), tree(out)
        self.assertEqual(sorted(committed), sorted(fresh))
        for rel in fresh:
            self.assertEqual(committed[rel], fresh[rel], rel + " differs: run python tools/make_sample.py")

    def test_generation_is_deterministic(self):
        self.assertEqual(make_sample.build(), make_sample.build())

    def test_sample_is_small(self):
        self.assertLess(sum(len(v) for v in tree(SAMPLE).values()), 64 * 1024)

    def test_check_passes_on_the_sample_from_the_command_line(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(REPO, "serve.py"), "--project", SAMPLE, "--check"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("ok: "), r.stdout)
        self.assertIn("1 anim, 3 model, 2 sound, 5 texture, 1 vfx (1 models on shared materials)", r.stdout)

    def test_every_kind_is_in_the_sample(self):
        from viewer.app import App
        app = App(SAMPLE, None, tool_dir=REPO)
        items = app.catalog.get()[2]["items"]
        self.assertEqual(sorted({i["kind"] for i in items}), ["anim", "model", "sound", "texture", "vfx"])
        (clip,) = [i for i in items if i["kind"] == "anim"]
        self.assertEqual((clip["id"], clip["rig"], clip["frames"], clip["looped"]), ("assets/animations/wave.json#Wave", "box", 8, True))
        rig = app.project.read_json("rigs/box.json")
        self.assertEqual(sorted(rig["joints"]), ["Arm", "Hand"])
        frame = app.project.read_json("assets/animations/wave.json")["Wave"]["frames"][0]
        self.assertEqual(sorted(frame), sorted(rig["joints"]))
        self.assertTrue(all(len(v) == 7 for v in frame.values()))
        formats = {os.path.splitext(i["path"])[1] for i in items if i["kind"] == "model"}
        self.assertEqual(formats, {".obj", ".gltf"})
        crate = [i for i in items if i["name"] == "crate"][0]
        self.assertEqual(crate["materials"]["*"]["maps"]["Color"], "assets/textures/atlas_Color.png")
        cube = [i for i in items if i["name"] == "cube"][0]
        self.assertEqual(sorted(cube["maps"]), ["Base", "Red"])

    def test_review_file_points_at_real_items(self):
        from viewer.app import App
        app = App(SAMPLE, None, tool_dir=REPO)
        ids = {i["id"] for i in app.catalog.get()[2]["items"]}
        _, payload = app.review().read()
        self.assertIsNone(payload["error"])
        used = [it["id"] for r in payload["reviews"] for g in r["groups"] for it in g["items"]]
        self.assertTrue(used)
        self.assertEqual([u for u in used if u not in ids], [])

    def test_the_sample_is_served_over_http(self):
        from viewer.app import App
        client = self.serve(App(SAMPLE, None, tool_dir=REPO))
        for item in client.get("/api/catalog").json()["items"]:
            r = client.get("/files/" + item["path"])
            self.assertEqual((r.status, len(r.body)), (200, item["bytes"]), item["path"])
        self.assertEqual(len(client.get("/api/review").json()["reviews"]), 1)

    def test_wav_and_png_are_valid(self):
        import wave
        import zlib
        data = make_sample.build()
        with wave.open(os.path.join(SAMPLE, "assets", "sounds", "beep.wav")) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 8000, 2400))
        png = data["assets/vfx/spark_4x4.png"]
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        idat_len = int.from_bytes(png[33:37], "big")
        raw = zlib.decompress(png[41:41 + idat_len])
        self.assertEqual(len(raw), 64 * (1 + 64 * 4))


if __name__ == "__main__":
    unittest.main()
