// apps/puzzle_ui/viewer/app.js
import * as THREE from './libs/three/three.module.js';
import { OrbitControls } from './libs/three/examples/jsm/controls/OrbitControls.js';

// Define window.viewer very early
window.viewer = window.viewer || {};

const rootEl = document.getElementById('app');
let renderer, camera, controls;
let lastRunId = null;
let lastBBoxKey = null;

// Camera persistence lock (first-fit only)
let __zoomLocked = false;
let __savedOrthoZoom = null;
let __savedPerspDist = null;

function __saveZoom() {
  if (typeof camera === "undefined" || !camera) return;
  if (camera.isOrthographicCamera) {
    __savedOrthoZoom = camera.zoom;
  } else {
    // distance from camera to current controls target (or origin fallback)
    const tgt = (typeof controls !== "undefined" && controls) ? controls.target : new THREE.Vector3(0,0,0);
    __savedPerspDist = camera.position.clone().sub(tgt).length();
  }
}

function __restoreZoom() {
  if (typeof camera === "undefined" || !camera) return;
  if (camera.isOrthographicCamera) {
    if (__savedOrthoZoom != null) {
      camera.zoom = __savedOrthoZoom;
      camera.updateProjectionMatrix();
    }
  } else if (typeof controls !== "undefined" && controls && __savedPerspDist != null) {
    const dir = camera.position.clone().sub(controls.target).normalize();
    camera.position.copy(dir.multiplyScalar(__savedPerspDist).add(controls.target));
    // perspective projection doesn’t need updateProjectionMatrix for zoom restores
  }
}

// Optional (nice-to-have): keep the saved zoom in sync with user actions:
if (typeof controls !== "undefined" && controls) {
  controls.addEventListener("change", __saveZoom);
}

const PALETTE = [
  '#FF6B6B', '#4D96FF', '#FFD166', '#06D6A0', '#9B5DE5',
  '#FF924C', '#00BBF9', '#F15BB5', '#43AA8B', '#EE964B',
  '#577590', '#E63946', '#2A9D8F', '#E9C46A', '#F4A261',
  '#8ECAE6', '#219EBC', '#3A86FF', '#8338EC', '#FB5607',
  '#FFBE0B', '#7CB518', '#2EC4B6', '#B5179E', '#3F88C5'
];

// ---------- init ----------
function initThree() {
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  // nicer tonemapping / color
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  renderer.setSize(rootEl.clientWidth, rootEl.clientHeight);
  rootEl.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  window.scene = scene;  // expose to Qt-injected JS

  (function(){
    window.viewer = window.viewer || {};

    function collectMeshes(){
      const out = [];
      if (scene) scene.traverse(o => { if (o && o.isMesh) out.push(o); });
      out.sort((a,b) => {
        const an=(a.name||"")+"", bn=(b.name||"")+"";
        return an.localeCompare(bn) || (a.id-b.id);
      });
      return out;
    }

    window.viewer.getPieceCount = function(){
      return collectMeshes().length | 0;
    };

    window.viewer.setRevealCount = function(n){
      const arr = collectMeshes();
      const t = Math.max(0, Math.min(arr.length, (n|0)));
      for (let i=0; i<arr.length; i++) arr[i].visible = (i < t);
      return arr.length | 0;
    };

    window.viewer.resetRevealOrder = function(){ return true; };
  })();

  camera = new THREE.OrthographicCamera(-5, 5, 5, -5, -100, 100);
  camera.position.set(5, 5, 5);
  camera.up.set(0, 1, 0);
  camera.lookAt(0, 0, 0);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enablePan = true;
  controls.enableRotate = true;
  controls.enableZoom = true;

  // --- Brightness control (v1.3 add) ---
  let ambientLight = new THREE.AmbientLight(0xffffff, 1.0);
  scene.add(ambientLight);

  let dirLight = new THREE.DirectionalLight(0xffffff, 1.5);
  dirLight.position.set(5, 6, 7);
  scene.add(dirLight);

  window.setBrightness = function (value) {
    const v = Math.max(0.1, Math.min(Number(value) || 1.0, 5.0));
    ambientLight.intensity = 1.0 * v;
    dirLight.intensity = 1.5 * v;

    if (renderer && renderer.toneMappingExposure !== undefined) {
      renderer.toneMappingExposure = v;
    }
    console.log("Brightness set to", v);
  };

  setBrightness(1.5);

  animate();
  window.addEventListener('resize', onResize);
}

function onResize() {
  const w = rootEl.clientWidth, h = rootEl.clientHeight;
  const aspect = Math.max(1e-6, w / Math.max(1, h));
  const view = 6;
  camera.left = -view * aspect;
  camera.right = view * aspect;
  camera.top = view;
  camera.bottom = -view;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(window.scene, camera);
}

