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
let __pivotCenter = null;  // Computed once per container
let __containerHash = null;  // Track container changes
let __cameraInitialized = false;  // Track if camera has been set for current container

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
  controls.addEventListener("change", function() {
    __saveZoom();
    // Prevent sphere disappearing during camera movement in shape editor
    if (_shapeEditorMode && _activeSpheres && _activeSpheres.length > 0) {
      _validateShapeEditorSpheres();
    }
  });
}

// ===== Distinct color strategies (dynamic piece count) =====
let __colorStrategy = "golden-3band";  // default
let __PIECE_COUNT_DEFAULT = 25;  // fallback, will be updated dynamically

// Small helpers
function __hash32(str) {
  let h = 2166136261 >>> 0; // FNV-1a
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function __hslColor(h /*0..1*/, s, l) {
  const c = new THREE.Color(); c.setHSL(((h%1)+1)%1, s, l); return c;
}
function __hexColor(hex) { return new THREE.Color(hex); }
function __bandLight(idx, bands) { return bands[idx % bands.length]; }

// Strategy implementations (return THREE.Color)
// 1) HSL golden ratio hues with 3-band lightness
function STRAT_golden_3band(key, idx, total) {
  const seed = __hash32(String(key)) / 4294967296;         // 0..1
  const h = (seed + idx * 0.61803398875) % 1;             // golden-step
  const s = 0.72, L = [0.42, 0.55, 0.68];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 2) HSL equal spacing with 3 bands
function STRAT_equal_3band(key, idx, total) {
  const h = idx / total; const s = 0.72, L = [0.40, 0.56, 0.72];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 3) HSL equal spacing with 4 bands
function STRAT_equal_4band(key, idx, total) {
  const h = idx / total; const s = 0.70, L = [0.38, 0.50, 0.62, 0.74];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 4) Warm/Cool alternating bands
function STRAT_warm_cool(key, idx, total) {
  const warm = [0.02, 0.06, 0.10, 0.15, 0.08, 0.12, 0.18, 0.21, 0.25, 0.30, 0.35, 0.40];
  const cool = [0.58, 0.62, 0.66, 0.70, 0.74, 0.78, 0.82, 0.86, 0.90, 0.94, 0.98, 0.54, 0.50];
  const pool = (idx % 2 === 0) ? warm : cool;
  const h = pool[Math.floor(idx / 2) % pool.length];
  const s = 0.75, L = [0.46, 0.60, 0.72];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 5) High-contrast (strong saturation, alternating lightness)
function STRAT_high_contrast(key, idx, total) {
  const h = (idx / total + 0.03 * (idx%3)) % 1;
  const s = 0.85, L = [0.38, 0.66];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 6) Pastel
function STRAT_pastel(key, idx, total) {
  const h = (idx / total + 0.11) % 1; const s = 0.50, L = [0.72, 0.78, 0.84];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 7) Muted
function STRAT_muted(key, idx, total) {
  const h = (idx / total + 0.17) % 1; const s = 0.45, L = [0.46, 0.56, 0.66];
  return __hslColor(h, s, __bandLight(idx, L));
}
// 8) Okabe–Ito base extended to 25 by lightness bands
const OKABE_ITO = [
  "#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#000000"
];
function STRAT_okabe_ito_25(key, idx, total) {
  const base = OKABE_ITO[idx % OKABE_ITO.length];
  // apply banded lightness tweaks
  const L = [0.40, 0.52, 0.64];
  const c = new THREE.Color(base);
  const hsl = { h:0, s:0, l:0 }; c.getHSL(hsl);
  const l = L[Math.floor(idx / OKABE_ITO.length) % L.length];
  return __hslColor(hsl.h, Math.min(0.80, Math.max(0.35, hsl.s)), l);
}
// 9) Tableau-like base extended
const TABLEAU20 = [
  "#4E79A7","#F28E2B","#E15759","#76B7B2","#59A14F",
  "#EDC948","#B07AA1","#FF9DA7","#9C755F","#BAB0AC",
  "#1F77B4","#FF7F0E","#2CA02C","#D62728","#9467BD",
  "#8C564B","#E377C2","#7F7F7F","#BCBD22","#17BECF"
];
function STRAT_tableau_25(key, idx, total) {
  const base = TABLEAU20[idx % TABLEAU20.length];
  const bands = [0.46, 0.58, 0.70];
  const c = new THREE.Color(base);
  const hsl = { h:0, s:0, l:0 }; c.getHSL(hsl);
  const l = bands[Math.floor(idx / TABLEAU20.length) % bands.length];
  return __hslColor(hsl.h, Math.min(0.85, Math.max(0.40, hsl.s)), l);
}
// 10) Distinct seeded (hash the piece key to scramble order)
function STRAT_distinct_seeded(key, idx, total) {
  const seed = __hash32(String(key)) / 4294967296;
  const h = (idx / total + seed * 0.37) % 1; const s = 0.70, L = [0.44, 0.58, 0.72];
  return __hslColor(h, s, __bandLight(idx + Math.floor(seed*1e6), L));
}

const COLOR_STRATEGIES = {
  "golden-3band": STRAT_golden_3band,
  "equal-3band": STRAT_equal_3band,
  "equal-4band": STRAT_equal_4band,
  "warm-cool": STRAT_warm_cool,
  "high-contrast": STRAT_high_contrast,
  "pastel": STRAT_pastel,
  "muted": STRAT_muted,
  "okabe-ito-25": STRAT_okabe_ito_25,
  "tableau-25": STRAT_tableau_25,
  "distinct-seeded": STRAT_distinct_seeded
};

// Material factory: same material per piece (bonds will match)
function makePieceMaterialFor(pieceKey, index, total) {
  const fn = COLOR_STRATEGIES[__colorStrategy] || STRAT_golden_3band;
  const col = fn(pieceKey, index, total || __PIECE_COUNT_DEFAULT);
  return new THREE.MeshStandardMaterial({ color: col, metalness: 0.2, roughness: 0.45 });
}

// Recolor everything already drawn, without changing camera
function recolorSceneInPlace() {
  // Prefer a known display root if you have one; otherwise use scene
  const root = scene.getObjectByName("DISPLAY_ROOT") || scene;
  const groups = root.children.filter(ch => ch.isGroup && ch.children.some(k => k.isMesh));
  const N = groups.length || __PIECE_COUNT_DEFAULT;

  groups.forEach((g, i) => {
    const key = g.userData?.pieceKey || g.name || String(i);
    const mat = makePieceMaterialFor(key, i, N);
    g.traverse(o => { if (o.isMesh) o.material = mat; });
  });

  renderer && camera && renderer.render(scene, camera);
}

// Public API for the dropdown
window.setColorStrategy = function(name) {
  if (!COLOR_STRATEGIES[name]) return;
  __colorStrategy = name;
  // Try to recolor in place to avoid any camera changes
  recolorSceneInPlace();
};

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
  
  // Remove polar angle limits to allow full 360° vertical rotation
  // Note: OrbitControls has inherent limitations due to gimbal lock at poles
  // Setting to slightly less than full range to avoid camera flip issues
  controls.minPolarAngle = 0.01; // Nearly straight down (avoid gimbal lock)
  controls.maxPolarAngle = Math.PI - 0.01; // Nearly straight up (avoid gimbal lock)
  
  console.log('[Camera] OrbitControls configured for full 360° rotation in all planes');

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
  
  // Validate shape editor spheres during animation loop
  if (_shapeEditorMode && _activeSpheres && _activeSpheres.length > 0) {
    _validateShapeEditorSpheres();
  }
  
  renderer.render(window.scene, camera);
}

