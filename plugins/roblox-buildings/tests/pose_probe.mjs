// SPDX-License-Identifier: AGPL-3.0-or-later
// Run by test_pose.py under node: prints the maths of pose.js on small inputs as one JSON object.
// argv: the repo root. three is resolved to the vendored copy.
import { register } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

const root = process.argv[2];
const three = pathToFileURL(path.join(root, "vendor", "three", "build", "three.module.js")).href;
register("data:text/javascript," + encodeURIComponent(
  `export async function resolve(s, c, n) { return s === "three" ? { url: ${JSON.stringify(three)}, shortCircuit: true } : n(s, c); }`));
const THREE = await import("three");
const pose = await import(pathToFileURL(path.join(root, "plugins", "roblox-buildings", "pose.js")).href);

const r = (v) => [v.x, v.y, v.z].map((x) => Math.round(x * 1e4) / 1e4);
const at = (m, v) => r(new THREE.Vector3(...v).applyMatrix4(m));
const out = {};

// placement: a pos and yaw is (-x, y, -z) turned by the yaw; a cf of that yaw gives the same matrix
const a = pose.placement({ pos: [3, 1, 2], yaw: 90 });
const yaw = 90 * Math.PI / 180;
const cf = [3, 1, 2, Math.cos(yaw), 0, Math.sin(yaw), 0, 1, 0, -Math.sin(yaw), 0, Math.cos(yaw)];
const b = pose.placement({ pos: [0, 0, 0], yaw: 0, cf });
out.yawEqualsCf = a.elements.every((e, i) => Math.abs(e - b.elements[i]) < 1e-9);
out.yawOrigin = r(new THREE.Vector3().setFromMatrixPosition(a));

// a cf tipped onto its side: the prop's up (0,1,0) points along Roblox +X, so along the FBX frame's -X
const tip = pose.placement({ cf: [0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, -1] });
out.tippedUp = at(tip, [0, 1, 0]);
out.tippedFront = at(tip, [0, 0, 1]); // prop +Z (FBX frame): a conjugated rotation, checked against the Roblox one below
// the Roblox rotation's columns are the images of X, Y, Z: R * (0, 1, 0) = column 1 = (1, 0, 0), flipped by the turn
out.robloxUpImage = [-1, 0, 0];

// pos and yaw when cf is bad
out.badCfFallsBack = pose.placement({ pos: [1, 2, 3], yaw: 0, cf: [1, 2] }).elements[12] === -1;

// a drawer: a slide of the prop's own (0, 0, -0.5) moves 0.5 of it toward the FBX frame's +Z
const objects = {
  Body: {}, Drawer_1: { slide: [0, 0, -1] }, Glass_D: { slide: [0, 0, -1], glass: true },
  Door_L: { hinge: [1, 0, 0], axis: "y", swing: 90 }, Glass_Door_L: { hinge: [1, 0, 0], axis: "y", swing: 90, glass: true },
  Glass_Other: { hinge: [5, 0, 0], axis: "y", swing: 90, glass: true },
};
const fs = pose.furnitureState(objects, { Drawer_1: 0.5, Door_L: 90, Nope: 1, Body: 3 });
out.furnitureNames = fs.map((t) => t.name);
out.drawerMove = at(fs.find((t) => t.name === "Drawer_1").matrix, [0, 0, 0]);
// a hinge at prop x=1 turned 90 degrees (counter-clockwise from +Y): the leaf's point at prop (0, 0, 0) goes to z = +1 - (the swing)
// prop frame: the point is 1 to the -x of the hinge; Ry(90) takes (-1, 0, 0) to (0, 0, +1); in the prop frame the hinge is (1, 0, 0)
// so the point ends at (1, 0, 1). In the FBX frame that is the flip of it: (-1, 0, -1), from a start of (0, 0, 0)
out.hingeEnd = at(fs.find((t) => t.name === "Door_L").matrix, [0, 0, 0]);

// an opening: the lead leaf is the first hinged mover by name; its pane follows; another mover's angle is ignored
const door = {
  Body: {}, Leaf_B: { hinge: [2, 0, 0], axis: "y", swing: 90 }, Leaf_A: { hinge: [-2, 0, 0], axis: "y", swing: 90 },
  Glass_A: { hinge: [-2, 0, 0], axis: "y", glass: true }, Glass_fixed: { glass: true }, Slider: { slide: [1, 0, 0] },
};
const ajar = pose.openingState(door, { Leaf_A: 20, Leaf_B: 30 });
out.ajarPose = ajar.pose.map((t) => t.name);
out.ajarHide = ajar.hide;
const gone = pose.openingState(door, "removed");
out.removedHide = gone.hide.sort();
out.shutPose = pose.openingState(door, null);
out.zeroPose = pose.openingState(door, { Leaf_A: 0 }).pose.length;

// apply and restore on a mesh under the root
const rootObj = new THREE.Group();
const mesh = new THREE.Mesh();
mesh.position.set(0, 0, 0.25);
rootObj.add(mesh);
mesh.updateMatrix();
const base = mesh.matrix.clone();
pose.apply(mesh, rootObj, base, pose.poseMatrix(pose.motionOf({ slide: [0, 0, 1] }), 2));
out.applied = r(mesh.position);
pose.apply(mesh, rootObj, base, new THREE.Matrix4());
out.restored = r(mesh.position);

console.log(JSON.stringify(out));
