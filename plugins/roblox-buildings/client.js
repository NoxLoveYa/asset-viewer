// SPDX-License-Identifier: AGPL-3.0-or-later
// roblox-buildings, client side: two model add-ons.
//   building      a model with a building report beside it (<stem>.json: objects + files): the Building look (one mesh per
//                 finish, tiling set, Roblox's SurfaceAppearance.Color tint), its door, window and furniture props in
//                 their places (Props, I; orange boxes are props still to make) and a cut under each storey's ceiling (C).
//   prop-sidecar  a prop with a sidecar (<stem>.json: objects, some with a slide or a hinge and swing): turned front to -Z
//                 like Studio's importer, and Open (O) eases its drawers, panels and doors open and back.
// The server (server.py) says which models have which file: item.ext["roblox-buildings"] = {report} | {sidecar}.
import * as THREE from "three";
import { pref, setPref, url, own } from "viewer/ui.js";
import { themeColor } from "viewer/views/stage.js";
import { onChange } from "viewer/theme.js";

const PLUGIN = "roblox-buildings";
const EASE_S = 0.4; // an opening part takes this long
const MAX_PROPS = 48; // loaded prop models kept; the least recently used is freed
const CUT_BELOW_CEILING = 0.6; // studs under a storey's ceiling the cut plane lands
const DEFAULT_GLASS = "^Glass"; // pane meshes of a prop when the config names no glassNames
const AXES = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const isMover = (m) => !!m && (m.slide || (m.hinge && m.swing));

let viewerRef = null;
const openingProps = () => viewerRef?.config?.openingProps || null;

async function getJson(ref) {
  const r = await fetch(url(ref.path, ref.mtime), { signal: AbortSignal.timeout(30000) });
  if (!r.ok) throw new Error(`${ref.path}: ${r.status} ${r.statusText}`);
  return r.json();
}

// ---------- themed materials the props share (one each, repainted when the theme changes) ----------
let glass = null, wish = null;
function propGlass() {
  if (!glass) {
    glass = new THREE.MeshPhysicalMaterial({ roughness: 0.05, transmission: 0.6, transparent: true });
    paintGlass();
  }
  return glass;
}
function wishMaterial() {
  if (!wish) {
    wish = new THREE.MeshStandardMaterial({ transparent: true, roughness: 0.8 });
    paintWish();
  }
  return wish;
}
function paintGlass() {
  if (!glass) return;
  const g = themeColor("model-glass");
  glass.color.copy(g.color);
  glass.opacity = g.alpha;
}
function paintWish() {
  if (!wish) return;
  const w = themeColor("wish-box");
  wish.color.copy(w.color);
  wish.opacity = w.alpha;
}
onChange(() => { paintGlass(); paintWish(); });

// ---------- prop models: loaded once, cloned per place (clones share geometry and materials) ----------
// A cached source is never added to a scene, and nothing but the cache frees it: a model's disposal must not take a prop
// that the next building draws too.
const propCache = new Map(); // project path -> Promise<Object3D | null>, least recently used first

function loadProp(api, path) {
  let p = propCache.get(path);
  if (p) {
    propCache.delete(path); // used now: move to the end
  } else {
    p = (async () => {
      const item = api.lookup(path);
      const obj = await api.loadModel(path, item?.mtime, { blank: true }); // the maps below, not the file's named ones
      const set = item?.maps?.Base;
      const mat = set ? api.pbrMaterial(set, api.lookup) : null;
      const glassRx = new RegExp((api.config.glassNames || [DEFAULT_GLASS]).join("|") || DEFAULT_GLASS);
      obj.traverse((o) => {
        if (!o.isMesh) return;
        if (glassRx.test(o.name)) o.material = propGlass();
        else if (mat) o.material = mat;
        o.castShadow = o.receiveShadow = true;
      });
      return obj;
    })().catch((e) => { propCache.delete(path); console.warn(`${PLUGIN}: prop ${path} did not load:`, e.message); return null; });
  }
  propCache.set(path, p);
  if (propCache.size > MAX_PROPS) {
    const [oldest, old] = propCache.entries().next().value;
    propCache.delete(oldest);
    old.then((src) => src && api.disposeObject(src)); // dispose is not destructive: a clone still drawn is uploaded again
  }
  return p.then((src) => (src ? src.clone() : null));
}

