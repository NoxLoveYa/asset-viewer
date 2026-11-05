// SPDX-License-Identifier: AGPL-3.0-or-later
// The client smoke test (run by tools/smoke.py in headless Chrome or Edge against the sample project). It mounts every
// core view and, when its plugin is on, the anim view; drives the review board; then runs the real app (app/app.js) in
// frames to check what the audit fixed: one live view after two selects, a pick that a slow poll does not undo, notes
// saved per item, a review board that a catalog change leaves alone, a start from corrupt storage and a bad hash, and a
// save error that shows and retries. It writes one line per step into #result: "PASS name", "FAIL name: reason",
// "SKIP name: reason", "ERROR ..." for a console error, an uncaught error or a CSP violation, and "DONE ..." last.
// ?webgl=0 skips every step that needs WebGL. The server side of the failures it injects is tools/smoke.py (/smoke/ctl).
const result = document.getElementById("result");
const counts = { PASS: 0, FAIL: 0, SKIP: 0, ERROR: 0 };
function log(kind, text) {
  if (kind in counts) counts[kind]++;
  result.textContent += kind + (text ? " " + text : "") + "\n";
}

// ---------- everything that goes wrong on the page is a line, from the first statement on ----------
addEventListener("error", (e) => log("ERROR", `uncaught: ${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`));
addEventListener("unhandledrejection", (e) => log("ERROR", "unhandled rejection: " + String(e.reason?.message || e.reason)));
document.addEventListener("securitypolicyviolation", (e) => log("ERROR", `CSP ${e.violatedDirective}: ${e.blockedURI}`));
const consoleError = console.error.bind(console);
const consoleWarn = console.warn.bind(console);
console.error = (...a) => { log("ERROR", "console.error: " + a.map((x) => x?.message || String(x)).join(" ")); consoleError(...a); };
console.warn = (...a) => { log("WARN", a.map((x) => x?.message || String(x)).join(" ")); consoleWarn(...a); };

const WEBGL = new URLSearchParams(location.search).get("webgl") !== "0";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const enc = encodeURIComponent;

function check(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function waitFor(fn, what, ms = 8000) {
  const end = performance.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (performance.now() > end) throw new Error("timed out waiting for " + (typeof what === "function" ? what() : what));
    await sleep(25);
  }
}

// ---------- steps ----------
class Skip extends Error {}
async function step(name, fn, { webgl = false, needs = null } = {}) {
  log("STEP", name);
  if (webgl && !WEBGL) return log("SKIP", `${name}: needs WebGL (run with it on)`);
  if (needs && !needs()) return log("SKIP", `${name}: ${needs.why}`);
  try {
    await fn();
    log("PASS", name);
  } catch (e) {
    if (e instanceof Skip) return log("SKIP", `${name}: ${e.message}`);
    log("FAIL", `${name}: ${e?.message || e}`);
  }
}

// ---------- the page's modules ----------
const mods = {};
async function load(name, path) {
  try {
    mods[name] = await import(path);
  } catch (e) {
    log("FAIL", `import ${path}: ${e?.message || e}`);
  }
}
for (const [n, p] of [["api", "viewer/api.js"], ["ui", "viewer/ui.js"], ["theme", "viewer/theme.js"], ["plugins", "viewer/plugins.js"],
  ["picks", "viewer/picks.js"], ["viewhost", "viewer/viewhost.js"], ["review", "viewer/review.js"], ["stage", "viewer/views/stage.js"],
  ["model", "viewer/views/model.js"], ["image", "viewer/views/image.js"], ["sound", "viewer/views/sound.js"], ["wave", "viewer/views/wave.js"]]) {
  await load(n, p);
}

