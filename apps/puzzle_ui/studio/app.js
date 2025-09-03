// ES-module imports from local Studio libs
import * as THREE from './libs/three.module.js';
import { OrbitControls }     from './libs/OrbitControls.js';

// Optional: expose THREE if other code reads window.THREE
window.THREE = window.THREE || THREE;

// Robust cannon-es loader (caches + works with .js/.mjs and window.CANNON)
let __cannonMod;
async function loadCannon() {
  if (__cannonMod) return __cannonMod;
  const candidates = [
    "./libs/cannon-es.js",
    "../viewer/libs/cannon-es.js",
    "./libs/cannon-es.mjs",
    "../viewer/libs/cannon-es.mjs",
  ];
  for (const p of candidates) {
    try {
      const m = await import(p);
      const C = m?.default ?? m;
      if (C?.World) return (__cannonMod = C);
    } catch {}
  }
  if (globalThis.CANNON?.World) return (__cannonMod = globalThis.CANNON);
  studioStatus("cannon-es not found (put ESM at studio/libs/).", "error");
  return null;
}

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
  const col = new THREE.Color().setHSL(((h%1)+1)%1,s,l);
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

// ---- Studio world orientation (None / Largest face to XY / Smallest face to XY)
let __orientMode = "none";                             // 'none' | 'largest-face-xy' | 'smallest-face-xy'
let __orientQuat = new THREE.Quaternion();             // identity by default
let __lastSnapshot = null;                             // normalized snapshot we last built

// Compute covariance of centered points (3x3 symmetric)
function _covariance3(pts) {
  const n = pts.length;
  if (n === 0) return { xx:0, xy:0, xz:0, yy:0, yz:0, zz:0, mean:new THREE.Vector3() };

  const mean = new THREE.Vector3();
  for (const p of pts) mean.add(p);
  mean.multiplyScalar(1 / n);

  let xx=0, xy=0, xz=0, yy=0, yz=0, zz=0;
  for (const p of pts) {
    const x = p.x - mean.x, y = p.y - mean.y, z = p.z - mean.z;
    xx += x*x; xy += x*y; xz += x*z;
    yy += y*y; yz += y*z;
    zz += z*z;
  }
  const inv = 1 / Math.max(1, n - 1);  // unbiased estimate
  return {
    xx: xx*inv, xy: xy*inv, xz: xz*inv,
    yy: yy*inv, yz: yz*inv,
    zz: zz*inv,
    mean
  };
}

// Jacobi eigen-decomposition for 3x3 symmetric matrix (covariance)
function _eigenSym3(c) {
  // Matrix in array form [a00,a01,a02, a01,a11,a12, a02,a12,a22]
  let a00=c.xx, a01=c.xy, a02=c.xz, a11=c.yy, a12=c.yz, a22=c.zz;

  // Eigenvectors start as identity
  let v00=1, v01=0, v02=0,
      v10=0, v11=1, v12=0,
      v20=0, v21=0, v22=1;

  const EPS = 1e-12, MAX_IT=50;
  for (let it=0; it<MAX_IT; it++) {
    // find largest off-diagonal
    let p=0, q=1, max = Math.abs(a01);
    let a02abs = Math.abs(a02), a12abs = Math.abs(a12);
    if (a02abs > max) { max = a02abs; p=0; q=2; }
    if (a12abs > max) { max = a12abs; p=1; q=2; }
    if (max < EPS) break;

    // compute rotation
    let apq, app, aqq;
    if (p===0 && q===1) { apq=a01; app=a00; aqq=a11; }
    else if (p===0 && q===2) { apq=a02; app=a00; aqq=a22; }
    else { apq=a12; app=a11; aqq=a22; }

    const phi = 0.5 * Math.atan2(2*apq, (aqq - app));
    const coss = Math.cos(phi), sinn = Math.sin(phi);

    // rotate A
    function rotA(i,j) {
      // A' = R^T A R ; implement by updating needed elements
    }
    // Update A explicitly for each p,q choice
    if (p===0 && q===1) {
      const a00n = coss*coss*a00 - 2*coss*sinn*a01 + sinn*sinn*a11;
      const a11n = sinn*sinn*a00 + 2*coss*sinn*a01 + coss*coss*a11;
      const a01n = 0;
      const a02n = coss*a02 - sinn*a12;
      const a12n = sinn*a02 + coss*a12;
      a00=a00n; a11=a11n; a01=a01n; a02=a02n; a12=a12n;
    } else if (p===0 && q===2) {
      const a00n = coss*coss*a00 - 2*coss*sinn*a02 + sinn*sinn*a22;
      const a22n = sinn*sinn*a00 + 2*coss*sinn*a02 + coss*coss*a22;
      const a02n = 0;
      const a01n = coss*a01 - sinn*a12;
      const a12n = sinn*a01 + coss*a12;
      a00=a00n; a22=a22n; a02=a02n; a01=a01n; a12=a12n;
    } else { // p=1,q=2
      const a11n = coss*coss*a11 - 2*coss*sinn*a12 + sinn*sinn*a22;
      const a22n = sinn*sinn*a11 + 2*coss*sinn*a12 + coss*coss*a22;
      const a12n = 0;
      const a01n = coss*a01 - sinn*a02;
      const a02n = sinn*a01 + coss*a02;
      a11=a11n; a22=a22n; a12=a12n; a01=a01n; a02=a02n;
    }

    // rotate V (accumulate eigenvectors)
    function updV(ix, jx) { /* no-op; expand below */ }
    if (p===0 && q===1) {
      const nv00 = coss*v00 - sinn*v01, nv01 = sinn*v00 + coss*v01;
      const nv10 = coss*v10 - sinn*v11, nv11 = sinn*v10 + coss*v11;
      const nv20 = coss*v20 - sinn*v21, nv21 = sinn*v20 + coss*v21;
      v00=nv00; v01=nv01;
      v10=nv10; v11=nv11;
      v20=nv20; v21=nv21;
    } else if (p===0 && q===2) {
      const nv00 = coss*v00 - sinn*v02, nv02 = sinn*v00 + coss*v02;
      const nv10 = coss*v10 - sinn*v12, nv12 = sinn*v10 + coss*v12;
      const nv20 = coss*v20 - sinn*v22, nv22 = sinn*v20 + coss*v22;
      v00=nv00; v02=nv02;
      v10=nv10; v12=nv12;
      v20=nv20; v22=nv22;
    } else {
      const nv01 = coss*v01 - sinn*v02, nv02 = sinn*v01 + coss*v02;
      const nv11 = coss*v11 - sinn*v12, nv12 = sinn*v11 + coss*v12;
      const nv21 = coss*v21 - sinn*v22, nv22 = sinn*v21 + coss*v22;
      v01=nv01; v02=nv02;
      v11=nv11; v12=nv12;
      v21=nv21; v22=nv22;
    }
  }

  // Eigenvalues on the diagonal; eigenvectors are columns of V
  const evals = [a00, a11, a22];
  const evecs = [
    new THREE.Vector3(v00, v10, v20),
    new THREE.Vector3(v01, v11, v21),
    new THREE.Vector3(v02, v12, v22),
  ];
  // Normalize eigenvectors
  for (const v of evecs) v.normalize();

  // Sort by descending eigenvalue
  const idx = [0,1,2].sort((i,j) => evals[j]-evals[i]);
  return {
    evals: [evals[idx[0]], evals[idx[1]], evals[idx[2]]],
    evecs: [evecs[idx[0]], evecs[idx[1]], evecs[idx[2]]]
  };
}

// Compute quaternion that orients the chosen "face normal" to +Z
function _computeOrientQuat(points, mode) {
  if (!points || points.length < 2 || mode === "none") {
    return new THREE.Quaternion(); // identity
  }
  // PCA on centers → principal directions
  const cov = _covariance3(points);
  const { evals, evecs } = _eigenSym3(cov);
  // For "largest face to XY": pick smallest variance dir as normal
  // For "smallest face to XY": pick largest variance dir as normal
  let normal, inPlane;
  if (mode === "largest-face-xy") {
    normal = evecs[2].clone();        // smallest eigenvalue
    inPlane = evecs[0].clone();       // largest eigenvalue (for deterministic X)
  } else { // "smallest-face-xy"
    normal = evecs[0].clone();        // largest eigenvalue
    inPlane = evecs[1].clone();       // second-largest
  }

  // Step 1: rotate normal -> +Z
  const zAxis = new THREE.Vector3(0,0,1);
  const q1 = new THREE.Quaternion().setFromUnitVectors(normal.clone().normalize(), zAxis);

  // Step 2: align in-plane axis to +X for stable orientation
  const ip = inPlane.clone().applyQuaternion(q1);
  ip.z = 0; // project to XY
  if (ip.lengthSq() > 1e-12) {
    ip.normalize();
    const ang = Math.atan2(ip.y, ip.x);          // angle from +X
    const q2 = new THREE.Quaternion().setFromAxisAngle(zAxis, -ang);
    return q2.multiply(q1);
  }
  return q1;
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

// Brightness persistence
const BRIGHTNESS_KEY = "studio.brightness.v1";

function saveBrightness(factor) {
  localStorage.setItem(BRIGHTNESS_KEY, JSON.stringify(factor));
}

function loadBrightness() {
  const saved = localStorage.getItem(BRIGHTNESS_KEY);
  if (saved) {
    try {
      return JSON.parse(saved);
    } catch (e) {
      console.log('Failed to parse saved brightness:', e);
    }
  }
  return 1.0; // default
}

// Exposed to PyQt
window.setStudioBrightness = function(factor) {
  applyBrightness(factor);
  saveBrightness(factor);
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
  camera.up.set(0, 0, 1);  // Z-up for turntable feel (no roll)
  camera.position.set(8,8,8);

  // Export core objects for external integrations / debugging
  globalThis.STUDIO   = { scene, camera, renderer };
  globalThis.scene    = scene;
  globalThis.camera   = camera;
  globalThis.renderer = renderer;
  // Also export on window for explicit compatibility
  window.STUDIO   = { scene, camera, renderer };
  window.scene    = scene;
  window.camera   = camera;
  window.renderer = renderer;

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  AMB_LIGHT = new THREE.AmbientLight(0xffffff, __baseAmb);
  scene.add(AMB_LIGHT);

  DIR_LIGHT = new THREE.DirectionalLight(0xffffff, __baseDir);
  DIR_LIGHT.position.set(10,12,8);
  scene.add(DIR_LIGHT);

  // Load and apply saved brightness
  const savedBrightness = loadBrightness();
  applyBrightness(savedBrightness);

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
    studioStatus("Studio: animation complete");
  }
}

