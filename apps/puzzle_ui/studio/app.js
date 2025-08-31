// ES-module imports from your Viewer libs
import * as THREE from '../viewer/libs/three/three.module.js';
import { OrbitControls }     from '../viewer/libs/three/examples/jsm/controls/OrbitControls.js';

// Optional: expose THREE if other code reads window.THREE
window.THREE = THREE;

/* Studio viewer — isolated from main viewer. No file watcher. */
let scene, camera, renderer, controls;
let DISPLAY_ROOT;
const statusEl = document.getElementById('status');

// ---- Color strategies (same as viewer) ----
let __colorStrategy = "golden-3band";
const __PIECE_COUNT_DEFAULT = 25;

function __hash32(str){let h=2166136261>>>0;for(let i=0;i<str.length;i++){h^=str.charCodeAt(i);h=Math.imul(h,16777619);}return h>>>0;}
function __hslColor(h,s,l){const c=new THREE.Color(); c.setHSL(((h%1)+1)%1,s,l); return c;}
function __bandLight(i,b){return b[i%b.length];}

function STRAT_golden_3band(key, idx, total){const seed=__hash32(String(key))/4294967296;const h=(seed+idx*0.61803398875)%1;const s=0.72,L=[0.42,0.55,0.68];return __hslColor(h,s,__bandLight(idx,L));}
function STRAT_equal_3band(key, idx, total){const h=idx/total, s=0.72,L=[0.40,0.56,0.72];return __hslColor(h,s,__bandLight(idx,L));}
function STRAT_equal_4band(key, idx, total){const h=idx/total, s=0.70,L=[0.38,0.50,0.62,0.74];return __hslColor(h,s,__bandLight(idx,L));}
function STRAT_warm_cool(key, idx, total){const warm=[0.02,0.06,0.10,0.15,0.08,0.12,0.18,0.21,0.25,0.30,0.35,0.40];const cool=[0.58,0.62,0.66,0.70,0.74,0.78,0.82,0.86,0.90,0.94,0.98,0.54,0.50];const pool=(idx%2===0)?warm:cool;const h=pool[Math.floor(idx/2)%pool.length];const s=0.75,L=[0.46,0.60,0.72];return __hslColor(h,s,__bandLight(idx,L));}
function STRAT_high_contrast(key, idx, total){const h=(idx/total+0.03*(idx%3))%1, s=0.85,L=[0.38,0.66];return __hslColor(h,s,__bandLight(idx,L));}
function STRAT_pastel(key, idx, total){const h=(idx/total+0.11)%1, s=0.50,L=[0.72,0.78,0.84];return __hslColor(h,s,__bandLight(idx,L));}
function STRAT_muted(key, idx, total){const h=(idx/total+0.17)%1, s=0.45,L=[0.46,0.56,0.66];return __hslColor(h,s,__bandLight(idx,L));}
const OKABE_ITO=["#E69F00","#56B4E9","#009E73","#F0E442","#0072B2","#D55E00","#CC79A7","#000000"];
function STRAT_okabe_ito_25(key, idx, total){const base=OKABE_ITO[idx%OKABE_ITO.length];const L=[0.40,0.52,0.64];const c=new THREE.Color(base);const hsl={h:0,s:0,l:0};c.getHSL(hsl);const l=L[Math.floor(idx/OKABE_ITO.length)%L.length];return __hslColor(hsl.h,Math.min(0.80,Math.max(0.35,hsl.s)),l);}
const TABLEAU20=["#4E79A7","#F28E2B","#E15759","#76B7B2","#59A14F","#EDC948","#B07AA1","#FF9DA7","#9C755F","#BAB0AC","#1F77B4","#FF7F0E","#2CA02C","#D62728","#9467BD","#8C564B","#E377C2","#7F7F7F","#BCBD22","#17BECF"];
function STRAT_tableau_25(key, idx, total){const base=TABLEAU20[idx%TABLEAU20.length];const bands=[0.46,0.58,0.70];const c=new THREE.Color(base);const hsl={h:0,s:0,l:0};c.getHSL(hsl);const l=bands[Math.floor(idx/TABLEAU20.length)%bands.length];return __hslColor(hsl.h,Math.min(0.85,Math.max(0.40,hsl.s)),l);}
function STRAT_distinct_seeded(key, idx, total){const seed=__hash32(String(key))/4294967296;const h=(idx/total+seed*0.37)%1, s=0.70,L=[0.44,0.58,0.72];return __hslColor(h,s,__bandLight(idx+Math.floor(seed*1e6),L));}

