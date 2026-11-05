// SPDX-License-Identifier: AGPL-3.0-or-later
// Asset viewer entry: loads the config and the plugins, polls the catalog, draws the nav (categories, list in folders),
// mounts the view for the selected item, and keeps the picks. Views live in views/*.js (or a plugin's):
// mount(item, ctx) -> { update(item), destroy(), key(e) }. Review mode (review.js, key R, link #review) swaps all of it
// for one board of what the review file puts up for picking.
import { el, icon, activatable, fmtBytes, mapPaths, pref, setPref, configurePrefs, safeDecode, sleep, url, own, isString } from "./ui.js";
import { api, ApiError } from "./api.js";
import * as theme from "./theme.js";
import { registry, addCategory, loadPlugins, updatePluginConfigs, viewLoader } from "./plugins.js";
import { createPicks } from "./picks.js";
import { createViewHost } from "./viewhost.js";
import { mount as mountReview, progress } from "./review.js";

const POLL_MS = 2000;
const POLL_MAX_MS = 15000; // the poll backs off to this while the server can't be reached
const FALLBACK_CATEGORIES = ["model", "texture", "vfx", "sound"];

const modelView = () => import("./views/model.js");
const imageView = () => import("./views/image.js");
const soundView = () => import("./views/sound.js");
addCategory({ kind: "model", label: "Models", icon: "model", view: modelView });
addCategory({ kind: "texture", label: "Textures", icon: "texture", view: imageView });
addCategory({ kind: "vfx", label: "VFX", icon: "vfx", view: imageView });
addCategory({ kind: "sound", label: "Sounds", icon: "sound", view: soundView });

const $ = (s) => document.querySelector(s);
const state = {
  items: [], byId: new Map(), byPath: new Map(), extra: {}, version: "", catalogEtag: undefined, reviewEtag: undefined,
  cats: [], kind: null, q: "", onlyPicked: false, sel: null, lastByKind: {}, visible: [], folders: [], open: new Map(),
  reviews: [], board: null,
};
let config = { title: "Assets", categories: FALLBACK_CATEGORIES, data: {}, model: {} };

const picks = createPicks({
  onChange: () => { dirty.picks = true; queueMicrotask(flush); },
  onError: (msg) => notify("save", msg, "err"),
});

const ctx = {
  view2d: $("#view2d"),
  stage3d: $("#stage3d"),
  bar: $("#bar"),
  meta: (text) => { const m = $("#info .meta"); if (m) m.textContent = text; },
  item: (path) => state.byPath.get(path),
  catalogExtra: (name) => state.extra[name],
  url,
  theme,
  config,
};
const host = createViewHost({ view2d: ctx.view2d, bar: ctx.bar, ctx, loaderFor: viewLoader });

// ---------- notices: a config error, plugin or save problems, catalog warnings ----------
const notices = new Map(); // key -> { lines, level }
function notify(key, lines, level = "warn") {
  const list = [].concat(lines || []).filter(Boolean);
  const had = notices.get(key);
  if (JSON.stringify(had) === JSON.stringify(list.length ? { lines: list, level } : undefined)) return; // no change: don't re-announce
  if (list.length) notices.set(key, { lines: list, level });
  else notices.delete(key);
  const banner = $("#banner");
  banner.replaceChildren(...[...notices.values()].flatMap((n) => n.lines.map((l) => el("div", n.level, l))));
  banner.hidden = notices.size === 0;
}

// ---------- catalog ----------
// What the open item shows: its entry and, for a model, its maps' times (a re-bake may change only those); for a
// plugin's kind also the plugins' catalog data (the rigs an animation plays on).
const CORE_KINDS = ["model", "texture", "vfx", "sound"];
const stamp = (item) => item && JSON.stringify([item, mapPaths(item).map((p) => state.byPath.get(p)?.mtime),
  CORE_KINDS.includes(item.kind) ? null : state.extra]);

