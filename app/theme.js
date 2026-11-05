// SPDX-License-Identifier: AGPL-3.0-or-later
// The theme's tokens for JS: the same custom properties the CSS uses (themes/aero/theme.css defines every one, a project
// theme overrides any), read live, so a theme switch reaches the 3D stage and the waveforms too.
// A public module: plugins import it as "viewer/theme.js".

const listeners = new Set();
const colours = new Map(); // css text -> parsed colour (a value never changes meaning, so no invalidation)
const warned = new Set();
let probe;

// The token's text: token("fg") reads --fg.
export const token = (name) => getComputedStyle(document.documentElement).getPropertyValue("--" + name).trim();

// A number token (an intensity); `fallback` when it is missing or not a number.
export function num(name, fallback) {
  const v = parseFloat(token(name));
  return Number.isFinite(v) ? v : fallback;
}

// Any CSS colour syntax -> { css, r, g, b, a } (r g b 0-255, a 0-1), normalised by the browser's canvas.
export function color(name) {
  const css = token(name);
  let c = colours.get(css);
  if (!c) {
    c = parse(css);
    if (!c) {
      if (!warned.has(name)) { warned.add(name); console.warn(`theme: --${name} is not a colour (${JSON.stringify(css)})`); }
      c = { css: "magenta", r: 255, g: 0, b: 255, a: 1 }; // an unmistakable "this token is broken"
    } else colours.set(css, c);
  }
  return c;
}

function parse(css) {
  probe ||= document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  // An invalid value leaves the previous fillStyle in place: assigned after two different ones, a valid colour reads the
  // same both times and an invalid one reads back whichever came before.
  const read = (before) => { probe.fillStyle = before; probe.fillStyle = css; return probe.fillStyle; };
  const s = read("transparent");
  if (s !== read("white")) return null;
  let m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(s);
  if (m) return { css, r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16), a: 1 };
  m = /^rgba?\(\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\s*\)$/.exec(s);
  if (m) return { css, r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  // a wide-gamut or colour() form: read the pixel
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = css;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data;
  return { css, r, g, b, a: a / 255 };
}

// fn() runs after the theme stylesheet was swapped; returns the unsubscribe.
export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of [...listeners]) {
    try { fn(); } catch (e) { console.error("theme listener:", e); }
  }
}

// ---------- live reload ----------
// /api/config carries themeVersion (the theme file's mtime). On a change the project theme's <link> is swapped for a
// cache-busted one; once it has loaded the old one goes and the listeners fire. The baseline (aero) is the tool's own.
let version = null;

export function setThemeVersion(next) {
  if (next === version) return;
  const first = version === null;
  version = next;
  if (first) return; // the page loaded with the current file
  const old = document.querySelector('link[data-theme="project"]');
  if (!old) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.dataset.theme = "project";
  link.href = "/theme/theme.css?v=" + encodeURIComponent(next);
  link.addEventListener("load", () => {
    document.querySelectorAll('link[data-theme="project"]').forEach((l) => l !== link && l.remove());
    emit();
  });
  link.addEventListener("error", () => { link.remove(); console.warn("theme: the theme file did not load"); });
  old.after(link);
}
