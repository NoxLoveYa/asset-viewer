// SPDX-License-Identifier: AGPL-3.0-or-later
// Model view: FBX / glTF / OBJ on the stage with the PBR maps the catalog found beside it (Stem_<Map>.png,
// Stem_<Variant>_<Map>.png), shared atlases (config `materials`), a clay look, wireframe, spin and frame. Plugins extend
// it through model add-ons (viewer.modelAddon): extra looks, groups, bar controls, a clipping plane, a per-frame tick.
// A public module (plugins import "viewer/views/model.js"): loadModel, loadTexture, pbrMaterial, disposeObject, mount.
import * as THREE from "three";
import { getStage, themeColor } from "./stage.js";
import { button, seg, sep, el, pref, setPref, url, own, isString } from "../ui.js";
import * as theme from "../theme.js";
import { registry } from "../plugins.js";
import { timedFetch } from "../api.js";

const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="; // 1 x 1
const MAX_TEXTURES = 48; // decoded textures kept on the GPU side; the least recently used is disposed
const ANISOTROPY = 8;
const LOAD_TIMEOUT_MS = 60000;
const FBX_MAGIC = "Kaydara FBX Binary";
const SEP = "  ·  ";

// ---------- loading ----------
// blank: the textures the file names are not beside it (a model on shared atlases): they load as 1 x 1 instead of 404s.
export async function loadModel(path, mtime, { blank = false } = {}) {
  const ext = path.split(".").pop().toLowerCase();
  const u = url(path, mtime);
  if (ext === "fbx") {
    const { FBXLoader } = await import("three/addons/loaders/FBXLoader.js");
    const manager = new THREE.LoadingManager();
    if (blank) manager.setURLModifier((x) => (/\.(png|jpe?g|webp)(\?|$)/i.test(x) ? BLANK : x));
    const res = await timedFetch(u, {}, LOAD_TIMEOUT_MS);
    if (!res.ok) throw new Error(res.status + " " + res.statusText);
    let buf = await res.arrayBuffer();
    if (new TextDecoder().decode(buf.slice(0, FBX_MAGIC.length)) !== FBX_MAGIC) {
      // Blockbench's ASCII FBX: three's text parser fails on its header comment and top-level FileId / Creator lines
      const text = new TextDecoder().decode(buf).split("\n")
        .filter((l, i) => i > 1 && !/^\w+:(?![^\n]*\{)/.test(l)).join("\n");
      buf = new TextEncoder().encode("; FBX 7.3.0 project file\n" + text).buffer;
    }
    return new FBXLoader(manager).parse(buf, url(path.slice(0, path.lastIndexOf("/") + 1)));
  }
  if (ext === "glb" || ext === "gltf") {
    const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
    return (await new GLTFLoader().loadAsync(u)).scene;
  }
  const { OBJLoader } = await import("three/addons/loaders/OBJLoader.js");
  return new OBJLoader().loadAsync(u);
}

// ---------- textures: one cache, least recently used disposed ----------
const textures = new Map(); // key -> Texture, least recently used first
const cached = new WeakSet(); // the cache's textures: a model's disposal must not take them

export function loadTexture(path, mtime, { srgb = false, repeat = false } = {}) {
  const key = `${path}?${mtime ?? ""}${srgb ? "#s" : ""}${repeat ? "#r" : ""}`;
  let t = textures.get(key);
  if (t) {
    textures.delete(key);
  } else {
    t = new THREE.TextureLoader().load(url(path, mtime), undefined, undefined, () => console.warn("texture did not load:", path));
    t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
    t.anisotropy = ANISOTROPY;
    if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
    cached.add(t);
  }
  textures.set(key, t);
  if (textures.size > MAX_TEXTURES) {
    const [oldest, old] = textures.entries().next().value;
    textures.delete(oldest);
    old.dispose(); // still drawn by a live material? three uploads it again on the next frame
  }
  return t;
}

// A Roblox SurfaceAppearance look from a map set {Color, Normal, Metalness, Roughness, Emissive}: paths, and the
// catalog lookup for their mtimes (so a re-baked map loads fresh). With alpha "cutout" the Color's alpha cuts the shape
// out (test 0.5, depth written, no blending, as Roblox draws a cutout card); doubleSided draws both faces of a card.
export function pbrMaterial(set, lookup, { alpha, doubleSided } = {}) {
  const t = (p, srgb) => loadTexture(p, lookup(p)?.mtime, { srgb });
  const m = new THREE.MeshStandardMaterial({ roughness: set.Roughness ? 1 : 0.6, metalness: set.Metalness ? 1 : 0 });
  if (alpha === "cutout") { m.alphaTest = 0.5; m.transparent = false; m.depthWrite = true; }
  if (doubleSided) m.side = THREE.DoubleSide;
  if (set.Color) m.map = t(set.Color, true);
  if (set.Normal) m.normalMap = t(set.Normal);
  if (set.Roughness) m.roughnessMap = t(set.Roughness);
  if (set.Metalness) m.metalnessMap = t(set.Metalness);
  if (set.Emissive) { m.emissiveMap = t(set.Emissive, true); m.emissive.setScalar(1); } // the map sets the colour; no tint
  return m;
}

// ---------- themed materials: the colours come from --model-clay and --model-glass ----------
const paintClay = (m) => m.color.copy(themeColor("model-clay").color);
const paintGlass = (m) => {
  const g = themeColor("model-glass");
  m.color.copy(g.color);
  m.opacity = g.alpha;
};
function makeClay() {
  const m = new THREE.MeshStandardMaterial({ roughness: 0.75, metalness: 0 });
  paintClay(m);
  return m;
}
function makeGlass() {
  const m = new THREE.MeshPhysicalMaterial({ roughness: 0.05, transmission: 0.6, transparent: true });
  paintGlass(m);
  return m;
}

// ---------- disposal ----------
function disposeMaterial(m) {
  for (const v of Object.values(m)) if (v?.isTexture && !cached.has(v)) v.dispose();
  m.dispose();
}

// Frees an object's geometry, materials and the textures its loader made (not the cache's). `skip`: objects (an
// add-on's groups) that own their resources and are disposed by their owner.
export function disposeObject(obj, skip) {
  const walk = (o) => {
    if (skip?.has(o)) return;
    o.geometry?.dispose();
    for (const m of [].concat(o.material || [])) disposeMaterial(m);
    o.children.forEach(walk);
  };
  if (obj) walk(obj);
}

function compileAll(list) {
  return (list || []).flatMap((s) => {
    try { return [new RegExp(s)]; } catch { console.warn("model.glassNames: not a regex:", s); return []; }
  });
}

// ---------- the view ----------
export async function mount(item, ctx) {
  const stage = getStage(ctx.stage3d);
  const lease = stage.claim();
  const cfg = ctx.config?.model || {};
  const front = cfg.front === "-z" ? "-z" : "+z";
  const glassNames = compileAll(cfg.glassNames);

  let look = pref("model.look", null, isString); // kept when the next model has that look too
  let wire = pref("model.wire", false);
  let spin = pref("model.spin", false);
  let root = null, holder = null, original = new Map(), shownPath = null, token = 0, chain = Promise.resolve();
  let lift = 0; // the model's height above y = 0 before it was stood on the ground
  let clay = null, glass = null;
  let variantMats = new Map(), sharedMats = new Map();
  // from the add-ons of the shown model
  let addons = [], groups = new Set(), addonLooks = new Map(), keys = new Map(), ticks = [], planes = [], customMeta = null;
  let defaultMeta = "";
  stage.controls.autoRotate = spin;

  const lookBox = el("span", "barslot");
  const addonBox = el("span", "barslot");
  const wireBtn = button("Wireframe", () => { wire = !wire; setPref("model.wire", wire); wireBtn.set(wire); apply(); }, { key: "W", on: wire });
  const spinBtn = button("Spin", () => { spin = !spin; setPref("model.spin", spin); spinBtn.set(spin); stage.controls.autoRotate = spin; lease.invalidate(); }, { key: "S", on: spin });
  const frameBtn = button("Frame", () => holder && lease.frame(new THREE.Box3().setFromObject(holder), front), { key: "F" });
  ctx.bar.append(lookBox, sep(), wireBtn, spinBtn, frameBtn, addonBox);

  const unsubTheme = theme.onChange(() => {
    if (clay) paintClay(clay);
    if (glass) paintGlass(glass);
    lease.invalidate();
  });

  const clayMat = () => (clay ||= makeClay());
  const glassMat = () => (glass ||= makeGlass());
  const showMeta = () => ctx.meta(customMeta ? customMeta.join(SEP) : defaultMeta);

  // ---------- looks ----------
  // A model on shared atlases (config `materials`): the rule for a mesh, by its name, its material's name, else "*".
  const ruleFor = (it, o) => {
    const rules = it.materials || {};
    return [o.name, ...[].concat(o.material).map((m) => m?.name)].map((n) => own(rules, n)).find(Boolean) || own(rules, "*");
  };
  function sharedFor(o) {
    const rule = ruleFor(item, o);
    if (!rule) return null;
    const k = JSON.stringify(rule);
    if (!sharedMats.has(k)) sharedMats.set(k, pbrMaterial(rule.maps, ctx.item, rule));
    return sharedMats.get(k);
  }
  function variantMat(name) {
    if (!variantMats.has(name)) variantMats.set(name, pbrMaterial({ ...(own(item.maps, "Base") || {}), ...(own(item.maps, name) || {}) }, ctx.item));
    return variantMats.get(name);
  }

  // each map variant (a variant falls back to the Base set per map), the file's own materials, clay; add-on looks first
  function looks(hasShared) {
    const names = Object.keys(item.maps || {}).sort((a, b) => (a === "Base" ? -1 : b === "Base" ? 1 : a.localeCompare(b)));
    const added = [...addonLooks].map(([id, l]) => [id, l.label]);
    const shared = hasShared ? [["@shared", "Textured"]] : [];
    return [...added, ...shared, ...names.map((n) => [n, n]),
      ...(names.length || added.length || shared.length ? [] : [["@file", "File"]]), ["@clay", "Clay"]];
  }

  function materialFor(o) {
    const a = addonLooks.get(look);
    if (a) return a.materialFor(o) || original.get(o);
    if (look === "@shared") return sharedFor(o) || original.get(o);
    if (look === "@file") return original.get(o);
    if (glassNames.some((r) => r.test(o.name))) return glassMat();
    return look === "@clay" ? clayMat() : variantMat(look);
  }

  const eachMesh = (fn, includeGroups) => {
    const walk = (o) => {
      if (!includeGroups && groups.has(o)) return;
      if (o.isMesh) fn(o);
      o.children.forEach(walk);
    };
    if (root) walk(root);
  };

  function applyClipping() {
    stage.renderer.localClippingEnabled = planes.length > 0;
    eachMesh((o) => [].concat(o.material).forEach((m) => { m.clippingPlanes = planes.length ? planes : null; m.clipShadows = true; m.needsUpdate = true; }), true);
    lease.invalidate();
  }

  function apply() {
    if (!root) return;
    eachMesh((o) => {
      o.material = materialFor(o);
      [].concat(o.material).forEach((m) => {
        m.wireframe = wire;
        m.clippingPlanes = planes.length ? planes : null;
      });
    });
    lease.invalidate();
  }

  // ---------- the add-on api: what a plugin's prepare(api) gets ----------
  function addonApi(next, obj, draft) {
    return {
      item: next, obj, holder: draft.holder, stage, THREE, pbrMaterial, loadModel, loadTexture, disposeObject, url, theme,
      materials: { clay: clayMat, glass: glassMat },
      config: cfg,
      lookup: ctx.item, // catalog item of a project path: its maps, its mtime
      get lift() { return lift; },
      // a look in the Look selector; materialFor(mesh) -> Material, or null to keep the mesh's own
      addLook(id, label, materialFor, { preferred = false } = {}) { draft.looks.set(id, { label, materialFor, preferred }); },
      // a group that looks, wireframe and disposal leave alone (props); the add-on frees it in destroy()
      addGroup(group) {
        if (!group.parent) obj.add(group);
        draft.groups.add(group);
      },
      addButton(label, key, onToggle, { on = false } = {}) {
        let state = !!on;
        const b = button(label, () => { state = !state; b.set(state); onToggle(state); lease.invalidate(); }, { key, on: state });
        if (key) draft.keys.set(key.toLowerCase(), () => !b.hidden && b.click());
        draft.controls.push(b);
        return b;
      },
      // options [[value, label]...]; with `key`, that key cycles the options
      addSeg(options, value, onChange, { key, label } = {}) {
        const s = seg(options, value, (v) => { onChange(v); lease.invalidate(); }, { label });
        if (key) draft.keys.set(key.toLowerCase(), () => s.cycle());
        draft.controls.push(s);
        return s;
      },
      setClipping(list) {
        draft.planes = list || [];
        if (draft.live) { planes = draft.planes; applyClipping(); }
      },
      onTick(fn) { draft.ticks.push(fn); }, // fn(dt) returns false when it changed nothing
      onShown(fn) { draft.shown.push(fn); }, // fn() once the model stands on the stage: `lift` and `holder` are final
      turnY(rad) {
        draft.turn = rad;
        if (draft.live && holder) { holder.rotation.y = rad; lease.invalidate(); }
      },
      meta(parts) {
        draft.meta = parts;
        if (draft.live) { customMeta = parts; showMeta(); }
      },
    };
  }

  const destroyAddons = () => {
    for (const a of addons) {
      try { a.destroy?.(); } catch (e) { console.error(`model add-on ${a.id}:`, e); }
    }
    addons = [];
  };

  // ---------- load ----------
  async function run(next, t) {
    if (t !== token) return; // superseded while waiting: skip the intermediate models
    const reframe = next.path !== shownPath;
    let obj;
    try {
      obj = await loadModel(next.path, next.mtime, { blank: !!next.materials });
    } catch (e) {
      if (t === token) { clearModel(); ctx.meta("Can't load this model: " + e.message); }
      return;
    }
    if (t !== token) return disposeObject(obj);

    // the old model's add-ons end before the new ones start; the new state is built in `draft`, committed after the checks
    destroyAddons();
    const draft = { holder: new THREE.Group(), looks: new Map(), groups: new Set(), controls: [], keys: new Map(), ticks: [],
      shown: [], planes: [], meta: null, turn: 0, live: false };
    const prepared = [];
    const api = addonApi(next, obj, draft);
    for (const def of registry.addons) {
      try {
        if (!def.applies(next)) continue;
        prepared.push(def);
        await def.prepare(api);
      } catch (e) { console.error(`model add-on ${def.id}:`, e); }
      if (t !== token) {
        prepared.forEach((d) => { try { d.destroy?.(); } catch { /* the add-on's own state */ } });
        return disposeObject(obj, draft.groups);
      }
    }

    // ---- commit ----
    const oldRoot = root, oldGroups = groups;
    variantMats.forEach(disposeMaterial);
    sharedMats.forEach(disposeMaterial);
    variantMats = new Map();
    sharedMats = new Map();
    disposeObject(oldRoot, oldGroups);
    item = next;
    root = obj;
    holder = draft.holder;
    addons = prepared;
    groups = draft.groups;
    addonLooks = draft.looks;
    keys = draft.keys;
    ticks = draft.ticks;
    planes = draft.planes;
    customMeta = draft.meta;
    original = new Map();
    let tris = 0, meshes = 0, hasShared = false;
    eachMesh((o) => {
      original.set(o, o.material);
      o.castShadow = o.receiveShadow = true;
      meshes++;
      const g = o.geometry;
      tris += (g.index ? g.index.count : g.attributes.position.count) / 3;
      if (item.materials && ruleFor(item, o)) hasShared = true;
    });

    const options = looks(hasShared);
    const preferred = [...addonLooks].find(([, l]) => l.preferred)?.[0];
    if (!options.some(([v]) => v === look) || (reframe && preferred && look === "@clay")) look = preferred || options[0][0];
    lookBox.replaceChildren(seg(options, look, (v) => { look = v; setPref("model.look", v); apply(); }, { label: "Look" }));
    addonBox.replaceChildren(...(draft.controls.length ? [sep(), ...draft.controls] : []));
    apply();

    // stand it on the ground, centred
    const box = new THREE.Box3().setFromObject(root);
    const c = box.getCenter(new THREE.Vector3());
    root.position.sub(new THREE.Vector3(c.x, box.min.y, c.z));
    lift = box.min.y;
    holder.add(root);
    holder.rotation.y = draft.turn;
    draft.live = true;
    lease.show(holder, ticks.length ? (dt) => ticks.map((fn) => fn(dt)).some((r) => r !== false) : null);
    if (planes.length) applyClipping();
    const size = box.getSize(new THREE.Vector3());
    if (reframe) lease.frame(new THREE.Box3().setFromObject(holder), front);
    shownPath = item.path;
    defaultMeta = [`${Math.round(tris).toLocaleString()} tris`, `${meshes} mesh${meshes === 1 ? "" : "es"}`,
      `${size.x.toFixed(2)} × ${size.y.toFixed(2)} × ${size.z.toFixed(2)} units`,
      Object.keys(item.maps || {}).length ? "maps " + Object.keys(item.maps).join(", ") : "no maps"].join(SEP);
    showMeta();
    for (const fn of draft.shown) {
      try { fn(); } catch (e) { console.error("model add-on onShown:", e); }
    }
  }

  function clearModel() {
    destroyAddons();
    disposeObject(root, groups);
    root = holder = null;
    groups = new Set();
    addonLooks = new Map();
    keys = new Map();
    ticks = [];
    planes = [];
    customMeta = null;
    shownPath = null;
    lookBox.replaceChildren();
    addonBox.replaceChildren();
    lease.show(null);
  }

  // Loads run one after another (a plugin's add-on keeps state between prepare and destroy); a newer request skips
  // the ones queued before it.
  function update(next) {
    const t = ++token;
    chain = chain.then(() => run(next, t)).catch((e) => console.error("model view:", e));
    return chain;
  }

  await update(item);
  return {
    update,
    destroy() {
      token++;
      unsubTheme();
      destroyAddons();
      disposeObject(root, groups);
      variantMats.forEach(disposeMaterial);
      sharedMats.forEach(disposeMaterial);
      if (clay) disposeMaterial(clay);
      if (glass) disposeMaterial(glass);
      root = holder = null;
      lease.release();
    },
    key(e) {
      const k = e.key.toLowerCase();
      if (k === "w") { wireBtn.click(); return true; }
      if (k === "s") { spinBtn.click(); return true; }
      if (k === "f") { frameBtn.click(); return true; }
      const fn = keys.get(k);
      if (fn) { fn(); return true; }
    },
  };
}