const dirty = { picks: false, catalog: false, review: false };
let firstCatalog = true;
let loadedSig = null; // the categories and plugins the page was set up with

function applyCatalog(cat) {
  const was = state.sel ? stamp(state.byId.get(state.sel)) : null; // to tell its re-bake from others'
  state.version = cat.version;
  state.items = cat.items;
  state.byId = new Map(cat.items.map((i) => [i.id, i]));
  state.byPath = new Map();
  for (const i of cat.items) if (!state.byPath.has(i.path)) state.byPath.set(i.path, i);
  state.extra = cat.plugins || {};
  notify("catalog", cat.warnings);
  dirty.catalog = true;
  dirty.rebaked = state.sel && stamp(state.byId.get(state.sel)) !== was;
}

function applyReview(data) {
  state.reviews = data.reviews || [];
  notify("review", data.error, "err");
  dirty.review = true;
}

function applyConfig(next) {
  config = next;
  ctx.config = next;
  document.title = next.title ? `${next.title} · Asset viewer` : "Asset viewer";
  $("#title").textContent = next.title || "Assets";
  $("#title").title = next.title || "";
  theme.setThemeVersion(next.themeVersion);
  notify("config", [next.error && "Config: " + next.error, ...(next.warnings || [])], next.error ? "err" : "warn");
  // plugin code, styles and the categories are set up once, at start: a change to which plugins are on, their files or the
  // categories needs a reload. A plugin's config alone does not: its viewer gets it in place.
  const sig = JSON.stringify([next.categories, (next.plugins || []).map((p) => [p.name, p.client, p.styles])]);
  if (loadedSig === null) loadedSig = sig;
  notify("reload", sig === loadedSig ? null : "The plugins or categories changed: reload the page to apply them.");
  // the open item is shown again, so add-ons prepare with the new config
  if (updatePluginConfigs(next.plugins).length && !firstCatalog && !state.board && state.sel) {
    const item = state.byId.get(state.sel);
    if (item) host.show(item).catch((e) => console.error("config change:", e));
  }
}

function computeCats() {
  const kinds = config.categories?.length ? config.categories : FALLBACK_CATEGORIES;
  state.cats = kinds.map((k) => {
    const c = registry.categories.get(k);
    if (!c) console.warn(`category "${k}" has no view registered (a plugin that failed to load?)`);
    return c;
  }).filter(Boolean);
  state.kind = state.cats[0]?.kind ?? null;
}

let delay = POLL_MS;
let timer = 0;
let polling = false;
const schedule = (ms = delay) => { clearTimeout(timer); timer = setTimeout(poll, ms); };
document.addEventListener("visibilitychange", () => { if (!document.hidden) schedule(0); }); // paused while hidden

async function poll() {
  if (document.hidden || polling) return;
  polling = true;
  const writesAtStart = picks.writes;
  const totalEl = $("#total");
  // the fetching: only a server that cannot be reached is OFFLINE
  const results = await Promise.allSettled([
    api("/api/config"),
    api("/api/catalog", { etag: state.catalogEtag }),
    api("/api/picks"),
    api("/api/review", { etag: state.reviewEtag }),
  ]);
  const offline = results.every((r) => r.status === "rejected" && !(r.reason instanceof ApiError));
  if (offline) {
    totalEl.textContent = "OFFLINE";
    totalEl.classList.add("red");
    delay = Math.min(POLL_MAX_MS, delay * 2);
  } else {
    // the drawing: a bug here is logged and shown as a notice, never mistaken for a lost connection
    try {
      applyPollResults(results, writesAtStart);
      notify("render");
      totalEl.classList.remove("red");
      totalEl.textContent = state.items.length;
      delay = POLL_MS;
    } catch (e) {
      console.error("poll: drawing an update failed:", e);
      notify("render", "The page could not draw an update (see the browser console).", "err");
      delay = Math.min(POLL_MAX_MS, delay * 2);
    }
  }
  polling = false;
  schedule();
}

