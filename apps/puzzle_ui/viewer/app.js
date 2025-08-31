// apps/puzzle_ui/viewer/app.js
import * as THREE from './libs/three/three.module.js';
import { OrbitControls } from './libs/three/examples/jsm/controls/OrbitControls.js';

// Define window.viewer very early
window.viewer = window.viewer || {};

const rootEl = document.getElementById('app');
let renderer, camera, controls;
let lastRunId = null;
let lastBBoxKey = null;

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

  // Fit only on new run or bbox change (keeps user zoom/angle stable)
  if (isNewRun || bboxKey !== lastBBoxKey) {
    fitOrthoToBbox(bbox);
    lastRunId = payload?.run_id ?? lastRunId;
    lastBBoxKey = bboxKey;
  }

  // a bit smoother for nicer specular highlights (tune if perf dips)
  const sphereGeom = new THREE.SphereGeometry(r, 24, 16);

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
    for (let i = 0; i < centers.length; i++) {
      const c = centers[i];
      if (!Array.isArray(c) || c.length < 3) continue;
      tmp.position.set(c[0], c[1], c[2]);
      tmp.updateMatrix();
      inst.setMatrixAt(i, tmp.matrix);
    }
    inst.instanceMatrix.needsUpdate = true;
    window.scene.add(inst);
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

    camera.zoom = zoom;
    camera.updateProjectionMatrix();

    return true;
  }

  // Public API
  window.viewer.fitOnce = function (opts) {
    if (_fitDone) return true;
    // Try now; if geometry isn't ready yet, retry a few frames.
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