const COLOR_STRATEGIES={
  "golden-3band":STRAT_golden_3band,
  "equal-3band":STRAT_equal_3band,
  "equal-4band":STRAT_equal_4band,
  "warm-cool":STRAT_warm_cool,
  "high-contrast":STRAT_high_contrast,
  "pastel":STRAT_pastel,
  "muted":STRAT_muted,
  "okabe-ito-25":STRAT_okabe_ito_25,
  "tableau-25":STRAT_tableau_25,
  "distinct-seeded":STRAT_distinct_seeded
};

function makePieceMaterialFor(pieceKey, index, total){
  const fn = COLOR_STRATEGIES[__colorStrategy] || STRAT_golden_3band;
  const N = Math.max(total || 0, __PIECE_COUNT_DEFAULT);    // <-- ensure at least 25
  const col = fn(pieceKey, index, N);
  return new THREE.MeshStandardMaterial({ color: col, metalness:0.2, roughness:0.45 });
}

// Vibrant one-off material for containers (stable by container key)
function makeContainerMaterial(key) {
  const seed = __hash32(String(key)) / 4294967296;      // 0..1 stable
  const h = (seed + 0.08) % 1;                          // hue
  const s = 0.78, l = 0.54;                             // vivid but not neon
  const col = new THREE.Color().setHSL(h, s, l);
  return new THREE.MeshStandardMaterial({
    color: col,
    metalness: 0.25,
    roughness: 0.40,
  });
}

// Helper function to convert cells to centers
function cellsToCenters(cells, lattice) {
  const out = [];
  if (!Array.isArray(cells)) return out;

  if ((lattice || "").toUpperCase() === "FCC") {
    const S = Math.SQRT1_2; // = 1 / sqrt(2) ≈ 0.70710678
    for (const c of cells) {
      if (!Array.isArray(c) || c.length !== 3) continue;
      const i = c[0], j = c[1], k = c[2];
      // FCC integer → world (nearest-neighbor = 1)
      out.push({
        x: (j + k) * S,
        y: (i + k) * S,
        z: (i + j) * S
      });
    }
    return out;
  }

  // Default (e.g., SC): identity mapping
  for (const c of cells) {
    if (Array.isArray(c) && c.length === 3) {
      out.push({ x: c[0], y: c[1], z: c[2] });
    }
  }
  return out;
}

// ---- Bonds helpers (neighbor-only) ----
const BOND_RADIUS = 0.12, BOND_SEGMENTS = 12, _UP_Y = new THREE.Vector3(0,1,0);
const SHARED_BOND_GEOMETRY = new THREE.CylinderGeometry(1,1,1,BOND_SEGMENTS,1,false);

function _createBondMesh(a,b,mat){
  const dir = new THREE.Vector3().subVectors(b,a);
  const len = dir.length(); if (len < 1e-9) return null;
  const mesh = new THREE.Mesh(SHARED_BOND_GEOMETRY, mat);
  mesh.position.copy(new THREE.Vector3().addVectors(a,b).multiplyScalar(0.5));
  mesh.quaternion.setFromUnitVectors(_UP_Y, dir.clone().normalize());
  mesh.scale.set(BOND_RADIUS, len, BOND_RADIUS);
  mesh.userData.isBond = true;
  mesh.userData.baseLen = len;   // remember full length for grow-in
  return mesh;
}

function addBondsForAtoms(container, atoms, mat){
  if (!atoms || atoms.length < 2) return;
  // clear old bonds if any
  for (let i=container.children.length-1; i>=0; i--){
    const ch = container.children[i];
    if (ch.isMesh && ch.userData?.isBond) container.remove(ch);
  }
  // neighbor distance = smallest non-zero pair
  let minDist = Infinity;
  for (let i=0;i<atoms.length;i++) for (let j=i+1;j<atoms.length;j++){
    const d = atoms[i].position.distanceTo(atoms[j].position);
    if (d>1e-6 && d<minDist) minDist = d;
  }
  if (!isFinite(minDist)) return;
  const EPS = minDist * 0.05;
  for (let i=0;i<atoms.length;i++) for (let j=i+1;j<atoms.length;j++){
    const d = atoms[i].position.distanceTo(atoms[j].position);
    if (Math.abs(d - minDist) <= EPS){
      const m = _createBondMesh(atoms[i].position, atoms[j].position, mat);
      if (m) container.add(m);
    }
  }
}

// ---- Scene setup (one-time fit, no auto-refit) ----
let __zoomLocked = false, __savedOrthoZoom = null;