// ---------- helpers ----------
function letterId(str) {
  // Map 'A'..'Z','AA'.. to 0.. (base-26), then wrap into palette length
  const s = String(str).toUpperCase();
  let n = 0;
  for (let i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64);
  return (n - 1) % PALETTE.length;
}

function normalizePieces(raw) {
  const arr = Array.isArray(raw) ? raw : [];
  return arr.map((p, idx) => {
    const centers =
      Array.isArray(p.centers) ? p.centers :
      Array.isArray(p.world_centers) ? p.world_centers :
      [];
    let idNorm;
    if (typeof p.id === 'number') idNorm = p.id;
    else if (typeof p.id === 'string') idNorm = letterId(p.id);
    else idNorm = idx;

    const name = p.name || (typeof p.id === 'string' ? p.id : `P${String(idNorm).padStart(2, '0')}`);
    return { id: idNorm, name, centers };
  });
}

function computeBbox(pieces, r) {
  // Build from all centers
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  let any = false;
  for (const p of pieces) {
    for (const c of p.centers) {
      if (!Array.isArray(c) || c.length < 3) continue;
      const [x, y, z] = c;
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) continue;
      any = true;
      if (x < minX) minX = x; if (y < minY) minY = y; if (z < minZ) minZ = z;
      if (x > maxX) maxX = x; if (y > maxY) maxY = y; if (z > maxZ) maxZ = z;
    }
  }
  if (!any) {
    // fallback tiny bbox around origin
    return { min: [-r, -r, -r], max: [r, r, r] };
  }
  const pad = Math.max(r * 1.5, 0.25);
  return { min: [minX - pad, minY - pad, minZ - pad], max: [maxX + pad, maxY + pad, maxZ + pad] };
}

function fitOrthoToBbox(bbox) {
  if (__zoomLocked) return;  // never refit after the first time

  const min = new THREE.Vector3().fromArray(bbox.min);
  const max = new THREE.Vector3().fromArray(bbox.max);
  const size = new THREE.Vector3().subVectors(max, min);
  const center = new THREE.Vector3().addVectors(min, max).multiplyScalar(0.5);

  const margin = 1.25;
  const longest = Math.max(size.x, size.y, size.z) * margin;
  const aspect = (rootEl.clientWidth || 1) / (rootEl.clientHeight || 1);

  camera.left = -longest * aspect * 0.6;
  camera.right = longest * aspect * 0.6;
  camera.top = longest * 0.6;
  camera.bottom = -longest * 0.6;

  camera.position.set(center.x + longest, center.y + longest, center.z + longest);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  __saveZoom();     // capture the initial zoom
  __zoomLocked = true;
}

function clearSceneMeshes() {
  const toRemove = [];
  window.scene.traverse(o => { if (o.userData && o.userData.isPieceMesh) toRemove.push(o); });
  toRemove.forEach(o => {
    window.scene.remove(o);
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  });
}

// Add a tiny cleaner that removes the previous display for a piece
function clearPieceDisplay(container) {
  const toRemove = [];
  container.children.forEach(o => {
    if (o.isMesh && (o.userData?.isAtom || o.userData?.isBond)) toRemove.push(o);
  });
  toRemove.forEach(o => {
    container.remove(o);
    // If bonds use per-bond CylinderGeometry, free it:
    if (o.userData?.isBond && o.geometry) o.geometry.dispose();
    // Do NOT dispose shared sphere geometries/materials if reused elsewhere.
  });
}

// ---- Display root: everything we draw for the current state lives here ----
let DISPLAY_ROOT;
function ensureDisplayRoot() {
  if (!DISPLAY_ROOT) {
    DISPLAY_ROOT = new THREE.Group();
    DISPLAY_ROOT.name = "DISPLAY_ROOT";
    scene.add(DISPLAY_ROOT);
  }
  return DISPLAY_ROOT;
}
function resetDisplayRoot() {
  const root = ensureDisplayRoot();
  for (let i = root.children.length - 1; i >= 0; i--) {
    const child = root.children[i];
    root.remove(child);
    // We’re using shared geometries for bonds; no per-mesh dispose needed here.
  }
}

// ===== Bonds (fixed radius, neighbor-only) =====
const BOND_RADIUS   = 0.12;   // tweak to taste (e.g. 0.08–0.18 world units)
const BOND_SEGMENTS = 12;     // cylinder roundness
const _UP_Y         = new THREE.Vector3(0, 1, 0);
const SHARED_BOND_GEOMETRY = new THREE.CylinderGeometry(1, 1, 1, BOND_SEGMENTS, 1, false);

