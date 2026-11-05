// SPDX-License-Identifier: AGPL-3.0-or-later
// Animation view: a clip from a baked JSON ({clip: {fps, looped, frames: [{joint: [x, y, z, qx, qy, qz, qw]}]}}, the
// Transform of the joint whose Part1 is that part) played on a rig from <rigsDir>/*.json (the character's real meshes,
// clothing and decals as export_rig.luau writes them; boxes for parts without a mesh), the way Roblox poses a joint:
// part1 = part0 * C0 * Transform * C1:Inverse(). A prop rig (one with a grip, e.g. a held tool) joins the character's hand.
import * as THREE from "three";
import { getStage, themeColor } from "viewer/views/stage.js";
import { loadModel, pbrMaterial } from "viewer/views/model.js";
import { el, button, seg, sep, stepper, pref, setPref, url, own as ownProp, oneOf, isBoolean } from "viewer/ui.js";
import { onChange } from "viewer/theme.js";

const PLUGIN = "roblox-anim";
const DEFAULT_FPS = 30; // a clip whose fps is not a positive number
const FETCH_TIMEOUT_MS = 30000;
const ANISOTROPY = 8;
const DECAL_OFFSET = -2; // polygonOffsetFactor: a decal sits over its mesh
const IDENTITY = new THREE.Matrix4();
// an FBX exported with axes turned 180 deg about Y from Roblox's (FBX x = -Roblox x, z = -Roblox z)
const FBX_TO_ROBLOX = new THREE.Matrix4().makeRotationY(Math.PI);

const cf = (a) => new THREE.Matrix4().set(a[3], a[4], a[5], a[0], a[6], a[7], a[8], a[1], a[9], a[10], a[11], a[2], 0, 0, 0, 1);

// ---------- loading: rigs, clip files and images are cached by name + mtime; a failure is never cached ----------
// Each is bounded, the least recently used dropped first, so a long session over many files does not hold them all.
const MAX_RIGS = 16, MAX_CLIP_FILES = 32, MAX_IMAGES = 96;
const rigCache = new Map(); // name -> { mtime, promise }
const clipSets = new Map(); // path -> { mtime, data }
const images = new Map(); // url -> Promise<img>

// map.get(key), counted as a use; set() below drops the oldest past `cap`
function touch(map, key) {
  if (!map.has(key)) return undefined;
  const v = map.get(key);
  map.delete(key);
  map.set(key, v);
  return v;
}
function remember(map, key, value, cap) {
  map.delete(key);
  map.set(key, value);
  while (map.size > cap) map.delete(map.keys().next().value);
}

async function fetchJson(u, what) {
  const r = await fetch(u, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  if (!r.ok) throw new Error(`${what}: ${r.status} ${r.statusText}`);
  return r.json();
}

function getRig(dir, { name, mtime }) {
  const have = touch(rigCache, name);
  if (have && have.mtime === mtime) return have.promise;
  const promise = fetchJson(url(`${dir}/${name}.json`, mtime), `rig ${name}`).then((rig) => {
    if (!rig || typeof rig.parts !== "object" || typeof rig.joints !== "object") throw new Error(`rig ${name}: not a rig`);
    return rig;
  });
  remember(rigCache, name, { mtime, promise }, MAX_RIGS);
  promise.catch(() => { if (rigCache.get(name)?.promise === promise) rigCache.delete(name); });
  return promise;
}

async function getClips(item) {
  const cached = touch(clipSets, item.path);
  if (cached?.mtime === item.mtime) return cached.data;
  const data = await fetchJson(url(item.path, item.mtime), item.path);
  if (!data || typeof data !== "object") throw new Error(`${item.path}: not an animation file`);
  remember(clipSets, item.path, { mtime: item.mtime, data }, MAX_CLIP_FILES);
  return data;
}

function image(src) {
  let p = touch(images, src);
  if (!p) {
    p = new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("image did not load: " + src));
      img.src = src;
    });
    remember(images, src, p, MAX_IMAGES);
    p.catch(() => images.delete(src));
  }
  return p.catch(() => null); // a missing texture draws as the part's colour
}

// ---------- what one built rig owns: geometry, materials, textures, freed when it is replaced ----------
const own = (b, x) => { b.own.push(x); return x; };