function _stepAnimation() {
  if (!__anim) return;
  if (__anim.kind === 'bottomup') _stepAssembleBottomUp();
  else if (__anim.kind === 'orbitxy') _stepOrbitXY();
  else if (__anim.kind === 'puzzle-shape') {
    if (__anim.phase === 'collapse') {
      _stepPuzzleShapeCollapse();
    } else {
      _stepPuzzleShape();
    }
  }
}

function animate(){
  const now = performance.now();
  const dt  = (now - (window.__studioPrev || now)) / 1000;
  window.__studioPrev = now;

  controls.update();
  window.studioAnimStep?.(dt);   // ← call the physics stepper
  _stepAnimation();              // ← dispatch other Studio anims safely
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

// Step the camera around Z (XY plane) 0→360°, keeping distance and height constant
function _stepOrbitXY() {
  if (!__anim || __anim.kind !== 'orbitxy') return;
  const now = performance.now();
  const t = Math.min(1, (now - __anim.start) / __anim.duration);  // 0..1
  const phi = __anim.phi0 + 2 * Math.PI * t;

  // Rotate position around Z, keep z offset and target fixed
  const cx = __anim.center.x, cy = __anim.center.y, cz = __anim.center.z;
  const x = cx + __anim.rxy * Math.cos(phi);
  const y = cy + __anim.rxy * Math.sin(phi);
  const z = cz + __anim.zOff;

  camera.position.set(x, y, z);
  camera.lookAt(__anim.center);

  if (t >= 1) {
    // end exactly at full rotation
    camera.position.set(
      cx + __anim.rxy * Math.cos(__anim.phi0 + 2 * Math.PI),
      cy + __anim.rxy * Math.sin(__anim.phi0 + 2 * Math.PI),
      cz + __anim.zOff
    );
    camera.lookAt(__anim.center);
    __anim = null;
    studioStatus("Studio: orbit complete");
  }
}

// Public: start Orbit 360° (XY)
window.studioPlayOrbitXY = function(durationSec) {
  if (!camera || !controls) { studioStatus("Studio: camera not ready"); return; }

  const center = controls.target.clone();
  const rel = camera.position.clone().sub(center);
  const rxy = Math.hypot(rel.x, rel.y);   // horizontal radius from center
  const zOff = rel.z;                      // keep current height
  const phi0 = Math.atan2(rel.y, rel.x);  // starting angle in XY

  __anim = {
    kind: 'orbitxy',
    start: performance.now(),
    duration: Math.max(500, (Number(durationSec)||10) * 1000),
    center,
    rxy,
    zOff,
    phi0,
  };
  studioStatus(`Studio: orbit 360° in ${Math.round(__anim.duration/1000)}s`);
};

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
let _studioStatusDiv = null;
function studioStatus(msg, kind = "info") {
  if (!_studioStatusDiv) {
    _studioStatusDiv = document.createElement("div");
    _studioStatusDiv.style.cssText =
      "position:absolute;left:12px;bottom:12px;z-index:9999;padding:6px 10px;border-radius:8px;font:12px/1.2 system-ui;background:#0009;color:#fff;pointer-events:none;max-width:46vw;";
    document.body.appendChild(_studioStatusDiv);
  }
  _studioStatusDiv.textContent = msg;
  _studioStatusDiv.style.background = kind === "error" ? "#b0002099" : "#0009";
  setTimeout(() => {
    if (_studioStatusDiv && _studioStatusDiv.textContent === msg) _studioStatusDiv.textContent = "";
  }, 2500);
}

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
  const total = pieces.length || __PIECE_COUNT_DEFAULT;

  // --- NEW: gather all centers into an array of THREE.Vector3 for orientation ---
  const allPts = [];
  for (const p of pieces) {
    for (const c of (p.centers || [])) {
      allPts.push(new THREE.Vector3(c.x, c.y, c.z));
    }
  }
  // Compute the quaternion once for this snapshot (according to current mode)
  __orientQuat = _computeOrientQuat(allPts, __orientMode);

  const atomR = snapshot.radius || 0.5;

  let min = new THREE.Vector3( Infinity, Infinity, Infinity);
  let max = new THREE.Vector3(-Infinity,-Infinity,-Infinity);

  pieces.forEach((p, idx) => {
    const g = new THREE.Group();
    g.name = p.id;
    if (isContainer) g.userData.isContainer = true;
    else {
      g.userData.pieceKey = p.material_key || p.id;
      g.userData.kind = 'piece';
    }

    const mat = isContainer
      ? makeContainerMaterial(p.material_key || p.id)
      : makePieceMaterialFor(p.material_key || p.id, idx, total);

    const atoms = [];
    for (const c of p.centers) {
      const v = new THREE.Vector3(c.x, c.y, c.z).applyQuaternion(__orientQuat);  // <--- apply orientation
      const s = new THREE.Mesh(new THREE.SphereGeometry(atomR, 24, 16), mat);
      s.userData.isAtom = true;
      s.position.copy(v);
      g.add(s); atoms.push(s);

      min.min(v); max.max(v);  // bbox from oriented points
    }

    // Tag local atom data for physics/analysis consumers
    if (!isContainer) {
      g.userData.atoms = atoms.map(s => s.position.clone()); // LOCAL offsets
      g.userData.atomRadius = atomR;                          // visual radius in scene units

      // Fallback inference if metadata missing or invalid (safety)
      if (!Array.isArray(g.userData.atoms) || !g.userData.atoms.length || !Number.isFinite(g.userData.atomRadius)) {
        const inferred = []; let r = null;
        g.traverse(o => {
          if (o.isMesh) {
            inferred.push(o.position.clone());
            if (o.geometry?.parameters?.radius && r == null) r = o.geometry.parameters.radius;
          }
        });
        g.userData.atoms = inferred;
        g.userData.atomRadius = r ?? 0.5;
      }
      addBondsForAtoms(g, atoms, mat);
    }
    root.add(g);
  });

  const bbox = new THREE.Box3().setFromObject(root);
  const newCenter = new THREE.Vector3().addVectors(bbox.min, bbox.max).multiplyScalar(0.5);

  if (!__zoomLocked) {
    // First load: fit once, then lock zoom
    controls.target.copy(newCenter);

    const ext = new THREE.Vector3().subVectors(bbox.max, bbox.min);
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

  // Reset all visual properties to default state
  root.traverse(obj => {
    if (obj.isMesh) {
      obj.visible = true;
      obj.material.transparent = false;
      obj.material.opacity = 1.0;
      if (obj.userData?.isBond && obj.userData.baseLen != null) {
        obj.scale.y = 1.0; // Reset bond scale
      }
    }
    if (obj.isGroup) {
      obj.visible = true;
    }
  });

  // Recolor using the actual number of groups that were created (prevents repeats)
  // Skip recolor for containers (they use one colorful material on purpose)
  if (!isContainer) {
    recolorSceneInPlace();
  }

  studioStatus(`Studio: loaded ${isContainer ? pieces[0]?.centers?.length ?? 0 : pieces.length} ${isContainer ? "container cell(s)" : "piece(s)"}`);
  renderer.render(scene, camera);
  __lastSnapshot = snapshot;   // keep for re-orienting on dropdown change
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
  try { obj = JSON.parse(jsonText); } catch { studioStatus("Studio: invalid JSON"); return; }

  resetDisplayRoot();                   // hard wipe previous display
  const snap = normalizeSnapshot(obj);
  buildSceneFromSnapshot(snap);         // fresh build
  
  // Ensure snapshot is saved for animation resets
  window.__lastSnapshot = snap;
  console.log("Snapshot saved for animation resets:", snap);
};


// Helper function to collect pieces and atoms in world space
function _collectPiecesAtomsWorld() {
  const groups = _pieceGroups();
  const pieces = [];
  
  for (let i = 0; i < groups.length; i++) {
    const group = groups[i];
    const atoms = [];
    
    // Collect all mesh positions in world space
    group.traverse(obj => {
      if (obj.isMesh && obj.userData?.isAtom) {
        const worldPos = new THREE.Vector3();
        obj.getWorldPosition(worldPos);
        atoms.push({ pos: worldPos, mesh: obj });
      }
    });
    
    if (atoms.length > 0) {
      // Calculate centroid
      const centroid = new THREE.Vector3();
      atoms.forEach(atom => centroid.add(atom.pos));
      centroid.divideScalar(atoms.length);
      
      pieces.push({
        group: group,
        atoms: atoms,
        centroid: centroid,
        minZ: Math.min(...atoms.map(a => a.pos.z))
      });
    }
  }
  
  return pieces;
}

// Start: assemble pieces from lowest Z to highest (duration in seconds)
function studioPlayAssembleBottomUp(durationSec) {
  const groups = _pieceGroups();
  if (!groups.length) { studioStatus("Studio: no pieces to animate"); return; }

  // Gather world-space atoms, minZ, centroids (oriented positions already baked)
  const pcs = _collectPiecesAtomsWorld();

  // Sort pieces by Z position (bottom-up assembly)
  pcs.sort((a, b) => a.minZ - b.minZ);
  
  studioStatus(`Studio: assembling ${pcs.length} piece(s) bottom-up`);

  // Initialize visual state: hide all; transparent; bonds collapsed
  for (const g of groups) {
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
  }

  // Map pieces to groups for animation
  const orderedGroups = pcs.map(p => p.group);

  __anim = {
    kind: 'bottomup',
    start: performance.now(),
    duration: Math.max(500, (Number(durationSec) || 10) * 1000),
    groups: orderedGroups
  };
};

// Puzzle Shape: Physics-based container formation
function studioPlayPuzzleShape(settings) {
  // Handle both old (durationSec, material) and new (settings object) signatures
  if (typeof settings === 'number' || typeof settings === 'string') {
    settings = {
      duration: arguments[0] || 15,
      material: arguments[1] || 'metallic',
      scatterRadius: 15,
      collisionRadius: 2.2,
      avoidanceStrength: 2.0,
      colorStrategy: 'golden-3band'
    };
  }
  
  const groups = _pieceGroups();
  if (!groups.length) { studioStatus("Studio: no pieces to animate"); return; }

  // Collect all individual spheres with their world positions from file
  const allSpheres = [];
  groups.forEach((group, groupIndex) => {
    group.traverse(obj => {
      if (obj.isMesh && obj.userData?.isAtom) {
        // Get the world position (this forms the puzzle shape)
        const worldPos = new THREE.Vector3();
        obj.getWorldPosition(worldPos);
        allSpheres.push({
          mesh: obj,
          originalWorldPos: worldPos.clone(), // Original world position from file
          targetPos: worldPos.clone(), // Target is the world position (forms shape)
          group: group,
          groupIndex: groupIndex
        });
      }
    });
  });

  if (!allSpheres.length) {
    studioStatus("Studio: no spheres found");
    return;
  }

  // Target positions are the original world positions (forms the puzzle shape)
  allSpheres.forEach(sphere => {
    sphere.targetPos = sphere.originalWorldPos.clone();
  });

  // Set up random cloud start state
  const center = calculateCentroid(allSpheres.map(s => {
    const worldPos = new THREE.Vector3();
    s.mesh.getWorldPosition(worldPos);
    return worldPos;
  }));
  
  allSpheres.forEach((sphere, sphereIndex) => {
    // Random position in cloud around center
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.random() * settings.scatterRadius;
    
    const worldStartPos = new THREE.Vector3(
      center.x + r * Math.sin(phi) * Math.cos(theta),
      center.y + r * Math.sin(phi) * Math.sin(theta),
      center.z + r * Math.cos(phi)
    );
    
    // Store world positions for animation
    sphere.startPos = worldStartPos.clone();
    
    // Apply material based on color strategy or uniform material
    if (settings.colorStrategy && settings.colorStrategy !== 'uniform') {
      // Use app's color palette
      const colorFn = COLOR_STRATEGIES[settings.colorStrategy] || COLOR_STRATEGIES['golden-3band'];
      const color = colorFn(sphere.group.name || String(sphere.groupIndex), sphere.groupIndex, groups.length);
      sphere.mesh.material = new THREE.MeshStandardMaterial({ 
        color: color, 
        metalness: 0.2, 
        roughness: 0.45 
      });
    } else {
      // Use uniform material
      sphere.mesh.material = createPuzzleShapeMaterial(settings.material);
    }
  });

  // Hide all bonds for this animation - traverse entire scene
  const root = scene.getObjectByName("DISPLAY_ROOT");
  if (root) {
    root.traverse(obj => {
      if (obj.userData?.isBond) {
        obj.visible = false;
      }
    });
  }

  __anim = {
    kind: 'puzzle-shape',
    start: performance.now(),
    duration: Math.max(1000, settings.duration * 1000),
    spheres: allSpheres,
    settings: settings
  };

  studioStatus(`Studio: forming puzzle shape with ${allSpheres.length} spheres (${settings.colorStrategy || settings.material})`);
}

// Calculate convex hull of points (simplified 3D hull)
function calculateConvexHull(points) {
  if (points.length < 4) return points;
  
  // For simplicity, use bounding box vertices as hull approximation
  const bbox = new THREE.Box3().setFromPoints(points);
  const corners = [
    new THREE.Vector3(bbox.min.x, bbox.min.y, bbox.min.z),
    new THREE.Vector3(bbox.max.x, bbox.min.y, bbox.min.z),
    new THREE.Vector3(bbox.min.x, bbox.max.y, bbox.min.z),
    new THREE.Vector3(bbox.max.x, bbox.max.y, bbox.min.z),
    new THREE.Vector3(bbox.min.x, bbox.min.y, bbox.max.z),
    new THREE.Vector3(bbox.max.x, bbox.min.y, bbox.max.z),
    new THREE.Vector3(bbox.min.x, bbox.max.y, bbox.max.z),
    new THREE.Vector3(bbox.max.x, bbox.max.y, bbox.max.z)
  ];
  
  // Add some surface points for better shape
  const surfacePoints = [];
  for (let i = 0; i < points.length; i += Math.max(1, Math.floor(points.length / 20))) {
    surfacePoints.push(points[i]);
  }
  
  return [...corners, ...surfacePoints];
}

// Assign target positions to spheres
function assignTargetPositions(spheres, hullPoints) {
  spheres.forEach((sphere, i) => {
    // Distribute spheres among hull points
    const targetIdx = i % hullPoints.length;
    sphere.targetPos = hullPoints[targetIdx].clone();
    
    // Add small random offset to prevent exact overlap
    const offset = new THREE.Vector3(
      (Math.random() - 0.5) * 2,
      (Math.random() - 0.5) * 2,
      (Math.random() - 0.5) * 2
    );
    sphere.targetPos.add(offset);
  });
}

// Calculate centroid of points
function calculateCentroid(points) {
  const center = new THREE.Vector3();
  points.forEach(p => center.add(p));
  center.divideScalar(points.length);
  return center;
}

// Create material for puzzle shape spheres
function createPuzzleShapeMaterial(materialType) {
  const materials = {
    metallic: new THREE.MeshStandardMaterial({ 
      color: 0x888888, metalness: 0.8, roughness: 0.2 
    }),
    glass: new THREE.MeshPhysicalMaterial({ 
      color: 0x88ccff, transmission: 0.9, opacity: 0.8, transparent: true 
    }),
    plastic: new THREE.MeshStandardMaterial({ 
      color: 0xff6644, metalness: 0.1, roughness: 0.6 
    }),
    gold: new THREE.MeshStandardMaterial({ 
      color: 0xffd700, metalness: 0.9, roughness: 0.1 
    }),
    chrome: new THREE.MeshStandardMaterial({ 
      color: 0xffffff, metalness: 1.0, roughness: 0.05, envMapIntensity: 1.5 
    }),
    matte: new THREE.MeshLambertMaterial({ 
      color: 0x666666 
    })
  };
  
  return materials[materialType] || materials.chrome;
}

// Puzzle Shape Settings Panel (similar to snuggle dialog)
const PUZZLE_SHAPE_KEY = "studio.puzzleshape.cfg.v1";

function getPuzzleShapeDefaults() {
  return {
    duration: 15,
    scatterRadius: 15,
    collisionRadius: 2.2,
    avoidanceStrength: 2.0,
    colorStrategy: 'golden-3band',
    material: 'chrome',
    collapseDelay: 2.0,
    gravity: 9.8,
    bounce: 0.3,
    damping: 0.85
  };
}

function openPuzzleShapeDialog() {
  const cfg = getPuzzleShapeDefaults();
  const colorStrategies = Object.keys(COLOR_STRATEGIES);
  const materials = ['chrome', 'metallic', 'glass', 'plastic', 'gold', 'matte'];
  
  // Create modal backdrop
  const backdrop = document.createElement('div');
  backdrop.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
  
  // Create modal content
  const modal = document.createElement('div');
  modal.style.cssText = 'background:#2a2a2a;border-radius:12px;padding:24px;max-width:500px;width:90%;color:#fff;font:14px/1.4 system-ui;box-shadow:0 8px 32px rgba(0,0,0,0.5);';
  
  modal.innerHTML = `
    <h3 style="margin:0 0 20px 0;color:#4a9eff;">Puzzle: Shape Formation Settings</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Duration (seconds)</label>
        <input type="number" id="shape-duration" min="5" max="60" step="0.5" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Scatter Radius</label>
        <input type="number" id="shape-scatter" min="5" max="50" step="1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Collision Radius</label>
        <input type="number" id="shape-collision" min="0.5" max="5.0" step="0.1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Avoidance Strength</label>
        <input type="number" id="shape-avoidance" min="0.5" max="10.0" step="0.1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Collapse Delay (seconds)</label>
        <input type="number" id="shape-collapse-delay" min="0.5" max="10" step="0.5" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Gravity Strength</label>
        <input type="number" id="shape-gravity" min="1" max="20" step="0.5" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Bounce Factor</label>
        <input type="number" id="shape-bounce" min="0" max="1" step="0.1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Damping Factor</label>
        <input type="number" id="shape-damping" min="0.1" max="1" step="0.05" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Color Strategy</label>
        <select id="shape-color" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
          ${colorStrategies.map(strategy => `<option value="${strategy}">${strategy}</option>`).join('')}
        </select>
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Material Type</label>
        <select id="shape-material" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
          ${materials.map(material => `<option value="${material}">${material}</option>`).join('')}
        </select>
      </div>
    </div>
    <div style="display:flex;gap:12px;margin-top:24px;justify-content:flex-end;">
      <button id="shape-reset" style="padding:8px 16px;border:1px solid #666;border-radius:6px;background:#444;color:#fff;cursor:pointer;">Reset</button>
      <button id="shape-cancel" style="padding:8px 16px;border:1px solid #666;border-radius:6px;background:#444;color:#fff;cursor:pointer;">Cancel</button>
      <button id="shape-run" style="padding:8px 16px;border:1px solid #4a9eff;border-radius:6px;background:#4a9eff;color:#fff;cursor:pointer;font-weight:500;">Run</button>
    </div>
  `;
  
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  
  // Set values after DOM insertion
  modal.querySelector('#shape-duration').value = cfg.duration;
  modal.querySelector('#shape-scatter').value = cfg.scatterRadius;
  modal.querySelector('#shape-collision').value = cfg.collisionRadius;
  modal.querySelector('#shape-avoidance').value = cfg.avoidanceStrength;
  modal.querySelector('#shape-collapse-delay').value = cfg.collapseDelay || 2.0;
  modal.querySelector('#shape-gravity').value = cfg.gravity || 9.8;
  modal.querySelector('#shape-bounce').value = cfg.bounce || 0.3;
  modal.querySelector('#shape-damping').value = cfg.damping || 0.85;
  modal.querySelector('#shape-color').value = cfg.colorStrategy;
  modal.querySelector('#shape-material').value = cfg.material;
  
  // Event handlers
  const resetBtn = modal.querySelector('#shape-reset');
  const cancelBtn = modal.querySelector('#shape-cancel');
  const runBtn = modal.querySelector('#shape-run');
  
  resetBtn.addEventListener('click', () => {
    localStorage.removeItem(PUZZLE_SHAPE_KEY);
    const defaults = getPuzzleShapeDefaults();
    modal.querySelector('#shape-duration').value = defaults.duration;
    modal.querySelector('#shape-scatter').value = defaults.scatterRadius;
    modal.querySelector('#shape-collision').value = defaults.collisionRadius;
    modal.querySelector('#shape-avoidance').value = defaults.avoidanceStrength;
    modal.querySelector('#shape-collapse-delay').value = defaults.collapseDelay || 2.0;
    modal.querySelector('#shape-gravity').value = defaults.gravity || 9.8;
    modal.querySelector('#shape-bounce').value = defaults.bounce || 0.3;
    modal.querySelector('#shape-damping').value = defaults.damping || 0.85;
    modal.querySelector('#shape-color').value = defaults.colorStrategy;
    modal.querySelector('#shape-material').value = defaults.material;
    studioStatus('Puzzle shape settings reset to defaults');
  });
  
  cancelBtn.addEventListener('click', () => {
    document.body.removeChild(backdrop);
  });
  
  runBtn.addEventListener('click', (e) => {
    if (runBtn.disabled) return;
    runBtn.disabled = true;
    runBtn.textContent = 'Running...';
    
    // Collect settings
    const settings = {
      duration: +modal.querySelector('#shape-duration').value || 15,
      scatterRadius: +modal.querySelector('#shape-scatter').value || 15,
      collisionRadius: +modal.querySelector('#shape-collision').value || 2.2,
      avoidanceStrength: +modal.querySelector('#shape-avoidance').value || 2.0,
      colorStrategy: modal.querySelector('#shape-color').value || 'golden-3band',
      material: modal.querySelector('#shape-material').value || 'chrome',
      enableCollapse: true,
      collapseDelay: +modal.querySelector('#shape-collapse-delay').value || 2.0,
      gravity: +modal.querySelector('#shape-gravity').value || 9.8,
      bounce: +modal.querySelector('#shape-bounce').value || 0.3,
      damping: +modal.querySelector('#shape-damping').value || 0.85,
      fallDuration: 3.0     // Duration of physics fall
    };
    
    // Save to localStorage
    localStorage.setItem(PUZZLE_SHAPE_KEY, JSON.stringify(settings));
    
    // Close modal
    document.body.removeChild(backdrop);
    
    // Start animation
    window.studioPlayPuzzleShape?.(settings);
  });
  
  // Close on backdrop click
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) {
      document.body.removeChild(backdrop);
    }
  });
}