// Make one cylinder between two LOCAL positions a,b, using the piece's material
function _createBondMesh(a, b, radius, material) {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  if (len < 1e-9) return null;

  const mesh = new THREE.Mesh(SHARED_BOND_GEOMETRY, material);

  // midpoint
  mesh.position.copy(new THREE.Vector3().addVectors(a, b).multiplyScalar(0.5));
  // orient +Y → dir
  mesh.quaternion.setFromUnitVectors(_UP_Y, dir.clone().normalize());
  // scale: X/Z = radius, Y = actual distance
  mesh.scale.set(radius, len, radius);

  // tag so later traversals can ignore bonds when collecting atoms
  mesh.userData.isBond = true;
  return mesh;
}

/**
 * Build bonds right where the piece's spheres were just added.
 * container: Object3D you added the spheres to (piece container)
 * atoms:     array of the sphere Meshes you just created (for this piece)
 * material:  the same material used for those spheres (pass the reference)
 */
function addBondsForAtoms(container, atoms, material) {
  if (!atoms || atoms.length < 2 || BOND_RADIUS <= 0) return;

  // shortest non-zero distance = neighbor distance
  let minDist = Infinity;
  for (let i = 0; i < atoms.length; i++) {
    for (let j = i + 1; j < atoms.length; j++) {
      const d = atoms[i].position.distanceTo(atoms[j].position);
      if (d > 1e-6 && d < minDist) minDist = d;
    }
  }
  if (!isFinite(minDist)) return;

  const EPS = minDist * 0.05; // ±5% tolerance; bump to 0.06–0.08 if needed

  // only connect neighbor pairs (distance ≈ minDist)
  for (let i = 0; i < atoms.length; i++) {
    for (let j = i + 1; j < atoms.length; j++) {
      const d = atoms[i].position.distanceTo(atoms[j].position);
      if (Math.abs(d - minDist) <= EPS) {
        const bond = _createBondMesh(atoms[i].position, atoms[j].position, BOND_RADIUS, material);
        if (bond) container.add(bond);
      }
    }
  }
}

// ---------- main payload entry ----------
function drawPayload(payload) {
  // Normalize schema differences
  const r = (typeof payload?.r === 'number') ? payload.r : 0.5;
  const pieces = normalizePieces(payload?.pieces);

  // Choose/compute bbox
  let bbox = payload?.bbox;
  if (!bbox || !Array.isArray(bbox.min) || !Array.isArray(bbox.max)) {
    bbox = computeBbox(pieces, r);
  }

  const isNewRun = payload?.run_id !== lastRunId;
  const bboxKey = JSON.stringify(bbox);

  clearSceneMeshes();

  // Zoom persistence
  let __zoomLocked = false;   // becomes true after the first successful fit
  let __savedOrthoZoom = null;
  let __savedPerspDist = null;

  function __saveZoom() {
    if (!camera) return;
    if (camera.isOrthographicCamera) {
      __savedOrthoZoom = camera.zoom;
    } else {
      // distance from camera to pivot/target
      const tgt = controls?.target || new THREE.Vector3();
      __savedPerspDist = camera.position.distanceTo(tgt);
    }
  }

  function __restoreZoom() {
    if (!camera) return;
    if (camera.isOrthographicCamera && __savedOrthoZoom != null) {
      camera.zoom = __savedOrthoZoom;
      camera.updateProjectionMatrix();
    } else if (__savedPerspDist != null && controls) {
      const dir = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
      camera.position.copy(dir.multiplyScalar(__savedPerspDist).add(controls.target));
      // no projection change needed for perspective
    }
  }

  // Optional: keep the saved zoom in sync with the user’s wheel/dragging
  controls?.addEventListener('change', () => __saveZoom());

  // Guard your initial fit vs. later refreshes
  if (!__zoomLocked) {
    fitOrthoToBbox(bbox);
    __saveZoom();     // capture the initial zoom
    __zoomLocked = true;
  } else {
    // On refresh/reveal/watcher updates: do NOT refit or change zoom
    __restoreZoom();  // keep user zoom exactly as it was
  }

  // Skip any later code that recomputes zoom/FOV
  if (!__zoomLocked) {
    // existing auto-zoom/FOV code (first time only)
    __saveZoom();
    __zoomLocked = true;
  } else {
    // later updates should NOT touch zoom/FOV
    __restoreZoom();
  }

  // a bit smoother for nicer specular highlights (tune if perf dips)
  const sphereGeom = new THREE.SphereGeometry(r, 24, 16);

  resetDisplayRoot();

  pieces.forEach(piece => {
    const centers = piece.centers || [];
    if (!centers.length) return;
    const color = new THREE.Color(PALETTE[piece.id % PALETTE.length]);
    const mat = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.85,
      roughness: 0.25
      // envMapIntensity: 1.0  // leave for later if we add an env map
    });
    const inst = new THREE.InstancedMesh(sphereGeom, mat, centers.length);
    inst.userData.isPieceMesh = true;

    const tmp = new THREE.Object3D();
    const atomMeshes = [];
    for (let i = 0; i < centers.length; i++) {
      const c = centers[i];
      if (!Array.isArray(c) || c.length < 3) continue;
      tmp.position.set(c[0], c[1], c[2]);
      tmp.updateMatrix();
      inst.setMatrixAt(i, tmp.matrix);
      const atomMesh = new THREE.Mesh(sphereGeom, mat);
      atomMesh.position.set(c[0], c[1], c[2]);
      atomMesh.userData.isAtom = true;  // Tag atoms when you create them
      atomMeshes.push(atomMesh);
    }
    inst.instanceMatrix.needsUpdate = true;
    const pieceGroup = new THREE.Group();
    pieceGroup.add(inst);
    atomMeshes.forEach(mesh => pieceGroup.add(mesh));
    clearPieceDisplay(pieceGroup);      // Call the cleaner at the start of your piece draw/update function
    ensureDisplayRoot().add(pieceGroup);
    addBondsForAtoms(pieceGroup, atomMeshes, mat);
  });
}

