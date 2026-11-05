# SPDX-License-Identifier: AGPL-3.0-or-later
"""Takes rigs exported from Roblox Studio (export_rig.luau) into the rigs folder of the roblox-anim plugin.

POST rig/<name>                 the rig JSON      -> <rigsDir>/<name>.json
POST rig/<name>/<file>.png      an image, either  ?w=&h=&part=&parts=  one base64 chunk of raw RGBA rows, or
                                                  ?thumb=<asset id>    Roblox's public thumbnail of the asset
Studio sends no Origin, so the routes take its requests without the token (token="optional-if-no-origin"); a browser
page still needs it. Every size is capped, partial uploads expire, writes stay inside the rigs folder and are atomic.
"""
import base64
import binascii
import json
import posixpath
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from viewer.plugins import HttpError, PluginError, json_response
from viewer.project import PathError, WriteError, split_rel

NAME = r"[A-Za-z_][A-Za-z0-9_]{0,40}"  # a bare name, never a path
FILE = r"[A-Za-z0-9_]{1,60}"
THUMBS = "https://thumbnails.roblox.com/v1/assets?assetIds=%s&returnPolicy=PlaceHolder&size=420x420&format=Png"
THUMB_HOST_SUFFIX = ".rbxcdn.com"
MAX_RIG_JSON = 8 << 20  # bytes
MAX_CHUNK_BODY = 1 << 20  # bytes of base64 in one POST (Studio posts at most 1 MB)
MAX_CHUNK_RAW = MAX_CHUNK_BODY // 4 * 3
MAX_SIDE = 4096
MAX_PIXELS = MAX_SIDE * MAX_SIDE
MAX_PARTS = 64
MAX_PENDING = 8  # partial uploads held at once
MAX_PENDING_BYTES = 192 << 20  # raw bytes held across them
PENDING_TTL = 300.0  # seconds a partial upload may wait for its next chunk
MAX_THUMB = 16 << 20
MAX_THUMB_INFO = 64 << 10
FETCH_TIMEOUT = 20
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_RIG_PARTS = 4096

clock = time.monotonic  # tests replace it


def png(w, h, rgba):
    """An RGBA PNG from raw rows (no filter), standard library only."""
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(kind, d):
        return struct.pack(">I", len(d)) + kind + d + struct.pack(">I", zlib.crc32(kind + d) & 0xFFFFFFFF)
    return (PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class Uploads:
    """Partial image uploads: a pinned size and part count per image, a time limit, and caps on how many and how big."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}  # (name, file) -> {w, h, parts, got: {part: bytes}, size, at}

    def _purge(self, now):
        for key in [k for k, u in self.pending.items() if now - u["at"] > PENDING_TTL]:
            del self.pending[key]

    def add(self, key, w, h, part, parts, raw):
        """Store one chunk. Returns the assembled RGBA bytes when the image is complete, else None."""
        with self.lock:
            now = clock()
            self._purge(now)
            u = self.pending.get(key)
            if u is None:
                if len(self.pending) >= MAX_PENDING:
                    raise HttpError(429, "too many uploads in progress: wait a few minutes")
                u = self.pending[key] = {"w": w, "h": h, "parts": parts, "got": {}, "size": 0, "at": now}
            if (u["w"], u["h"], u["parts"]) != (w, h, parts):
                del self.pending[key]
                raise HttpError(400, "w, h and parts must stay the same for every chunk of an image: upload dropped")
            old = len(u["got"].get(part, b""))
            if u["size"] - old + len(raw) > w * h * 4 or sum(x["size"] for x in self.pending.values()) - old + len(raw) > MAX_PENDING_BYTES:
                del self.pending[key]
                raise HttpError(400, "more data than a %dx%d image holds: upload dropped" % (w, h))
            u["got"][part] = raw
            u["size"] += len(raw) - old
            u["at"] = now
            if len(u["got"]) < parts:
                return None
            del self.pending[key]
            data = b"".join(u["got"][i] for i in range(parts))
        if len(data) != w * h * 4:
            raise HttpError(400, "the chunks hold %d bytes, a %dx%d RGBA image needs %d" % (len(data), w, h, w * h * 4))
        return data


def _https_host(url, ok_host):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not ok_host(host) or p.username or p.password or p.port not in (None, 443):
        raise HttpError(502, "address not allowed: only https://thumbnails.roblox.com and https://*.rbxcdn.com")
    return url


def check_info_url(url):
    return _https_host(url, lambda host: host == "thumbnails.roblox.com")


UPLOADS = Uploads()  # module-level: `register` runs again when the config is edited, the partial uploads must survive that


def check_image_url(url):
    """The check for a thumbnail's image address (and any redirect on the way): https, a *.rbxcdn.com host, no credentials."""
    return _https_host(url, lambda host: host.endswith(THUMB_HOST_SUFFIX) and len(host) > len(THUMB_HOST_SUFFIX))


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check):
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(urllib.parse.urljoin(req.full_url, newurl))
        return urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)