// Step puzzle shape animation
function _stepPuzzleShape() {
  if (!__anim || __anim.kind !== 'puzzle-shape') return;
  
  const now = performance.now();
  const elapsed = now - __anim.start;
  const progress = Math.min(1, elapsed / __anim.duration);
  
  // Check for phase transitions - allocate time between formation and collapse
  const formationRatio = 0.4; // 40% of time for formation, 60% for collapse
  const formationDuration = __anim.duration * formationRatio;
  const sphereProgress = Math.min(1, elapsed / formationDuration);
  
  // Get sphere radius for collision detection
  const sphereRadius = __anim.spheres[0]?.mesh.geometry?.parameters?.radius || 0.5;
  const collisionRadius = sphereRadius * (__anim.settings?.collisionRadius || 2.2);
  
  __anim.spheres.forEach((sphere, index) => {
    const startPos = sphere.startPos;
    const targetPos = sphere.targetPos;
    
    // Smooth easing without oscillation
    const easeProgress = 1 - Math.pow(1 - progress, 2.5); // Smoother ease-out
    
    // Base interpolated position
    let currentWorldPos = new THREE.Vector3().lerpVectors(startPos, targetPos, easeProgress);
    
    // Collision avoidance - check against other spheres
    const avoidanceForce = new THREE.Vector3();
    __anim.spheres.forEach((otherSphere, otherIndex) => {
      if (index === otherIndex) return;
      
      // Get other sphere's current position
      const otherStartPos = otherSphere.startPos;
      const otherTargetPos = otherSphere.targetPos;
      const otherCurrentPos = new THREE.Vector3().lerpVectors(otherStartPos, otherTargetPos, easeProgress);
      
      // Calculate distance and avoidance
      const distance = currentWorldPos.distanceTo(otherCurrentPos);
      if (distance < collisionRadius && distance > 0.001) {
        // Calculate avoidance direction
        const avoidDirection = new THREE.Vector3()
          .subVectors(currentWorldPos, otherCurrentPos)
          .normalize();
        
        // Stronger avoidance when closer
        const avoidStrength = (collisionRadius - distance) / collisionRadius;
        const maxAvoidance = __anim.settings?.avoidanceStrength || 2.0;
        
        avoidanceForce.add(avoidDirection.multiplyScalar(avoidStrength * maxAvoidance));
      }
    });
    
    // Apply avoidance force with falloff as animation progresses
    const avoidanceFalloff = Math.max(0, 1 - progress * 1.5); // Reduce avoidance near end
    currentWorldPos.add(avoidanceForce.multiplyScalar(avoidanceFalloff));
    
    // Convert world position to local position for the mesh
    const groupWorldMatrix = sphere.group.matrixWorld.clone();
    const groupWorldMatrixInverse = groupWorldMatrix.invert();
    const localPos = currentWorldPos.clone().applyMatrix4(groupWorldMatrixInverse);
    
    sphere.mesh.position.copy(localPos);
  });
  
  // Check for phase transitions
  if (progress >= 1 && __anim.settings?.enableCollapse && !__anim.phase) {
    // Transition to collapse phase
    __anim.phase = 'collapse';
    __anim.collapseStart = now;
    __anim.collapseWaitDuration = (__anim.settings.collapseDelay || 2) * 1000;
    
    // Find a sphere on XY plane to disturb
    const xyPlaneThreshold = 2.0;
    const xySpheres = __anim.spheres.filter(sphere => {
      const worldPos = new THREE.Vector3();
      sphere.mesh.getWorldPosition(worldPos);
      return Math.abs(worldPos.z) < xyPlaneThreshold;
    });
    
    if (xySpheres.length > 0) {
      __anim.disturbedSphere = xySpheres[Math.floor(Math.random() * xySpheres.length)];
    }
    
    studioStatus("Studio: shape formed - preparing collapse...");
    return; // Don't end animation yet
  } else if (progress >= 1 && !__anim.settings?.enableCollapse) {
    __anim = null;
    studioStatus("Studio: puzzle shape formation complete - bonds remain hidden");
  }
}

