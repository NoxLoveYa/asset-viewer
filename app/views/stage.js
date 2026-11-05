// SPDX-License-Identifier: AGPL-3.0-or-later
// The one three.js stage the 3D views share (one WebGL context for the page): renderer, orbit camera, soft studio
// light with shadows, a ground grid, PBR environment, all coloured by the theme's --stage-* tokens. Views claim the
// stage (a lease) and put one object on it; a newer claim releases the older lease, so a view that finishes mounting
// late can't wipe the live one. The stage draws on demand: when something changed, not 60 times a second.
// A public module: plugins import it as "viewer/views/stage.js".
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { color, num, onChange } from "../theme.js";

const MAX_PIXEL_RATIO = 2;
const SHADOW_MAP = 2048;
const FOV = 35;
const MAX_FRAME_DT = 0.1; // s: a stalled tab doesn't jump an animation
const GRID_SIZE = 40;
const GRID_DIVISIONS = 40;
const GROUND_RADIUS = 20;
const ENV_BLUR = 0.04;
const DAMPING = 0.12;
const AUTO_ROTATE_SPEED = 1.5;
const FRAME_PADDING = 1.45; // room for the bars around a framed box
// the 3/4 view of a model's front, camera side first (x, y, z), for fronts toward -Z and +Z
const VIEW_FRONT = { "-z": [0.55, 0.35, -0.76], "+z": [-0.55, 0.35, 0.76] };

let stage;

// A theme colour token as a three Color and its alpha.
export function themeColor(name) {
  const c = color(name);
  return { color: new THREE.Color().setRGB(c.r / 255, c.g / 255, c.b / 255, THREE.SRGBColorSpace), alpha: c.a };
}

// A loading manager of your own (a loader with a URL modifier, say) asks for a frame whenever one of its files is in, as the
// default one does. Returns the manager.
export function redrawOnLoad(manager) {
  manager.onProgress = () => stage?.invalidate();
  return manager;
}