function applyPollResults(results, writesAtStart) {
  const [cfg, cat, pk, rv] = results;
  if (cfg.status === "fulfilled") applyConfig(cfg.value.data);
  if (cat.status === "fulfilled") {
    notify("catalog-error");
    if (!cat.value.notModified) {
      state.catalogEtag = cat.value.etag;
      if (cat.value.data.version !== state.version) applyCatalog(cat.value.data);
      else notify("catalog", cat.value.data.warnings);
    }
  } else notify("catalog-error", "Catalog: " + cat.reason.message, "err");
  if (pk.status === "fulfilled") { notify("picks"); picks.load(pk.value.data, writesAtStart); }
  else notify("picks", "Picks: " + pk.reason.message, "err");
  if (rv.status === "fulfilled" && !rv.value.notModified) { state.reviewEtag = rv.value.etag; applyReview(rv.value.data); }
  else if (rv.status === "rejected") notify("review", "Review: " + rv.reason.message, "err");
  flush();
}

// Redraws what the last changes touched.
function flush() {
  const d = { ...dirty };
  dirty.picks = dirty.catalog = dirty.review = false;
  dirty.rebaked = false;
  if (!d.picks && !d.catalog && !d.review) return;
  if (d.catalog) {
    renderCats();
    renderList();
    if (firstCatalog) { firstCatalog = false; start(); }
    else if (state.board) state.board.build(state.reviews, state.byId); // files came or went (it only redraws when a used one did)
    else if (state.sel) {
      const item = state.byId.get(state.sel);
      if (!item) clearView();
      // only the open file re-baked reloads in place: any file changing replayed an open sound (Autoplay)
      else if (d.rebaked) host.show(item);
    }
  } else if (d.review && state.board) state.board.build(state.reviews, state.byId);
  else if (d.picks) state.board ? state.board.update() : renderList();
  if (d.picks) renderPick();
  if (d.picks || d.review || d.catalog) renderCats();
}

// #review or #review/<review title as a slug>: the review board, at that review
const reviewLink = () => /^#review(\/|$)/.test(location.hash);
const reviewWant = () => safeDecode(location.hash.slice(8));
const hashId = () => safeDecode(location.hash.slice(1));

// a link's #<id> (or #review), else what was open last time in this browser
function start() {
  if (reviewLink() || (pref("review", false) && !location.hash)) return enterReview();
  const item = state.byId.get(hashId()) || state.byId.get(pref("last", "", isString));
  if (item && state.cats.some((c) => c.kind === item.kind)) return setKind(item.kind, item.id);
  setKind((state.cats.find((c) => state.items.some((i) => i.kind === c.kind)) || state.cats[0])?.kind);
}

// ---------- review mode ----------
function enterReview() {
  if (state.board) return;
  const want = reviewLink() ? reviewWant() : "";
  clearView();
  setPref("review", true);
  history.replaceState(null, "", "#review");
  document.body.classList.add("reviewing");
  $("#review").hidden = false;
  state.board = mountReview({ board: $("#review"), nav: $("#list") },
    { open: (id) => setKind(state.byId.get(id)?.kind, id), picks, want, loaderFor: viewLoader, ctx, stamp, config });
  state.board.build(state.reviews, state.byId);
  renderCats();
}

function leaveReview() {
  if (!state.board) return;
  state.board.destroy();
  state.board = null;
  setPref("review", false);
  document.body.classList.remove("reviewing");
  $("#review").hidden = true;
}