// Handle collapse phase of puzzle shape animation
function _stepPuzzleShapeCollapse() {
  if (!__anim || __anim.kind !== 'puzzle-shape' || __anim.phase !== 'collapse') return;
  
  const now = performance.now();
  const collapseElapsed = now - __anim.collapseStart;
  
  if (collapseElapsed < __anim.collapseWaitDuration) {
    // Waiting phase - shape holds steady
    return;
  }
  
  const fallElapsed = collapseElapsed - __anim.collapseWaitDuration;
  const fallProgress = Math.min(1, fallElapsed / __anim.fallDuration);
  
  if (fallProgress === 0) {
    // Initialize physics for all spheres
    __anim.spheres.forEach(sphere => {
      sphere.velocity = new THREE.Vector3();
      sphere.grounded = false;
    });
    
    // Remove one sphere to trigger collapse
    if (__anim.disturbedSphere) {
      __anim.disturbedSphere.mesh.visible = false;
      studioStatus("Studio: sphere removed - structure collapsing...");
    }
  }
  
  // Apply gravity and collision physics to all remaining spheres
  const dt = 0.016; // Fixed timestep
  const gravity = -(__anim.settings.gravity || 9.8);
  const damping = __anim.settings.damping || 0.85;
  const bounce = __anim.settings.bounce || 0.3;
  const sphereRadius = __anim.spheres[0]?.mesh.geometry?.parameters?.radius || 0.5;
  
  __anim.spheres.forEach((sphere, index) => {
    if (!sphere.mesh.visible) return; // Skip removed sphere
    
    // Get current world position
    const worldPos = new THREE.Vector3();
    sphere.mesh.getWorldPosition(worldPos);
    
    // Initialize velocity if needed
    if (!sphere.velocity) sphere.velocity = new THREE.Vector3();
    
    // Apply gravity
    sphere.velocity.z += gravity * dt;
    
    // Add horizontal spreading forces during fall
    if (!sphere.grounded) {
      const horizontalForce = new THREE.Vector3(
        (Math.random() - 0.5) * 2,
        (Math.random() - 0.5) * 2,
        0
      );
      sphere.velocity.add(horizontalForce.multiplyScalar(dt));
    }
    
    // Sphere-to-sphere collisions
    __anim.spheres.forEach((otherSphere, otherIndex) => {
      if (index === otherIndex || !otherSphere.mesh.visible) return;
      
      const otherWorldPos = new THREE.Vector3();
      otherSphere.mesh.getWorldPosition(otherWorldPos);
      
      const distance = worldPos.distanceTo(otherWorldPos);
      const minDistance = sphereRadius * 2;
      
      if (distance < minDistance && distance > 0.001) {
        // Collision response with stronger horizontal push
        const normal = new THREE.Vector3().subVectors(worldPos, otherWorldPos).normalize();
        const overlap = minDistance - distance;
        
        // Stronger horizontal forces to encourage spreading
        normal.x *= 2.0;
        normal.y *= 2.0;
        normal.z *= 0.5;
        
        const pushForce = normal.multiplyScalar(overlap * 100);
        sphere.velocity.add(pushForce.multiplyScalar(dt));
      }
    });
    
    // Update position
    const newWorldPos = worldPos.add(sphere.velocity.clone().multiplyScalar(dt));
    
    // Ground collision (world Z = 0)
    if (newWorldPos.z <= sphereRadius) {
      newWorldPos.z = sphereRadius;
      sphere.velocity.multiplyScalar(damping);
      if (sphere.velocity.z < 0) {
        sphere.velocity.z *= -bounce;
      }
      
      // Stop small bounces
      if (Math.abs(sphere.velocity.z) < 0.5) {
        sphere.velocity.z = 0;
        sphere.grounded = true;
      }
      
      // Add some spreading on ground contact
      if (!sphere.hasSpread) {
        const spreadForce = new THREE.Vector3(
          (Math.random() - 0.5) * 8,
          (Math.random() - 0.5) * 8,
          0
        );
        sphere.velocity.add(spreadForce);
        sphere.hasSpread = true;
      }
    }
    
    // Convert back to local coordinates
    const groupWorldMatrix = sphere.group.matrixWorld.clone();
    const groupWorldMatrixInverse = groupWorldMatrix.invert();
    const localPos = newWorldPos.clone().applyMatrix4(groupWorldMatrixInverse);
    
    sphere.mesh.position.copy(localPos);
  });
  
  // End animation based on total elapsed time from start
  const totalElapsed = now - __anim.start;
  const totalDuration = __anim.duration; // Use the UI-specified duration
  const totalProgress = totalElapsed / totalDuration;
  
  if (totalProgress >= 1) {
    __anim = null;
    studioStatus("Studio: animation complete - spheres settled on XY plane");
  }
}