function boxPart(b, p) {
  const [sx, sy, sz] = p.size;
  const geo = own(b, new THREE.BoxGeometry(sx, sy, sz));
  const mat = own(b, new THREE.MeshStandardMaterial({ color: rgb(p.color), roughness: 0.85 })); // colour from data
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true;
  const edge = own(b, new THREE.LineBasicMaterial({ transparent: true }));
  b.edges.push(edge);
  paintEdge(edge);
  return new THREE.Group().add(mesh, new THREE.LineSegments(own(b, new THREE.EdgesGeometry(geo)), edge));
}

function paintEdge(m) {
  const e = themeColor("model-edge");
  m.color.copy(e.color);
  m.opacity = e.alpha;
}

const rgb = (c) => new THREE.Color().setRGB(c[0] / 255, c[1] / 255, c[2] / 255, THREE.SRGBColorSpace); // colour from data

// ---------- real parts: meshes, classic clothing, textures, decals (as export_rig.luau writes them) ----------

// The classic Shirt / Pants template (585 x 559) as Roblox lays it out: each face of a body segment's box, seen from
// outside, upright. R15 parts make up the R6 segments the template was drawn for.
const TEMPLATE = [585, 559];
const TORSO = { F: [231, 74, 128, 128], B: [427, 74, 128, 128], R: [165, 74, 64, 128], L: [361, 74, 64, 128], U: [231, 8, 128, 64], D: [231, 204, 128, 64] };
const RIGHT = { F: [217, 355, 64, 128], B: [85, 355, 64, 128], R: [151, 355, 64, 128], L: [19, 355, 64, 128], U: [217, 289, 64, 64], D: [217, 485, 64, 64] };
const LEFT = { F: [308, 355, 64, 128], B: [440, 355, 64, 128], R: [506, 355, 64, 128], L: [374, 355, 64, 128], U: [308, 289, 64, 64], D: [308, 485, 64, 64] };
const SEGMENTS = { Torso: [TORSO, ["pants", "shirt"]], RightArm: [RIGHT, ["shirt"]], LeftArm: [LEFT, ["shirt"]], RightLeg: [RIGHT, ["pants"]], LeftLeg: [LEFT, ["pants"]] };
const SEGMENT_OF = {
  UpperTorso: "Torso", LowerTorso: "Torso",
  RightUpperArm: "RightArm", RightLowerArm: "RightArm", RightHand: "RightArm",
  LeftUpperArm: "LeftArm", LeftLowerArm: "LeftArm", LeftHand: "LeftArm",
  RightUpperLeg: "RightLeg", RightLowerLeg: "RightLeg", RightFoot: "RightLeg",
  LeftUpperLeg: "LeftLeg", LeftLowerLeg: "LeftLeg", LeftFoot: "LeftLeg",
};
// Where a point lands on a box face, [across, down] in 0..1, by the axis the face looks along (Roblox fronts face -Z).
const FACES = {
  "-z": ["F", (p, lo, hi) => [(hi.x - p.x) / (hi.x - lo.x), (hi.y - p.y) / (hi.y - lo.y)]],
  "+z": ["B", (p, lo, hi) => [(p.x - lo.x) / (hi.x - lo.x), (hi.y - p.y) / (hi.y - lo.y)]],
  "+x": ["R", (p, lo, hi) => [(hi.z - p.z) / (hi.z - lo.z), (hi.y - p.y) / (hi.y - lo.y)]],
  "-x": ["L", (p, lo, hi) => [(p.z - lo.z) / (hi.z - lo.z), (hi.y - p.y) / (hi.y - lo.y)]],
  "+y": ["U", (p, lo, hi) => [(hi.x - p.x) / (hi.x - lo.x), (hi.z - p.z) / (hi.z - lo.z)]],
  "-y": ["D", (p, lo, hi) => [(hi.x - p.x) / (hi.x - lo.x), (p.z - lo.z) / (hi.z - lo.z)]],
};
const DECAL_AXIS = { Front: "-z", Back: "+z", Right: "+x", Left: "-x", Top: "+y", Bottom: "-y" };
const axisOf = (n) => {
  const x = Math.abs(n.x), y = Math.abs(n.y), z = Math.abs(n.z);
  return x >= y && x >= z ? (n.x > 0 ? "+x" : "-x") : y >= z ? (n.y > 0 ? "+y" : "-y") : n.z > 0 ? "+z" : "-z";
};
const AXIS = { "+x": [1, 0, 0], "-x": [-1, 0, 0], "+y": [0, 1, 0], "-y": [0, -1, 0], "+z": [0, 0, 1], "-z": [0, 0, -1] };