export function getStage(container) {
  if (stage) {
    container.hidden = false;
    stage.resize();
    return stage;
  }
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, MAX_PIXEL_RATIO));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.append(renderer.domElement);
  container.hidden = false;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const buildEnvironment = () => {
    scene.environment?.dispose();
    scene.environment = pmrem.fromScene(new RoomEnvironment(), ENV_BLUR).texture;
  };
  buildEnvironment();
  const hemi = new THREE.HemisphereLight();
  const key = new THREE.DirectionalLight();
  key.position.set(-6, 10, -8);
  key.castShadow = true;
  key.shadow.mapSize.set(SHADOW_MAP, SHADOW_MAP);
  key.shadow.bias = -0.0005;
  const rim = new THREE.DirectionalLight();
  rim.position.set(6, 4, 8);
  scene.add(hemi, key, key.target, rim);

  const ground = new THREE.Group();
  const grid = new THREE.GridHelper(GRID_SIZE, GRID_DIVISIONS);
  grid.material.vertexColors = false; // one colour from the theme, not the helper's baked ones
  grid.material.transparent = true;
  const shadow = new THREE.Mesh(new THREE.CircleGeometry(GROUND_RADIUS, 64), new THREE.ShadowMaterial());
  shadow.rotation.x = -Math.PI / 2;
  shadow.receiveShadow = true;
  ground.add(grid, shadow);
  scene.add(ground);

  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.01, 500);
  camera.position.set(6, 5, -10); // a character-sized view while the first thing loads
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 2.5, 0);
  controls.enableDamping = true;
  controls.dampingFactor = DAMPING;
  controls.autoRotateSpeed = AUTO_ROTATE_SPEED;

  let dirty = true, content = null, tick = null, owner = 0, last = performance.now(), lost = false;
  const invalidate = () => { dirty = true; };
  controls.addEventListener("change", invalidate);
  // An image that arrives after the model was drawn changes what is drawn, and nothing else says so: every file a loader on
  // three's default manager (textures, glTF, OBJ) or on `redrawOnLoad(manager)` finishes asks for a frame.
  THREE.DefaultLoadingManager.onProgress = invalidate;

  // the tokens the stage is made of; run again when the theme changes
  const applyTheme = () => {
    const sky = themeColor("stage-sky"), ground_ = themeColor("stage-ground"), k = themeColor("stage-key"), r = themeColor("stage-rim");
    hemi.color.copy(sky.color);
    hemi.groundColor.copy(ground_.color);
    hemi.intensity = num("stage-hemi-intensity", 0.8);
    key.color.copy(k.color);
    key.intensity = num("stage-key-intensity", 2.2);
    rim.color.copy(r.color);
    rim.intensity = num("stage-rim-intensity", 0.8);
    scene.environmentIntensity = num("stage-env-intensity", 0.6);
    const g = themeColor("stage-grid");
    grid.material.color.copy(g.color);
    grid.material.opacity = g.alpha;
    grid.material.needsUpdate = true;
    const s = themeColor("stage-shadow");
    shadow.material.color.copy(s.color);
    shadow.material.opacity = s.alpha;
    invalidate();
  };
  applyTheme();
  onChange(applyTheme);

  const lose = (e) => { e.preventDefault(); lost = true; container.dataset.lost = "1"; };
  const restore = () => { lost = false; delete container.dataset.lost; buildEnvironment(); invalidate(); };
  renderer.domElement.addEventListener("webglcontextlost", lose);
  renderer.domElement.addEventListener("webglcontextrestored", restore);

  stage = {
    THREE, scene, camera, controls, renderer, invalidate,
    get lost() { return lost; },
    resize() {
      const w = container.clientWidth, h = container.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      invalidate();
    },
    // Takes the stage: whoever held it before is released (its content leaves, its tick stops). The lease's calls do
    // nothing once a newer claim exists.
    claim() {
      const mine = ++owner;
      clear();
      container.hidden = false;
      const live = () => mine === owner;
      return {
        stage,
        get live() { return live(); },
        // Puts obj on the stage (replacing the last one). tick(dt) runs every frame; it returns false when it
        // changed nothing (the stage then redraws only on input), anything else keeps the frames coming.
        show(obj, onTick) {
          if (!live()) return;
          if (content) scene.remove(content);
          content = obj || null;
          tick = onTick || null;
          if (obj) scene.add(obj);
          invalidate();
        },
        invalidate() { if (live()) invalidate(); },
        // Frames a box from the front-left 3/4 (front: "-z" or "+z", the model's), scaling the grid and shadow to it.
        frame(box, front = "+z") {
          if (!live()) return;
          frameBox(box, front);
        },
        release() {
          if (!live()) return;
          owner++;
          clear();
          container.hidden = true;
        },
      };
    },
  };

  function clear() {
    if (content) scene.remove(content);
    content = null;
    tick = null;
    controls.autoRotate = false;
  }

  function frameBox(box, front) {
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 0.01);
    const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * FRAME_PADDING;
    camera.near = dist / 100;
    camera.far = dist * 100;
    camera.updateProjectionMatrix();
    camera.position.copy(centre).add(new THREE.Vector3(...(VIEW_FRONT[front] || VIEW_FRONT["+z"])).normalize().multiplyScalar(dist));
    controls.target.copy(centre);
    controls.update();
    const s = Math.max(radius / 3, 0.05);
    ground.scale.setScalar(s);
    ground.position.y = box.min.y;
    key.position.set(-6 * s, 10 * s + box.min.y, -8 * s);
    key.target.position.copy(centre);
    Object.assign(key.shadow.camera, { left: -radius * 2, right: radius * 2, top: radius * 2, bottom: -radius * 2, near: 0.1 * s, far: 40 * s });
    key.shadow.camera.updateProjectionMatrix();
    invalidate();
  }

  new ResizeObserver(() => stage.resize()).observe(container);
  stage.resize();
  let reported = "";
  // One frame of the loop: runs the tick and the controls, and draws only if something asked for it. `stage.draw` is the same
  // call, for a test that must not wait on the browser's frame timing.
  function frame() {
    const now = performance.now();
    const dt = Math.min((now - last) / 1000, MAX_FRAME_DT);
    last = now;
    if (container.hidden || lost) return;
    try {
      if (tick && tick(dt) !== false) dirty = true;
      if (controls.update()) dirty = true;
      if (!dirty) return;
      dirty = false;
      renderer.render(scene, camera);
    } catch (e) { // a throwing tick would freeze the stage: drop it, say so once, keep drawing
      tick = null;
      if (String(e) !== reported) { reported = String(e); console.error("stage:", e); }
    }
  }
  stage.draw = frame;
  renderer.setAnimationLoop(frame);
  return stage;
}