// Optional: stop
function studioStopAnimation(){
  __anim = null;
  studioStatus("Studio: animation stopped");
};

// expose public APIs (module-safe)
window.setColorStrategy        = window.setColorStrategy        || setColorStrategy;
window.setStudioBrightness     = window.setStudioBrightness     || setStudioBrightness;
window.studioLoadJson          = window.studioLoadJson          || studioLoadJson;
window.studioPlayAssembleBottomUp = window.studioPlayAssembleBottomUp || studioPlayAssembleBottomUp;
window.studioPlayOrbitXY          = window.studioPlayOrbitXY          || studioPlayOrbitXY;
window.studioPlayPuzzleShape      = window.studioPlayPuzzleShape      || studioPlayPuzzleShape;
window.openPuzzleShapeDialog      = window.openPuzzleShapeDialog      || openPuzzleShapeDialog;
window.setStudioOrientation    = window.setStudioOrientation    || setStudioOrientation;

// boot
init();

// NEW: set orientation dropdown handler
function setStudioOrientation(mode) {
  const m = String(mode || "none");
  if (m !== "none" && m !== "largest-face-xy" && m !== "smallest-face-xy") return;
  if (__orientMode === m) return;

  __orientMode = m;

  // Rebuild with the same snapshot, preserving camera feel:
  if (__lastSnapshot) {
    // compute old pivot
    const oldTarget = controls.target.clone();

    // rebuild scene (will compute new pivot)
    buildSceneFromSnapshot(__lastSnapshot);

    // After build, we already translate the camera by pivot delta inside the build
    // (your build logic moves camera by delta when __zoomLocked is true).
    // Nothing else to do here.
  }
};

// NEW: Physics preset — PD control with world→local safe write-back
async function studioPlaySnuggle(durationSec = 12) {
  // cancel previous if any
  if (window.__anim) { window.__anim.active = false; window.__anim = null; }

  const C = await loadCannon();
  if (!C) return;

  // scene + pieces
  const scene = window.scene, camera = window.camera, renderer = window.renderer;
  if (!scene || !camera || !renderer) { studioStatus("Studio scene not ready.", "error"); return; }

  const groups = [];
  scene.traverse(o => { if (o?.userData?.kind === "piece") groups.push(o); });
  if (!groups.length) { studioStatus("No pieces in Studio.", "error"); return; }

  // targets + ensure metadata
  const targets = groups.map(g => {
    const p = new THREE.Vector3(), q = new THREE.Quaternion();
    g.getWorldPosition(p); g.getWorldQuaternion(q);
    if (!Array.isArray(g.userData.atoms) || !g.userData.atoms.length || !Number.isFinite(g.userData.atomRadius)) {
      const atoms = []; let r = null;
      g.traverse(o => { if (o.isMesh) { atoms.push(o.position.clone()); if (o.geometry?.parameters?.radius && r == null) r = o.geometry.parameters.radius; }});
      g.userData.atoms = atoms; g.userData.atomRadius = r ?? 0.5;
    }
    return { g, p, q, atoms: g.userData.atoms, r: g.userData.atomRadius };
  });

  // scatter ring around pivot (keep Z)
  const pivot = targets.reduce((a,t)=>a.add(t.p), new THREE.Vector3()).multiplyScalar(1/targets.length);
  let maxD = 0; for (const t of targets) maxD = Math.max(maxD, Math.hypot(t.p.x - pivot.x, t.p.y - pivot.y));
  const SCATTER = 1.15, SHRINK = 0.97;
  const R = SCATTER * (maxD + 3 * (targets[0].r || 0.5));

  // physics world
  const world = new C.World();
  world.gravity.set(0,0,0);
  world.defaultContactMaterial.friction = 0.5;
  world.defaultContactMaterial.restitution = 0.0;

  // bodies
  const items = [];
  for (let i=0;i<targets.length;i++){
    const t = targets[i];
    const b = new C.Body({ mass: 1 });
    const shp = new C.Sphere(t.r * SHRINK);
    for (const a of t.atoms) b.addShape(shp, new C.Vec3(a.x,a.y,a.z));
    const ang = (i/targets.length) * Math.PI * 2;
    b.position.set(pivot.x + R*Math.cos(ang), pivot.y + R*Math.sin(ang), t.p.z);
    b.quaternion.set(t.q.x, t.q.y, t.q.z, t.q.w);
    b.linearDamping = 0.2; b.angularDamping = 0.25;
    world.addBody(b);
    items.push({ group: t.g, targetPos: t.p, targetQuat: t.q, body: b, hold: 0, snapped: false });
  }

  // PD stepper (KR/KW for rotation, KP/KD for position)
  const KP=32, KD=8, KR=20, KW=6;
  const FIXED_DT = 1/120, SUB=4;
  const POS_OK=0.015, ANG_OK = 1.5*Math.PI/180, V_OK=0.02, W_OK=0.02, HOLD=0.35;

  function quatErr(T,Q){ // shortest-arc
    const ew=T.w*Q.w - T.x*Q.x - T.y*Q.y - T.z*Q.z;
    const ex=T.w*Q.x + T.x*Q.w + T.y*Q.z - T.z*Q.y;
    const ey=T.w*Q.y - T.x*Q.z + T.y*Q.w + T.z*Q.x;
    const ez=T.w*Q.z + T.x*Q.y - T.y*Q.x + T.z*Q.w;
    const s = ew < 0 ? -1 : 1; return {w:s*ew, x:s*ex, y:s*ey, z:s*ez};
  }

  window.__anim = { active:true, C, world, items, t:0, duration: Math.max(1, +durationSec || 12) };

  window.studioAnimStep = function(dt=1/60){
    const A = window.__anim; if (!A?.active) return;
    const h = FIXED_DT / SUB;
    for (let s=0;s<SUB;s++){
      for (const it of A.items){
        if (it.snapped) continue;
        const b = it.body;
        // position PD
        const ex = it.targetPos.x - b.position.x;
        const ey = it.targetPos.y - b.position.y;
        const ez = it.targetPos.z - b.position.z;
        b.force.x += b.mass*(KP*ex - KD*b.velocity.x);
        b.force.y += b.mass*(KP*ey - KD*b.velocity.y);
        b.force.z += b.mass*(KP*ez - KD*b.velocity.z);
        // rotation PD (axis*angle)
        const qe = quatErr(it.targetQuat, b.quaternion);
        const w = Math.max(-1, Math.min(1, qe.w));
        const ang = 2*Math.acos(w);
        const sden = Math.sqrt(Math.max(1 - w*w, 0));
        const rx = sden>1e-6 ? (qe.x/sden)*ang : 2*qe.x;
        const ry = sden>1e-6 ? (qe.y/sden)*ang : 2*qe.y;
        const rz = sden>1e-6 ? (qe.z/sden)*ang : 2*qe.z;
        b.torque.x += KR*rx - KW*b.angularVelocity.x;
        b.torque.y += KR*ry - KW*b.angularVelocity.y;
        b.torque.z += KR*rz - KW*b.angularVelocity.z;

        // stability & snap
        const pOk = Math.hypot(ex,ey,ez) < POS_OK;
        const aOk = ang < ANG_OK;
        const vOk = b.velocity.length() < V_OK;
        const wOk = b.angularVelocity.length() < W_OK;
        it.hold = (pOk && aOk && vOk && wOk) ? it.hold + h : 0;
        if (it.hold >= HOLD) {
          b.velocity.scale(0, b.velocity); b.angularVelocity.scale(0, b.angularVelocity);
          b.position.set(it.targetPos.x, it.targetPos.y, it.targetPos.z);
          b.quaternion.set(it.targetQuat.x, it.targetQuat.y, it.targetQuat.z, it.targetQuat.w);
          it.snapped = true;
        }
      }
      world.step(FIXED_DT, h, 1);
    }

    // DRASTIC: Final stage direct positioning for any remaining pieces
    if (progress > 0.95) {
      for (const it of A.items) {
        if (!it.snapped) {
          const dist = Math.hypot(
            it.targetPos.x - it.body.position.x,
            it.targetPos.y - it.body.position.y,
            it.targetPos.z - it.body.position.z
          );
          if (dist > 0.02) {
            // Force snap to position
            it.body.position.set(it.targetPos.x, it.targetPos.y, it.targetPos.z);
            it.body.quaternion.set(it.targetQuat.x, it.targetQuat.y, it.targetQuat.z, it.targetQuat.w);
            it.body.velocity.set(0, 0, 0);
            it.body.angularVelocity.set(0, 0, 0);
            it.snapped = true;
          }
        }
      }
    }

    // DRASTIC: Restore collision shapes if disabled
    if (progress > 0.98) {
      for (const it of A.items) {
        if (it.collisionsDisabled && it.originalShapes) {
          it.body.shapes = [...it.originalShapes];
          it.collisionsDisabled = false;
        }
      }
    }

    // world → local write-back (handles parent transforms)
    const v = new THREE.Vector3(), qw = new THREE.Quaternion(), qp = new THREE.Quaternion();
    for (const it of A.items){
      const b = it.body, g = it.group, p = g.parent;
      if (!p) continue;
      v.set(b.position.x, b.position.y, b.position.z);
      p.worldToLocal(v); g.position.copy(v);
      p.getWorldQuaternion(qp).invert();
      qw.set(b.quaternion.x,b.quaternion.y,b.quaternion.z,b.quaternion.w).premultiply(qp);
      g.quaternion.copy(qw);
    }

    A.t += dt;
    if (A.items.every(i=>i.snapped) || A.t >= A.duration) A.active = false;
  };
}