// ---------- nav ----------
function renderCats() {
  const box = $("#cats");
  box.replaceChildren();
  const focus = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  const row = (cls, ico, label, count, cap, fn, key, current) => {
    const r = activatable(el("div", "row" + (current ? " sel" : "")), fn);
    r.dataset.key = key;
    if (current) r.setAttribute("aria-current", "true");
    r.append(icon(ico), el("span", "label", label), el("span", "count", count), el("span", "cap", cap));
    box.append(r);
  };
  if (state.reviews.some((r) => r.groups?.length)) { // the review row, first: what waits for a pick
    const { done, total } = progress(state.reviews, picks.data);
    row("", "review", "Review", `${done}/${total}`, "R", enterReview, "review", !!state.board);
  }
  state.cats.forEach((c, n) => {
    const count = state.items.filter((i) => i.kind === c.kind).length;
    row("", c.icon, c.label, String(count), String(n + 1), () => setKind(c.kind), "cat:" + c.kind, c.kind === state.kind && !state.board);
  });
  if (focus) [...box.children].find((c) => c.dataset.key === focus)?.focus({ preventScroll: true });
}

const catOf = (kind) => registry.categories.get(kind);

// The folder an item is listed under: the server's `dir` label; its key is the label itself.
const dirLabel = (i) => (i.dir ? i.dir.split(/\s*\/\s*/).join(" / ") : "(root)");

// What the list shows of a kind: the search (name, path or folder) and the picks filter.
function matching() {
  const q = state.q.toLowerCase();
  return state.items.filter((i) => i.kind === state.kind
    && (!q || (i.name + " " + i.path + " " + dirLabel(i)).toLowerCase().includes(q))
    && (!state.onlyPicked || picks.data[i.id]?.picked));
}

const rowMeta = (i) => catOf(i.kind)?.rowMeta?.(i) ?? fmtBytes(i.bytes);

// ---------- folders: the list groups items by the folder their file is in ----------
// A folder is open when the user opened it (remembered per kind), and while a search or the picks filter is on.
const filtering = () => !!(state.q || state.onlyPicked);
const openDirs = (kind) => {
  if (!state.open.has(kind)) state.open.set(kind, new Set(pref("open." + kind, [])));
  return state.open.get(kind);
};
function setOpen(keys) {
  state.open.set(state.kind, new Set(keys));
  setPref("open." + state.kind, [...keys]);
}
function openDir(key) {
  if (openDirs(state.kind).has(key)) return false;
  setOpen([...openDirs(state.kind), key]);
  return true;
}
function toggleDir(key) {
  if (filtering()) return;
  const keys = new Set(openDirs(state.kind));
  keys.delete(key) || keys.add(key);
  setOpen(keys);
  renderList();
}
const allDirs = () => [...new Set(state.items.filter((i) => i.kind === state.kind).map((i) => i.dir || ""))];

// Only the rows of open folders are built (textures can be 600+); the scroll and the focus stay where they were.
function renderList() {
  if (state.board) return; // the board draws the nav's list in review mode
  const list = $("#list");
  const scroll = list.scrollTop;
  const focus = list.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  list.replaceChildren();
  state.visible = [];
  state.folders = [];
  const shown = matching();
  if (!shown.length) {
    const e = el("div", "empty");
    e.append(el("b", null, filtering() ? "No match" : "Nothing here yet"),
      el("span", null, filtering() ? "Clear the search or the picks filter." : "Files dropped in the viewer's roots show up here."));
    list.append(e);
    return;
  }
  const dirs = new Map();
  for (const i of shown) {
    const key = i.dir || "";
    if (!dirs.has(key)) dirs.set(key, { key, label: dirLabel(i), items: [] });
    dirs.get(key).items.push(i);
  }
  const open = openDirs(state.kind);
  const selVisible = shown.some((i) => i.id === state.sel);
  for (const f of dirs.values()) {
    const on = filtering() || open.has(f.key);
    const picked = f.items.filter((i) => picks.data[i.id]?.picked).length;
    const h = activatable(el("div", "ghead fold" + (on ? " open" : "")), () => toggleDir(f.key));
    h.dataset.key = "d:" + f.key;
    h.title = f.label;
    h.setAttribute("aria-expanded", on ? "true" : "false");
    h.append(el("span", "chev", on ? "▾" : "▸"), el("span", "flabel", f.label),
      el("span", "count", (picked ? picked + " / " : "") + f.items.length));
    list.append(h);
    state.folders.push(f);
    if (!on) continue;
    for (const i of f.items) {
      const p = picks.data[i.id];
      const r = el("div", "row item" + (i.id === state.sel ? " sel" : ""));
      r.dataset.id = i.id;
      r.dataset.key = "i:" + i.id;
      r.title = i.path;
      r.setAttribute("role", "button");
      r.tabIndex = i.id === state.sel || (!selVisible && !state.visible.length) ? 0 : -1; // roving: one stop in the list
      if (i.id === state.sel) r.setAttribute("aria-current", "true");
      r.append(el("span", "star" + (p?.picked ? " on" : p?.note ? " note" : "")), el("span", "name", i.name), el("span", "meta", rowMeta(i)));
      r.addEventListener("click", () => select(i.id));
      r.addEventListener("keydown", (k) => { // Space on the open item stays the view's (play / pause)
        if (k.key === "Enter" || (k.key === " " && i.id !== state.sel)) { k.preventDefault(); k.stopPropagation(); select(i.id); }
      });
      list.append(r);
      state.visible.push(i);
    }
  }
  list.scrollTop = scroll;
  if (focus) [...list.children].find((c) => c.dataset.key === focus)?.focus({ preventScroll: true });
}