function geometryOf(b, m) {
  const g = own(b, new THREE.BufferGeometry());
  g.setAttribute("position", new THREE.Float32BufferAttribute(m.v, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(m.n, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(m.uv.map((x, i) => (i % 2 ? 1 - x : x)), 2)); // Roblox v runs down
  g.setIndex(m.i);
  if (!m.n.some(Boolean)) g.computeVertexNormals();
  return g;
}

// A new unindexed geometry of the triangles keep(axis, normal) picks, each corner's UV from uvOf(axis, point): the axis
// the triangle's normal looks along and the corner, both in the space of `to`.
function project(b, geo, to, keep, uvOf) {
  const src = geo.toNonIndexed();
  const pos = src.attributes.position, nrm = src.attributes.normal;
  const rot = new THREE.Matrix3().setFromMatrix4(to);
  const out = { v: [], n: [], uv: [] };
  const p = new THREE.Vector3(), n = new THREE.Vector3(), q = new THREE.Vector3();
  for (let t = 0; t < pos.count; t += 3) {
    n.set(0, 0, 0);
    for (let k = 0; k < 3; k++) n.add(q.fromBufferAttribute(nrm, t + k));
    const axis = axisOf(n.applyMatrix3(rot));
    if (!keep(axis, n)) continue;
    for (let k = 0; k < 3; k++) {
      p.fromBufferAttribute(pos, t + k);
      out.v.push(p.x, p.y, p.z);
      q.fromBufferAttribute(nrm, t + k);
      out.n.push(q.x, q.y, q.z);
      out.uv.push(...uvOf(axis, p.applyMatrix4(to)));
    }
  }
  src.dispose();
  const g = own(b, new THREE.BufferGeometry());
  g.setAttribute("position", new THREE.Float32BufferAttribute(out.v, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(out.n, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(out.uv, 2));
  return g;
}

function texture(b, img) {
  const t = own(b, img instanceof HTMLCanvasElement ? new THREE.CanvasTexture(img) : new THREE.Texture(img));
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = ANISOTROPY;
  t.needsUpdate = true;
  return t;
}

// The part's colour with the clothing layers over it, on a template-sized canvas (one per colour and layers, per rig).
async function clothes(b, color, layers) {
  const key = color.join() + "|" + layers.join();
  if (!b.composites.has(key)) {
    b.composites.set(key, (async () => {
      const c = document.createElement("canvas");
      [c.width, c.height] = TEMPLATE;
      const g = c.getContext("2d");
      g.fillStyle = `rgb(${color})`; // colour from data
      g.fillRect(0, 0, c.width, c.height);
      for (const path of layers) {
        const img = await b.image(path);
        if (img) g.drawImage(img, 0, 0, c.width, c.height);
      }
      return texture(b, c);
    })());
  }
  return b.composites.get(key);
}

// Every part of a rig as an object: its mesh with the clothing projected onto it (or its own texture), its decals.
async function rigParts(b, rig) {
  const rest = Object.fromEntries(Object.entries(rig.parts).map(([n, p]) => [n, cf(p.cf)]));
  const geos = Object.fromEntries(Object.entries(rig.parts).filter(([, p]) => p.mesh).map(([n, p]) => [n, geometryOf(b, p.mesh)]));
  // each segment's box at rest, over the meshes of its parts
  const boxes = {};
  for (const [n, g] of Object.entries(geos)) {
    const seg_ = SEGMENT_OF[n];
    if (!seg_) continue;
    g.computeBoundingBox();
    boxes[seg_] ||= new THREE.Box3();
    boxes[seg_].union(g.boundingBox.clone().applyMatrix4(rest[n]));
  }
  const out = {};
  for (const [n, p] of Object.entries(rig.parts)) {
    const geo = geos[n];
    if (!geo) { out[n] = boxPart(b, p); continue; }
    const seg_ = SEGMENTS[SEGMENT_OF[n]];
    const layers = seg_ && rig.clothing ? seg_[1].map((k) => rig.clothing[k]).filter(Boolean) : [];
    let mesh;
    if (layers.length) {
      // each triangle on the template face of the segment's box it looks at
      const [W, H] = TEMPLATE;
      const box = boxes[SEGMENT_OF[n]];
      const uvOf = (axis, pt) => {
        const [face, st] = FACES[axis];
        const [x, y, w, h] = seg_[0][face];
        let [s, t] = st(pt, box.min, box.max);
        s = Math.min(Math.max(s, 0.5 / w), 1 - 0.5 / w); // half a pixel in, so nothing bleeds from the next face
        t = Math.min(Math.max(t, 0.5 / h), 1 - 0.5 / h);
        return [(x + s * w) / W, 1 - (y + t * h) / H];
      };
      const g = project(b, geo, rest[n], () => true, uvOf);
      mesh = new THREE.Mesh(g, own(b, new THREE.MeshStandardMaterial({ map: await clothes(b, p.color, layers), roughness: 0.8 })));
    } else {
      const img = p.texture && (await b.image(p.texture));
      const mat = img
        ? new THREE.MeshStandardMaterial({ map: texture(b, img), roughness: 0.8, alphaTest: 0.1 })
        : new THREE.MeshStandardMaterial({ color: rgb(p.color), roughness: 0.8 }); // colour from data
      mesh = new THREE.Mesh(geo, own(b, mat));
    }
    mesh.castShadow = true;
    const obj = new THREE.Group().add(mesh);
    // decals project flat from their face over the mesh's half that faces it, as Roblox projects a Decal
    geo.computeBoundingBox();
    for (const d of p.decals || []) {
      const img = d.texture && (await b.image(d.texture));
      const axis = DECAL_AXIS[d.face];
      if (!img || !axis) continue;
      const dir = new THREE.Vector3(...AXIS[axis]);
      const { min, max } = geo.boundingBox;
      const g = project(b, geo, IDENTITY, (_, nrm) => nrm.dot(dir) > 0, (_, pt) => {
        const [s, t] = FACES[axis][1](pt, min, max); // the decal's face decides, whatever the triangle's own axis
        return [s, 1 - t];
      });
      const decal = new THREE.Mesh(g, own(b, new THREE.MeshStandardMaterial({ map: texture(b, img), transparent: true, roughness: 0.8,
        polygonOffset: true, polygonOffsetFactor: DECAL_OFFSET, depthWrite: false })));
      obj.add(decal);
    }
    out[n] = obj;
  }
  return out;
}

// The character rig and an optional prop as one set of parts and joints; the prop's root joins the grip part.
// `b` holds what the build owns (disposed with it); the result's dispose() frees it.
async function buildRig(rig, prop, lookup, imageBase) {
  const b = { own: [], edges: [], composites: new Map(), image: (path) => image(imageBase(path)) };
  const group = new THREE.Group();
  const parts = {};
  const joints = {};
  const add = async (r) => {
    const objs = await rigParts(b, r);
    for (const [name, p] of Object.entries(r.parts)) {
      const obj = objs[name];
      obj.matrixAutoUpdate = false;
      group.add(obj);
      parts[name] = { obj, rest: cf(p.cf) };
    }
    for (const [name, j] of Object.entries(r.joints)) joints[name] = { parent: j.parent, c0: cf(j.c0), c1inv: cf(j.c1).invert() };
  };
  await add(rig);
  // feet on the ground: the lowest point of the rest pose
  let minY = Infinity;
  for (const p of Object.values(rig.parts)) minY = Math.min(minY, p.cf[1] - p.size[1] / 2);
  if (prop) {
    await add(prop);
    joints[prop.root] = { parent: prop.grip.part, c0: cf(prop.grip.c0), c1inv: cf(prop.grip.c1).invert() };
    if (prop.mesh) await propMeshes(b, prop, parts, lookup);
  }
  return {
    group, parts, joints, root: rig.root, propRoot: prop?.root, base: new THREE.Matrix4().makeTranslation(0, -minY, 0),
    repaint: () => b.edges.forEach(paintEdge),
    dispose() { b.own.forEach((x) => x.dispose()); b.own.length = 0; },
  };
}

// Swaps a prop's boxes for its model's meshes (named after the parts), each centred on its own box as a MeshPart is.
async function propMeshes(b, prop, parts, lookup) {
  const item = lookup(prop.mesh);
  let model;
  try { model = await loadModel(prop.mesh, item?.mtime); } catch (e) { console.warn(`roblox-anim: ${prop.mesh} did not load:`, e.message); return; }
  model.updateMatrixWorld(true);
  const mat = item?.maps?.Base ? own(b, pbrMaterial(item.maps.Base, lookup)) : null;
  model.traverse((o) => {
    const part = o.isMesh && parts[o.name];
    if (!part) return;
    const geo = own(b, o.geometry.clone().applyMatrix4(new THREE.Matrix4().multiplyMatrices(FBX_TO_ROBLOX, o.matrixWorld)));
    geo.computeBoundingBox();
    geo.translate(...geo.boundingBox.getCenter(new THREE.Vector3()).negate().toArray());
    const own_ = [].concat(o.material);
    if (!mat) own_.forEach((m) => own(b, m)); // the loaded model's own material is drawn, so the build frees it
    const mesh = new THREE.Mesh(geo, mat || own_[0]);
    mesh.castShadow = true;
    part.obj.clear();
    part.obj.add(mesh);
  });
  model.traverse((o) => o.geometry?.dispose()); // the clones above are what is drawn; the loaded geometry is done
}

// ---------- posing ----------

// Joint transforms of one pose: a Matrix4 per joint name, kept between frames (no allocation per frame), valid only for
// the joints set since the last clear().
class Pose {
  constructor() { this.m = new Map(); this.stamp = new Map(); this.epoch = 0; }
  clear() { this.epoch++; }
  slot(name) {
    let m = this.m.get(name);
    if (!m) this.m.set(name, (m = new THREE.Matrix4()));
    this.stamp.set(name, this.epoch);
    return m;
  }
  get(name) { return this.stamp.get(name) === this.epoch ? this.m.get(name) : undefined; }
}

const fpsOf = (clip) => (clip.fps > 0 ? clip.fps : DEFAULT_FPS);
const lengthOf = (clip) => (clip && clip.frames.length ? (clip.frames.length - 1) / fpsOf(clip) : 0);

const _pa = new THREE.Vector3(), _pb = new THREE.Vector3(), _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion();
const _one = new THREE.Vector3(1, 1, 1);

function blend(name, A, B, a, out) {
  const va = A[name] || B[name], vb = B[name] || va;
  _pa.set(va[0], va[1], va[2]).lerp(_pb.set(vb[0], vb[1], vb[2]), a);
  // the bakes round to 4 decimals, so near frames can dot past 1 and three's slerp would skip the blend: normalize first
  _qa.set(va[3], va[4], va[5], va[6]).normalize().slerp(_qb.set(vb[3], vb[4], vb[5], vb[6]).normalize(), a);
  out.slot(name).compose(_pa, _qa, _one);
}

// Joint transforms at time t, interpolated between the baked frames, into out over what it holds (a clip played over
// another takes the joints it keys, as a higher priority does in Roblox; unkeyed joints stay as they were).
function sample(clip, t, out) {
  const n = clip.frames.length;
  if (!n) return;
  const f = Math.min(Math.max(t * fpsOf(clip), 0), n - 1);
  const i = Math.floor(f), a = f - i;
  const A = clip.frames[i], B = clip.frames[Math.min(i + 1, n - 1)];
  for (const name in A) blend(name, A, B, a, out);
  for (const name in B) if (!(name in A)) blend(name, A, B, a, out);
}

// Every part's frame for joint transforms T, the root at base, and offsets on joints' parent-side frames under the
// animations, as the aim layers put them (part1 = part0 * C0 * offset * Transform * C1:Inverse()).
function solve(built, T, base = built.base, offsets = new Map()) {
  const world = new Map([[built.root, base]]);
  const get = (name) => {
    let m = world.get(name);
    if (m) return m;
    const j = built.joints[name];
    const known = j && (world.has(j.parent) || built.parts[j.parent] || built.joints[j.parent]);
    m = known
      ? get(j.parent).clone().multiply(j.c0).multiply(offsets.get(name) || IDENTITY).multiply(T.get(name) || IDENTITY).multiply(j.c1inv)
      : base.clone().multiply(built.parts[name]?.rest || IDENTITY);
    world.set(name, m);
    return m;
  };
  for (const name of Object.keys(built.parts)) get(name);
  return world;
}

function pose(built, T, base, offsets) {
  const world = solve(built, T, base, offsets);
  for (const [name, p] of Object.entries(built.parts)) {
    p.obj.matrix.copy(world.get(name));
    p.obj.matrixWorldNeedsUpdate = true;
  }
}

// ---------- the aim layers on a held-item clip (the plugin config's bodyAim and a file's aimHead) ----------
// The look is -Z (the camera's). A raised item (stance "up") blades the hips `blade` right of it and locks the barrel
// onto it with a turn about the waist; a clip worked during the aim ("busy", Aim held) keeps the blade and twists the
// chest `busyTwist` further right. The neck keeps the head on the look either way; with the item up, `aimHead`
// [yaw, roll, nod?] then nods the head down, turns it right onto the stock and tilts it, right ear down.
const rotY = (a) => new THREE.Matrix4().makeRotationY(a);
const rotX = (a) => new THREE.Matrix4().makeRotationX(a);
const rotZ = (a) => new THREE.Matrix4().makeRotationZ(a);
const rotationOf = (m) => new THREE.Matrix4().extractRotation(m);
const yawOf = (v) => Math.atan2(-v.x, -v.z);
const pitchOf = (v) => Math.atan2(v.y, Math.hypot(v.x, v.z));
const deg = THREE.MathUtils.degToRad;
const NO_AIM = { blade: 0, busyTwist: 0, lockMax: 0 }; // a stance without bodyAim numbers changes nothing

// The turn about the waist joint, in its frame, that brings the barrel (the prop root's look) onto the look: a yaw and
// a pitch, never a roll, at most lockMax (GunLock.update, settled).
function gunLock(built, T, base, lockMax) {
  const waist = built.joints.UpperTorso;
  if (!built.propRoot || !waist) return new THREE.Matrix4();
  const world = solve(built, T, base);
  const barrel = new THREE.Vector3(0, 0, -1).transformDirection(world.get(built.propRoot));
  const gun = barrel.applyMatrix4(rotationOf(world.get("UpperTorso")).invert()); // in the chest
  const u = gun.applyMatrix4(rotationOf(T.get("UpperTorso") || IDENTITY).multiply(rotationOf(waist.c1inv)));
  const frame = rotationOf(world.get("LowerTorso")).multiply(rotationOf(waist.c0)); // the waist's frame, before its turn
  const v = new THREE.Vector3(0, 0, -1).applyMatrix4(frame.invert());
  const turn = rotY(yawOf(v)).multiply(rotX(pitchOf(v) - pitchOf(u))).multiply(rotY(-yawOf(u)));
  const q = new THREE.Quaternion().setFromRotationMatrix(turn);
  const angle = 2 * Math.acos(Math.min(1, Math.abs(q.w)));
  if (angle > lockMax) q.copy(new THREE.Quaternion().slerp(q, lockMax / angle));
  return new THREE.Matrix4().makeRotationFromQuaternion(q);
}

// The root and the joint offsets of a stance, or null for none.
function stancePose(built, T, stance, aim, aimHead) {
  if (!stance) return null;
  aim = { ...NO_AIM, ...aim };
  const base = built.base.clone().multiply(rotY(-deg(aim.blade)));
  const offsets = new Map();
  if (stance === "busy") {
    offsets.set("UpperTorso", rotY(-deg(aim.busyTwist)));
    offsets.set("Head", rotY(deg(aim.blade + aim.busyTwist)));
  } else {
    const lock = gunLock(built, T, base, deg(aim.lockMax));
    offsets.set("UpperTorso", lock);
    const head = lock.clone().invert().multiply(rotY(deg(aim.blade)));
    if (aimHead) head.multiply(rotX(-deg(aimHead[2] || 0))).multiply(rotZ(-deg(aimHead[1]))).multiply(rotY(-deg(aimHead[0])));
    offsets.set("Head", head);
  }
  return { base, offsets };
}

// ---------- the view ----------
export async function mount(item, ctx) {
  const stage = getStage(ctx.stage3d);
  const lease = stage.claim();
  const extra = () => ctx.catalogExtra(PLUGIN) || { rigsDir: "", rigs: [] };
  const imageBase = (path) => url(`${extra().rigsDir}/${path}`, rigMtime(path));
  const rigMtime = (path) => extra().rigs.find((r) => r.name === path.split("/")[0])?.mtime;

  let rigs = []; // [{ name, mtime, rig }] the rigs that loaded; a broken one is left out
  let characters = [], props = [], rigSig = "";
  let clip, built, token = 0, path = null, clipId = null;
  let t = 0, playing = true, loop = true;
  let speed = pref("anim.speed", 1, oneOf(0.25, 0.5, 1, 2));
  // layers: the clips under it (config `under`) and the aim stance; off shows the clip alone
  let under = [];
  let layered = pref("anim.game", true), aimed = pref("anim.aimed", true);
  let rigName = null, propName = null;
  const T = new Pose();

  // the rig and prop of a clip file: the last ones picked for it in this browser, else the config's
  const choiceFor = (n) => {
    const saved = ownProp(pref("anim.rigs", {}), n.path);
    const names = characters.map((r) => r.name);
    const rig = [saved?.rig, n.rig].find((x) => x && names.includes(x)) || names[0] || null;
    const propNames = props.map((r) => r.name);
    const prop = saved && (saved.prop === null || propNames.includes(saved.prop)) ? saved.prop : propNames.includes(n.prop) ? n.prop : null;
    return { rig, prop };
  };
  const saveChoice = () => setPref("anim.rigs", { ...pref("anim.rigs", {}), [path]: { rig: rigName, prop: propName } });

  const playBtn = button("Pause", () => setPlaying(!playing), { key: "Space", on: true });
  const timeline = el("input", "timeline");
  Object.assign(timeline, { type: "range", min: 0, max: 1, step: 0.0001, value: 0 });
  timeline.setAttribute("aria-label", "Timeline");
  timeline.addEventListener("input", () => { setPlaying(false); t = +timeline.value * lengthOf(clip); draw(); });
  const readout = el("span", "readout");
  const speedSeg = seg([[0.25, "¼x"], [0.5, "½x"], [1, "1x"], [2, "2x"]], speed, (v) => { speed = v; setPref("anim.speed", v); }, { label: "Speed" });
  // once set, the loop choice holds for every clip; until then each clip plays as authored
  const loopBtn = button("Loop", () => { loop = !loop; setPref("anim.loop", loop); loopBtn.set(loop); }, { key: "L", on: loop });
  const pickers = el("span", "barslot"); // the rig and prop steppers, made again when the rig list changes
  const layersBtn = button("Layers", () => { layered = !layered; setPref("anim.game", layered); layersBtn.set(layered); showAim(); draw(); },
    { key: "G", on: layered, title: "Play the clips layered under this one and the aim layers" });
  const aimBtn = button("Aim held", () => { aimed = !aimed; setPref("anim.aimed", aimed); aimBtn.set(aimed); draw(); },
    { key: "A", on: aimed });
  const showAim = () => (aimBtn.hidden = !(layered && item.stance === "busy"));
  ctx.bar.append(timeline, playBtn, readout, sep(), speedSeg, loopBtn, sep(), pickers, sep(), layersBtn, aimBtn);

  let rigStep = null, propStep = null;
  function makePickers() {
    rigStep = stepper("Rig", characters.map((r) => [r.name, r.name]), rigName, (v) => { rigName = v; saveChoice(); rebuild(true); });
    propStep = stepper("Prop", [[null, "None"], ...props.map((r) => [r.name, r.name])], propName, (v) => { propName = v; saveChoice(); rebuild(false); });
    pickers.replaceChildren(rigStep, propStep);
  }

  function setPlaying(v) {
    playing = v;
    if (v && !loop && t >= lengthOf(clip)) t = 0;
    playBtn.label(v ? "Pause" : "Play");
    playBtn.set(v);
  }

  function draw() {
    if (!clip || !built) return;
    T.clear();
    if (layered) {
      for (const c of under) {
        const len = lengthOf(c);
        sample(c, c.looped && len > 0 ? t % len : t, T); // looped ones run on under it
      }
    }
    sample(clip, t, T);
    const stance = layered && (item.stance === "up" || (item.stance === "busy" && aimed)) ? item.stance : null;
    const s = stancePose(built, T, stance, item.bodyAim, item.aimHead);
    pose(built, T, s?.base, s?.offsets);
    const len = lengthOf(clip);
    timeline.value = len ? t / len : 0;
    readout.textContent = `${String(Math.round(t * fpsOf(clip))).padStart(2, "0")} / ${clip.frames.length - 1}  ·  ${t.toFixed(2)} s`;
    lease.invalidate();
  }

  // advances the clock while playing; false (nothing changed) lets the stage rest while it is paused
  function tick(dt) {
    if (!playing || !clip) return false;
    t += dt * speed;
    const len = lengthOf(clip);
    if (t >= len) {
      if (loop && len > 0) t %= len;
      else { t = len; setPlaying(false); }
    }
    draw();
    return true;
  }

  // The rigs the catalog lists, each loaded (cached by name + mtime); one that fails is skipped, not fatal.
  async function syncRigs() {
    const e = extra();
    const sig = JSON.stringify([e.rigsDir, e.rigs]);
    if (sig === rigSig) return false;
    const results = await Promise.allSettled(e.rigs.map((r) => getRig(e.rigsDir, r)));
    rigs = e.rigs.map((r, i) => (results[i].status === "fulfilled" ? { ...r, rig: results[i].value } : null)).filter(Boolean);
    results.forEach((r, i) => { if (r.status === "rejected") console.warn("roblox-anim:", r.reason.message); });
    characters = rigs.filter((r) => !r.rig.grip);
    props = rigs.filter((r) => r.rig.grip);
    rigSig = sig;
    return true;
  }

  function swapBuilt(next, reframe) {
    const old = built;
    built = next;
    lease.show(built.group, tick);
    old?.dispose();
    if (reframe) {
      pose(built, new Pose());
      built.group.updateMatrixWorld(true);
      lease.frame(new THREE.Box3().setFromObject(built.group), "-z"); // Roblox fronts face -Z
    }
  }

  async function rebuild(reframe) {
    const t0 = ++token;
    const rigEntry = characters.find((r) => r.name === rigName);
    if (!rigEntry) { ctx.meta(`No rig to play on: add one to ${extra().rigsDir || "the rigs folder"}`); return; }
    const propEntry = propName ? props.find((r) => r.name === propName) : null;
    let next;
    try {
      next = await buildRig(rigEntry.rig, propEntry?.rig || null, ctx.item, imageBase);
    } catch (e) {
      console.error("roblox-anim: building the rig failed:", e);
      if (t0 === token) ctx.meta("Can't build the rig: " + e.message);
      return;
    }
    if (t0 !== token) return next.dispose();
    swapBuilt(next, reframe);
    draw();
  }

  async function update(nextItem) {
    const t0 = ++token;
    const newSet = nextItem.path !== path;
    let data, layers;
    try {
      data = await getClips(nextItem);
      layers = await Promise.all((nextItem.under || []).filter((u) => u.path && ctx.item(u.path))
        .map(async (u) => { try { return ownProp(await getClips(ctx.item(u.path)), u.name); } catch { return undefined; } }));
    } catch (e) {
      if (t0 === token) ctx.meta("Can't load this animation: " + e.message);
      return;
    }
    const rigsChanged = await syncRigs();
    if (t0 !== token) return;
    const c = ownProp(data, nextItem.name);
    if (!c || !Array.isArray(c.frames) || !c.frames.length) { ctx.meta(`${nextItem.name}: no frames in ${nextItem.path}`); return; }
    under = layers.filter((l) => l && Array.isArray(l.frames) && l.frames.length);
    const changed = nextItem.id !== clipId;
    const at = clipId === null ? pref("anim.at", null, (v) => v && typeof v.id === "string" && Number.isFinite(v.t) && typeof v.playing === "boolean") : null; // reopened (a reload, back from another view): where it was
    item = nextItem;
    clip = c;
    clipId = nextItem.id;
    path = nextItem.path;
    if (changed) {
      const back = at?.id === nextItem.id;
      t = back ? Math.min(at.t, lengthOf(clip)) : 0;
      loop = pref("anim.loop", null, isBoolean) ?? !!c.looped;
      loopBtn.set(loop);
      setPlaying(back ? at.playing : true);
    }
    const keyed = new Set(c.frames.flatMap((f) => Object.keys(f)));
    ctx.meta([`${c.frames.length - 1} frames`, `${fpsOf(c)} fps`, `${lengthOf(c).toFixed(2)} s`, c.looped ? "looped" : "one-shot",
      c.priority, `${keyed.size} joints keyed`, nextItem.under?.length && `over ${nextItem.under.map((u) => u.name).join(", ")}`,
      nextItem.stance === "up" && "item raised"].filter(Boolean).join("  ·  "));
    showAim();
    const want = choiceFor(nextItem);
    if (!built || rigsChanged || (newSet && (want.rig !== rigName || want.prop !== propName))) {
      const reframe = !built || want.rig !== rigName;
      rigName = want.rig;
      propName = want.prop;
      makePickers();
      await rebuild(reframe);
    } else draw();
  }

  const saveAt = () => clip && setPref("anim.at", { id: clipId, t, playing });
  addEventListener("pagehide", saveAt);
  const unsubTheme = onChange(() => { built?.repaint(); lease.invalidate(); });
  await update(item);
  return {
    update,
    destroy() {
      saveAt();
      removeEventListener("pagehide", saveAt);
      unsubTheme();
      token++;
      lease.release();
      built?.dispose();
      built = null;
    },
    key(e) {
      if (e.key === " ") { setPlaying(!playing); return true; }
      if (e.key === "l" || e.key === "L") { loopBtn.click(); return true; }
      if (e.key === "g" || e.key === "G") { layersBtn.click(); return true; }
      if ((e.key === "a" || e.key === "A") && !aimBtn.hidden) { aimBtn.click(); return true; }
      if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && clip) {
        setPlaying(false);
        const f = Math.round(t * fpsOf(clip)) + (e.key === "ArrowRight" ? 1 : -1);
        t = Math.min(Math.max(f, 0), clip.frames.length - 1) / fpsOf(clip);
        draw();
        return true;
      }
      if (e.key === "Home") { t = 0; draw(); return true; }
    },
  };
}