function studioAnimStep(dt) {
  // Placeholder for physics stepping
}

function cancelAnim(reason = "cancel") {
  studioStatus(`Animation ${reason}.`);
}

function studioOnSceneReset() { 
  cancelAnim("canceled (scene reset)"); 
}

function studioOnOrientationChange() { 
  cancelAnim("canceled (orientation change)"); 
}

// Expose additional APIs - use the configurable version as default
window.studioPlaySnuggle = () => studioPlaySnuggleWithConfig();
window.studioAnimStep = studioAnimStep;
window.studioCancelAnim = cancelAnim;
window.studioOnSceneReset = studioOnSceneReset;
window.studioOnOrientationChange = studioOnOrientationChange;

// ---------- Unified Animation UI ----------
function setupUnifiedAnimationUI() {
  const animSelect = document.querySelector("#animPreset");
  const playBtn = document.querySelector("#btnPlay");
  
  if (!animSelect || !playBtn) {
    console.warn("Animation controls not found");
    return;
  }

  console.log("Setting up animation UI with elements:", animSelect, playBtn);

  // Add scene reset on animation change
  animSelect.addEventListener("change", () => {
    console.log("Animation selection changed to:", animSelect.value);
    console.log("Last snapshot exists:", !!window.__lastSnapshot);
    
    // Skip reset if no selection or empty selection
    if (!animSelect.value || animSelect.value === "") {
      console.log("Empty selection, skipping reset");
      return;
    }
    
    // Cancel any running animation first
    if (typeof cancelAnim === 'function') {
      cancelAnim("switching animation");
    }
    
    // Reset scene to original loaded state
    if (window.__lastSnapshot) {
      console.log("Rebuilding scene from snapshot");
      
      // Force complete scene reset
      resetDisplayRoot();
      buildSceneFromSnapshot(window.__lastSnapshot);
      
      studioStatus("Scene reset for new animation");
    } else {
      console.warn("No snapshot available for scene reset");
      studioStatus("No file loaded - cannot reset scene");
    }
  });

  // Wire play button with once flag to prevent duplicate handlers
  if (!playBtn.__wired) {
    playBtn.__wired = true;
    
    playBtn.addEventListener("click", (e) => {
      const val = animSelect.value;
      
      if (val === "physics-snuggle") {
        e.preventDefault(); 
        e.stopPropagation();
        if (!playBtn.__dialogOpen) {
          playBtn.__dialogOpen = true;
          window.openSnuggleDialog();
          setTimeout(() => { playBtn.__dialogOpen = false; }, 100);
        }
      } else if (val === "assemble-bottomup") {
        // Use requestAnimationFrame to ensure proper timing
        requestAnimationFrame(() => {
          const dur = prompt("Duration (seconds):", "10");
          if (dur && dur !== null && dur !== "") {
            window.studioPlayAssembleBottomUp?.(+dur);
          }
        });
      } else if (val === "orbit-xy") {
        // Use requestAnimationFrame to ensure proper timing
        requestAnimationFrame(() => {
          const dur = prompt("Duration (seconds):", "10");
          if (dur && dur !== null && dur !== "") {
            window.studioPlayOrbitXY?.(+dur);
          }
        });
      } else if (val === "puzzle-shape") {
        e.preventDefault();
        e.stopPropagation();
        if (!playBtn.__shapeDialogOpen) {
          playBtn.__shapeDialogOpen = true;
          window.openPuzzleShapeDialog();
          setTimeout(() => { playBtn.__shapeDialogOpen = false; }, 100);
        }
      }
    });
  }

  studioStatus("Unified animation UI ready.");
}

// ---------- UI integration (robust wiring + fallback overlay) ----------
function tryWireUI() {
  // Use the new unified animation UI
  setupUnifiedAnimationUI();
}

function mountSnuggleOverlay() {
  // Unified UI handles all animations now
  console.log("Overlay not needed - using unified animation controls");
}

// ---------- Boot ----------
function initializeUI() {
  console.log("Initializing UI, DOM state:", document.readyState);
  
  // Wait a bit for elements to be available
  setTimeout(() => {
    const animSelect = document.querySelector("#animPreset");
    const playBtn = document.querySelector("#btnPlay");
    
    console.log("Elements found:", {
      animSelect: !!animSelect,
      playBtn: !!playBtn,
      animSelectId: animSelect?.id,
      playBtnId: playBtn?.id
    });
    
    if (animSelect && playBtn) {
      tryWireUI();
    } else {
      console.warn("Animation UI elements not found, retrying...");
      setTimeout(initializeUI, 500);
    }
  }, 100);
}

document.addEventListener("DOMContentLoaded", initializeUI);

// Also try immediately in case DOM is already ready
if (document.readyState !== "loading") {
  initializeUI();
}

// ---------- Snuggle Settings Panel ----------

const SNUG_KEY = "studio.snuggle.cfg.v1";

function getSnuggleDefaults() {
  const saved = localStorage.getItem(SNUG_KEY);
  console.log('Loading settings from localStorage:', saved);
  if (saved) { 
    try { 
      const parsed = JSON.parse(saved);
      console.log('Parsed saved settings:', parsed);
      return parsed;
    } catch (e) {
      console.log('Failed to parse saved settings:', e);
    }
  }
  console.log('Using default settings');
  return {
    durationSec: 20,
    // PD gains (much stronger for guaranteed convergence)
    KP: 80, KD: 16,
    KR: 60, KW: 12,
    // stepping
    FIXED_DT: 1/120, SUBSTEPS: 4,
    // contact
    FRICTION: 0.6, RESTITUTION: 0.0,
    CONTACT_STIFF: 3e7, CONTACT_RELAX: 2.0,
    FRICTION_STIFF: 3e7, FRICTION_RELAX: 2.0,
    // colliders & scatter
    COLLIDER_SHRINK: 0.965, SCATTER: 1.50,
    // solution scaling (minimal for guaranteed convergence)
    SOLUTION_SCALE: 1.01,
    // stability thresholds (tighter for guaranteed landing)
    STABLE_POS: 0.015, STABLE_ANG_DEG: 1.5,
    STABLE_LIN: 0.015, STABLE_ANG: 0.015, STABLE_HOLD: 0.25,
    // drastic options
    MAGNETIC_WELLS: true, DISABLE_COLLISIONS: true, DIRECT_INTERPOLATION: true,
    SNAP_DISTANCE: 0.08
  };
}