function setKind(kind, id) {
  if (!kind || !catOf(kind)) return;
  leaveReview();
  state.kind = kind;
  renderCats();
  // a remembered item may have been deleted since
  const next = [id, own(state.lastByKind, kind)].find((i) => state.byId.get(i)?.kind === kind) || matching()[0]?.id;
  if (next) openDir(state.byId.get(next).dir || "");
  renderList();
  if (next) select(next, { scroll: true });
  else clearView();
}

// ---------- selection and views ----------
async function select(id, { scroll = false, focus = false } = {}) {
  const item = state.byId.get(id);
  if (!item) return;
  if (state.sel && state.sel !== id) picks.flush(state.sel); // its note, if still waiting
  state.sel = id;
  state.lastByKind[item.kind] = id;
  setPref("last", id);
  setPref("lastByKind", state.lastByKind);
  history.replaceState(null, "", "#" + encodeURIComponent(id));
  document.querySelectorAll("#list .item").forEach((r) => {
    const on = r.dataset.id === id;
    r.classList.toggle("sel", on);
    r.tabIndex = on ? 0 : -1;
    if (on) r.setAttribute("aria-current", "true"); else r.removeAttribute("aria-current");
  });
  const row = [...$("#list").querySelectorAll(".item")].find((r) => r.dataset.id === id);
  if (scroll) row?.scrollIntoView({ block: "nearest" });
  if (focus) row?.focus({ preventScroll: true });
  renderInfo(item);
  renderPick();
  try {
    await host.show(item);
  } catch (e) {
    console.error("select:", e);
  }
}

function clearView() {
  state.sel = null;
  host.clear();
  $("#info").replaceChildren();
  renderPick();
}

function renderInfo(item) {
  $("#info").replaceChildren(el("h2", null, item.name), el("div", "path", item.id), el("div", "meta"));
}

// ---------- picks: made on the review board; the views keep a note per item ----------
const pickNote = $("#pickNote");

function renderPick() {
  const item = state.byId.get(state.sel);
  $("#pick").hidden = !item;
  if (!item) return;
  if (document.activeElement !== pickNote) pickNote.value = picks.data[item.id]?.note || "";
}

pickNote.addEventListener("input", () => state.sel && picks.note(state.sel, pickNote.value));
pickNote.addEventListener("blur", () => state.sel && picks.flush(state.sel));
addEventListener("pagehide", () => picks.flushAll());