// ---------- helpers ----------
function letterId(str) {
  // Map 'A'..'Z','AA'.. to 0.. (base-26), then wrap into palette length
  const s = String(str).toUpperCase();
  let n = 0;
  for (let i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64);
  return (n - 1) % Math.max(__PIECE_COUNT_DEFAULT, 25); // Use dynamic piece count
}

// Update piece count based on container size
function updatePieceCount(containerCells) {
  if (containerCells && containerCells.length > 0) {
    __PIECE_COUNT_DEFAULT = Math.max(containerCells.length / 4, 1);
  }
}

function normalizePieces(raw) {
  const arr = Array.isArray(raw) ? raw : [];
  
  // Update piece count if we have container info
  if (raw && raw.container_cells) {
    updatePieceCount(raw.container_cells);
  }
  
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

function computePivotCenter(pieces) {
  // Compute center of bounding box of all sphere centers (no padding)
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
    return new THREE.Vector3(0, 0, 0);  // fallback to origin
  }
  
  // Return center of bounding box
  return new THREE.Vector3(
    (minX + maxX) * 0.5,
    (minY + maxY) * 0.5,
    (minZ + maxZ) * 0.5
  );
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
  controls.target.copy(center);
  camera.updateProjectionMatrix();
  __saveZoom();     // capture the initial zoom
  __zoomLocked = true;
  console.log('[Camera] Initial fit completed, zoom locked');
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
  console.log('[viewer] drawPayload called with action:', payload?.action);
  
  // Handle clear action ONLY
  if (payload?.action === "clear") {
    console.log('[viewer] CLEAR action - clearing viewer');
    clearSceneMeshes();
    resetDisplayRoot();
    // Reset container tracking for new container
    __containerHash = null;
    __pivotCenter = null;
    __zoomLocked = false;
    __cameraInitialized = false;  // Allow camera setup for new container
    renderer.render(scene, camera);
    return;
  }

  // For all other payloads (updates), do NOT clear anything
  console.log('[viewer] UPDATE action - preserving existing geometry');

  // Normalize schema differences
  const r = (typeof payload?.r === 'number') ? payload.r : 0.5;
  const pieces = normalizePieces(payload?.pieces);

  // Check if this is a new container by creating a hash of container structure
  const containerHash = JSON.stringify({
    container_name: payload?.container_name,
    container_cells: payload?.container_cells?.length || 0,
    piece_count: pieces.length
  });

  // Compute pivot center and initialize camera only once per container
  if (__containerHash !== containerHash) {
    console.log('[Camera] New container detected - computing pivot center and initializing camera');
    __containerHash = containerHash;
    __pivotCenter = computePivotCenter(pieces);
    __cameraInitialized = false;  // Allow camera initialization for new container
    console.log('[Camera] Pivot center set to:', __pivotCenter);
  }

  // Initialize camera view ONLY once per container
  if (!__cameraInitialized) {
    // Choose/compute bbox
    let bbox = payload?.bbox;
    if (!bbox || !Array.isArray(bbox.min) || !Array.isArray(bbox.max)) {
      bbox = computeBbox(pieces, r);
    }

    // Set up camera position, rotation, and scale once
    fitOrthoToBbox(bbox);
    
    // Set controls target to pivot center ONLY on initialization
    if (__pivotCenter && controls) {
      controls.target.copy(__pivotCenter);
      console.log('[Camera] Controls target set to pivot center:', __pivotCenter);
    }
    
    __cameraInitialized = true;
    console.log('[Camera] Camera initialized for container - position/rotation/scale locked');
  } else {
    console.log('[Camera] Preserving user camera settings - no camera or controls changes');
    // Do NOT update controls.target or any camera properties during updates
  }

  // a bit smoother for nicer specular highlights (tune if perf dips)
  const sphereGeom = new THREE.SphereGeometry(r, 24, 16);

  const root = ensureDisplayRoot();

  // Clear all existing geometry for updates (but preserve camera/scene structure)
  const toRemove = [];
  root.children.forEach(child => {
    if (child.isGroup && child.userData?.pieceKey) {
      toRemove.push(child);
    }
  });
  toRemove.forEach(child => {
    root.remove(child);
    // Dispose geometry/materials to prevent memory leaks
    child.traverse(obj => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) {
          obj.material.forEach(mat => mat.dispose());
        } else {
          obj.material.dispose();
        }
      }
    });
  });
  
  // Rebuild all pieces with new geometry
  pieces.forEach((piece, pieceIndex) => {
    const centers = piece.centers || [];
    if (!centers.length) return;
    const pieceKey = piece.id ?? piece.name ?? String(pieceIndex);
    
    // Create new piece group
    const pieceGroup = new THREE.Group();
    pieceGroup.userData.pieceKey = pieceKey;
    root.add(pieceGroup);
    
    const mat = makePieceMaterialFor(pieceKey, pieceIndex, pieces.length);
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
      atomMesh.userData.isAtom = true;
      atomMeshes.push(atomMesh);
    }
    inst.instanceMatrix.needsUpdate = true;
    
    pieceGroup.add(inst);
    atomMeshes.forEach(mesh => pieceGroup.add(mesh));
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
    if (__zoomLocked) {
      console.log('[Camera] _centerAndFitOrtho skipped - zoom locked');
      return false;
    }
    
    const box = _sceneBox();
    if (!box) return false;

    // center of bounding box becomes the pivot point
    const center = new THREE.Vector3();
    box.getCenter(center);

    // Set controls target to bounding box center for proper rotation pivot
    controls.target.copy(center);
    
    // Position camera relative to the new pivot point
    const oldTarget = new THREE.Vector3(0, 0, 0); // assume previous target was origin
    const delta = center.clone().sub(oldTarget);
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
    __saveZoom();
    __zoomLocked = true;
    console.log('[Camera] Fitted and locked zoom at', zoom);

    return true;
  }

  // Public API
  window.viewer.fitOnce = function (opts) {
    if (_fitDone || __zoomLocked) {
      console.log('[Camera] fitOnce skipped - already fitted or zoom locked');
      return true;
    }
    // Try now; if geometry isn’t ready yet, retry a few frames.
    let tries = 0;
    function attempt() {
      tries++;
      const ok = _centerAndFitOrtho((opts && opts.margin) || 1.15);
      if (ok || tries > 30) { 
        _fitDone = ok; 
        if (ok) console.log('[Camera] fitOnce completed successfully');
        return; 
      }
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

// ---------- Shape Editor Functions ----------
let _shapeEditorMode = false;
  let _activeSpheres = [];
  let _frontierSpheres = [];
  let _allFrontierSpheres = [];
  let _hoverSphere = null;
  let _editColor = 'blue';
  let _shapeRadius = 0.5;
  let _showNeighbors = true;
  
  // Raycaster for mouse picking
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = 0.1;
  const mouse = new THREE.Vector2();
  
  // Materials for shape editing
  const _shapeMaterials = {
    active: null,
    frontier: null,
    hover: null
  };
  
  function _initShapeMaterials() {
    const colors = {
      red: 0xff4444, blue: 0x4444ff, green: 0x44ff44, yellow: 0xffff44,
      purple: 0xff44ff, orange: 0xff8844, cyan: 0x44ffff, pink: 0xff88cc
    };
    
    const baseColor = colors[_editColor] || colors.blue;
    
    _shapeMaterials.active = new THREE.MeshLambertMaterial({
      color: baseColor,
      transparent: false
    });
    
    _shapeMaterials.frontier = new THREE.MeshLambertMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.1
    });
    
    _shapeMaterials.hover = new THREE.MeshLambertMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.6
    });
    
    _shapeMaterials.hoverDelete = new THREE.MeshLambertMaterial({
      color: 0xff0000,
      transparent: true,
      opacity: 0.6
    });
  }
  
  function _clearShapeEditor() {
    const root = ensureDisplayRoot();
    // Remove all shape editor objects
    const toRemove = [];
    root.traverse(obj => {
      if (obj.userData?.isShapeEditor) {
        toRemove.push(obj);
      }
    });
    toRemove.forEach(obj => {
      if (obj.parent) obj.parent.remove(obj);
    });
    
    if (_hoverSphere) {
      scene.remove(_hoverSphere);
      _hoverSphere = null;
    }
  }
  
  function loadShapeEditor(activeSpheres, frontierSpheres, radius, color, showNeighbors) {
    console.log('[Shape] === LOAD SHAPE EDITOR START ===');
    console.log('[Shape] Parameters received:');
    console.log('[Shape]   - Active spheres:', activeSpheres?.length || 0);
    console.log('[Shape]   - Frontier spheres:', frontierSpheres?.length || 0);
    console.log('[Shape]   - Radius:', radius);
    console.log('[Shape]   - Color:', color);
    console.log('[Shape]   - Show neighbors:', showNeighbors);
    
    // Validate input data
    if (!Array.isArray(activeSpheres)) {
      console.error('[Shape] ERROR: activeSpheres is not an array:', typeof activeSpheres);
      return;
    }
    if (!Array.isArray(frontierSpheres)) {
      console.error('[Shape] ERROR: frontierSpheres is not an array:', typeof frontierSpheres);
      return;
    }
    
    // Check for invalid sphere data
    activeSpheres.forEach((sphere, idx) => {
      if (!sphere || typeof sphere.x !== 'number' || typeof sphere.y !== 'number' || typeof sphere.z !== 'number') {
        console.error('[Shape] ERROR: Invalid active sphere data at index', idx, ':', sphere);
      }
    });
    
    frontierSpheres.forEach((sphere, idx) => {
      if (!sphere || typeof sphere.x !== 'number' || typeof sphere.y !== 'number' || typeof sphere.z !== 'number') {
        console.error('[Shape] ERROR: Invalid frontier sphere data at index', idx, ':', sphere);
      }
    });
    
    _activeSpheres = activeSpheres || [];
    _frontierSpheres = frontierSpheres || [];
    _shapeRadius = radius || 0.5;
    _editColor = color || 'blue';
    _showNeighbors = showNeighbors !== false;
    
    console.log('[Shape] Stored data:');
    console.log('[Shape]   - _activeSpheres length:', _activeSpheres.length);
    console.log('[Shape]   - _frontierSpheres length:', _frontierSpheres.length);
    console.log('[Shape]   - _shapeRadius:', _shapeRadius);
    
    _buildShapeEditor();
  }

  function _buildShapeEditor(data) {
    console.log('[Shape] === BUILD SHAPE EDITOR START ===');
    console.log('[Shape] _buildShapeEditor called with:', data);
    console.log('[Shape] Current _shapeEditorMode:', _shapeEditorMode);
    console.log('[Shape] Active spheres to create:', data?.active_spheres?.length || _activeSpheres?.length || 0);
    console.log('[Shape] Frontier spheres to create:', data?.frontier_spheres?.length || _frontierSpheres?.length || 0);
    console.log('[Shape] Current scene children count:', scene?.children?.length || 0);
    
    // Always do a full clear for interactive editing to avoid accumulation issues
    console.log('[Shape] Doing full clear for interactive editing');
    _clearShapeEditor();
    _initShapeMaterials();
    
    const root = ensureDisplayRoot();
    
    // Use data parameter if provided, otherwise use stored values
    if (data) {
      _shapeRadius = data.radius || 0.5;
      _editColor = data.edit_color || 'blue';
      _activeSpheres = data.active_spheres || [];
      _frontierSpheres = data.frontier_spheres || [];
      _allFrontierSpheres = data.all_frontier_spheres || data.frontier_spheres || [];
    }
    
    console.log('[Shape] Using sphere data:');
    console.log('[Shape]   - Radius:', _shapeRadius);
    console.log('[Shape]   - Active spheres:', _activeSpheres.length);
    console.log('[Shape]   - Frontier spheres:', _frontierSpheres.length);
    console.log('[Shape]   - Show neighbors:', _showNeighbors);
    
    // Create active spheres with critical threshold monitoring
    console.log('[Shape] Creating', _activeSpheres.length, 'active spheres with radius:', _shapeRadius);
    
    
    _activeSpheres.forEach((center, idx) => {
      // Use consistent high-quality geometry like file loading
      let segments = 24;
      let rings = 16;
      
      const geometry = new THREE.SphereGeometry(_shapeRadius, segments, rings);
      const sphere = new THREE.Mesh(geometry, _shapeMaterials.active);
      
      // Validate position data
      if (typeof center.x !== 'number' || typeof center.y !== 'number' || typeof center.z !== 'number') {
        console.error('[Shape] Invalid position data for sphere', idx, ':', center);
        return;
      }
      
      sphere.position.set(center.x, center.y, center.z);
      sphere.userData.isShapeEditor = true;
      sphere.userData.shapeType = 'active';
      sphere.userData.shapeIndex = idx;
      
      root.add(sphere);
    });
    
    const createdActiveCount = root.children.filter(c => c.userData?.shapeType === 'active').length;
    console.log('[Shape] Active spheres created. Total in scene:', createdActiveCount);
    
    
    // Create visible frontier spheres
    console.log('[Shape] Creating', _frontierSpheres.length, 'frontier spheres with radius:', _shapeRadius);
    _frontierSpheres.forEach((center, idx) => {
      // Validate position data
      if (typeof center.x !== 'number' || typeof center.y !== 'number' || typeof center.z !== 'number') {
        console.error('[Shape] Invalid frontier position data for sphere', idx, ':', center);
        return;
      }
      
      // Use consistent geometry like file loading
      let segments = 16;
      let rings = 12;
      
      const geometry = new THREE.SphereGeometry(_shapeRadius * 0.7, segments, rings);
      const sphere = new THREE.Mesh(geometry, _shapeMaterials.frontier);
      sphere.position.set(center.x, center.y, center.z);
      sphere.userData.isShapeEditor = true;
      sphere.userData.shapeType = 'frontier';
      sphere.userData.shapeIndex = idx;
      root.add(sphere);
    });
    
    // Create invisible frontier spheres for interaction (when neighbors are hidden)
    if (_frontierSpheres.length === 0 && _allFrontierSpheres.length > 0) {
      _allFrontierSpheres.forEach((center, idx) => {
        const geometry = new THREE.SphereGeometry(_shapeRadius * 0.7, 24, 16);
        const invisibleMaterial = new THREE.MeshLambertMaterial({
          color: 0xffffff,
          transparent: true,
          opacity: 0.0,
          visible: false
        });
        const sphere = new THREE.Mesh(geometry, invisibleMaterial);
        sphere.position.set(center.x, center.y, center.z);
        sphere.userData.isShapeEditor = true;
        sphere.userData.shapeType = 'frontier';
        sphere.userData.shapeIndex = idx;
        sphere.userData.isInvisible = true;
        root.add(sphere);
      });
    }
    
    // Skip camera pivot updates at critical sphere counts to avoid rendering issues
    if (_activeSpheres.length < 15) {
      _updateCameraPivot();
    } else {
      console.log('[Shape] Skipping camera pivot update at', _activeSpheres.length, 'spheres to avoid rendering issues');
    }
    
    // Final verification - count actual spheres in scene
    const finalActiveCount = root.children.filter(c => c.userData?.shapeType === 'active').length;
    const finalFrontierCount = root.children.filter(c => c.userData?.shapeType === 'frontier').length;
    console.log('[Shape] FINAL VERIFICATION: Scene contains', finalActiveCount, 'active and', finalFrontierCount, 'frontier spheres');
    
    if (finalActiveCount !== _activeSpheres.length) {
      console.error('[Shape] SPHERE COUNT MISMATCH! Expected', _activeSpheres.length, 'active spheres, but scene has', finalActiveCount);
    }
    
    // Check if any spheres are outside camera view or have rendering issues
    const camera = window.camera;
    if (camera) {
      let visibleCount = 0;
      let renderableCount = 0;
      root.children.forEach(child => {
        if (child.userData?.shapeType === 'active') {
          const distance = camera.position.distanceTo(child.position);
          console.log('[Shape] Active sphere at', child.position, 'distance from camera:', distance, 'visible:', child.visible, 'material opacity:', child.material?.opacity);
          
          // Check if sphere is renderable
          if (child.visible && child.material && child.material.opacity > 0 && child.geometry) {
            renderableCount++;
          }
          
          if (distance < 1000) visibleCount++; // Arbitrary large distance
        }
      });
      console.log('[Shape] Spheres within camera range:', visibleCount, 'of', finalActiveCount);
      console.log('[Shape] Renderable spheres:', renderableCount, 'of', finalActiveCount);
      
      if (renderableCount < finalActiveCount) {
        console.error('[Shape] RENDERING ISSUE: Some spheres are not renderable!');
      }
    }
    
    // Check Three.js renderer and scene state
    if (window.renderer) {
      console.log('[Shape] Renderer info - geometries:', window.renderer.info.memory.geometries, 'textures:', window.renderer.info.memory.textures);
      console.log('[Shape] Renderer calls:', window.renderer.info.render.calls, 'triangles:', window.renderer.info.render.triangles);
    }
    
    // Force a render to see if that helps
    if (window.renderer && window.scene && window.camera) {
      console.log('[Shape] Forcing render after sphere creation');
      window.renderer.render(window.scene, window.camera);
      
    }
  }
  
  // Validation function to ensure spheres remain visible during camera changes
  let _validationThrottle = 0;
  function _validateShapeEditorSpheres() {
    if (!_shapeEditorMode || !_activeSpheres || _activeSpheres.length === 0) return;
    
    // Throttle validation to avoid excessive rebuilds during rapid camera movement
    const now = performance.now();
    if (now - _validationThrottle < 100) return; // Max 10 times per second
    _validationThrottle = now;
    
    const root = ensureDisplayRoot();
    const activeSphereObjects = root.children.filter(obj => obj.userData?.shapeType === 'active');
    
    // Check if we have the expected number of active spheres
    if (activeSphereObjects.length !== _activeSpheres.length) {
      console.warn('[Shape] VALIDATION: Expected', _activeSpheres.length, 'active spheres, found', activeSphereObjects.length, '- rebuilding');
      _buildShapeEditor(); // Rebuild if count mismatch
      return;
    }
    
    // Check visibility and opacity of existing spheres
    let fixedCount = 0;
    activeSphereObjects.forEach((sphere, idx) => {
      if (!sphere.visible) {
        console.warn('[Shape] VALIDATION: Fixed invisible sphere', idx);
        sphere.visible = true;
        fixedCount++;
      }
      if (sphere.material && sphere.material.opacity < 1.0) {
        console.warn('[Shape] VALIDATION: Fixed transparent sphere', idx);
        sphere.material.opacity = 1.0;
        sphere.material.transparent = false;
        fixedCount++;
      }
      
      // Ensure sphere is properly added to scene hierarchy
      if (!sphere.parent) {
        console.warn('[Shape] VALIDATION: Re-adding orphaned sphere', idx);
        root.add(sphere);
        fixedCount++;
      }
    });
    
    if (fixedCount > 0) {
      console.log('[Shape] VALIDATION: Fixed', fixedCount, 'sphere visibility/hierarchy issues');
    }
  }
  
  function _updateCameraPivot() {
    // Do NOT update camera or pivot during updates - preserve user settings completely
    return;
    
    // DISABLED: All camera/pivot adjustments removed to prevent centering during updates
    /*
    if (_activeSpheres.length === 0) return;
    
    // Calculate bounding box of active spheres
    const bbox = new THREE.Box3();
    _activeSpheres.forEach(center => {
      bbox.expandByPoint(new THREE.Vector3(center.x, center.y, center.z));
    });
    
    const center = new THREE.Vector3();
    bbox.getCenter(center);
    
    // Update controls target to center of active spheres
    if (controls && controls.target) {
      const oldTarget = controls.target.clone();
      const delta = center.clone().sub(oldTarget);
      
      // Move camera by the same delta to maintain view
      controls.target.copy(center);
      camera.position.add(delta);
      
      controls.update();
    }
    
    // Do NOT force camera fit during shape editor updates - preserve user camera position
    // __zoomLocked = false;
    // if (window.viewer && window.viewer.resetFit) window.viewer.resetFit();
    // if (window.viewer && window.viewer.fitOnce) window.viewer.fitOnce({ margin: 1.02 });
    */
  }
  
  let _lastMouseMoveTime = 0;
  const MOUSE_THROTTLE_MS = 16; // ~60fps
  
  function _onMouseMove(event) {
    if (!_shapeEditorMode) return;
    
    // Throttle mouse move events to prevent performance issues
    const now = performance.now();
    if (now - _lastMouseMoveTime < MOUSE_THROTTLE_MS) return;
    _lastMouseMoveTime = now;
    
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    
    raycaster.setFromCamera(mouse, camera);
    
    // Find intersections with shape editor objects
    const root = ensureDisplayRoot();
    const shapeObjects = [];
    root.traverse(obj => {
      if (obj.userData?.isShapeEditor && obj.isMesh) {
        shapeObjects.push(obj);
      }
    });
    
    const intersects = raycaster.intersectObjects(shapeObjects);
    
    // Remove previous hover sphere
    if (_hoverSphere) {
      scene.remove(_hoverSphere);
      _hoverSphere = null;
    }
    
    if (intersects.length > 0) {
      // Use same target selection logic as click for consistent hover preview
      let target = intersects[0].object;
      
      // If we hit multiple objects, prefer active spheres for consistent behavior
      for (let i = 0; i < intersects.length; i++) {
        if (intersects[i].object.userData.shapeType === 'active') {
          target = intersects[i].object;
          break;
        }
      }
      
      const shapeType = target.userData.shapeType;
      
      // Show hover preview with appropriate color
      const geometry = new THREE.SphereGeometry(_shapeRadius * 1.1, 16, 12);
      const material = (shapeType === 'active') ? _shapeMaterials.hoverDelete : _shapeMaterials.hover;
      _hoverSphere = new THREE.Mesh(geometry, material);
      _hoverSphere.position.copy(target.position);
      scene.add(_hoverSphere);
      
      renderer.domElement.style.cursor = 'pointer';
    } else {
      renderer.domElement.style.cursor = 'default';
    }
  }
  
  function _onMouseClick(event) {
    if (!_shapeEditorMode) return;
    
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    
    raycaster.setFromCamera(mouse, camera);
    
    const root = ensureDisplayRoot();
    const shapeObjects = [];
    root.traverse(obj => {
      if (obj.userData?.isShapeEditor && obj.isMesh) {
        shapeObjects.push(obj);
      }
    });
    
    const intersects = raycaster.intersectObjects(shapeObjects);
    
    if (intersects.length > 0) {
      // Prioritize active spheres for deletion clicks
      let target = intersects[0].object;
      
      // If we hit multiple objects, prefer active spheres for easier deletion
      for (let i = 0; i < intersects.length; i++) {
        if (intersects[i].object.userData.shapeType === 'active') {
          target = intersects[i].object;
          break;
        }
      }
      
      const shapeType = target.userData.shapeType;
      const shapeIndex = target.userData.shapeIndex;
      
      // Send click event back to Python
      const clickData = {
        type: shapeType,
        index: shapeIndex,
        position: {
          x: target.position.x,
          y: target.position.y,
          z: target.position.z
        }
      };
      
      // Call Python callback if available
      if (window.qt && window.qt.webChannelTransport) {
        console.log('[Shape] Sphere clicked:', clickData);
        // Send to Python via web channel
        new QWebChannel(qt.webChannelTransport, channel => {
          const bridge = channel.objects.bridge;
          if (bridge && bridge.onSphereClicked) {
            bridge.onSphereClicked(JSON.stringify(clickData));
          }
        });
      }
    }
  }
  
  // Public API
  window.viewer.loadShapeEditor = function(data) {
    _shapeEditorMode = true;
    _buildShapeEditor(data);
    
    // Add event listeners
    renderer.domElement.addEventListener('mousemove', _onMouseMove);
    renderer.domElement.addEventListener('click', _onMouseClick);
    
    renderer.render(scene, camera);
    return true;
  };
  
  window.viewer.exitShapeEditor = function() {
    _shapeEditorMode = false;
    _clearShapeEditor();
    
    // Remove event listeners
    renderer.domElement.removeEventListener('mousemove', _onMouseMove);
    renderer.domElement.removeEventListener('click', _onMouseClick);
    renderer.domElement.style.cursor = 'default';
    
    renderer.render(scene, camera);
    return true;
  };
  
  window.viewer.updateShapeColor = function(colorName) {
    _editColor = colorName;
    if (_shapeEditorMode) {
      _initShapeMaterials();
      // Update existing spheres
      const root = ensureDisplayRoot();
      root.traverse(obj => {
        if (obj.userData?.isShapeEditor && obj.isMesh) {
          if (obj.userData.shapeType === 'active') {
            obj.material = _shapeMaterials.active;
          } else if (obj.userData.shapeType === 'frontier') {
            obj.material = _shapeMaterials.frontier;
          }
        }
      });
      renderer.render(scene, camera);
    }
    return true;
  };

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
    // Check if this is the Solve tab bridge (has sendPayload) or Shape tab bridge
    if (bridge.sendPayload && bridge.sendPayload.connect) {
      bridge.sendPayload.connect(drawPayload);
      console.log('[viewer] WebChannel connected (Solve tab)');
    } else {
      console.log('[viewer] WebChannel connected (Shape tab)');
    }
  });
}

initThree();
onResize();
setupWebChannel();
