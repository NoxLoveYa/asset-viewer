// SPDX-License-Identifier: AGPL-3.0-or-later
// roblox-buildings: the placement and pose maths of a building's props, apart from the page so a script can run it.
// Frames: a model's FBX (Blockbench's export) is turned 180 degrees about Y from the Roblox frame, so a report's
// (x, y, z) is (-x, y, -z) in the FBX's frame, and a rotation R is its conjugate by that turn (the sign of every
// element with one X or Z index flips). Everything below returns matrices and vectors in the FBX's frame.
import * as THREE from "three";

const S = [-1, 1, -1];
const AXES = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const has = (o, k) => !!o && Object.prototype.hasOwnProperty.call(o, k);
const isVec = (v) => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
export const flip = (v) => new THREE.Vector3(S[0] * v[0], S[1] * v[1], S[2] * v[2]);

// Where a prop's origin goes: `cf` [x, y, z, r00 .. r22] (row-major, the mesh origin's CFrame), else `pos` and `yaw` degrees.
export function placement(p) {
  const m = new THREE.Matrix4();
  const cf = p.cf;
  if (Array.isArray(cf) && cf.length === 12 && cf.every(Number.isFinite)) {
    const r = (i, j) => S[i] * S[j] * cf[3 + i * 3 + j];
    m.set(r(0, 0), r(0, 1), r(0, 2), S[0] * cf[0],
          r(1, 0), r(1, 1), r(1, 2), S[1] * cf[1],
          r(2, 0), r(2, 1), r(2, 2), S[2] * cf[2],
          0, 0, 0, 1);
  } else {
    m.makeRotationY(THREE.MathUtils.degToRad(p.yaw || 0)).setPosition(S[0] * p.pos[0], S[1] * p.pos[1], S[2] * p.pos[2]);
  }
  return m;
}

// The motion of a sidecar object ({slide} or {hinge, axis}), in the FBX's frame; null when it does not move.
// A slide wins over a hinge. `key` tells two objects with one motion apart from the rest (a pane follows its leaf).
export function motionOf(o, defaultAxis) {
  if (!o) return null;
  if (isVec(o.slide)) return { slide: flip(o.slide), key: "s:" + o.slide.join() };
  if (isVec(o.hinge)) {
    const axis = o.axis ?? defaultAxis;
    return { hinge: flip(o.hinge), axis: flip(has(AXES, axis) ? AXES[axis] : AXES.y), key: "h:" + o.hinge.join() + ":" + axis };
  }
  return null;
}

// The matrix that poses a motion: a slide moves `amount` of its offset; a hinge turns `amount` degrees about its axis
// (positive counter-clockwise seen from the axis's + end).
export function poseMatrix(motion, amount) {
  if (motion.slide) return new THREE.Matrix4().makeTranslation(motion.slide.x * amount, motion.slide.y * amount, motion.slide.z * amount);
  const h = motion.hinge;
  return new THREE.Matrix4().makeTranslation(h.x, h.y, h.z)
    .multiply(new THREE.Matrix4().makeRotationAxis(motion.axis, THREE.MathUtils.degToRad(amount)))
    .multiply(new THREE.Matrix4().makeTranslation(-h.x, -h.y, -h.z));
}

// A furniture prop's state {object: amount} (a drawer's fraction of its slide, a door's degrees): the named object, and
// every `Glass_*` object with the same motion, posed. -> [{name, matrix}]
export function furnitureState(objects, state) {
  const out = [];
  for (const [name, amount] of Object.entries(state || {})) {
    const motion = has(objects, name) && typeof amount === "number" ? motionOf(objects[name], "y") : null;
    if (!motion) continue;
    const m = poseMatrix(motion, amount);
    for (const [n, o] of Object.entries(objects)) {
      if (n === name || (n.startsWith("Glass_") && motionOf(o, "y")?.key === motion.key)) out.push({ name: n, matrix: m });
    }
  }
  return out;
}

// A door's or window's state, as Studio's install reads it. The movers are the objects that hinge or slide and are not
// glass; each glass object with the same motion (same slide, or same hinge and axis) belongs to the first such mover.
// "removed": the hinged movers and their panes are not drawn. {Mover: degrees}: the lead leaf (the first hinged mover by
// name) turns; any other mover's angle is ignored. -> {hide: [name], pose: [{name, matrix}]}
export function openingState(objects, state) {
  const out = { hide: [], pose: [] };
  const entries = Object.entries(objects || {}).filter(([, o]) => o && typeof o === "object");
  const motionRaw = (o) => motionOf(o, undefined); // an unset axis equals only an unset axis
  const movers = entries.filter(([, o]) => !o.glass && motionRaw(o));
  const hinged = movers.filter(([, o]) => !isVec(o.slide)).map(([n]) => n);
  const withPanes = (name) => {
    const k = motionRaw(objects[name]).key;
    return [name, ...entries.filter(([, o]) => o.glass && motionRaw(o)?.key === k).map(([n]) => n)];
  };
  if (state === "removed") {
    out.hide = [...new Set(hinged.flatMap(withPanes))];
  } else if (state && typeof state === "object") {
    const lead = [...hinged].sort()[0];
    const amount = lead && state[lead];
    if (typeof amount === "number" && amount !== 0) {
      const m = poseMatrix(motionOf(objects[lead], undefined), amount);
      out.pose = withPanes(lead).map((name) => ({ name, matrix: m }));
    }
  }
  return out;
}

// Pose `mesh` by `matrix` (a pose in the root's frame): its own matrix changes, so `base` (its matrix before) is kept.
// A mesh under other nodes than the root takes the pose in its parent's frame.
const _rel = new THREE.Matrix4();
const _local = new THREE.Matrix4();
export function apply(mesh, root, base, matrix) {
  _rel.identity();
  for (let n = mesh.parent; n && n !== root; n = n.parent) _rel.premultiply(n.matrix);
  _local.copy(_rel).invert().multiply(matrix).multiply(_rel).multiply(base);
  _local.decompose(mesh.position, mesh.quaternion, mesh.scale);
  mesh.updateMatrix();
}
