// SPDX-License-Identifier: AGPL-3.0-or-later
// Loaded into the app's frame by the smoke page (the app does not load it): what the page's own modules hold, for the page
// to read, a way to hold images back, and the stage's frame by hand.
import { configOf } from "viewer/plugins.js";
import { getStage } from "viewer/views/stage.js";

window.__pluginConfig = (name) => JSON.parse(JSON.stringify(configOf(name) ?? null));

// While `window.__holdImages` is on, an image does not start loading when its src is set but when `__releaseImages()` is
// called. The smoke page runs on virtual time, which stands still while a request is in flight: a slow server would only
// make the page wait, and every image would be there before the first frame. Holding them is how an image comes after
// the model was drawn.
window.__holdImages = false;
const held = [];
const srcSetter = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
Object.defineProperty(HTMLImageElement.prototype, "src", {
  ...srcSetter,
  set(value) {
    if (window.__holdImages) held.push(() => srcSetter.set.call(this, value));
    else srcSetter.set.call(this, value);
  },
});
window.__releaseImages = () => { window.__holdImages = false; held.splice(0).forEach((go) => go()); };

// getStage() resizes, and a resize asks for a frame: fetched once, so that reading the stage does not draw it
let stage = null;
const stageNow = () => (stage ||= getStage(document.getElementById("stage3d")));

// One frame of the stage's loop: it draws only if something asked for a frame, as the browser's own frames do.
window.__draw = () => stageNow().draw();

// The maps of the meshes on the stage: how many, how many have their image, and how many of those the GPU has the
// current version of (an upload happens only in a render, so a map that loaded and was never drawn is not uploaded).
window.__stageTextures = () => {
  const { scene, renderer } = stageNow();
  const maps = new Set();
  scene.traverse((o) => {
    if (!o.isMesh) return;
    for (const m of [].concat(o.material)) for (const v of Object.values(m)) if (v?.isTexture) maps.add(v);
  });
  const loaded = [...maps].filter((t) => t.image && (t.image.complete ?? true));
  const uploaded = loaded.filter((t) => renderer.properties.get(t).__version === t.version);
  return { uuids: [...maps].map((t) => t.uuid), total: maps.size, loaded: loaded.length, uploaded: uploaded.length, frames: renderer.info.render.frame };
};