function ensureDisplayRoot(){
  if (!DISPLAY_ROOT){
    DISPLAY_ROOT = new THREE.Group();
    DISPLAY_ROOT.name = "DISPLAY_ROOT";
    scene.add(DISPLAY_ROOT);
  }
  return DISPLAY_ROOT;
}

function resetDisplayRoot() {
  // Remove the old root entirely (in case anything bypassed it previously)
  if (DISPLAY_ROOT) {
    // Optional: dispose old geometries to be tidy
    DISPLAY_ROOT.traverse(o => {
      if (o.isMesh) {
        if (o.geometry) o.geometry.dispose?.();
        // We keep materials — they’re lightweight and often shared
      }
    });
    scene.remove(DISPLAY_ROOT);
    DISPLAY_ROOT = null;
  }
  // Recreate an empty root
  DISPLAY_ROOT = new THREE.Group();
  DISPLAY_ROOT.name = "DISPLAY_ROOT";
  scene.add(DISPLAY_ROOT);
}

let AMB_LIGHT = null, DIR_LIGHT = null;
const __baseAmb = 0.6, __baseDir = 0.8;

function applyBrightness(factor) {
  const k = Math.max(0, Number(factor) || 0);
  if (AMB_LIGHT) AMB_LIGHT.intensity = __baseAmb * k;
  if (DIR_LIGHT) DIR_LIGHT.intensity = __baseDir * k;
  if (renderer && camera) renderer.render(scene, camera);
}

// Exposed to PyQt
window.setStudioBrightness = function(factor) {
  applyBrightness(factor);
};