function openSnuggleDialog() {
  const cfg = getSnuggleDefaults();
  console.log('Opening dialog with config:', cfg);
  
  // Create modal backdrop
  const backdrop = document.createElement('div');
  backdrop.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
  
  // Create modal content
  const modal = document.createElement('div');
  modal.style.cssText = 'background:#2a2a2a;border-radius:12px;padding:24px;max-width:500px;width:90%;color:#fff;font:14px/1.4 system-ui;box-shadow:0 8px 32px rgba(0,0,0,0.5);';
  
  // Create form elements dynamically to ensure proper value binding
  modal.innerHTML = `
    <h3 style="margin:0 0 20px 0;color:#4a9eff;">Physics: Snuggle Settings</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Duration (sec)</label>
        <input type="number" id="snug-duration" min="1" max="60" step="1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Scatter Factor</label>
        <input type="number" id="snug-scatter" min="1.0" max="2.0" step="0.05" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Position KP</label>
        <input type="number" id="snug-kp" min="1" max="100" step="1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Position KD</label>
        <input type="number" id="snug-kd" min="1" max="50" step="1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Rotation KR</label>
        <input type="number" id="snug-kr" min="1" max="100" step="1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Rotation KW</label>
        <input type="number" id="snug-kw" min="1" max="50" step="1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Contact Stiffness</label>
        <input type="number" id="snug-contact-stiff" min="1e6" max="1e8" step="1e6" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Friction</label>
        <input type="number" id="snug-friction" min="0" max="1" step="0.1" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
      <div>
        <label style="display:block;margin-bottom:8px;font-weight:500;">Solution Scale</label>
        <input type="number" id="snug-solution-scale" min="1.0" max="1.2" step="0.01" style="width:100%;padding:6px;border:1px solid #555;border-radius:4px;background:#333;color:#fff;">
      </div>
    </div>
    <div style="display:flex;gap:12px;margin-top:24px;justify-content:flex-end;">
      <button id="snug-reset" style="padding:8px 16px;border:1px solid #666;border-radius:6px;background:#444;color:#fff;cursor:pointer;">Reset</button>
      <button id="snug-cancel" style="padding:8px 16px;border:1px solid #666;border-radius:6px;background:#444;color:#fff;cursor:pointer;">Cancel</button>
      <button id="snug-run" style="padding:8px 16px;border:1px solid #4a9eff;border-radius:6px;background:#4a9eff;color:#fff;cursor:pointer;font-weight:500;">Run</button>
    </div>
  `;
  
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  
  // Set values after DOM insertion to ensure proper binding
  modal.querySelector('#snug-duration').value = cfg.durationSec;
  modal.querySelector('#snug-scatter').value = cfg.SCATTER;
  modal.querySelector('#snug-kp').value = cfg.KP;
  modal.querySelector('#snug-kd').value = cfg.KD;
  modal.querySelector('#snug-kr').value = cfg.KR;
  modal.querySelector('#snug-kw').value = cfg.KW;
  modal.querySelector('#snug-contact-stiff').value = cfg.CONTACT_STIFF;
  modal.querySelector('#snug-friction').value = cfg.FRICTION;
  modal.querySelector('#snug-solution-scale').value = cfg.SOLUTION_SCALE;
  
  // Event handlers
  const resetBtn = modal.querySelector('#snug-reset');
  const cancelBtn = modal.querySelector('#snug-cancel');
  const runBtn = modal.querySelector('#snug-run');
  
  resetBtn.addEventListener('click', () => {
    console.log('Resetting settings - removing from localStorage');
    localStorage.removeItem(SNUG_KEY);
    const defaults = getSnuggleDefaults();
    modal.querySelector('#snug-duration').value = defaults.durationSec;
    modal.querySelector('#snug-scatter').value = defaults.SCATTER;
    modal.querySelector('#snug-kp').value = defaults.KP;
    modal.querySelector('#snug-kd').value = defaults.KD;
    modal.querySelector('#snug-kr').value = defaults.KR;
    modal.querySelector('#snug-kw').value = defaults.KW;
    modal.querySelector('#snug-contact-stiff').value = defaults.CONTACT_STIFF;
    modal.querySelector('#snug-friction').value = defaults.FRICTION;
    modal.querySelector('#snug-solution-scale').value = defaults.SOLUTION_SCALE;
    studioStatus('Settings reset to defaults');
  });
  
  cancelBtn.addEventListener('click', () => {
    document.body.removeChild(backdrop);
  });
  
  runBtn.addEventListener('click', (e) => {
    // Prevent multiple clicks
    if (runBtn.disabled) return;
    runBtn.disabled = true;
    runBtn.textContent = 'Running...';
    
    // Collect settings - only form values, no spreading of defaults
    const settings = {
      durationSec: +modal.querySelector('#snug-duration').value || 16,
      SCATTER: +modal.querySelector('#snug-scatter').value || 1.20,
      KP: +modal.querySelector('#snug-kp').value || 32,
      KD: +modal.querySelector('#snug-kd').value || 10,
      KR: +modal.querySelector('#snug-kr').value || 24,
      KW: +modal.querySelector('#snug-kw').value || 8,
      CONTACT_STIFF: +modal.querySelector('#snug-contact-stiff').value || 3e7,
      FRICTION: +modal.querySelector('#snug-friction').value || 0.6,
      SOLUTION_SCALE: +modal.querySelector('#snug-solution-scale').value || 1.08,
      // Add other required defaults that aren't in the form
      FIXED_DT: 1/120,
      SUBSTEPS: 4,
      RESTITUTION: 0.0,
      CONTACT_RELAX: 2.0,
      FRICTION_STIFF: 3e7,
      FRICTION_RELAX: 2.0,
      COLLIDER_SHRINK: 0.965,
      STABLE_POS: 0.035,
      STABLE_ANG_DEG: 3.5,
      STABLE_LIN: 0.035,
      STABLE_ANG: 0.035,
      STABLE_HOLD: 0.45
    };
    
    // Debug: log collected settings
    console.log('Settings from panel (clean):', settings);
    
    // Save to localStorage
    localStorage.setItem(SNUG_KEY, JSON.stringify(settings));
    console.log('Saved to localStorage');
    
    // Close modal
    document.body.removeChild(backdrop);
    
    // Start physics with settings
    studioPlaySnuggleWithConfig(settings);
  });
  
  // Close on backdrop click
  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) {
      document.body.removeChild(backdrop);
    }
  });
}

async function studioPlaySnuggleWithConfig(cfg = null) {
  console.log('studioPlaySnuggleWithConfig called with cfg:', cfg);
  const config = cfg || getSnuggleDefaults();
  
  // Debug: log the actual config being used
  console.log('Final Snuggle Config being used:', config);
  
  // Visual confirmation of key parameters
  studioStatus(`Physics: KP=${config.KP}, KD=${config.KD}, KR=${config.KR}, Scale=${config.SOLUTION_SCALE}x, Duration=${config.durationSec}s`);
  
  // cancel previous if any
  if (window.__anim) { window.__anim.active = false; window.__anim = null; }

  const C = await loadCannon();
  if (!C) return;

  // scene + pieces
  const scene = window.scene, camera = window.camera, renderer = window.renderer;
  if (!scene || !camera || !renderer) { studioStatus("Studio scene not ready.", "error"); return; }

  const groups = [];
  scene.traverse(o => { if (o?.userData?.kind === "piece") groups.push(o); });
  if (!groups.length) { studioStatus("No pieces in Studio.", "error"); return; }

  // targets + ensure metadata
  const targets = groups.map(g => {
    const p = new THREE.Vector3(), q = new THREE.Quaternion();
    g.getWorldPosition(p); g.getWorldQuaternion(q);
    if (!Array.isArray(g.userData.atoms) || !g.userData.atoms.length || !Number.isFinite(g.userData.atomRadius)) {
      const atoms = []; let r = null;
      g.traverse(o => { if (o.isMesh) { atoms.push(o.position.clone()); if (o.geometry?.parameters?.radius && r == null) r = o.geometry.parameters.radius; }});
      g.userData.atoms = atoms; g.userData.atomRadius = r ?? 0.5;
    }
    return { g, p, q, atoms: g.userData.atoms, r: g.userData.atomRadius };
  });

  // Apply solution scaling to create snuggle room
  const pivot = targets.reduce((a,t)=>a.add(t.p), new THREE.Vector3()).multiplyScalar(1/targets.length);
  if (config.SOLUTION_SCALE && config.SOLUTION_SCALE !== 1.0) {
    for (const t of targets) {
      const offset = t.p.clone().sub(pivot).multiplyScalar(config.SOLUTION_SCALE);
      t.p.copy(pivot).add(offset);
    }
    studioStatus(`Solution scaled by ${config.SOLUTION_SCALE}x for better snuggle dynamics`);
  }

  // scatter ring around pivot (keep Z)
  let maxD = 0; for (const t of targets) maxD = Math.max(maxD, Math.hypot(t.p.x - pivot.x, t.p.y - pivot.y));
  const R = config.SCATTER * (maxD + 3 * (targets[0].r || 0.5));

  // physics world
  const world = new C.World();
  world.gravity.set(0,0,0);
  world.defaultContactMaterial.friction = config.FRICTION;
  world.defaultContactMaterial.restitution = config.RESTITUTION || 0.0;

  // bodies
  const items = [];
  for (let i=0;i<targets.length;i++){
    const t = targets[i];
    const b = new C.Body({ mass: 1 });
    
    // scatter position
    const a = (i / targets.length) * 2 * Math.PI;
    const sx = pivot.x + R * Math.cos(a), sy = pivot.y + R * Math.sin(a), sz = t.p.z;
    b.position.set(sx, sy, sz);
    
    // collider (compound spheres)
    for (const atom of t.atoms) {
      const shape = new C.Sphere(t.r * config.COLLIDER_SHRINK);
      b.addShape(shape, new C.Vec3(atom.x, atom.y, atom.z));
    }
    world.addBody(b);
    items.push({ group: t.g, body: b, targetPos: t.p.clone(), targetQuat: t.q.clone(), hold: 0, snapped: false });
  }

  studioStatus(`Snuggle: ${items.length} pieces, ${config.durationSec}s`);
  
  // Quaternion error helper (shortest-arc)
  function quatErr(T,Q){
    const ew=T.w*Q.w - T.x*Q.x - T.y*Q.y - T.z*Q.z;
    const ex=T.w*Q.x + T.x*Q.w + T.y*Q.z - T.z*Q.y;
    const ey=T.w*Q.y - T.x*Q.z + T.y*Q.w + T.z*Q.x;
    const ez=T.w*Q.z + T.x*Q.y - T.y*Q.x + T.z*Q.w;
    const s = ew < 0 ? -1 : 1; return {w:s*ew, x:s*ex, y:s*ey, z:s*ez};
  }
  
  // PD stepper
  const { KP, KD, KR, KW, FIXED_DT, STABLE_POS, STABLE_ANG_DEG, STABLE_LIN, STABLE_ANG, STABLE_HOLD } = config;
  const POS_OK = STABLE_POS, ANG_OK = STABLE_ANG_DEG * Math.PI/180, V_OK = STABLE_LIN, W_OK = STABLE_ANG, HOLD = STABLE_HOLD;
  
  window.__anim = { active: true, C, world, items, t: 0, duration: Math.max(1, config.durationSec) };
  window.studioAnimStep = function(dt = 1/60) {
    const A = window.__anim;
    if (!A?.active) return;
    
    const h = Math.min(dt, 0.1);
    for (let sub = 0; sub < (config.SUBSTEPS || 4); sub++) {
      for (const it of A.items) {
        if (it.snapped) continue;
        const b = it.body;
        
        // Calculate position errors first
        const ex = it.targetPos.x - b.position.x;
        const ey = it.targetPos.y - b.position.y;
        const ez = it.targetPos.z - b.position.z;
        const dist = Math.hypot(ex, ey, ez);
        
        // DRASTIC: Multi-stage with forced final positioning
        const progress = Math.min(1.0, A.t / A.duration);
        let dynamicKP, dynamicKR;
        
        if (progress < 0.5) {
          // Stage 1: Moderate exploration (0-50%)
          dynamicKP = KP * 1.0;
          dynamicKR = KR * 1.0;
        } else if (progress < 0.8) {
          // Stage 2: Strong attraction (50-80%)
          dynamicKP = KP * 3.0;
          dynamicKR = KR * 3.0;
        } else {
          // Stage 3: FORCED convergence (80-100%)
          dynamicKP = KP * 10.0;
          dynamicKR = KR * 10.0;
          
          // DRASTIC: Magnetic wells - exponential attraction near targets
          if (config.MAGNETIC_WELLS && dist < 0.2) {
            const magneticForce = 2000 * Math.exp(-dist * 10) * Math.min(1.0, (progress - 0.8) / 0.2);
            b.force.x += magneticForce * ex / Math.max(dist, 1e-6);
            b.force.y += magneticForce * ey / Math.max(dist, 1e-6);
            b.force.z += magneticForce * ez / Math.max(dist, 1e-6);
          }
          
          // DRASTIC: Add direct teleportation force for stubborn pieces
          if (dist > 0.05) {
            const teleportForce = 1000 * Math.min(1.0, (progress - 0.8) / 0.2);
            b.force.x += teleportForce * ex / Math.max(dist, 1e-6);
            b.force.y += teleportForce * ey / Math.max(dist, 1e-6);
            b.force.z += teleportForce * ez / Math.max(dist, 1e-6);
          }
          
          // DRASTIC: Direct velocity damping in final stage
          b.velocity.scale(0.8, b.velocity);
          b.angularVelocity.scale(0.8, b.angularVelocity);
          
          // DRASTIC: Disable collisions between pieces in final stage
          if (config.DISABLE_COLLISIONS && progress > 0.85) {
            // Remove collision shapes temporarily
            if (!it.collisionsDisabled) {
              it.originalShapes = [...b.shapes];
              b.shapes.length = 0;
              it.collisionsDisabled = true;
            }
          }
        }
        
        // DRASTIC: Direct interpolation for pieces within snap distance
        if (config.DIRECT_INTERPOLATION && dist < config.SNAP_DISTANCE && progress > 0.7) {
          const lerpFactor = Math.min(1.0, (progress - 0.7) / 0.3) * 0.1;
          b.position.x = b.position.x + lerpFactor * ex;
          b.position.y = b.position.y + lerpFactor * ey;
          b.position.z = b.position.z + lerpFactor * ez;
          
          // Slerp rotation
          const currentQ = new THREE.Quaternion(b.quaternion.x, b.quaternion.y, b.quaternion.z, b.quaternion.w);
          const targetQ = new THREE.Quaternion(it.targetQuat.x, it.targetQuat.y, it.targetQuat.z, it.targetQuat.w);
          currentQ.slerp(targetQ, lerpFactor);
          b.quaternion.set(currentQ.x, currentQ.y, currentQ.z, currentQ.w);
        }
        
        // PD position control with dynamic gains
        b.force.x += dynamicKP * ex - KD * b.velocity.x;
        b.force.y += dynamicKP * ey - KD * b.velocity.y;
        b.force.z += dynamicKP * ez - KD * b.velocity.z;
        
        // PD rotation control (use same quatErr as original)
        const qe = quatErr(it.targetQuat, b.quaternion);
        const w = Math.abs(qe.w);
        const ang = 2*Math.acos(w);
        const sden = Math.sqrt(Math.max(1 - w*w, 0));
        const rx = sden>1e-6 ? (qe.x/sden)*ang : 2*qe.x;
        const ry = sden>1e-6 ? (qe.y/sden)*ang : 2*qe.y;
        const rz = sden>1e-6 ? (qe.z/sden)*ang : 2*qe.z;
        b.torque.x += dynamicKR*rx - KW*b.angularVelocity.x;
        b.torque.y += dynamicKR*ry - KW*b.angularVelocity.y;
        b.torque.z += dynamicKR*rz - KW*b.angularVelocity.z;
        
        // stability & snap
        const pOk = Math.hypot(ex,ey,ez) < POS_OK;
        const aOk = ang < ANG_OK;
        const vOk = b.velocity.length() < V_OK;
        const wOk = b.angularVelocity.length() < W_OK;
        it.hold = (pOk && aOk && vOk && wOk) ? it.hold + h : 0;
        if (it.hold >= HOLD) {
          b.velocity.scale(0, b.velocity); b.angularVelocity.scale(0, b.angularVelocity);
          b.position.set(it.targetPos.x, it.targetPos.y, it.targetPos.z);
          b.quaternion.set(it.targetQuat.x, it.targetQuat.y, it.targetQuat.z, it.targetQuat.w);
          it.snapped = true;
        }
      }
      world.step(config.FIXED_DT, h, 1);
    }
    
    // world → local write-back (handles parent transforms)
    const v = new THREE.Vector3(), qw = new THREE.Quaternion(), qp = new THREE.Quaternion();
    for (const it of A.items){
      const b = it.body, g = it.group, p = g.parent;
      if (!p) continue;
      v.set(b.position.x, b.position.y, b.position.z);
      p.worldToLocal(v); g.position.copy(v);
      p.getWorldQuaternion(qp).invert();
      qw.set(b.quaternion.x,b.quaternion.y,b.quaternion.z,b.quaternion.w).premultiply(qp);
      g.quaternion.copy(qw);
    }
    
    A.t += dt;
    if (A.items.every(i=>i.snapped) || A.t >= A.duration) A.active = false;
  };
}

