# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate examples/sample deterministically (standard library only): a few KB of textures, a 4x4 VFX sheet, two
sounds, OBJ and glTF models, a review file and the config. Run `python tools/make_sample.py` to rewrite it;
tests/test_sample.py fails when the committed sample differs from what this writes.

Everything is integer arithmetic, uncompressed PNG data and a fixed byte order, so the output is identical on every
platform and zlib build.

The roblox-anim plugin is on in the sample: `rigs/box.json` is a box rig with two joints and
`assets/animations/wave.json` a clip for it (formats: plugins/roblox-anim/README.md).
"""
import argparse
import base64
import io
import json
import os
import struct
import wave
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(os.path.dirname(HERE), "examples", "sample")


def png(w, h, rows):
    """An RGBA PNG; `rows` is a list of h lists of (r, g, b, a). Stored (uncompressed) deflate blocks: stable bytes."""
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b""))


def checker(size, cell, a, b):
    return png(size, size, [[a if ((x // cell) + (y // cell)) % 2 == 0 else b for x in range(size)] for y in range(size)])


def atlas_color():
    quad = {(0, 0): (96, 140, 70, 255), (1, 0): (196, 170, 120, 255), (0, 1): (130, 130, 135, 255), (1, 1): (110, 80, 55, 255)}
    return png(16, 16, [[quad[(x // 8, y // 8)] for x in range(16)] for y in range(16)])


def flat_normal():
    return png(16, 16, [[(128, 128, 255, 255)] * 16 for _ in range(16)])


def spark_sheet():
    """A 4x4 flipbook of 16x16 frames: a ring that grows and fades."""
    size = 16
    rows = [[(0, 0, 0, 0)] * (size * 4) for _ in range(size * 4)]
    for f in range(16):
        ox, oy = (f % 4) * size, (f // 4) * size
        ring = f * f * 2 + 10
        for y in range(size):
            for x in range(size):
                d2 = (2 * x - 15) ** 2 + (2 * y - 15) ** 2
                alpha = max(0, 255 - abs(d2 - ring) * 6) * (16 - f) // 16
                if alpha:
                    rows[oy + y][ox + x] = (255, 170 - f * 8, 40, alpha)
    return png(size * 4, size * 4, rows)


def tone(period, length, amp):
    """A fading triangle wave as 16-bit mono 8 kHz WAV bytes (integers only)."""
    half = period // 2
    samples = []
    for i in range(length):
        phase = i % period
        v = phase if phase < half else period - phase  # 0..half
        samples.append((v * 2 - half) * (amp // half) * (length - i) // length)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buf.getvalue()


def box_obj(name, sx, sy, sz):
    """An OBJ box with 24 vertices (own normals and UVs per face)."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    faces = [  # normal, four corners counter-clockwise seen from outside
        ((0, 0, 1), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
        ((1, 0, 0), [(hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
        ((0, 1, 0), [(-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
    ]
    uv = [(0, 0), (1, 0), (1, 1), (0, 1)]
    lines = ["# asset-viewer sample", "o %s" % name]
    for n, corners in faces:
        lines += ["v %.4f %.4f %.4f" % c for c in corners]
        lines += ["vn %d %d %d" % n] * 4
        lines += ["vt %d %d" % t for t in uv]
    for f in range(6):
        b = f * 4 + 1
        lines.append("f " + " ".join("%d/%d/%d" % (b + k, b + k, b + k) for k in range(4)))
    return ("\n".join(lines) + "\n").encode("ascii")


def tetra_gltf():
    """A glTF with the buffer embedded as a data URI: one tetrahedron with smooth normals."""
    k = 0.57735026
    pos = [(0.5, 0.5, 0.5), (0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (-0.5, -0.5, 0.5)]
    nrm = [(k if x > 0 else -k, k if y > 0 else -k, k if z > 0 else -k) for x, y, z in pos]
    idx = [0, 1, 2, 0, 3, 1, 0, 2, 3, 1, 3, 2]
    blob = b"".join(struct.pack("<3f", *p) for p in pos) + b"".join(struct.pack("<3f", *n) for n in nrm) \
        + b"".join(struct.pack("<H", i) for i in idx)
    doc = {
        "asset": {"version": "2.0", "generator": "asset-viewer sample"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "tetra"}],
        "meshes": [{"name": "tetra", "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"name": "teal", "pbrMetallicRoughness": {"baseColorFactor": [0.1, 0.6, 0.6, 1], "metallicFactor": 0, "roughnessFactor": 0.6}}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]},
            {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 12, "type": "SCALAR"}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 48, "target": 34962},
                        {"buffer": 0, "byteOffset": 48, "byteLength": 48, "target": 34962},
                        {"buffer": 0, "byteOffset": 96, "byteLength": 24, "target": 34963}],
        "buffers": [{"byteLength": len(blob), "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii")}],
    }
    return text(doc)


def text(obj):
    return (json.dumps(obj, indent=2) + "\n").encode("utf-8")


def cframe(x, y, z):
    """A CFrame as the 12 numbers of CFrame:GetComponents(): position, then the identity rotation."""
    return [x, y, z, 1, 0, 0, 0, 1, 0, 0, 0, 1]


def box_rig():
    """A rig in the format roblox-rig-export writes: parts (size, colour, rest CFrame from the root) and joints
    (part1 -> {parent, c0, c1}). A base, an arm on top of it and a hand on top of the arm: two joints, Arm and Hand."""
    return {
        "root": "Base",
        "parts": {
            "Base": {"size": [2, 0.5, 2], "color": [120, 120, 130], "cf": cframe(0, 0, 0)},
            "Arm": {"size": [0.4, 1.5, 0.4], "color": [80, 150, 200], "cf": cframe(0, 1, 0)},
            "Hand": {"size": [0.6, 0.5, 0.6], "color": [230, 170, 70], "cf": cframe(0, 2, 0)},
        },
        "joints": {
            "Arm": {"parent": "Base", "c0": cframe(0, 0.25, 0), "c1": cframe(0, -0.75, 0)},
            "Hand": {"parent": "Arm", "c0": cframe(0, 0.75, 0), "c1": cframe(0, -0.25, 0)},
        },
    }


def wave_clip():
    """One looped clip on the box rig: the arm sways about Z, the hand nods about X. A joint's value is its Transform,
    [x, y, z, qx, qy, qz, qw]; the quaternions are fixed tables so no float maths decides the bytes."""
    sway = [(0, 1), (0.1305, 0.9914), (0.2588, 0.9659), (0.1305, 0.9914), (0, 1), (-0.1305, 0.9914), (-0.2588, 0.9659), (-0.1305, 0.9914)]
    nod = [(0, 1), (0.2588, 0.9659), (0.5, 0.866), (0.2588, 0.9659), (0, 1), (-0.2588, 0.9659), (-0.5, 0.866), (-0.2588, 0.9659)]
    frames = [{"Arm": [0, 0, 0, 0, 0, s[0], s[1]], "Hand": [0, 0, 0, n[0], 0, 0, n[1]]} for s, n in zip(sway, nod)]
    return {"Wave": {"fps": 12, "looped": True, "frames": frames}}


def build():
    """{relative path: bytes} of the whole sample."""
    config = {
        "title": "Asset Viewer sample",
        "roots": [{"path": "assets", "label": ""}],
        "materials": {"assets/models/crate*.obj": {"*": {"set": "assets/textures/atlas"}}},
        "plugins": {"roblox-anim": {"rigsDir": "rigs", "defaultRig": "box"}},
    }
    review = {"reviews": [{
        "title": "Sample: choose a few things",
        "about": "A review lists candidates for you to pick between. Open this board with R or #review.",
        "groups": [
            {"id": "Sample.Alert", "label": "Alert sound", "about": "Which alert plays when a job finishes?", "items": [
                {"id": "assets/sounds/beep.wav", "name": "Beep", "by": "sample", "note": "Longer, lower."},
                {"id": "assets/sounds/blip.wav", "name": "Blip", "by": "sample", "note": "Short and high."}]},
            {"id": "Sample.Box", "label": "Box", "about": "Which box ships?", "items": [
                {"id": "assets/models/cube.obj", "name": "Cube", "by": "sample", "note": "Own texture maps, with a Red variant."},
                {"id": "assets/models/crate.obj", "name": "Crate", "by": "sample", "note": "Uses the shared atlas."},
                {"id": "assets/textures/checker.png", "name": "Checker", "by": "sample", "note": "For comparison only.", "ref": True}]},
        ]}]}
    cell_a, cell_b = (240, 240, 240, 255), (60, 90, 160, 255)
    return {
        "asset-viewer.json": text(config),
        "review.json": text(review),
        "assets/textures/checker.png": checker(32, 4, cell_a, cell_b),
        "assets/textures/atlas_Color.png": atlas_color(),
        "assets/textures/atlas_Normal.png": flat_normal(),
        "assets/vfx/spark_4x4.png": spark_sheet(),
        "assets/sounds/beep.wav": tone(20, 2400, 12000),
        "assets/sounds/blip.wav": tone(12, 1200, 9000),
        "assets/models/cube.obj": box_obj("cube", 1, 1, 1),
        "assets/models/cube_Color.png": checker(16, 2, (220, 220, 220, 255), (90, 90, 90, 255)),
        "assets/models/cube_Red_Color.png": checker(16, 2, (230, 90, 80, 255), (120, 40, 40, 255)),
        "assets/models/crate.obj": box_obj("crate", 1.2, 0.8, 1),
        "assets/models/tetra.gltf": tetra_gltf(),
        "rigs/box.json": text(box_rig()),
        "assets/animations/wave.json": text(wave_clip()),
    }


def write(out):
    for rel, data in build().items():
        path = os.path.join(out, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write examples/sample.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="target folder (default: examples/sample)")
    args = ap.parse_args(argv)
    write(args.out)
    print("wrote %d files to %s" % (len(build()), args.out))


if __name__ == "__main__":
    main()