def fetch_bytes(url, limit, check):
    """GET a URL that passes `check` (so does every redirect), reading at most `limit` bytes; HttpError 502 on failure."""
    check(url)
    opener = urllib.request.build_opener(_Redirects(check))
    try:
        with opener.open(url, timeout=FETCH_TIMEOUT) as r:
            data = r.read(limit + 1)
    except HttpError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HttpError(502, "thumbnail fetch failed: %s" % getattr(e, "reason", e))
    if len(data) > limit:
        raise HttpError(502, "the thumbnail is larger than %d bytes" % limit)
    return data


def thumbnail(asset_id):
    """Roblox's PNG thumbnail of an asset id; the thumbnail must be Completed (not the placeholder)."""
    info = fetch_bytes(THUMBS % asset_id, MAX_THUMB_INFO, check_info_url)
    try:
        entry = json.loads(info.decode("utf-8"))["data"][0]
        state, url = entry["state"], entry.get("imageUrl")
    except (ValueError, KeyError, IndexError, TypeError):
        raise HttpError(502, "unexpected answer from the thumbnails service")
    if state != "Completed" or not url:
        raise HttpError(409, "the thumbnail of %s is %s, not Completed" % (asset_id, state))
    data = fetch_bytes(check_image_url(url), MAX_THUMB, check_image_url)
    if not data.startswith(PNG_MAGIC):
        raise HttpError(502, "the thumbnail is not a PNG")
    return data


def _int(query, key, lo, hi, default=None):
    raw = query.get(key, default)
    if raw is None:
        raise HttpError(400, "%s is required" % key)
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise HttpError(400, "%s must be a whole number" % key)
    if not lo <= v <= hi:
        raise HttpError(400, "%s must be %d-%d" % (key, lo, hi))
    return v


def register(api):
    anim = api.required_config("roblox-anim")
    rigs = anim.get("rigsDir") or posixpath.join(api.project.config_dir, "rigs")
    if not isinstance(rigs, str) or not rigs:
        raise PluginError("plugin roblox-rig-export: roblox-anim rigsDir must be a folder path")
    api.writes(rigs)
    uploads = UPLOADS

    def target(*parts):
        """The project path of a file under the rigs folder; a name Windows reserves (con, nul, aux...) is a 400."""
        rel = posixpath.join(rigs, *parts)
        try:
            split_rel(rel)
        except PathError:
            raise HttpError(400, "%s is not a usable file name" % "/".join(parts))
        return rel

    def write(rel, data):
        try:
            api.project.write_atomic(rel, data)
        except (WriteError, PathError) as e:
            raise HttpError(500, str(e))
        return json_response({"wrote": rel, "bytes": len(data)})

    def save_rig(req):
        rig = req.json(dict)
        parts, joints = rig.get("parts"), rig.get("joints")
        if not (isinstance(parts, dict) and isinstance(joints, dict)):
            raise HttpError(400, "a rig needs 'parts' and 'joints' objects")
        if len(parts) > MAX_RIG_PARTS or not all(isinstance(p, dict) for p in parts.values()) \
                or not all(isinstance(j, dict) for j in joints.values()):
            raise HttpError(400, "parts and joints must be objects (at most %d parts)" % MAX_RIG_PARTS)
        if rig.get("root") is not None and rig["root"] not in parts:
            raise HttpError(400, "root names no part")
        try:
            data = json.dumps(rig, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except ValueError:
            raise HttpError(400, "the rig holds NaN or Infinity, which JSON cannot carry")
        return write(target(req.params["name"] + ".json"), data)

    def save_image(req):
        name, file = req.params["name"], req.params["file"]
        rel = target(name, file + ".png")
        q = req.query
        if "thumb" in q:
            if not re.fullmatch(r"\d{1,20}", q["thumb"]):
                raise HttpError(400, "thumb must be an asset id")
            return write(rel, thumbnail(q["thumb"]))
        w, h = _int(q, "w", 1, MAX_SIDE), _int(q, "h", 1, MAX_SIDE)
        if w * h > MAX_PIXELS:
            raise HttpError(400, "the image is larger than %d pixels" % MAX_PIXELS)
        parts = _int(q, "parts", 1, MAX_PARTS, 1)
        part = _int(q, "part", 0, parts - 1, 0)
        try:
            raw = base64.b64decode(req.body.strip(), validate=True)
        except (binascii.Error, ValueError):
            raise HttpError(400, "the body must be base64")
        if not raw or len(raw) > MAX_CHUNK_RAW:
            raise HttpError(400, "a chunk holds 1 to %d bytes" % MAX_CHUNK_RAW)
        rgba = uploads.add((name, file), w, h, part, parts, raw)
        if rgba is None:
            return json_response({"part": part, "of": parts})
        return write(rel, png(w, h, rgba))

    texts = ("text/plain", "application/octet-stream", "application/json")
    api.route("POST", "rig/<name:%s>" % NAME, save_rig, max_body=MAX_RIG_JSON, token="optional-if-no-origin")
    api.route("POST", "rig/<name:%s>/<file:%s>.png" % (NAME, FILE), save_image, max_body=MAX_CHUNK_BODY,
              token="optional-if-no-origin", content_types=texts)
    api.check(lambda report: report.ok("rig export writes to %s" % rigs))