// ===== Bonds: Step 1 (scaffold only; no rendering yet) =====
const Bonds = {
  radiusR: 0.25,      // fraction of sphere radius; NOT used yet
  meshes: new Map(),  // reserved
  data:   new Map(),  // reserved
  baseGeom: null      // reserved
};

// Expose a global setter so the UI can send values; no side effects yet.
window.setBondRadius = function (v) {
  const x = Math.max(0.0, Math.min(Number(v) || 0.25, 0.60));
  Bonds.radiusR = x;
  // Debug only; remove later
  if (typeof console !== "undefined") console.log("[Bonds] radiusR =", x);
};
// ===== End Bonds: Step 1 =====

// ---- CH4: one-time pivot & orthographic fit (no refits on updates) ----
window.viewer = window.viewer || {};
(function () {
  let _fitDone = false;

  function _sceneBox() {
    const box = new THREE.Box3();
    let any = false;
    scene.traverse(o => {
      if (o && o.isMesh && o.geometry) { box.expandByObject(o); any = true; }
    });
    return any ? box : null;
  }

  function _centerAndFitOrtho(margin = 1.15) {
    const box = _sceneBox();
    if (!box) return false;

    // center
    const center = new THREE.Vector3();
    box.getCenter(center);

    // shift camera so target = center without changing view direction
    const oldTarget = controls.target.clone();
    const delta = center.clone().sub(oldTarget);
    controls.target.copy(center);
    camera.position.add(delta);
    controls.update();

    // bounding sphere for stable fit regardless of orientation
    const sphere = new THREE.Sphere();
    box.getBoundingSphere(sphere);
    const r = Math.max(sphere.radius, 1e-6); // avoid zero

    // orthographic half-sizes at zoom=1
    const halfW = Math.abs(camera.right - camera.left) * 0.5;
    const halfH = Math.abs(camera.top - camera.bottom) * 0.5;

    // choose zoom so sphere fits with margin in both dimensions
    const z1 = halfW / (r * margin);
    const z2 = halfH / (r * margin);
    const zoom = Math.max(0.01, Math.min(z1, z2));

    if (!__zoomLocked) {
      camera.zoom = zoom;
      camera.updateProjectionMatrix();
    }

    return true;
  }

  // Public API
  window.viewer.fitOnce = function (opts) {
    if (_fitDone) return true;
    // Try now; if geometry isn’t ready yet, retry a few frames.
    let tries = 0;
    function attempt() {
      tries++;
      const ok = _centerAndFitOrtho((opts && opts.margin) || 1.15);
      if (ok || tries > 30) { _fitDone = ok; return; }
      requestAnimationFrame(attempt);
    }
    attempt();
    return true;
  };

  window.viewer.resetFit = function () {
    _fitDone = false;
    return true;
  };
})();

// ---------- WebChannel hookup ----------
function setupWebChannel() {
  // qwebchannel.js must be loaded in index.html
  // eslint-disable-next-line no-undef
  new QWebChannel(qt.webChannelTransport, channel => {
    const bridge = channel.objects.bridge;
    if (!bridge) {
      console.error('[viewer] bridge object missing');
      return;
    }
    bridge.sendPayload.connect(drawPayload);
    console.log('[viewer] WebChannel connected');
  });
}

initThree();
onResize();
setupWebChannel();
