#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vendor the pinned three.js files and the aero theme's fonts, and verify them by sha256.

    python tools/vendor_three.py            fetch what the manifests list (hash-checked); first run builds them
    python tools/vendor_three.py --verify   hash the files on disk against the manifests (no network)
    python tools/vendor_three.py --update   re-crawl the imports of ENTRIES and rewrite the manifests (after a bump)

Dev tool only: the viewer itself never needs the network. Standard library only.
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
THREE_VERSION = "0.170.0"
THREE_BASE = "https://cdn.jsdelivr.net/npm/three@%s/" % THREE_VERSION
THREE_DIR = ROOT / "vendor" / "three"
# The modules the viewer imports by name; everything they import by a relative path is found by the crawl.
THREE_ENTRIES = [
    "build/three.module.js",
    "examples/jsm/controls/OrbitControls.js",
    "examples/jsm/environments/RoomEnvironment.js",
    "examples/jsm/loaders/FBXLoader.js",
    "examples/jsm/loaders/GLTFLoader.js",
    "examples/jsm/loaders/OBJLoader.js",
]
THREE_EXTRA = ["LICENSE"]

FONT_VERSIONS = {"titillium-web": "5.3.0", "roboto-mono": "5.3.0"}
FONT_BASE = "https://cdn.jsdelivr.net/npm/@fontsource/%s@%s/"
FONT_DIR = ROOT / "themes" / "aero" / "fonts"
# (package, file in the package, name here). The licence text of each package is joined into OFL.txt.
FONT_FILES = [
    ("titillium-web", "files/titillium-web-latin-300-normal.woff2", "titillium-web-300.woff2"),
    ("titillium-web", "files/titillium-web-latin-400-normal.woff2", "titillium-web-400.woff2"),
    ("titillium-web", "files/titillium-web-latin-600-normal.woff2", "titillium-web-600.woff2"),
    ("roboto-mono", "files/roboto-mono-latin-400-normal.woff2", "roboto-mono-400.woff2"),
]
FONT_LICENCES = [("titillium-web", "Titillium Web"), ("roboto-mono", "Roboto Mono")]
OFL_NAME = "OFL.txt"

IMPORT = re.compile(r"""(?:\bimport|\bexport)\s*(?:[^;'"]*?\sfrom\s*)?['"]([^'"]+)['"]|\bimport\(\s*['"]([^'"]+)['"]""")
FETCH_TIMEOUT = 60


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "asset-viewer-vendor"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read()


def relative_imports(text, rel):
    """The relative module paths `text` (the file at `rel`) imports, as paths under the package root."""
    base = "http://x/" + rel
    found = []
    for m in IMPORT.finditer(text):
        spec = m.group(1) or m.group(2)
        if spec.startswith("."):
            found.append(urljoin(base, spec)[len("http://x/"):])
    return found


def crawl(entries):
    """Fetch entries and everything they import by a relative path; returns {path: bytes}."""
    out, todo = {}, list(entries)
    while todo:
        rel = todo.pop(0)
        if rel in out:
            continue
        print("  fetch", rel)
        out[rel] = fetch(THREE_BASE + rel)
        if rel.endswith(".js"):
            todo.extend(relative_imports(out[rel].decode("utf-8"), rel))
    return out


def write_all(folder, files):
    for rel, data in files.items():
        dest = folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def licence_text():
    parts = []
    for pkg, label in FONT_LICENCES:
        parts.append("%s\n%s\n\n%s" % (label, "=" * len(label), fetch(FONT_BASE % (pkg, FONT_VERSIONS[pkg]) + "LICENSE").decode("utf-8").strip()))
    return ("\n\n\n".join(parts) + "\n").encode("utf-8")


def build_three(manifest_path, update):
    if update or not manifest_path.exists():
        print("three.js %s: crawling" % THREE_VERSION)
        files = crawl(THREE_ENTRIES)
        for rel in THREE_EXTRA:
            files[rel] = fetch(THREE_BASE + rel)
        write_manifest(manifest_path, {"package": "three", "version": THREE_VERSION, "source": THREE_BASE, "files": files})
    else:
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if manifest.get("version") != THREE_VERSION:
            raise SystemExit("%s is for %s, not %s: run with --update" % (manifest_path, manifest.get("version"), THREE_VERSION))
        files = {}
        for rel, digest in manifest["files"].items():
            if (THREE_DIR / rel).exists() and sha256((THREE_DIR / rel).read_bytes()) == digest:
                continue
            print("  fetch", rel)
            data = fetch(THREE_BASE + rel)
            if sha256(data) != digest:
                raise SystemExit("%s: downloaded hash differs from the manifest" % rel)
            files[rel] = data
    write_all(THREE_DIR, files)


def build_fonts(manifest_path, update):
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() and not update else None
    files = {}
    for pkg, remote, name in FONT_FILES:
        want = manifest["files"][name] if manifest else None
        if want and (FONT_DIR / name).exists() and sha256((FONT_DIR / name).read_bytes()) == want:
            continue
        print("  fetch", name)
        data = fetch(FONT_BASE % (pkg, FONT_VERSIONS[pkg]) + remote)
        if want and sha256(data) != want:
            raise SystemExit("%s: downloaded hash differs from the manifest" % name)
        files[name] = data
    want = manifest["files"][OFL_NAME] if manifest else None
    if not (want and (FONT_DIR / OFL_NAME).exists() and sha256((FONT_DIR / OFL_NAME).read_bytes()) == want):
        text = licence_text()
        if want and sha256(text) != want:
            raise SystemExit("%s: downloaded hash differs from the manifest" % OFL_NAME)
        files[OFL_NAME] = text
    if not manifest:
        everything = {n: (files.get(n) or (FONT_DIR / n).read_bytes()) for n in [f[2] for f in FONT_FILES] + [OFL_NAME]}
        write_manifest(manifest_path, {"package": "fontsource", "versions": FONT_VERSIONS, "files": everything})
    write_all(FONT_DIR, files)


def write_manifest(path, doc):
    doc = dict(doc)
    doc["files"] = {rel: sha256(data) for rel, data in sorted(doc["files"].items())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", "utf-8")


def verify(folder, manifest_path):
    """Problems of `folder` against its manifest: missing, changed and unlisted files."""
    if not manifest_path.exists():
        return ["%s: no manifest" % manifest_path.relative_to(ROOT)]
    manifest = json.loads(manifest_path.read_text("utf-8"))
    problems = []
    for rel, digest in manifest["files"].items():
        p = folder / rel
        if not p.exists():
            problems.append("missing: %s" % p.relative_to(ROOT))
        elif sha256(p.read_bytes()) != digest:
            problems.append("changed: %s" % p.relative_to(ROOT))
    listed = set(manifest["files"])
    for p in folder.rglob("*"):
        if p.is_file() and p != manifest_path and p.relative_to(folder).as_posix() not in listed:
            problems.append("unlisted: %s" % p.relative_to(ROOT))
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="check the files on disk against the manifests")
    mode.add_argument("--update", action="store_true", help="re-crawl and rewrite the manifests")
    args = ap.parse_args()
    three_manifest, font_manifest = THREE_DIR / "manifest.json", FONT_DIR / "manifest.json"
    if not args.verify:
        print("three.js")
        build_three(three_manifest, args.update)
        print("fonts")
        build_fonts(font_manifest, args.update)
    problems = verify(THREE_DIR, three_manifest) + verify(FONT_DIR, font_manifest)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print("vendor files match their manifests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
