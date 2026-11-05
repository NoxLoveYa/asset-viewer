// SPDX-License-Identifier: AGPL-3.0-or-later
// Texture and VFX view: the image on a chosen background, fit / 1:1 / 4x, tiled 3x3; a VFX sheet named *_<cols>x<rows>
// (Roblox flipbook layouts 2x2, 4x4, 8x8) also plays as a flipbook.
import { el, seg, button, sep, pref, setPref, oneOf, fmtBytes, url } from "../ui.js";

const FLIP = /_(\d+)x(\d+)$/;
const MAX_FIT_SCALE = 4; // a small flipbook frame is shown at most this large when fitting
const TILES = 3; // the tile check repeats the image TILES x TILES

// The loaded <img> of src (rejects when it can't be shown). Resolves on load, not on decode(), which can stall in a tab
// that isn't painting.
const loadImage = (src) => new Promise((resolve, reject) => {
  const img = new Image();
  img.onload = () => resolve(img);
  img.onerror = () => reject(new Error("image did not load"));
  img.src = src;
});

export async function mount(item, ctx) {
  let bg = pref("image.bg", "checker", oneOf("dark", "checker", "light", "add"));
  let zoom = pref("image.zoom", "fit", oneOf("fit", "1", "4"));
  let tile = pref("image.tile", false);
  let play = true;
  let fps = pref("image.fps", 30, oneOf(15, 30, 60));
  let current, timer, token = 0;

  const box = el("div", "img2d");
  ctx.view2d.append(box);

  const bgSeg = seg([["dark", "Dark"], ["checker", "Checker"], ["light", "Light"], ["add", "Additive"]], bg, (v) => { bg = v; setPref("image.bg", v); draw(); }, { label: "Background" });
  const zoomSeg = seg([["fit", "Fit"], ["1", "1:1"], ["4", "4x"]], zoom, (v) => { zoom = v; setPref("image.zoom", v); draw(); }, { label: "Zoom" });
  const tileBtn = button("Tile", () => { tile = !tile; setPref("image.tile", tile); tileBtn.set(tile); draw(); }, { key: "T", on: tile });
  const playBtn = button("Play", () => { play = !play; draw(); }, { key: "Space", on: true });
  const fpsSeg = seg([[15, "15"], [30, "30"], [60, "60 fps"]], fps, (v) => { fps = v; setPref("image.fps", v); draw(); }, { label: "Frame rate" });
  const flipBits = [sep(), playBtn, fpsSeg];
  ctx.bar.append(bgSeg, sep(), zoomSeg, tileBtn, ...flipBits);

  function draw() {
    clearInterval(timer);
    const img = current;
    if (!img) return;
    const flip = img.flip;
    box.className = "img2d bg-" + bg + (zoom !== "fit" ? " zoom scroll" : "");
    flipBits.forEach((b) => (b.hidden = !flip));
    playBtn.set(play);
    playBtn.label(play ? "Pause" : "Play");
    tileBtn.hidden = !!flip;
    const scale = zoom === "fit" ? null : +zoom;
    let node;
    if (flip && play) {
      // one frame of the sheet at a time, stepped by background position
      const [cols, rows] = flip;
      const w = img.naturalWidth / cols, h = img.naturalHeight / rows;
      const s = scale || Math.min(box.clientWidth / w, box.clientHeight / h, MAX_FIT_SCALE);
      node = el("div", "pic");
      Object.assign(node.style, { width: w * s + "px", height: h * s + "px", backgroundImage: `url("${img.src}")`,
        backgroundSize: `${cols * 100}% ${rows * 100}%`, imageRendering: scale ? "pixelated" : "" });
      let f = 0;
      const show = () => {
        const c = f % cols, r = Math.floor(f / cols);
        node.style.backgroundPosition = `${cols > 1 ? (c / (cols - 1)) * 100 : 0}% ${rows > 1 ? (r / (rows - 1)) * 100 : 0}%`;
        f = (f + 1) % (cols * rows);
      };
      show();
      timer = setInterval(show, 1000 / fps);
    } else if (tile && !flip) {
      node = el("div", "tile pic");
      node.style.backgroundImage = `url("${img.src}")`;
      if (scale) Object.assign(node.style, { width: img.naturalWidth * TILES * scale + "px", backgroundSize: img.naturalWidth * scale + "px" });
    } else {
      node = el("img", "pic");
      node.alt = item.name;
      node.src = img.src;
      if (scale) node.style.width = img.naturalWidth * scale + "px";
    }
    box.replaceChildren(node);
  }

  async function update(next) {
    const t = ++token;
    let img;
    try {
      img = await loadImage(url(next.path, next.mtime));
    } catch {
      if (t === token) { item = next; current = null; box.replaceChildren(el("div", "note", "Can't show this file")); ctx.meta(fmtBytes(next.bytes)); }
      return;
    }
    if (t !== token) return;
    item = next;
    const m = item.name.match(FLIP);
    img.flip = item.kind === "vfx" && m ? [+m[1], +m[2]] : null;
    current = img;
    const ext = item.path.split(".").pop().toUpperCase();
    ctx.meta([`${img.naturalWidth} × ${img.naturalHeight}`, img.flip && `flipbook ${img.flip[0]}x${img.flip[1]}`, fmtBytes(item.bytes), ext].filter(Boolean).join("  ·  "));
    draw();
  }

  const onResize = () => current?.flip && zoom === "fit" && draw();
  addEventListener("resize", onResize);
  await update(item);
  return {
    update,
    destroy() { token++; clearInterval(timer); removeEventListener("resize", onResize); },
    key(e) {
      if (e.key === " " && current?.flip) { play = !play; draw(); return true; }
      if (e.key === "t" || e.key === "T") { tileBtn.click(); return true; }
    },
  };
}