// ---------- the building add-on ----------
const building = {
  id: "building",
  applies: (item) => !!item.ext?.[PLUGIN]?.report,
  cut: "off",
  live: null,

  async prepare(api) {
    let report;
    try {
      report = await getJson(api.item.ext[PLUGIN].report);
    } catch (e) {
      console.warn(`${PLUGIN}: no building report:`, e.message);
      return;
    }
    const tiled = new Map(); // object name -> material
    const tex = (p, srgb) => api.loadTexture(p, undefined, { srgb, repeat: true });
    const tiledMaterial = (o) => {
      const m = new THREE.MeshStandardMaterial({ roughness: 1, metalness: o.maps.Metalness ? 1 : 0 });
      m.map = tex(o.maps.Color, true);
      if (o.maps.Normal) m.normalMap = tex(o.maps.Normal);
      if (o.maps.Roughness) m.roughnessMap = tex(o.maps.Roughness);
      if (o.maps.Metalness) m.metalnessMap = tex(o.maps.Metalness);
      if (o.tint) m.color = new THREE.Color("#" + o.tint); // colour from data
      return m;
    };
    api.addLook("@building", "Building", (mesh) => {
      const ob = own(report.objects, mesh.name);
      if (!tiled.has(mesh.name)) tiled.set(mesh.name, ob ? (ob.glass ? api.materials.glass() : tiledMaterial(ob)) : api.materials.clay());
      return tiled.get(mesh.name);
    }, { preferred: true });

    const show = pref("model.props", true);
    const props = await furnish(api, report, show);
    api.addGroup(props);
    api.addButton("Props", "I", (on) => { setPref("model.props", on); props.visible = on; }, { on: show });

    const storeys = report.storeys || [];
    const options = [["off", "Whole"], ...storeys.map((s) => ["s" + s.index, "Storey " + s.name])];
    if (!options.some(([v]) => v === this.cut)) this.cut = "off";
    const clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
    const applyCut = () => {
      const s = this.cut === "off" ? null : storeys.find((x) => "s" + x.index === this.cut);
      if (s) clip.constant = s.y + s.clear - CUT_BELOW_CEILING - api.lift;
      api.setClipping(s ? [clip] : []);
    };
    api.addSeg(options, this.cut, (v) => { this.cut = v; applyCut(); }, { key: "C", label: "Storey cut" });
    api.onShown(applyCut); // the plane needs the height the model ended up at
    api.turnY(Math.PI); // Blockbench's export turned every mesh 180 degrees about Y: back to the front at -Z
    const n = (x) => (x || []).length;
    api.meta([`${(report.tris || 0).toLocaleString()} tris`, `${Object.keys(report.objects).length} meshes`, `${n(report.rooms)} rooms`,
      `${n(report.openings)} openings`, `${n(report.props)} props`, report.size ? `${report.size.map((v) => v.toFixed(1)).join(" × ")} studs` : null,
      n(report.warnings) ? `${n(report.warnings)} warnings` : "no warnings"].filter(Boolean));
    this.live = { tiled, props };
  },

  destroy() {
    if (!this.live) return;
    const { tiled, props } = this.live;
    this.live = null;
    for (const m of tiled.values()) m.dispose(); // not its textures (the cache's) and not the shared glass / clay
    props.traverse((o) => { if (o.userData.wish) o.geometry.dispose(); }); // the boxes are its own; prop clones share the cache's
  },
};

