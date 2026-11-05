// SPDX-License-Identifier: AGPL-3.0-or-later
// Small DOM helpers in the viewer's look: element, button, segmented control, stepper, icon, formats, stored prefs.
// A public module: plugins import it as "viewer/ui.js".

export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// A caps button; `key` shows its key cap. A button given `on` is a toggle (aria-pressed); .set(on) toggles the focus look.
export function button(label, onClick, { key, on, title } = {}) {
  const b = el("button", "btn");
  b.type = "button";
  const text = el("span", null, label);
  b.append(text);
  if (key) b.append(el("span", "cap", key));
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  b.set = (v) => {
    b.classList.toggle("on", !!v);
    if (on !== undefined) b.setAttribute("aria-pressed", v ? "true" : "false");
  };
  b.label = (s) => (text.textContent = s);
  b.set(on);
  return b;
}

// Segmented control: options [[value, label]...]; .set(value), .value, .cycle().
export function seg(options, value, onChange, { label } = {}) {
  const box = el("div", "seg");
  box.setAttribute("role", "group");
  if (label) box.setAttribute("aria-label", label);
  const btns = options.map(([v, text]) => {
    const b = el("button", null, text);
    b.type = "button";
    b.addEventListener("click", () => { box.set(v); onChange(v); });
    box.append(b);
    return [v, b];
  });
  box.set = (v) => {
    box.value = v;
    btns.forEach(([bv, b]) => {
      b.classList.toggle("on", bv === v);
      b.setAttribute("aria-pressed", bv === v ? "true" : "false");
    });
  };
  // the next option, as a key press does; fires onChange
  box.cycle = () => {
    const at = options.findIndex(([v]) => v === box.value);
    const next = options[(at + 1) % options.length][0];
    box.set(next);
    onChange(next);
  };
  box.set(value);
  return box;
}

// Stepper ‹ VALUE ›: options [[value, label]...]; .set(value).
export function stepper(prefix, options, value, onChange) {
  const box = el("div", "stepper");
  box.setAttribute("role", "group");
  if (prefix) box.setAttribute("aria-label", prefix);
  const prev = el("button", null, "‹");
  const next = el("button", null, "›");
  prev.type = next.type = "button";
  prev.setAttribute("aria-label", "Previous");
  next.setAttribute("aria-label", "Next");
  const label = el("span");
  if (prefix) label.append(el("small", null, prefix));
  const text = el("b");
  label.append(text);
  let i = Math.max(0, options.findIndex(([v]) => v === value));
  const show = () => (text.textContent = options[i][1]);
  const step = (d) => { i = (i + d + options.length) % options.length; show(); onChange(options[i][0]); };
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  box.set = (v) => { i = Math.max(0, options.findIndex(([o]) => o === v)); show(); };
  box.append(prev, label, next);
  show();
  return box;
}

export const sep = () => el("span", "sep");

// Makes e a keyboard-operable control: a button role, in the tab order, Enter and Space activate it.
export function activatable(e, fn) {
  e.setAttribute("role", "button");
  e.tabIndex = 0;
  e.addEventListener("click", fn);
  e.addEventListener("keydown", (k) => {
    if (k.key !== "Enter" && k.key !== " ") return;
    k.preventDefault();
    k.stopPropagation();
    fn();
  });
  return e;
}

// An icon from the theme's --icon-<name> token, drawn as a mask in the text colour.
export function icon(name) {
  const e = el("span", "ico");
  e.setAttribute("aria-hidden", "true");
  e.style.setProperty("--ico", `var(--icon-${String(name).replace(/^(--)?icon-/, "")})`);
  return e;
}

export function fmtBytes(n) {
  return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(0) + " KB" : (n / 1048576).toFixed(1) + " MB";
}

// A stored setting shared by every view of a kind (background, speed, autoplay...), namespaced by the project's
// prefsPrefix. A blocked or corrupt store falls back to the default.
let prefix = "viewer.";
export const configurePrefs = (p) => { prefix = p || "viewer."; };
// The stored value must be of the fallback's type (`null` and an array are not an object; a stored `null` is no number);
// `valid(v)` replaces that check where the fallback has no type (null) or only some values make sense.
const sameType = (v, fallback) => v !== null && typeof v === typeof fallback && Array.isArray(v) === Array.isArray(fallback);
export function pref(key, fallback, valid) {
  try {
    const raw = localStorage.getItem(prefix + key);
    if (raw == null) return fallback;
    const v = JSON.parse(raw);
    return (valid ? valid(v) : fallback === null || sameType(v, fallback)) ? v : fallback;
  } catch { return fallback; }
}
export const oneOf = (...values) => (v) => values.includes(v);
export const isString = (v) => typeof v === "string";
export const isBoolean = (v) => typeof v === "boolean";

// obj[key] only when it is obj's own property: a kind or mesh named "constructor" finds nothing, not a function
export const own = (obj, key) => (obj && Object.hasOwn(obj, key) ? obj[key] : undefined);
export function setPref(key, v) {
  try { localStorage.setItem(prefix + key, JSON.stringify(v)); } catch { /* storage blocked or full: the setting just won't stick */ }
}

// decodeURIComponent that answers "" for a malformed escape (a hand-edited link) instead of throwing
export function safeDecode(s) {
  try { return decodeURIComponent(s); } catch { return ""; }
}

// Every map file a model draws with: its Stem_<Map> sets and the shared sets of its `materials` rules.
export const mapPaths = (item) => [
  ...Object.values(item.maps || {}).flatMap((v) => Object.values(v)),
  ...Object.values(item.materials || {}).flatMap((m) => Object.values(m.maps || {})),
];

// URL of a project file, cache-busted by its mtime so a re-baked file loads fresh.
export const url = (path, mtime) => "/files/" + path.split("/").map(encodeURIComponent).join("/") + (mtime ? "?v=" + mtime : "");

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