// ---------- input ----------
$("#q").addEventListener("input", (e) => { state.q = e.target.value; renderList(); });
$("#onlyPicked").addEventListener("click", (e) => {
  state.onlyPicked = !state.onlyPicked;
  e.currentTarget.classList.toggle("on", state.onlyPicked);
  e.currentTarget.setAttribute("aria-pressed", state.onlyPicked ? "true" : "false");
  renderList();
});
$("#expandAll").addEventListener("click", () => { setOpen(allDirs()); renderList(); });
$("#collapseAll").addEventListener("click", () => { setOpen([]); renderList(); });

// Up / Down: through the items of the open folders only
function step(d) {
  const ids = state.visible.map((i) => i.id);
  if (!ids.length) return;
  const i = ids.indexOf(state.sel);
  select(ids[i < 0 ? 0 : Math.min(ids.length - 1, Math.max(0, i + d))], { scroll: true, focus: $("#list").contains(document.activeElement) });
}

// [ ]: the previous / next folder, opened, at its first item
function jump(d) {
  const at = state.folders.findIndex((f) => f.items.some((i) => i.id === state.sel));
  const f = state.folders[Math.min(state.folders.length - 1, Math.max(0, at < 0 ? (d > 0 ? 0 : state.folders.length - 1) : at + d))];
  if (!f) return;
  openDir(f.key);
  renderList();
  select(f.items[0].id, { scroll: true });
}

const digitCat = (e) => (/^[1-9]$/.test(e.key) ? state.cats[+e.key - 1] : null);

document.addEventListener("keydown", (e) => {
  const tag = e.target.tagName;
  const typing = (tag === "INPUT" && e.target.type !== "range") || tag === "TEXTAREA" || e.target.isContentEditable;
  if (tag === "BUTTON" && (e.key === " " || e.key === "Enter")) return; // the button's own press
  if (state.board && !typing && !(e.ctrlKey || e.metaKey || e.altKey)) {
    if (e.key === "r" || e.key === "R") return setKind(state.kind);
    const cat = digitCat(e);
    if (cat) return setKind(cat.kind);
    if (state.board.key(e)) e.preventDefault();
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (typing && e.target !== $("#q")) return;
    e.preventDefault();
    return step(e.key === "ArrowDown" ? 1 : -1);
  }
  if (typing) {
    if (e.key === "Escape" || e.key === "Enter") e.target.blur();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); return $("#q").focus(); }
  const cat = digitCat(e);
  if (cat) return setKind(cat.kind);
  if ((e.key === "r" || e.key === "R") && state.reviews.length) return enterReview();
  if (e.key === "[" || e.key === "]") { e.preventDefault(); return jump(e.key === "]" ? 1 : -1); }
  if (host.view?.key?.(e)) e.preventDefault();
});

// a link to #<item id> selects it, even on an open page; #review opens the review board, #review/<slug> at that review
addEventListener("hashchange", () => {
  if (reviewLink()) return state.board ? state.board.select(reviewWant()) : enterReview();
  const item = state.byId.get(hashId());
  if (item && item.id !== state.sel) setKind(item.kind, item.id);
});

// ---------- start ----------
function keyHints() {
  const hints = ["↑ ↓ item", "[ ] folder", "/ search", "R review", `1-${Math.max(state.cats.length, 1)} category`, ...registry.keyHelp];
  $("#keys").textContent = hints.join("   ·   ");
}

async function boot() {
  let wait = POLL_MS;
  for (;;) {
    try {
      applyConfig((await api("/api/config")).data);
      break;
    } catch (e) {
      $("#total").textContent = "OFFLINE";
      $("#total").classList.add("red");
      await sleep(wait);
      wait = Math.min(POLL_MAX_MS, wait * 2);
    }
  }
  configurePrefs(config.prefsPrefix);
  state.lastByKind = pref("lastByKind", {});
  await loadPlugins(config.plugins || [], (msg) => notify("plugins", [...(notices.get("plugins")?.lines || []), msg], "err"));
  computeCats();
  keyHints();
  poll();
}

boot();