function init(){
  const el = document.getElementById('root');
  renderer = new THREE.WebGLRenderer({ antialias:true, alpha:false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(el.clientWidth, el.clientHeight);
  el.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x111111);

  camera = new THREE.PerspectiveCamera(75, el.clientWidth / el.clientHeight, 0.01, 1000);
  camera.position.set(8,8,8);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  AMB_LIGHT = new THREE.AmbientLight(0xffffff, __baseAmb);
  scene.add(AMB_LIGHT);

  DIR_LIGHT = new THREE.DirectionalLight(0xffffff, __baseDir);
  DIR_LIGHT.position.set(10,12,8);
  scene.add(DIR_LIGHT);

  window.addEventListener('resize', onResize);
  animate();
}

function onResize(){
  const el = renderer.domElement.parentElement;
  renderer.setSize(el.clientWidth, el.clientHeight);
  camera.aspect = el.clientWidth / el.clientHeight;
  camera.updateProjectionMatrix();
}

let __anim = null;  // holds current animation or null

function _pieceGroups() {
  const root = scene.getObjectByName("DISPLAY_ROOT");
  if (!root) return [];
  // Only animate real pieces, not containers
  return root.children.filter(g => g.isGroup && !g.userData?.isContainer);
}

function _minWorldZ(group) {
  // find the minimum Z of the group's atom meshes in WORLD space
  let minZ = Infinity;
  const v = new THREE.Vector3();
  group.traverse(o => {
    if (o.isMesh && o.userData?.isAtom) {
      o.getWorldPosition(v);
      if (v.z < minZ) minZ = v.z;
    }
  });
  return minZ;
}

// Drive one animation frame
function _stepAssembleBottomUp() {
  if (!__anim || __anim.kind !== 'bottomup') return;
  const now = performance.now();
  const elapsed = now - __anim.start;
  const N = __anim.groups.length;
  if (N === 0) { __anim = null; return; }

  const slot = __anim.duration / N;        // per-piece time window
  const grow = slot * 0.8;                  // 80% of slot is the fade/grow window

  let allDone = true;
  for (let i = 0; i < N; i++) {
    const g = __anim.groups[i];
    const t0 = slot * i;
    const p  = Math.max(0, Math.min(1, (elapsed - t0) / Math.max(1, grow))); // 0..1

    if (elapsed >= t0) {
      if (!g.visible) g.visible = true;
      // fade in + bond grow
      g.traverse(o => {
        if (o.isMesh) {
          // fade material
          o.material.transparent = true;
          o.material.opacity = 0.1 + 0.9 * p;
          // grow bonds along length
          if (o.userData?.isBond && o.userData.baseLen != null) {
            o.scale.y = Math.max(0.0001, o.userData.baseLen * p);
          }
        }
      });
    }
    if (elapsed < t0 + slot) allDone = false;
  }

  if (elapsed >= __anim.duration) {
    // finalize: fully visible, full bond lengths, disable transparency
    __anim.groups.forEach(g => {
      g.visible = true;
      g.traverse(o => {
        if (o.isMesh) {
          o.material.opacity = 1.0;
          o.material.transparent = false;
          if (o.userData?.isBond && o.userData.baseLen != null) {
            o.scale.y = o.userData.baseLen;
          }
        }
      });
    });
    __anim = null;
    setStatus("Studio: animation complete");
  }
}

// Hook the stepper into your render loop
function animate(){
  requestAnimationFrame(animate);
  controls.update();
  _stepAssembleBottomUp();   // <-- add this line
  renderer.render(scene, camera);
}

// ---- PNG capture (export current view) ----
window.studioCapturePng = function(scale = 2) {
  if (!renderer || !scene || !camera) return null;

  // Save current size/ratio
  const oldSize = new THREE.Vector2();
  renderer.getSize(oldSize);
  const oldPR = renderer.getPixelRatio();

  // Scale canvas uniformly (keeps aspect & projection)
  const w = Math.max(1, Math.floor(oldSize.x * scale));
  const h = Math.max(1, Math.floor(oldSize.y * scale));

  renderer.setPixelRatio(1);
  renderer.setSize(w, h, false);
  renderer.render(scene, camera);

  const dataURL = renderer.domElement.toDataURL("image/png");

  // Restore renderer
  renderer.setSize(oldSize.x, oldSize.y, false);
  renderer.setPixelRatio(oldPR);
  renderer.render(scene, camera);

  return dataURL; // "data:image/png;base64,...."
};

// ---- Loading & normalization ----
function setStatus(msg){ if (statusEl) statusEl.textContent = msg; }

function normalizeSnapshot(anyObj){
  // --- Known "pieces"-style payload (solution/partial) ---
  if (anyObj && Array.isArray(anyObj.pieces)) {
    const pieces = anyObj.pieces.map((p, i) => {
      const id = p.id ?? p.name ?? `piece_${i+1}`;
      const centersRaw = p.centers ?? p.world_centers ?? [];
      const centers = centersRaw.map(c => Array.isArray(c) ? { x:c[0], y:c[1], z:c[2] } : { x:c.x, y:c.y, z:c.z });
      return { id, centers, material_key: p.material_key ?? id };
    });
    return {
      kind: "pieces",
      radius: Number(anyObj.r) || 0.5,
      pieces,
      palette: anyObj.palette || {}
    };
  }

  // --- Container-style payload (no pieces, lattice + cells) ---
  if (anyObj && Array.isArray(anyObj.cells)) {
    const id = (anyObj.meta && anyObj.meta.name) ? String(anyObj.meta.name) : "container";
    const centers = cellsToCenters(anyObj.cells, anyObj.lattice);
    return {
      kind: "container",
      radius: Number(anyObj.r) || 0.5,
      pieces: [{ id, centers, material_key: id }],
      palette: { strategy: "muted" } // default; Studio recolor not applied to containers
    };
  }

  // Fallback empty scene
  return { kind: "empty", radius: 0.5, pieces: [], palette: {} };
}

function buildSceneFromSnapshot(snapshot) {
  resetDisplayRoot();                     // clears any previous display
  const root = ensureDisplayRoot();       // fresh container

  const pieces = snapshot.pieces || [];
  const isContainer = snapshot.kind === "container";

  for (const p of pieces) {
    const total = pieces.length || __PIECE_COUNT_DEFAULT;

    // For containers: force a single vibrant material; skip bonds
    const mat = isContainer
      ? makeContainerMaterial(p.material_key || p.id)    // <- colorful single material
      : makePieceMaterialFor(p.material_key || p.id, 0, total);

    const atomR = snapshot.radius || 0.5;
    const g = new THREE.Group();
    g.name = p.id;

    // mark containers so recolor skips them
    if (isContainer) g.userData.isContainer = true;

    // (optional) for pieces, keep a stable key for recolor:
    if (!isContainer) g.userData.pieceKey = p.material_key || p.id;

    const atoms = p.centers.map(c => {
      const s = new THREE.Mesh(new THREE.SphereGeometry(atomR, 24, 16), mat);
      s.position.set(c.x, c.y, c.z);
      g.add(s);
      return s;
    });

    if (!isContainer) {
      addBondsForAtoms(g, atoms, mat);     // bonds ON for pieces
    }

    root.add(g);                            // <- must be root.add(g), NOT scene.add(g)
  }

  const bbox = new THREE.Box3().setFromObject(root);
  const min = bbox.min;
  const max = bbox.max;

  const newCenter = new THREE.Vector3().addVectors(min, max).multiplyScalar(0.5);

  if (!__zoomLocked) {
    // First load: fit once, then lock zoom
    controls.target.copy(newCenter);

    const ext = new THREE.Vector3().subVectors(max, min);
    const longest = Math.max(ext.x, ext.y, ext.z) * 0.6 + 6;
    camera.position.set(newCenter.x + longest, newCenter.y + longest, newCenter.z + longest);
    camera.lookAt(newCenter);

    __savedOrthoZoom = camera.zoom;
    __zoomLocked = true;
  } else {
    // Subsequent loads: move the pivot to the new center
    // and translate the camera by the same delta to avoid any jump.
    const oldTarget = controls.target.clone();
    const delta = newCenter.clone().sub(oldTarget);
    controls.target.copy(newCenter);
    camera.position.add(delta);
  }

  // Recolor using the actual number of groups that were created (prevents repeats)
  // Skip recolor for containers (they use one colorful material on purpose)
  if (!isContainer) {
    recolorSceneInPlace();
  }

  setStatus(`Studio: loaded ${isContainer ? pieces[0]?.centers?.length ?? 0 : pieces.length} ${isContainer ? "container cell(s)" : "piece(s)"}`);
  renderer.render(scene, camera);
}

// Recolor all piece groups in place (skip containers), using the actual group count
function recolorSceneInPlace() {
  const root = scene.getObjectByName("DISPLAY_ROOT");
  if (!root) return;

  // Only recolor non-container groups
  const groups = root.children.filter(
    (ch) => ch.isGroup && !ch.userData?.isContainer && ch.children.some(k => k.isMesh)
  );
  const N = groups.length || __PIECE_COUNT_DEFAULT;

  groups.forEach((g, i) => {
    const key = g.userData?.pieceKey || g.name || String(i);
    const mat = makePieceMaterialFor(key, i, N);
    g.traverse((o) => { if (o.isMesh) o.material = mat; });
  });

  renderer && camera && renderer.render(scene, camera);
}

// ---- Public APIs called from Qt ----
function setColorStrategy(name){
  if (!COLOR_STRATEGIES[name]) return;
  __colorStrategy = name;
  const root = scene.getObjectByName("DISPLAY_ROOT");
  if (!root) return;
  const groups = root.children.filter(ch => ch.isGroup);
  const N = groups.length || __PIECE_COUNT_DEFAULT;
  groups.forEach((g,i) => {
    const key = g.name || String(i);
    const mat = makePieceMaterialFor(key, i, N);
    g.traverse(o => { if (o.isMesh) o.material = mat; });
  });
  renderer.render(scene, camera);
};

function studioLoadJson(jsonText){
  let obj = null;
  try { obj = JSON.parse(jsonText); } catch { setStatus("Studio: invalid JSON"); return; }

  resetDisplayRoot();                   // hard wipe previous display
  const snap = normalizeSnapshot(obj);
  buildSceneFromSnapshot(snap);         // fresh build
};

// Start: assemble pieces from lowest Z to highest (duration in seconds)
function studioPlayAssembleBottomUp(durationSec) {
  const groups = _pieceGroups();
  if (!groups.length) {
    setStatus("Studio: no pieces to animate"); 
    return;
  }

  // sort by min world Z (lowest first)
  const sorted = groups
    .map(g => ({ g, z: _minWorldZ(g) }))
    .sort((a,b) => a.z - b.z)
    .map(o => o.g);

  // initialize: hide all, make materials transparent, collapse bonds
  sorted.forEach(g => {
    g.visible = false;
    g.traverse(o => {
      if (o.isMesh) {
        o.material.transparent = true;
        o.material.opacity = 0.0;
        if (o.userData?.isBond && o.userData.baseLen != null) {
          o.scale.y = 0.0001;
        }
      }
    });
  });

  __anim = {
    kind: 'bottomup',
    start: performance.now(),
    duration: Math.max(500, (Number(durationSec) || 10) * 1000),
    groups: sorted
  };
  setStatus(`Studio: assembling bottom-up (${groups.length} pieces, ${Math.round(__anim.duration/1000)}s)`);
};

// Optional: stop
function studioStopAnimation(){
  __anim = null;
  setStatus("Studio: animation stopped");
};

// expose public APIs (module-safe)
window.setColorStrategy        = window.setColorStrategy        || setColorStrategy;
window.setStudioBrightness     = window.setStudioBrightness     || setStudioBrightness;
window.studioLoadJson          = window.studioLoadJson          || studioLoadJson;
window.studioPlayAssembleBottomUp = window.studioPlayAssembleBottomUp || studioPlayAssembleBottomUp;

// boot
init();