// Export API to parent/top windows (covers iframe case)
(function exposeToParents() {
  const targets = [window, window.parent !== window ? window.parent : null, window.top !== window ? window.top : null];
  for (const w of targets) {
    if (!w) continue;
    try {
      w.studioPlaySnuggle = () => studioPlaySnuggleWithConfig();
      w.studioCancelAnim = cancelAnim;
      w.studioAnimStep = studioAnimStep;
      w.studioOnSceneReset = studioOnSceneReset;
      w.studioOnOrientationChange = studioOnOrientationChange;
    } catch (_) {}
  }
})();

// Deterministic dropdown wiring (works with your Admin Play UI)
function studioRegisterAnim(selectEl, playBtn){
  if (!selectEl || !playBtn) return studioStatus("Anim UI not found", "error");
  if (![...selectEl.options].some(o => o.value === "physics-snuggle")){
    selectEl.add(new Option("Physics: Snuggle", "physics-snuggle"));
  }
  if (!playBtn.__snuggleWired){
    playBtn.addEventListener("click", (e) => {
      if (selectEl.value === "physics-snuggle") {
        e.preventDefault(); e.stopPropagation();
        if (!playBtn.__dialogOpen) {
          playBtn.__dialogOpen = true;
          window.openSnuggleDialog();
          setTimeout(() => { playBtn.__dialogOpen = false; }, 100);
        }
      }
    });
    playBtn.__snuggleWired = true;
  }
  studioStatus("Snuggle UI wired.");
}

// Call once after DOM mounts (covers PyQt/iframe late-DOM too)
window.addEventListener("DOMContentLoaded", () => {
  // Prefer a labeled "Animation" select near your Admin Play UI; fallback to first select/button
  const selects = [...document.querySelectorAll("select")];
  const select = selects.find(s => (s.closest("label,div,section")?.innerText || "").toLowerCase().includes("animation"))
               || selects[0] || null;
  const playBtn = [...document.querySelectorAll("button")].find(b => /play/i.test(b.textContent||"")) || null;
  studioRegisterAnim(select, playBtn);
});

// (loader moved near top)

// ---- Export API to globals and to parent/top ----
(function exportStudioAPI(win){
  try {
    // Export to current window first
    if (typeof studioPlaySnuggle === "function") win.studioPlaySnuggle = () => studioPlaySnuggleWithConfig();
    if (typeof studioAnimStep === "function") win.studioAnimStep = studioAnimStep;
    if (typeof studioOnSceneReset === "function") win.studioOnSceneReset = studioOnSceneReset;
    if (typeof studioOnOrientationChange === "function") win.studioOnOrientationChange = studioOnOrientationChange;
    if (typeof cancelAnim === "function") win.studioCancelAnim = cancelAnim;
    
    // Also add to globalThis for extra safety
    globalThis.studioPlaySnuggle = win.studioPlaySnuggle;
    globalThis.studioAnimStep = win.studioAnimStep;
    globalThis.studioOnSceneReset = win.studioOnSceneReset;
    globalThis.studioOnOrientationChange = win.studioOnOrientationChange;
    globalThis.studioCancelAnim = win.studioCancelAnim;
  } catch {}

  const targets = [win.parent !== win ? win.parent : null, win.top !== win ? win.top : null];
  for (const t of targets) {
    if (!t) continue;
    try {
      if (win.studioPlaySnuggle)         t.studioPlaySnuggle = () => studioPlaySnuggleWithConfig();
      if (win.studioAnimStep)            t.studioAnimStep = win.studioAnimStep;
      if (win.studioOnSceneReset)        t.studioOnSceneReset = win.studioOnSceneReset;
      if (win.studioOnOrientationChange) t.studioOnOrientationChange = win.studioOnOrientationChange;
      if (win.studioCancelAnim)          t.studioCancelAnim = win.studioCancelAnim;
    } catch {}
  }
  studioStatus("Snuggle API exported.");
})(window);

(function ensureSnuggleUI(){
  function attach(){
    const select =
      document.querySelector('#animPreset, #animationPreset, select[data-role="anim-select"]')
      || document.querySelector('select');

    const play =
      document.querySelector('#btnPlay, #studioPlay, button[data-role="anim-play"]')
      || [...document.querySelectorAll('button')].find(b => /play/i.test(b.textContent||''));

    if (!select || !play) return false;

    if (![...select.options].some(o => o.value === 'physics-snuggle')) {
      select.add(new Option('Physics: Snuggle', 'physics-snuggle'));
    }
    if (!play.__snugWired){
      play.addEventListener('click', (e) => {
        if (select.value === 'physics-snuggle') {
          e.preventDefault(); e.stopPropagation();
          if (!play.__dialogOpen) {
            play.__dialogOpen = true;
            window.openSnuggleDialog();
            // Reset flag after dialog closes
            setTimeout(() => { play.__dialogOpen = false; }, 100);
          }
        }
      });
      play.__snugWired = true;
    }
    return true;
  }

  if (!attach()){
    const obs = new MutationObserver(() => { if (attach()) obs.disconnect(); });
    obs.observe(document.body, { childList: true, subtree: true });
  }
})();

// make panel & runner callable from Console / parent UI
(() => {
  try {
    if (typeof openSnuggleDialog === 'function') window.openSnuggleDialog = openSnuggleDialog;
    if (typeof studioPlaySnuggle === 'function') window.studioPlaySnuggle = () => studioPlaySnuggleWithConfig();
    if (typeof studioAnimStep === 'function') window.studioAnimStep = studioAnimStep;
  } catch (e) {
    console.warn('Snuggle: expose to window failed:', e);
  }
})();