// Props in the building's frame: Blockbench's export turned every mesh 180 degrees about Y, so (x, y, z) -> (-x, y, -z).
async function furnish(api, report, show) {
  const group = new THREE.Group();
  group.name = "@props";
  const jobs = [];
  const place = (obj, pos, yaw) => {
    obj.position.set(-pos[0], pos[1], -pos[2]);
    obj.rotation.y = THREE.MathUtils.degToRad(yaw || 0);
    group.add(obj);
  };
  for (const o of report.openings || []) {
    const template = openingProps();
    if (!o.prop || !template) continue;
    const dir = o.prop.startsWith("Door") ? "doors" : "windows";
    jobs.push(loadProp(api, template.replace("{dir}", dir).replace("{prop}", o.prop)).then((p) => p && place(p, o.pos, o.yaw)));
  }
  for (const p of report.props || []) {
    if (p.exists && p.path) jobs.push(loadProp(api, p.path).then((m) => m && place(m, p.pos, p.yaw)));
    else if (p.size) { // a prop still to make: a box of its size
      const [w, h, d] = p.size;
      const box = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), wishMaterial());
      box.geometry.translate(0, h / 2, 0);
      box.userData.wish = true;
      place(box, p.pos, p.yaw);
    }
  }
  await Promise.all(jobs);
  group.visible = show;
  return group;
}

// ---------- the prop-sidecar add-on ----------
const propSidecar = {
  id: "prop-sidecar",
  applies: (item) => !!item.ext?.[PLUGIN]?.sidecar,

  async prepare(api) {
    let objects;
    try {
      objects = (await getJson(api.item.ext[PLUGIN].sidecar)).objects || {};
    } catch (e) {
      console.warn(`${PLUGIN}: no sidecar:`, e.message);
      return;
    }
    api.turnY(Math.PI); // a prop: turned back like Studio's importer, front at -Z
    // the FBX is turned 180 degrees about Y from the prop's own axes (Blockbench's export), so its slides, hinges and axes are too
    const flip = (v) => new THREE.Vector3(-v[0], v[1], -v[2]);
    const moves = [];
    api.obj.traverse((o) => {
      const m = o.isMesh && own(objects, o.name);
      if (!isMover(m)) return;
      moves.push({ mesh: o, base: o.position.clone(), baseQ: o.quaternion.clone(), slide: m.slide && flip(m.slide),
        ...(m.hinge && m.swing ? { hinge: flip(m.hinge), axis: flip(own(AXES, m.axis) || AXES.y), swing: THREE.MathUtils.degToRad(m.swing) } : {}) });
    });
    if (!moves.length) return;
    let open = pref("model.open", false), amount = open ? 1 : 0;
    const q = new THREE.Quaternion();
    const place = () => {
      const e = amount * amount * (3 - 2 * amount); // smoothstep
      for (const m of moves) {
        m.mesh.position.copy(m.base);
        m.mesh.quaternion.copy(m.baseQ);
        if (m.axis) {
          q.setFromAxisAngle(m.axis, m.swing * e);
          m.mesh.position.sub(m.hinge).applyQuaternion(q).add(m.hinge);
          m.mesh.quaternion.premultiply(q);
        }
        if (m.slide) m.mesh.position.addScaledVector(m.slide, e);
      }
    };
    api.addButton("Open", "O", (on) => { open = on; setPref("model.open", on); }, { on: open });
    api.onTick((dt) => {
      const to = open ? 1 : 0;
      if (amount === to) return false;
      amount = open ? Math.min(1, amount + dt / EASE_S) : Math.max(0, amount - dt / EASE_S);
      place();
      return true;
    });
    place();
  },

  destroy() {},
};

export function register(viewer) {
  viewerRef = viewer; // its config is replaced when the project's config changes: read it where it is used
  viewer.modelAddon(building);
  viewer.modelAddon(propSidecar);
  viewer.keyHelp("I props · C storey cut (buildings) · O open (props)");
}