async function main() {
  const { api } = mods.api;
  const { el, configurePrefs, url } = mods.ui;
  const { registry, addCategory, loadPlugins, viewLoader } = mods.plugins;

  addCategory({ kind: "model", label: "Models", icon: "model", view: () => import("viewer/views/model.js") });
  addCategory({ kind: "texture", label: "Textures", icon: "texture", view: () => import("viewer/views/image.js") });
  addCategory({ kind: "vfx", label: "VFX", icon: "vfx", view: () => import("viewer/views/image.js") });
  addCategory({ kind: "sound", label: "Sounds", icon: "sound", view: () => import("viewer/views/sound.js") });

  // what this run keeps in localStorage is under the project's prefix: clear that, never the whole origin's
  const clearPrefs = (prefix) => { for (const k of Object.keys(localStorage)) if (k.startsWith(prefix)) localStorage.removeItem(k); };
  // the failures and counters the runner's handler offers (tools/smoke.py)
  const ctl = async (body) => {
    const r = await fetch("/smoke/ctl", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    check(r.ok, "/smoke/ctl answered " + r.status);
    return r.json();
  };

  let config, catalog;
  await step("config and catalog", async () => {
    config = (await api("/api/config")).data;
    catalog = (await api("/api/catalog")).data;
    check(config.app === "asset-viewer" && Array.isArray(config.categories), "unexpected /api/config");
    check(config.model && ["+z", "-z"].includes(config.model.front), "/api/config has no model.front");
    check(catalog.items.length > 0, "the catalog is empty");
    check(!catalog.warnings.length, "catalog warnings: " + catalog.warnings.join("; "));
    configurePrefs(config.prefsPrefix);
    check(config.prefsPrefix, "the project has no prefs prefix");
    clearPrefs(config.prefsPrefix);
  });
  if (!config || !catalog) return;

  await step("plugins load", async () => {
    const problems = [];
    await loadPlugins(config.plugins || [], (m) => problems.push(m));
    check(!problems.length, problems.join("; "));
  });

  const byId = new Map(catalog.items.map((i) => [i.id, i]));
  const byPath = new Map();
  for (const i of catalog.items) if (!byPath.has(i.path)) byPath.set(i.path, i);
  const info = $("#info");
  info.append(el("h2"), el("div", "path"), el("div", "meta"));
  let lastMeta = "";
  const ctx = {
    view2d: $("#view2d"), stage3d: $("#stage3d"), bar: $("#bar"),
    meta: (t) => { lastMeta = t; $(".meta", info).textContent = t; },
    item: (p) => byPath.get(p), catalogExtra: (name) => (catalog.plugins || {})[name], url, theme: mods.theme, config,
  };
  const host = mods.viewhost.createViewHost({ view2d: ctx.view2d, bar: ctx.bar, ctx, loaderFor: viewLoader });
  const item = (id) => check(byId.has(id), "the sample has no " + id) || byId.get(id);
  const show = async (id) => {
    await host.show(item(id));
    const note = $(".note", ctx.view2d);
    check(!note, "the view says: " + note?.textContent);
  };
  const triangles = () => mods.stage.getStage(ctx.stage3d).renderer.info.render.triangles;
  const seg = (name) => $$(".seg button", ctx.bar).find((b) => b.textContent.trim() === name);

  // ---------- theme ----------
  await step("theme tokens", () => {
    const { token, color, num } = mods.theme;
    check(token("fg") !== "" && token("accent") !== "", "--fg / --accent are not defined");
    for (const t of ["model-clay", "stage-sky", "wave-played", "stage-grid"]) {
      const c = color(t);
      check([c.r, c.g, c.b, c.a].every(Number.isFinite) && c.css !== "magenta", `--${t} is not a colour`);
    }
    check(Number.isFinite(num("stage-key-intensity", NaN)), "--stage-key-intensity is not a number");
  });

  await step("preferences: a stored value of the wrong type falls back", () => {
    const { pref, setPref, oneOf } = mods.ui;
    const key = "smoke.pref";
    const store = (raw) => localStorage.setItem(config.prefsPrefix + key, raw);
    store("null");
    check(pref(key, 80) === 80 && pref(key, "x") === "x" && pref(key, {}).constructor === Object, "a stored null was taken for a value");
    store('"loud"');
    check(pref(key, 80) === 80, "a string was taken for a number");
    store("[1]");
    check(pref(key, {}) !== undefined && !Array.isArray(pref(key, {})), "an array was taken for an object");
    store('{"a":1}');
    check(Array.isArray(pref(key, [])), "an object was taken for an array");
    store("{not json");
    check(pref(key, true) === true, "a corrupt value was not ignored");
    store('"checker"');
    check(pref(key, "dark", oneOf("dark", "light")) === "dark", "a value outside the allowed ones was kept");
    setPref(key, 3);
    check(pref(key, 80) === 3, "a good value was not kept");
    localStorage.removeItem(config.prefsPrefix + key);
  });

  // ---------- 2D views ----------
  await step("texture view", async () => {
    await show("assets/textures/checker.png");
    const img = await waitFor(() => $("img.pic", ctx.view2d), "the image");
    check(img.naturalWidth === 32 && img.naturalHeight === 32, `image is ${img.naturalWidth}x${img.naturalHeight}, not 32x32`);
    check(lastMeta.includes("32 × 32"), "meta line: " + lastMeta);
    check(host.view.key({ key: "t" }) && $(".tile", ctx.view2d), "T did not tile");
    host.view.key({ key: "t" });
    check($("img.pic", ctx.view2d), "T again did not untile");
  });

  await step("vfx flipbook", async () => {
    await show("assets/vfx/spark_4x4.png");
    const pic = await waitFor(() => $("div.pic", ctx.view2d), "the flipbook");
    check(pic.style.backgroundSize === "400% 400%", "flipbook size " + pic.style.backgroundSize);
    check(lastMeta.includes("flipbook 4x4"), "meta line: " + lastMeta);
    const at = pic.style.backgroundPosition;
    await waitFor(() => $("div.pic", ctx.view2d)?.style.backgroundPosition !== at, "the frame to change");
    check(host.view.key({ key: " " }), "Space did not pause");
    check($("img.pic", ctx.view2d), "paused flipbook shows no still image");
  });

  await step("sound view", async () => {
    await show("assets/sounds/beep.wav");
    await waitFor(() => lastMeta.includes("0.30 s"), "the decoded sound (meta: " + lastMeta + ")");
    const canvas = $("canvas", ctx.view2d);
    check(canvas && canvas.width > 0, "no waveform drawn");
    check(host.view.key({ key: " " }) && host.view.key({ key: " " }), "Space did not play and stop");
  });

  // ---------- models ----------
  await step("model view: obj with maps", async () => {
    await show("assets/models/cube.obj");
    await waitFor(() => triangles() > 0, "the model to be drawn");
    check($("canvas", ctx.stage3d), "no WebGL canvas");
    check(lastMeta.includes("12 tris") && lastMeta.includes("maps Base, Red"), "meta line: " + lastMeta);
    check(seg("Base") && seg("Red"), "the Look control lacks the map variants");
    seg("Red").click();
    check(seg("Red").classList.contains("on"), "the Red look did not apply");
    host.view.key({ key: "w" });
    host.view.key({ key: "w" });
    host.view.key({ key: "f" });
  }, { webgl: true });

  await step("model view: shared atlas", async () => {
    await show("assets/models/crate.obj");
    await waitFor(() => triangles() > 0, "the model to be drawn");
    check(seg("Textured")?.classList.contains("on"), "a model on a shared atlas should open Textured");
    check(seg("Clay"), "no Clay look");
    seg("Clay").click();
    check(seg("Clay").classList.contains("on"), "Clay did not apply");
  }, { webgl: true });

  await step("model view: glTF", async () => {
    await show("assets/models/tetra.gltf");
    await waitFor(() => triangles() > 0, "the model to be drawn");
    check(lastMeta.includes("4 tris"), "meta line: " + lastMeta);
    check(seg("File"), "a model without maps should offer its own File look");
  }, { webgl: true });

  // ---------- the plugin kinds ----------
  const hasAnim = () => registry.categories.has("anim");
  hasAnim.why = "the roblox-anim plugin is not on in this project";
  await step("anim view", async () => {
    const clip = catalog.items.find((i) => i.kind === "anim");
    check(clip, "the catalog has no anim item");
    check(ctx.catalogExtra("roblox-anim")?.rigs?.length > 0, "no rigs in the catalog extra");
    await host.show(clip);
    check(!$(".note", ctx.view2d), "the view says: " + $(".note", ctx.view2d)?.textContent);
    await waitFor(() => triangles() > 0, "the rig to be drawn");
    check(ctx.bar.querySelectorAll("button").length > 0, "the anim bar is empty");
    host.view.key({ key: " " });
    host.view.key({ key: " " });
    host.view.key({ key: "ArrowRight" });
  }, { webgl: true, needs: hasAnim });

  // ---------- the review board ----------
  await step("review board", async () => {
    host.clear();
    const { createPicks } = mods.picks;
    const board = $("#review"), nav = $("#list");
    board.hidden = false;
    document.body.classList.add("reviewing");
    const saveErrors = [];
    let b = null;
    const picks = createPicks({ onChange: () => b?.update(), onError: (m) => m && saveErrors.push(m) });
    picks.load((await api("/api/picks")).data, picks.writes);
    const review = (await api("/api/review")).data;
    check(!review.error && review.reviews.length > 0, "the sample review file is empty or broken: " + review.error);
    b = mods.review.mount({ board, nav }, { open() {}, picks, want: "", loaderFor: viewLoader, ctx, stamp: (i) => JSON.stringify(i), config });
    b.build(review.reviews, byId);

    const settle = async () => { await waitFor(() => !picks.busy, "saves to finish"); check(!saveErrors.length, "save failed: " + saveErrors.join("; ")); };
    const server = async () => (await api("/api/picks")).data.picks;
    const rows = await waitFor(() => { const r = $$(".rrow", board); return r.length === 5 && r; }, "five candidate rows");
    const pickBtn = (row) => $$("button", row).find((x) => x.textContent.trim().startsWith("Pick"));

    check($(".rbadge", rows[4]) && !pickBtn(rows[4]), "the reference row should have a badge and no Pick");
    check(rows.slice(0, 4).every(pickBtn), "every candidate needs a Pick button");

    // one pick per group, atomically
    pickBtn(rows[1]).click();
    await settle();
    let p = await server();
    check(p["assets/sounds/blip.wav"]?.picked === true, "Pick did not save");
    pickBtn(rows[0]).click();
    await settle();
    p = await server();
    check(p["assets/sounds/beep.wav"]?.picked === true && !p["assets/sounds/blip.wav"]?.picked, "picking another did not drop the first");

    // a note, saved for its own item
    const note = $("input.rnote", rows[0]);
    note.value = "smoke note";
    note.dispatchEvent(new Event("input", { bubbles: true }));
    note.dispatchEvent(new Event("blur"));
    await settle();
    check((await server())["assets/sounds/beep.wav"]?.note === "smoke note", "the note did not save");

    // a cut: a drag across the waveform, from 25% to 75% of the sound
    await waitFor(() => /0\.30 s/.test($(".rby", rows[0]).textContent), "the sound to decode");
    const wave = $("canvas.rwave", rows[0]);
    await waitFor(() => wave.getBoundingClientRect().width > 50, "the waveform to have a width");
    const r = wave.getBoundingClientRect();
    Element.prototype.setPointerCapture = function () {}; // a synthetic pointer is not an active one
    const fire = (type, f) => wave.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: r.left + r.width * f,
      clientY: r.top + r.height / 2, button: 0, pointerId: 1, isPrimary: true }));
    fire("pointerdown", 0.25);
    fire("pointermove", 0.75);
    fire("pointerup", 0.75);
    await settle();
    const cut = (await server())["assets/sounds/beep.wav"]?.cut;
    check(cut && cut[0] > 0.06 && cut[0] < 0.09 && cut[1] > 0.21 && cut[1] < 0.24, "cut " + JSON.stringify(cut) + ", expected about [0.075, 0.225]");
    check(/Keep 0\.0\d – 0\.2\d s/.test($(".rcut", rows[0]).textContent), "cut label: " + $(".rcut", rows[0]).textContent);
    check(b.key({ key: "x" }), "X was not handled");
    await settle();
    check(!(await server())["assets/sounds/beep.wav"]?.cut, "X did not clear the cut");
    check((await server())["assets/sounds/beep.wav"]?.picked === true, "clearing the cut dropped the pick");

    // the preview pane: a model, a model again, then the reference texture
    if (WEBGL) {
      check(b.key({ key: "ArrowRight" }), "Right (next group) was not handled");
      await waitFor(() => $("canvas:not(.rwave)", board), "the model preview in the pane");
      check(b.key({ key: "p" }), "P was not handled");
      b.key({ key: "ArrowDown" });
      b.key({ key: "p" });
      await settle();
      p = await server();
      check(p["assets/models/crate.obj"]?.picked === true && !p["assets/models/cube.obj"]?.picked, "keyboard picks in the model group");
      b.key({ key: "ArrowDown" });
      await waitFor(() => $("img.pic", board), "the texture preview in the pane");
      b.key({ key: "p" });
      await settle();
      check(!(await server())["assets/textures/checker.png"]?.picked, "a reference must not be pickable");
    }

    // layout: from a narrow window to a wide one nothing in a row spills past its card, and a sound's text keeps a width
    const was = board.style.width;
    try {
      for (const w of [520, 700, 900, 1200]) {
        board.style.width = w + "px";
        await sleep(50);
        for (const card of $$(".rgroup", board)) {
          const box = card.getBoundingClientRect();
          for (const row of $$(".rrow", card)) {
            for (const child of [row, ...row.querySelectorAll("*")]) {
              const c = child.getBoundingClientRect();
              if (!c.width && !c.height) continue; // not displayed
              check(c.left >= box.left - 0.5 && c.right <= box.right + 0.5,
                `at ${w}px a .${child.className || child.tagName} spills past its card (${Math.round(c.left)}–${Math.round(c.right)}, card ${Math.round(box.left)}–${Math.round(box.right)})`);
            }
            const text = $(".rtext", row);
            check(!text || text.getBoundingClientRect().width >= 100, `at ${w}px a row's text is squeezed to ${Math.round(text.getBoundingClientRect().width)}px`);
          }
        }
      }
    } finally {
      board.style.width = was;
    }

    b.destroy();
    document.body.classList.remove("reviewing");
    board.hidden = true;
    check(!board.children.length && !nav.children.length && !$(".rq"), "leaving the board left DOM behind");
    await sleep(100);
  });

  await step("a sound plays on again after the board", async () => {
    await show("assets/sounds/blip.wav");
    await waitFor(() => lastMeta.includes("0.15 s"), "the decoded sound (meta: " + lastMeta + ")");
  });

  host.clear();

  // ---------- the real app (app/app.js) in frames ----------
  // A frame is a fresh page load of `/`, so each scenario starts from its own storage and hash. What the page prints and
  // throws is a line of this run like the page's own.
  const frames = [];
  function openApp(hash = "") {
    return new Promise((resolve) => {
      const f = el("iframe");
      f.title = "app";
      Object.assign(f.style, { position: "fixed", right: "0", bottom: "0", width: "1100px", height: "700px", border: "0" });
      f.addEventListener("load", () => {
        const w = f.contentWindow, d = f.contentDocument;
        const was = w.console.error.bind(w.console);
        w.console.error = (...a) => { log("ERROR", "app console.error: " + a.map((x) => x?.message || String(x)).join(" ")); was(...a); };
        w.addEventListener("error", (e) => log("ERROR", `app uncaught: ${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`));
        w.addEventListener("unhandledrejection", (e) => log("ERROR", "app unhandled rejection: " + String(e.reason?.message || e.reason)));
        d.addEventListener("securitypolicyviolation", (e) => log("ERROR", `app CSP ${e.violatedDirective}: ${e.blockedURI}`));
        const app = { f, w, d, close: () => f.remove() };
        frames.push(app);
        resolve(app);
      }, { once: true });
      f.src = "/" + hash;
      document.body.append(f);
    });
  }
  // the app has loaded the catalog and shows an item (or, for a review link, the board)
  const started = (a, review = false) => waitFor(() => /^\d+$/.test($("#total", a.d)?.textContent || "")
    && (review ? $$(".rrow", a.d).length > 0 : !!$("#info h2", a.d)), "the app to start", 15000);
  // select by the app's own hash handler, synchronously: two calls in a row are two select() calls in one task
  const hop = (a, id) => { a.w.location.hash = "#" + enc(id); a.w.dispatchEvent(new a.w.HashChangeEvent("hashchange")); };
  const serverPicks = async () => (await api("/api/picks")).data.picks;
  const typeNote = (a, text) => {
    const input = $("#pickNote", a.d);
    input.value = text;
    input.dispatchEvent(new a.w.Event("input", { bubbles: true }));
    return input;
  };
  const name = (id) => id.split("/").pop().replace(/\.\w+$/, "");
  const stamp = Date.now().toString(36);
  // a scenario starts from clean storage (what an earlier one saved, the review mode for one, would change the start)
  const withApp = async (hash, fn, { keep = false } = {}) => {
    if (!keep) clearPrefs(config.prefsPrefix);
    const a = await openApp(hash);
    try { return await fn(a); } finally { a.close(); }
  };

  await step("app: starts, lists the sample and mounts a view", () => withApp("", async (a) => {
    await started(a);
    check($$("#cats .row", a.d).length >= 4, "the nav has no categories");
    check($("#list .item", a.d), "the list has no items");
    check($$("#view2d > .slot", a.d).length === 1, "no view is mounted");
    check(a.d.title.includes("Asset Viewer sample"), "the page title is " + a.d.title);
  }));

  await step("app: two selects in a row leave exactly one live view (H4)", () => withApp("", async (a) => {
    await started(a);
    const pairs = [["assets/textures/checker.png", "assets/sounds/beep.wav"], ["assets/vfx/spark_4x4.png", "assets/textures/checker.png"],
      ["assets/sounds/beep.wav", "assets/vfx/spark_4x4.png"]];
    if (WEBGL) pairs.push(["assets/models/cube.obj", "assets/sounds/blip.wav"], ["assets/sounds/beep.wav", "assets/models/cube.obj"]);
    for (const [first, second] of pairs) {
      hop(a, first);
      hop(a, second);
      const model = byId.get(second).kind === "model";
      await waitFor(() => $("#info h2", a.d)?.textContent === name(second) && $("#info .meta", a.d)?.textContent, `${name(second)} to be shown`);
      await sleep(400); // the first mount, superseded, finishes about now
      const what = `${name(first)} then ${name(second)}`;
      check($$("#view2d > .slot", a.d).length === 1, `${what}: ${$$("#view2d > .slot", a.d).length} view slots`);
      check($$("#bar > .barslot", a.d).length === 1, `${what}: ${$$("#bar > .barslot", a.d).length} bar slots`);
      check(model ? !$("#stage3d", a.d).hidden : $("#stage3d", a.d).hidden, `${what}: the 3D stage is ${model ? "hidden" : "showing"}`);
      if (!model) check(!($$(".img2d", a.d).length && byId.get(second).kind === "sound"), `${what}: an image of the first stayed`);
      check($("#info .path", a.d).textContent === second, `${what}: the info shows ${$("#info .path", a.d).textContent}`);
    }
  }));

  await step("app: a slow poll does not undo a pick (H5)", () => withApp("#review", async (a) => {
    await started(a, true);
    const rows = $$(".rrow", a.d);
    const pickBtn = (row) => $$("button", row).find((x) => x.textContent.trim().startsWith("Pick"));
    const row = rows.find((r) => pickBtn(r)?.textContent.trim() === "Pick" && /Beep|Blip/.test(r.textContent));
    check(row, "no unpicked sound row on the board");
    const id = /Blip/.test(row.textContent) ? "assets/sounds/blip.wav" : "assets/sounds/beep.wav";
    await ctl({ catalog_delay: 1200 }); // every poll from here on waits for its catalog; its picks come early
    try {
      // when a poll's answer with the picks as they are now arrives, pick: that answer is then stale, and the poll
      // has the slow catalog still to wait for
      let armed = true, clicked = false;
      const after = []; // whether the pick showed, just after each poll that ended once it was made
      const real = a.w.fetch.bind(a.w);
      a.w.fetch = (input, init) => {
        const p = real(input, init);
        const path = String(input);
        if (armed && path === "/api/picks" && (init?.method || "GET") === "GET") {
          armed = false;
          p.then(() => { pickBtn(row).click(); clicked = true; }).catch(() => {});
        }
        if (path === "/api/catalog") { // the last answer of a poll: it has been drawn a moment later
          p.then(() => sleep(150)).then(() => { if (clicked) after.push(row.classList.contains("picked") && $$("button.primary", row).length === 1); }).catch(() => {});
        }
        return p;
      };
      await waitFor(() => clicked, "a poll to start and the pick to be made", 12000);
      await waitFor(() => row.classList.contains("picked"), "the pick to show");
      await waitFor(() => after.length >= 3, "the polls after the pick to end", 15000); // the one that raced it, then two more
      check(after.every(Boolean), `a poll took the pick back off the board (showing after each poll: ${after.join(", ")})`);
      const p = await serverPicks();
      check(p[id]?.picked === true, "the server has no pick for " + id);
    } finally {
      await ctl({ catalog_delay: 0 });
    }
  }));

  await step("app: a note typed, then another item selected inside the debounce, is saved for its own item (M3)", () => withApp("", async (a) => {
    await started(a);
    const first = "assets/textures/checker.png", second = "assets/sounds/blip.wav", third = "assets/vfx/spark_4x4.png";
    hop(a, first);
    await waitFor(() => $("#info .path", a.d)?.textContent === first, "the first item");
    typeNote(a, "note A " + stamp);
    await sleep(50); // well inside the 500 ms debounce
    hop(a, second);
    await waitFor(() => $("#info .path", a.d)?.textContent === second, "the second item");
    check($("#pickNote", a.d).value === "", "the second item shows the first one's note");
    typeNote(a, "note B " + stamp);
    await sleep(50);
    hop(a, third);
    await waitFor(() => $("#info .path", a.d)?.textContent === third, "the third item");
    await sleep(1500);
    const p = await serverPicks();
    check(p[first]?.note === "note A " + stamp, `${first} has ${JSON.stringify(p[first]?.note)}`);
    check(p[second]?.note === "note B " + stamp, `${second} has ${JSON.stringify(p[second]?.note)}`);
    check(!p[third]?.note, `${third} has ${JSON.stringify(p[third]?.note)}`);
  }));

  await step("app: a catalog change the review does not use leaves the board and its sounds alone (H6)", () => withApp("#review", async (a) => {
    await started(a, true);
    await waitFor(() => $$(".rby", a.d).filter((e) => /\d\.\d\d s/.test(e.textContent)).length >= 2, "the sounds to decode");
    const rowBefore = $(".rrow", a.d);
    const texturesRow = () => $$("#cats .row", a.d).find((r) => r.textContent.includes("Textures"))?.querySelector(".count")?.textContent;
    const textures = texturesRow();
    const fetches = async () => (await ctl({ count: "/files/assets/sounds/" })).count;
    const before = await fetches();
    check(before >= 2, "the sounds were not fetched for the board");
    try {
      await ctl({ add_texture: true });
      await waitFor(() => texturesRow() !== textures, () => `the new file to show in the nav (textures ${texturesRow()}, was ${textures}; total ${$("#total", a.d).textContent}; banner ${$("#banner", a.d).textContent})`, 12000);
      await sleep(2500); // another poll or two
      check(rowBefore.isConnected && $(".rrow", a.d) === rowBefore, "the board was rebuilt for a change it does not use");
      check((await fetches()) === before, `the sounds were fetched again (${before} -> ${await fetches()})`);
    } finally {
      await ctl({ remove_extra: true });
    }
  }));

  await step("app: corrupt storage (also valid JSON of the wrong type) and a bad hash still start (M12)", async () => {
    const seed = {
      last: "null", lastByKind: "null", "open.model": '{"a":1}', "open.texture": "5", "open.sound": "null", review: '"yes"',
      "review.filter": "5", "review.current": "7", "review.all": "{", "sound.volume": '"loud"', "sound.loop": "null",
      "image.bg": "null", "image.zoom": "[]", "image.fps": '"x"', "model.look": "5", "model.wire": '"on"',
    };
    for (const hash of ["#%E0%A4", "#review/%E0%A4", "#%zz", "#" + enc("nothing/here")]) {
      clearPrefs(config.prefsPrefix);
      for (const [k, v] of Object.entries(seed)) localStorage.setItem(config.prefsPrefix + k, v);
      const errors = counts.ERROR;
      await withApp(hash, async (a) => {
        const review = hash.startsWith("#review");
        await started(a, review);
        check(review || $$("#view2d > .slot", a.d).length === 1, hash + ": no view");
        check(review ? a.d.body.classList.contains("reviewing") : $("#info h2", a.d)?.textContent, hash + ": nothing shown");
        check(!/OFFLINE/.test($("#total", a.d).textContent), hash + ": OFFLINE");
        await sleep(2500); // a poll or two on top
        check(counts.ERROR === errors, hash + ": the page logged an error");
        check($("#banner", a.d).hidden || !/could not draw/.test($("#banner", a.d).textContent), hash + ": a render error banner");
      }, { keep: true });
    }
    clearPrefs(config.prefsPrefix);
  });

  await step("app: a failed save shows, is kept and retried; a stale token is renewed (M4)", () => withApp("", async (a) => {
    await started(a);
    const id = "assets/sounds/beep.wav";
    hop(a, id);
    await waitFor(() => $("#info .path", a.d)?.textContent === id, "the item");
    const banner = $("#banner", a.d);
    const save = (text) => typeNote(a, text).dispatchEvent(new a.w.Event("blur")); // blur saves at once

    await ctl({ fail_picks: 1 });
    save("kept " + stamp);
    await waitFor(() => !banner.hidden && /Can't save picks/.test(banner.textContent), "the save error to show");
    await waitFor(() => banner.hidden, "the retry to succeed and the error to go", 10000);
    check((await serverPicks())[id]?.note === "kept " + stamp, "the failed save was lost: " + JSON.stringify((await serverPicks())[id]?.note));

    await ctl({ rotate_token: true }); // as after a restart of the server: the page's token is stale
    save("renewed " + stamp);
    await waitFor(async () => (await serverPicks())[id]?.note === "renewed " + stamp, "the save under a new token", 10000);
    check(banner.hidden, "a stale token showed an error: " + banner.textContent);
  }));

  await step("app: a plugin's config edited while the page is open reaches its viewer, with no reload banner", () => withApp("", async (a) => {
    await started(a);
    const probe = a.d.createElement("script");
    probe.type = "module";
    probe.src = "/smoke/probe.js";
    a.d.head.append(probe);
    await waitFor(() => a.w.__pluginConfig, "the probe to load");
    const blade = () => a.w.__pluginConfig("roblox-anim")?.bodyAim?.blade;
    const next = 37;
    check(a.w.__pluginConfig("roblox-anim") && blade() !== next, "the anim plugin's viewer is missing or already has blade 37: " + JSON.stringify(a.w.__pluginConfig("roblox-anim")));
    try {
      await ctl({ plugin_config: { "roblox-anim": { bodyAim: { blade: next } } } });
      try {
        await waitFor(() => blade() === next, () => `viewer.config.bodyAim.blade to become ${next} (is ${blade()})`, 12000);
      } catch (e) {
        const served = (await api("/api/config")).data;
        throw new Error(`${e.message}; the server serves ${JSON.stringify(served.plugins)}, error ${served.error}, banner "${$("#banner", a.d).textContent}", total ${$("#total", a.d).textContent}`);
      }
      await sleep(2500);
      check($("#banner", a.d).hidden || !/reload the page/.test($("#banner", a.d).textContent), "a config edit asked for a reload: " + $("#banner", a.d).textContent);
    } finally {
      await ctl({ restore_config: true });
    }
  }));

  await step("app: a texture that loads after the model is shown is drawn without any input", () => withApp("", async (a) => {
    const id = "assets/models/cube.obj";
    await started(a);
    const probe = a.d.createElement("script");
    probe.type = "module";
    probe.src = "/smoke/probe.js";
    a.d.head.append(probe);
    await waitFor(() => a.w.__stageTextures, "the probe to load");
    const state = () => a.w.__stageTextures();
    const before = new Set(state().uuids); // whatever the app opened with
    a.w.__holdImages = true; // the cube's maps do not start loading before it was drawn
    hop(a, id);
    await waitFor(() => { const s = state(); return s.total > 0 && s.uuids.every((u) => !before.has(u)); }, "the cube on the stage with its maps");
    check(state().loaded < state().total, `the maps were there before they were released (${JSON.stringify(state())})`);
    // A headless frame comes when it comes (not on this page's virtual clock), so the frames are taken by hand: stage.draw()
    // is the loop's own body, drawing only when something asked for a frame.
    for (let i = 0; i < 10; i++) a.w.__draw(); // until the framing and the controls' damping have settled: nothing asks for a frame
    a.w.__releaseImages();
    await waitFor(() => state().loaded === state().total, () => "the maps to load " + JSON.stringify(state()), 20000);
    // no mouse, no key: a frame is there only if the loading asked for it
    a.w.__draw();
    check(state().uploaded === state().loaded, `${state().loaded - state().uploaded} of ${state().loaded} loaded maps were never drawn (${JSON.stringify(state())})`);
  }), { webgl: true });

  frames.forEach((f) => f.close());
}

if (!mods.api || !mods.ui || !mods.theme || !mods.plugins || !mods.picks || !mods.viewhost || !mods.review) {
  log("FAIL", "a core module did not load");
} else {
  try {
    await main();
  } catch (e) {
    log("FAIL", "smoke run: " + (e?.stack || e));
  }
}
log("DONE", `${counts.PASS} pass, ${counts.FAIL} fail, ${counts.SKIP} skip, ${counts.ERROR} errors`);
document.body.dataset.done = "1";
