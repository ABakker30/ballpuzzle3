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
  camera.up.set(0, 0, 1);  // Z-up for turntable feel (no roll)
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
    studioStatus("Studio: animation complete");
  }
}

function _stepAnimation() {
  if (!__anim) return;
  if (__anim.kind === 'bottomup') _stepAssembleBottomUp();
  else if (__anim.kind === 'orbitxy') _stepOrbitXY();
}

function animate(){
  requestAnimationFrame(animate);
  controls.update();
  _stepAnimation();              // ← dispatch both kinds safely
  renderer.render(scene, camera);
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
    else             g.userData.pieceKey   = p.material_key || p.id;

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

    if (!isContainer) addBondsForAtoms(g, atoms, mat);
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
};

// Start: assemble pieces from lowest Z to highest (duration in seconds)
function studioPlayAssembleBottomUp(durationSec) {
  const groups = _pieceGroups();
  if (!groups.length) { studioStatus("Studio: no pieces to animate"); return; }

  // Gather world-space atoms, minZ, centroids (oriented positions already baked)
  const pcs = _collectPiecesAtomsWorld();

  // Estimate lattice neighbor distance and build adjacency
  const nn = _estimateNeighborDistance(pcs);
  const EPS = nn * 0.06;                      // 6% tolerance; adjust if needed

  const adj = _buildAdjacency(pcs, nn, EPS);
  // Choose start on XY plane: within tolZ of global minZ
  const tolZ = Math.max(1e-4, nn * 0.05);
  const startIdx = _chooseStartIndex(pcs, tolZ);
  if (startIdx < 0) { studioStatus("Studio: cannot choose start piece"); return; }

  const { order, complete } = _connectedOrder(pcs, adj, startIdx);
  if (!order.length) { studioStatus("Studio: no connected order"); return; }
  if (!complete) {
    studioStatus(`Studio: graph disconnected — assembling first ${order.length} connected piece(s)`);
  } else {
    studioStatus(`Studio: assembling ${order.length} piece(s)`);
  }

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

  // Map order of indices → ordered group list
  const orderedGroups = order.map(i => pcs[i].group);

  __anim = {
    kind: 'bottomup',
    start: performance.now(),
    duration: Math.max(500, (Number(durationSec) || 10) * 1000),
    groups: orderedGroups
  };
};

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

// NEW: add missing studioPlaySnuggle function and related physics functions
async function studioPlaySnuggle(forceDurationSec = null) {
  studioStatus("Snuggle: starting...");
  
  let duration = +forceDurationSec;
  if (!Number.isFinite(duration) || duration <= 0) {
    const input = prompt("Snuggle duration (seconds)?", "16");
    duration = Math.max(1, parseFloat(input ?? "16"));
  }
  
  studioStatus(`Snuggle: running for ${duration}s...`);
  
  // Simple placeholder animation
  setTimeout(() => {
    studioStatus("Snuggle: finished");
  }, duration * 1000);
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

// Expose additional APIs
window.studioPlaySnuggle = studioPlaySnuggle;
window.studioAnimStep = studioAnimStep;
window.studioCancelAnim = cancelAnim;
window.studioOnSceneReset = studioOnSceneReset;
window.studioOnOrientationChange = studioOnOrientationChange;

// ---------- Physics Implementation ----------

// Expose additional APIs
window.studioPlaySnuggle = studioPlaySnuggle;
window.studioAnimStep = studioAnimStep;
window.studioCancelAnim = cancelAnim;
window.studioOnSceneReset = studioOnSceneReset;
window.studioOnOrientationChange = studioOnOrientationChange;

// ---------- UI integration (robust wiring + fallback overlay) ----------
function tryWireUI() {
  const attach = () => {
    // Prefer explicit selectors; otherwise pick the first reasonable <select>
    const explicit = document.querySelector("#animPreset, #animationPreset, select[data-role='anim-select']");
    const candidates = Array.from(document.querySelectorAll("select"));
    const guess = candidates.find(sel => {
      const text = [sel.id, sel.className, ...Array.from(sel.options).map(o => o.textContent || "")]
        .join(" ").toLowerCase();
      return /anim|preset|reveal|timeline|spin|orbit/.test(text);
    });
    const select = explicit || guess;
    if (!select) return false;

    if (!Array.from(select.options).some(o => o.value === "physics-snuggle")) {
      const opt = new Option("Physics: Snuggle", "physics-snuggle");
      select.add(opt);
    }

    // Find a Play button
    const playBtn = document.querySelector("#btnPlay, #studioPlay, button[data-role='anim-play']") ||
      Array.from(document.querySelectorAll("button")).find(b =>
        /play|start/i.test(b.textContent || "") && /anim|studio|animation/i.test((b.id + b.className + b.textContent).toLowerCase())
      );

    if (!playBtn) return false;
    if (!playBtn.__snuggleWired) {
      playBtn.addEventListener("click", async () => {
        if (select.value === "physics-snuggle") await studioPlaySnuggle();
      });
      playBtn.__snuggleWired = true;
    }
    studioStatus("Snuggle UI wired.");
    return true;
  };

  // Try now
  if (attach()) return;

  // Wait for late DOM (PyQt-driven UIs often mount after load)
  const obs = new MutationObserver(() => { if (attach()) obs.disconnect(); });
  obs.observe(document.body, { childList: true, subtree: true });

  // Last-resort: add a tiny overlay after 1200ms
  setTimeout(() => { if (!attach()) mountSnuggleOverlay(); }, 1200);
}

function mountSnuggleOverlay() {
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:absolute;right:12px;bottom:12px;z-index:9999;background:#111a;border:1px solid #333;padding:8px 10px;border-radius:10px;color:#fff;font:12px system-ui;display:flex;gap:8px;align-items:center;";
  const label = document.createElement("span"); label.textContent = "Anim:";
  const sel = document.createElement("select");
  sel.innerHTML = "<option value='none'>—</option><option value='physics-snuggle'>Physics: Snuggle</option>";
  const btn = document.createElement("button");
  btn.textContent = "Play";
  btn.style.cssText = "padding:4px 10px;border-radius:8px;border:1px solid #444;background:#222;color:#fff;cursor:pointer;";
  btn.addEventListener("click", async () => { if (sel.value === "physics-snuggle") await studioPlaySnuggle(); });
  wrap.append(label, sel, btn);
  document.body.appendChild(wrap);
  studioStatus("Overlay added (fallback).");
}

// ---------- Boot ----------
tryWireUI();

// Export API to parent/top windows (covers iframe case)
(function exposeToParents() {
  const targets = [window, window.parent !== window ? window.parent : null, window.top !== window ? window.top : null];
  for (const w of targets) {
    if (!w) continue;
    try {
      w.studioPlaySnuggle = studioPlaySnuggle;
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
    playBtn.addEventListener("click", async () => {
      if (selectEl.value === "physics-snuggle") await studioPlaySnuggle();
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
