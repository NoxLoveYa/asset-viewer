# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one table of file types: what the catalog lists, what /files/ serves from the project, what the tool's own folders
serve, and each type's Content-Type.

A project file is served only when its extension is here: every listed type (a core kind), plus the data types that are
served but never listed (the sidecars models and plugins read). The two sets cannot drift apart: `SERVED_EXTS` is
`KINDS` plus `DATA_EXTS`.
"""
import os

JSON = "application/json; charset=utf-8"
TEXT = "text/plain; charset=utf-8"
BINARY = "application/octet-stream"

# extension -> (kind, Content-Type): files the catalog lists
LISTED = {
    ".fbx": ("model", BINARY), ".glb": ("model", "model/gltf-binary"), ".gltf": ("model", "model/gltf+json"), ".obj": ("model", TEXT),
    ".png": ("texture", "image/png"), ".jpg": ("texture", "image/jpeg"), ".jpeg": ("texture", "image/jpeg"),
    ".webp": ("texture", "image/webp"), ".gif": ("texture", "image/gif"), ".svg": ("texture", "image/svg+xml"),
    ".ogg": ("sound", "audio/ogg"), ".mp3": ("sound", "audio/mpeg"), ".wav": ("sound", "audio/wav"), ".flac": ("sound", "audio/flac"),
}
# extension -> Content-Type: served from the project, never listed (model sidecars and data, a model's .mtl and .bin)
DATA = {".json": JSON, ".txt": TEXT, ".mtl": TEXT, ".bin": BINARY}
# extension -> Content-Type: the tool's own folders only (app, vendor, themes, plugin client files)
TOOL = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
}

KINDS = {ext: kind for ext, (kind, _) in LISTED.items()}
MODEL_EXTS = tuple(ext for ext, kind in KINDS.items() if kind == "model")
MAP_EXTS = (".png", ".jpg", ".jpeg", ".webp")  # the textures a model's PBR maps may be
DATA_EXTS = frozenset(DATA)
SERVED_EXTS = frozenset(KINDS) | DATA_EXTS
CONTENT_TYPES = {**{ext: mime for ext, (_, mime) in LISTED.items()}, **DATA, **TOOL}
IMAGE_EXTS = frozenset(ext for ext, kind in KINDS.items() if kind == "texture")
# what the tool's own folders serve: its types, plus images (icons) and the data types (a vendor manifest)
TOOL_EXTS = frozenset(TOOL) | IMAGE_EXTS | DATA_EXTS
# what a theme folder may serve: stylesheet, fonts, images
THEME_EXTS = frozenset((".css", ".woff2", ".woff", ".ttf", ".otf")) | IMAGE_EXTS


def content_type(path):
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), BINARY)
