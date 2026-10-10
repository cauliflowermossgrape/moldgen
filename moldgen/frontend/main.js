import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';

const $ = (id) => document.getElementById(id);
const viewport = $('viewport');

const state = {
  meshId: null,
  stats: null,
  busy: false,
  partMesh: null,
  ghostMesh: null,
  planeMesh: null,
  halfMeshes: {},
  explodeMax: 60,
  blockBounds: null,
  // last suggestion applied to the slider, so a mode switch only moves the
  // plane when the user hasn't placed it by hand
  appliedSuggestion: null,
  // last /api/analyze result, kept for the draft-angle map toggle
  analysis: null,
  // what the camera presets frame: the part, or the mold once generated
  frame: null,
};

// --- Scene (Z-up, millimeters) ----------------------------------------------

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1d21);

const camera = new THREE.PerspectiveCamera(
  50, viewport.clientWidth / viewport.clientHeight, 0.1, 5000);
camera.up.set(0, 0, 1);
camera.position.set(120, -120, 90);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.localClippingEnabled = true;
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(viewport.clientWidth, viewport.clientHeight);
viewport.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xbfd4e6, 0x30261a, 1.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 1.6);
keyLight.position.set(80, 60, 140);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x8899bb, 0.5);
fillLight.position.set(-90, -70, 40);
scene.add(fillLight);

let grid = null;
const axes = new THREE.AxesHelper(30);
scene.add(axes);

// floor grid sized to the work: ~10 cells across with a round 1-2-5 step,
// so a 10 mm part and a 400 mm panel both get a readable scale
function updateGrid(extent) {
  const raw = Math.max(extent, 10) / 5;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((k) => k * pow).find((v) => v >= raw);
  const cells = 20;
  if (grid) {
    scene.remove(grid);
    grid.geometry.dispose();
  }
  grid = new THREE.GridHelper(step * cells, cells, 0x556070, 0x333a44);
  grid.rotation.x = Math.PI / 2; // lie in the XY plane, Z up
  scene.add(grid);
  axes.scale.setScalar(Math.max(extent, 10) / 100);
}
updateGrid(100);

function resizeViewport() {
  const w = viewport.clientWidth;
  const h = viewport.clientHeight;
  if (!w || !h) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}
// the viewport also resizes without a window resize (narrow-screen layout)
new ResizeObserver(resizeViewport).observe(viewport);

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

// --- Materials ---------------------------------------------------------------

function makeMatcap() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(90, 90, 16, 128, 128, 150);
  grad.addColorStop(0.0, '#f7f8f9');
  grad.addColorStop(0.45, '#b9bec6');
  grad.addColorStop(0.8, '#787f88');
  grad.addColorStop(1.0, '#41464d');
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

const matcap = makeMatcap();
const partMaterial = new THREE.MeshMatcapMaterial({ matcap, vertexColors: true });
const ghostMaterial = new THREE.MeshBasicMaterial({
  color: 0xd8dce2, transparent: true, opacity: 0.22, depthWrite: false,
});
const halfMaterials = {
  male: new THREE.MeshMatcapMaterial({ matcap, color: 0xe8955a }),
  female: new THREE.MeshMatcapMaterial({ matcap, color: 0x6fa8e8 }),
  tool: new THREE.MeshMatcapMaterial({ matcap, color: 0x5fc9a8 }),
};

// per-process fit clearances: resin/SLA is dimensionally tight, FDM needs
// play, machined tools can run 0.1 mm (Easy Composites' forged guidance)
const PRINTER_PRESETS = {
  resin: { pin: 0.15, plug: 0.15 },
  fdm: { pin: 0.25, plug: 0.3 },
  cnc: { pin: 0.1, plug: 0.1 },
};
const planeMaterial = new THREE.MeshBasicMaterial({
  color: 0x4fa3ff, transparent: true, opacity: 0.16,
  side: THREE.DoubleSide, depthWrite: false,
});

const NEUTRAL = [1, 1, 1];
const UNDERCUT = [0.95, 0.23, 0.2];
const HIDDEN_UNDERCUT = [0.69, 0.31, 0.88];
const LOW_DRAFT = [0.98, 0.72, 0.25];
const CORE = [0.31, 0.76, 0.85];

// approximate solid densities for the print-mass estimate (g/cm³)
const PRINT_DENSITY = { resin: 1.15, fdm: 1.24 };

// section view: keeps the half of the mold away from the default camera
const sectionPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);

// --- Helpers -----------------------------------------------------------------

function setStatus(text, isError = false) {
  $('status-text').textContent = text;
  $('status').classList.toggle('error', isError);
  $('status').classList.toggle('busy', state.busy && !isError);
}

// one long-running request at a time; the overlay says what's happening
function beginBusy(text) {
  if (state.busy) return false;
  state.busy = true;
  $('busy-text').textContent = text;
  $('busy').classList.remove('hidden');
  setStatus(text);
  return true;
}

function endBusy() {
  state.busy = false;
  $('busy').classList.add('hidden');
  $('status').classList.remove('busy');
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(body, res.status));
  return body;
}

function errorText(body, status) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map((e) => `${(e.loc || []).filter((x) => x !== 'body').join('.')}: ${e.msg}`).join('; ');
  }
  return `request failed (${status})`;
}

function currentMode() {
  return document.querySelector('input[name="mold-style"]:checked').value;
}

// which suggestion / profile fits the selected mold style
function suggestionKey() {
  return currentMode() === 'compression' ? 'two_part' : $('keep-side').value;
}

function disposeMesh(mesh) {
  if (!mesh) return;
  scene.remove(mesh);
  mesh.geometry.dispose();
}

const VIEW_DIRS = {
  iso: [1, -1, 0.65],
  top: [0, -1e-3, 1], // a hair off vertical keeps OrbitControls' up stable
  front: [0, -1, 0],
};

function fitCamera(min, max, view = 'iso') {
  state.frame = [min, max];
  const box = new THREE.Box3(
    new THREE.Vector3(...min), new THREE.Vector3(...max));
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length();
  controls.target.copy(center);
  const dir = new THREE.Vector3(...VIEW_DIRS[view]).normalize();
  // distance that fits the bounding sphere in the narrower field of view
  const vfov = THREE.MathUtils.degToRad(camera.fov);
  const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
  const dist = (size / 2) / Math.sin(Math.min(vfov, hfov) / 2) * 1.05;
  camera.position.copy(center).addScaledVector(dir, dist);
  camera.near = Math.max(size / 200, 0.05);
  camera.far = dist + size * 20;
  camera.updateProjectionMatrix();
  controls.update();
}

function setView(view) {
  if (!state.frame) return;
  fitCamera(state.frame[0], state.frame[1], view === 'fit' ? 'iso' : view);
}

function fillColors(geometry, rgb) {
  const n = geometry.attributes.position.count;
  const colors = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) colors.set(rgb, i * 3);
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
}

function resetAnalysis() {
  state.analysis = null;
  if (state.partMesh) {
    const colors = state.partMesh.geometry.attributes.color;
    for (let i = 0; i < colors.count; i++) colors.setXYZ(i, ...NEUTRAL);
    colors.needsUpdate = true;
  }
  $('verdicts').classList.add('hidden');
  $('legend').classList.add('hidden');
  $('draft-legend').classList.add('hidden');
  $('draft-map-row').classList.add('hidden');
}

// --- View state --------------------------------------------------------------

function showPartView() {
  if (state.partMesh) state.partMesh.visible = true;
  if (state.planeMesh) state.planeMesh.visible = true;
  if (state.ghostMesh) state.ghostMesh.visible = false;
  for (const m of Object.values(state.halfMeshes)) m.visible = false;
  $('result').classList.add('hidden');
}

function showMoldView() {
  $('legend').classList.add('hidden');
  $('draft-legend').classList.add('hidden');
  if (state.partMesh) state.partMesh.visible = false;
  if (state.planeMesh) state.planeMesh.visible = false;
  if (state.ghostMesh) state.ghostMesh.visible = $('ghost').checked;
  for (const m of Object.values(state.halfMeshes)) m.visible = true;
  $('result').classList.remove('hidden');
}

// --- Parting plane -----------------------------------------------------------

function updatePlane() {
  if (!state.planeMesh) return;
  state.planeMesh.position.z = parseFloat($('parting-z').value);
  $('parting-z-out').textContent = fmtZ(+$('parting-z').value);
  drawProfile();
}

// heights shown to the precision the slider can actually set (its step is
// 1/1000 of the part height), so the readout and the suggestion agree
function fmtZ(z) {
  const step = parseFloat($('parting-z').step) || 0.1;
  const decimals = Math.min(3, Math.max(1, Math.ceil(-Math.log10(step * 5))));
  return `${z.toFixed(decimals)} mm`;
}

// the value the range input will actually take for z (it snaps to its step)
function snapZ(z) {
  const slider = $('parting-z');
  const min = parseFloat(slider.min);
  const max = parseFloat(slider.max);
  const step = parseFloat(slider.step);
  if (![min, max, step].every(Number.isFinite) || step <= 0) return z;
  return Math.min(max, Math.max(min, min + Math.round((z - min) / step) * step));
}

function suggestedZ() {
  const s = state.stats;
  if (!s) return null;
  return (s.suggested && s.suggested[suggestionKey()]) ?? s.suggested_parting_z_mm;
}

function applySuggestion(force = false) {
  const z = suggestedZ();
  if (z == null) return;
  const slider = $('parting-z');
  const untouched = state.appliedSuggestion != null &&
    Math.abs(parseFloat(slider.value) - state.appliedSuggestion) < 1e-6;
  const btn = $('use-suggested');
  btn.textContent = `suggested: ${fmtZ(snapZ(z))}`;
  btn.classList.remove('hidden');
  if (force || untouched) {
    slider.value = z;
    state.appliedSuggestion = parseFloat(slider.value);
    updatePlane();
    resetAnalysis();
  }
  drawProfile();
}

function initPartingControls(stats) {
  const [lo, hi] = [stats.bounds[0][2], stats.bounds[1][2]];
  const slider = $('parting-z');
  slider.min = lo;
  slider.max = hi;
  slider.step = Math.max((hi - lo) / 1000, 0.001);
  slider.disabled = false;

  const [minB, maxB] = stats.bounds;
  const w = (maxB[0] - minB[0]) * 1.6 + 30;
  const h = (maxB[1] - minB[1]) * 1.6 + 30;
  disposeMesh(state.planeMesh);
  state.planeMesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), planeMaterial);
  state.planeMesh.position.set((minB[0] + maxB[0]) / 2, (minB[1] + maxB[1]) / 2, 0);
  scene.add(state.planeMesh);
  $('profile').classList.toggle('hidden', !stats.parting_profile);
  applySuggestion(true);
}

// Undercut area across the part's height for the selected mold style:
// green where the part releases, red where it locks, marker = current plane.
function drawProfile() {
  const canvas = $('profile');
  const prof = state.stats && state.stats.parting_profile;
  if (!prof || canvas.classList.contains('hidden')) return;
  const dpr = window.devicePixelRatio || 1;
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  if (!cw) return;
  canvas.width = cw * dpr;
  canvas.height = ch * dpr;
  const g = canvas.getContext('2d');
  g.scale(dpr, dpr);
  g.clearRect(0, 0, cw, ch);

  const values = prof[suggestionKey()] || prof.two_part;
  const [lo, hi] = [state.stats.bounds[0][2], state.stats.bounds[1][2]];
  const span = hi - lo || 1;
  const max = Math.max(...values);
  const n = values.length;
  const bw = cw / n;
  const css = getComputedStyle(document.documentElement);
  for (let i = 0; i < n; i++) {
    const v = values[i];
    const x = ((prof.z[i] - lo) / span) * cw - bw / 2;
    if (v <= 1e-9) {
      g.fillStyle = css.getPropertyValue('--good').trim() || '#62cf96';
      g.globalAlpha = 0.45;
      g.fillRect(x, ch - 4, bw + 0.5, 4);
    } else {
      g.fillStyle = css.getPropertyValue('--bad').trim() || '#e86a5f';
      g.globalAlpha = 0.85;
      const h = 4 + (ch - 8) * Math.sqrt(v / max);
      g.fillRect(x, ch - h, bw + 0.5, h);
    }
  }
  g.globalAlpha = 1;
  const z = parseFloat($('parting-z').value);
  const mx = ((z - lo) / span) * cw;
  g.strokeStyle = css.getPropertyValue('--accent').trim() || '#ef8540';
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(mx, 0);
  g.lineTo(mx, ch);
  g.stroke();
}

// --- Upload ------------------------------------------------------------------

async function loadPartFromServer(body) {
  // cache-buster: /api/orient rewrites part.stl under the same URL
  const geometry = await new STLLoader().loadAsync(`${body.mesh_url}?v=${Date.now()}`);
  fillColors(geometry, NEUTRAL);

  disposeMesh(state.partMesh);
  disposeMesh(state.ghostMesh);
  for (const m of Object.values(state.halfMeshes)) disposeMesh(m);
  state.halfMeshes = {};

  state.partMesh = new THREE.Mesh(geometry, partMaterial);
  state.ghostMesh = new THREE.Mesh(geometry, ghostMaterial);
  state.ghostMesh.visible = false;
  scene.add(state.partMesh, state.ghostMesh);

  state.meshId = body.mesh_id;
  state.stats = body;

  $('part-info').classList.remove('hidden');
  $('orient-row').classList.remove('hidden');
  if (body.filename) $('info-name').textContent = body.filename;
  $('info-dims').textContent =
    body.extents.map((e) => e.toFixed(1)).join(' × ') + ' mm';
  $('info-tris').textContent = body.triangles.toLocaleString();
  const wt = $('info-watertight');
  wt.textContent = body.watertight ? 'yes' : 'no — will attempt repair';
  wt.className = body.watertight ? 'good' : 'bad';
  if (body.volume_mm3) {
    $('info-tris').textContent += ` · ${(body.volume_mm3 / 1000).toFixed(1)} cm³`;
  }
  const warn = $('part-warnings');
  warn.replaceChildren(
    ...body.warnings.map((w) => Object.assign(document.createElement('li'), { textContent: w })));

  state.appliedSuggestion = null;
  initPartingControls(body);
  resetAnalysis();
  $('analyze').disabled = false;
  $('generate').disabled = false;
  showPartView();
  updateGrid(Math.max(...body.extents) * 2);
  fitCamera(body.bounds[0], body.bounds[1]);
}

async function uploadFile(file) {
  if (!beginBusy(`uploading ${file.name}…`)) return;
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('units', $('units').value);
    if (!$('step-opts').classList.contains('hidden')) {
      fd.append('linear_deflection_mm', $('lin-defl').value);
      fd.append('angular_deflection_deg', $('ang-defl').value);
    }
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorText(body, res.status));

    setStatus('loading preview…');
    await loadPartFromServer(body);
    setStatus(`loaded ${body.filename} — parting plane at suggested height`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    endBusy();
  }
}

// --- Lay on face ---------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const pointerNdc = new THREE.Vector2();
const layFlat = { active: false, normal: null };

const hoverTri = new THREE.Mesh(
  new THREE.BufferGeometry(),
  new THREE.MeshBasicMaterial({
    color: 0xef8540, transparent: true, opacity: 0.55,
    side: THREE.DoubleSide, depthTest: false,
  }),
);
hoverTri.geometry.setAttribute(
  'position', new THREE.BufferAttribute(new Float32Array(9), 3));
hoverTri.renderOrder = 10;
hoverTri.visible = false;
scene.add(hoverTri);

function setLayFlat(active) {
  layFlat.active = active;
  layFlat.normal = null;
  hoverTri.visible = false;
  $('lay-flat').classList.toggle('active', active);
  renderer.domElement.style.cursor = active ? 'crosshair' : '';
  if (active) {
    showPartView();
    setStatus('click a face to lay the part on it — Esc to cancel');
  }
}

async function orientPart(payload) {
  if (!state.meshId || !beginBusy('re-orienting part…')) return;
  try {
    const body = await postJSON('/api/orient', { mesh_id: state.meshId, ...payload });
    await loadPartFromServer(body);
    setStatus(payload.reset
      ? 'orientation reset to as-uploaded'
      : 'part laid on the selected face — parting plane re-suggested');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    endBusy();
  }
}

renderer.domElement.addEventListener('pointermove', (e) => {
  if (!layFlat.active || !state.partMesh) return;
  const r = renderer.domElement.getBoundingClientRect();
  pointerNdc.set(
    ((e.clientX - r.left) / r.width) * 2 - 1,
    -((e.clientY - r.top) / r.height) * 2 + 1,
  );
  raycaster.setFromCamera(pointerNdc, camera);
  const hit = raycaster.intersectObject(state.partMesh)[0];
  if (!hit) {
    hoverTri.visible = false;
    layFlat.normal = null;
    return;
  }
  const pos = state.partMesh.geometry.attributes.position;
  const tri = hoverTri.geometry.attributes.position;
  [hit.face.a, hit.face.b, hit.face.c].forEach((vi, i) => {
    tri.setXYZ(i, pos.getX(vi), pos.getY(vi), pos.getZ(vi));
  });
  tri.needsUpdate = true;
  hoverTri.geometry.computeBoundingSphere();
  hoverTri.visible = true;
  layFlat.normal = hit.face.normal.clone();
});

let pointerDownAt = null;
renderer.domElement.addEventListener('pointerdown', (e) => {
  pointerDownAt = [e.clientX, e.clientY];
});
renderer.domElement.addEventListener('pointerup', (e) => {
  if (!layFlat.active || !layFlat.normal || !pointerDownAt) return;
  // a real click, not the end of an orbit drag
  if (Math.hypot(e.clientX - pointerDownAt[0], e.clientY - pointerDownAt[1]) > 5) return;
  const n = layFlat.normal;
  setLayFlat(false);
  orientPart({ down: [n.x, n.y, n.z] });
});
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && layFlat.active) setLayFlat(false);
});

// --- Analyze -----------------------------------------------------------------

const UNMOLDED = [0.42, 0.45, 0.5];
const DRAFT_GOOD_LO = [0.25, 0.68, 0.42];
const DRAFT_GOOD_HI = [0.56, 0.94, 0.69];

// draft-map color for one face (matches the #draft-legend ramp)
function draftColor(deg, minDraft) {
  if (deg === -128) return UNMOLDED;
  if (deg < 0) return UNDERCUT;
  if (deg < minDraft) return LOW_DRAFT;
  const t = Math.min((deg - minDraft) / Math.max(2 * minDraft, 5), 1);
  return DRAFT_GOOD_LO.map((lo, i) => lo + t * (DRAFT_GOOD_HI[i] - lo));
}

function decodeDraft(b64) {
  const bin = atob(b64);
  const out = new Int8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = (bin.charCodeAt(i) << 24) >> 24;
  return out;
}

// color the part from the stored analysis: verdict colors, or the
// continuous draft-angle map when that toggle is on
function paintAnalysis() {
  const body = state.analysis;
  if (!body || !state.partMesh) return;
  const colors = state.partMesh.geometry.attributes.color;
  for (let i = 0; i < colors.count; i++) colors.setXYZ(i, ...NEUTRAL);
  const paintFace = (f, rgb) => {
    colors.setXYZ(3 * f, ...rgb);
    colors.setXYZ(3 * f + 1, ...rgb);
    colors.setXYZ(3 * f + 2, ...rgb);
  };
  const paint = (faces, rgb) => { for (const f of faces) paintFace(f, rgb); };
  const draftMap = $('draft-map').checked && body.draft;
  if (draftMap) {
    for (let f = 0; f < body.draft.length; f++) {
      paintFace(f, draftColor(body.draft[f], body.min_draft_deg));
    }
  } else {
    // later paints win: core < low draft < undercut < hidden undercut
    if (!body.single_sided) paint(body.core_faces, CORE);
    paint(body.low_draft_faces, LOW_DRAFT);
    paint(body.undercut_faces, UNDERCUT);
  }
  paint(body.occluded_faces, HIDDEN_UNDERCUT);
  colors.needsUpdate = true;

  const flagged = body.undercut_faces.length + body.low_draft_faces.length;
  $('legend').classList.toggle(
    'hidden', draftMap || flagged + (body.single_sided ? 0 : body.core_faces.length) === 0);
  $('draft-legend').classList.toggle('hidden', !draftMap);
  $('draft-legend-max').textContent = `≥ ${body.min_draft_deg}° releases`;
}

async function analyze() {
  if (!state.meshId || state.busy) return;
  const minDraft = parseFloat($('min-draft').value);
  if (!Number.isFinite(minDraft)) {
    setStatus('Min draft warn must be a number', true);
    return;
  }
  if (!beginBusy('analyzing undercuts…')) return;
  try {
    const body = await postJSON('/api/analyze', {
      mesh_id: state.meshId,
      parting_z_mm: parseFloat($('parting-z').value),
      min_draft_deg: minDraft,
      mode: currentMode(),
      keep_side: $('keep-side').value,
    });
    resetAnalysis();
    body.draft = body.draft_deg_b64 ? decodeDraft(body.draft_deg_b64) : null;
    state.analysis = body;
    $('draft-map-row').classList.toggle('hidden', !body.draft);
    paintAnalysis();

    const rows = body.single_sided
      ? [[$('keep-side').value === 'below' ? 'bottom' : 'top', 'Tool']]
      : [['bottom', 'Female half'], ['top', 'Male half']];
    for (const half of ['bottom', 'top']) $(`verdict-${half}-row`).classList.add('hidden');
    for (const [half, label] of rows) {
      $(`verdict-${half}-row`).classList.remove('hidden');
      $(`verdict-${half}-label`).textContent =
        `${label} (${half === 'bottom' ? '−' : '+'}Z pull)`;
      const el = $(`verdict-${half}`);
      const v = body[half];
      if (!v.likely_demoldable) {
        el.textContent = `${v.undercut_area_mm2.toFixed(0)} mm² undercut — locks`;
        el.className = 'bad';
      } else if (v.low_draft_area_mm2 > 0) {
        el.textContent = `${v.low_draft_area_mm2.toFixed(0)} mm² < ${body.min_draft_deg}° draft — drags`;
        el.className = 'warn';
      } else {
        el.textContent = 'releases cleanly';
        el.className = 'good';
      }
    }
    $('verdicts').classList.remove('hidden');
    showPartView();
    const flagged = body.undercut_faces.length + body.low_draft_faces.length;
    const hidden = body.occluded_faces.length;
    setStatus(flagged
      ? `${body.undercut_faces.length} undercut faces (red${hidden ? `, ${hidden} hidden — purple` : ''}) + ` +
        `${body.low_draft_faces.length} low-draft (amber)`
      : 'no undercuts or low-draft walls at this parting height');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    endBusy();
  }
}

// --- Generate ----------------------------------------------------------------

// numeric inputs sent to /api/generate, keyed by element id
const NUMERIC_FIELDS = {
  'parting-z': 'Parting plane', 'wall-margin': 'Wall margin',
  'base-thickness': 'Base thickness', 'cavity-offset': 'Cavity offset',
  'shrink-comp': 'Shrink comp.', plunge: 'Plunge depth', 'plug-border': 'Plug border',
  'plug-clearance': 'Plug clearance', 'plug-draft': 'Wall draft', 'lead-in': 'Lead-in',
  'jack-count': 'Jack screws', 'min-draft': 'Min draft warn', 'pin-clearance': 'Pin clearance',
  'bolt-count': 'Bolts', 'trim-allowance': 'Trim allowance', 'trim-kick': 'Kick angle',
  'tape-zone': 'Tape zone', 'flange-spacing': 'Hole spacing',
};

function invalidField() {
  for (const [id, label] of Object.entries(NUMERIC_FIELDS)) {
    if (!Number.isFinite(parseFloat($(id).value))) return label;
  }
  return null;
}

function currentParams() {
  return {
    mesh_id: state.meshId,
    mode: currentMode(),
    plug_shape: $('plug-shape').value,
    parting_z_mm: parseFloat($('parting-z').value),
    wall_margin_mm: parseFloat($('wall-margin').value),
    base_thickness_mm: parseFloat($('base-thickness').value),
    cavity_offset_mm: parseFloat($('cavity-offset').value),
    shrink_comp_pct: parseFloat($('shrink-comp').value),
    keep_side: $('keep-side').value,
    plunge_mm: parseFloat($('plunge').value),
    plug_border_mm: parseFloat($('plug-border').value),
    plug_clearance_mm: parseFloat($('plug-clearance').value),
    plug_draft_deg: parseFloat($('plug-draft').value),
    lead_in_mm: parseFloat($('lead-in').value),
    pry_slots: $('pry-slots').checked,
    overflow_channels: $('overflow').checked,
    jack_screws: $('jack-screws').checked,
    jack_screw_count: parseInt($('jack-count').value, 10),
    ejector_holes: $('ejector').checked,
    min_draft_deg: parseFloat($('min-draft').value),
    pins: $('pins').checked,
    pin_clearance_mm: parseFloat($('pin-clearance').value),
    bolts: $('bolts').checked,
    bolt_count: parseInt($('bolt-count').value, 10),
    bolt_diameter_mm: parseFloat($('bolt-diameter').value),
    trim_groove: $('trim-groove').checked,
    trim_allowance_mm: parseFloat($('trim-allowance').value),
    trim_kick_deg: parseFloat($('trim-kick').value),
    tape_line: $('tape-line').checked,
    tape_zone_mm: parseFloat($('tape-zone').value),
    flange_bolt_holes: $('flange-holes').checked,
    flange_hole_spacing_mm: parseFloat($('flange-spacing').value),
  };
}

function applyExplode() {
  const d = parseFloat($('explode').value) * state.explodeMax;
  if (state.halfMeshes.male) state.halfMeshes.male.position.z = d / 2;
  if (state.halfMeshes.female) state.halfMeshes.female.position.z = -d / 2;
}

async function generate() {
  if (!state.meshId || state.busy) return;
  const bad = invalidField();
  if (bad) {
    setStatus(`${bad} must be a number`, true);
    return;
  }
  if (!beginBusy('running booleans — this can take a moment on dense meshes…')) return;
  const btn = $('generate');
  btn.disabled = true;
  btn.textContent = 'Generating…';
  try {
    const body = await postJSON('/api/generate', currentParams());
    if (!body.ok) {
      $('gen-warnings').replaceChildren(
        ...(body.warnings || []).map((w) => Object.assign(document.createElement('li'), { textContent: w })));
      throw new Error(body.errors.join('; '));
    }

    $('busy-text').textContent = 'loading mold preview…';
    const loader = new STLLoader();
    const entries = Object.entries(body.halves);
    const geometries = await Promise.all(entries.map(([, info]) => loader.loadAsync(info.url)));
    for (const m of Object.values(state.halfMeshes)) disposeMesh(m);
    state.halfMeshes = {};
    entries.forEach(([name], i) => {
      const mesh = new THREE.Mesh(geometries[i], halfMaterials[name] || halfMaterials.tool);
      state.halfMeshes[name] = mesh;
      scene.add(mesh);
    });
    state.lastHalves = body.halves;
    renderDownloads();

    const bundle = $('bundle');
    bundle.href = body.bundle_url;
    bundle.classList.toggle('hidden', !body.bundle_url);

    const charge = body.metadata.forged && body.metadata.forged.charge;
    const chargeEl = $('charge');
    chargeEl.classList.toggle('hidden', !charge);
    if (charge) {
      chargeEl.innerHTML =
        `Charge: <strong>≈${charge.est_part_mass_g.toFixed(0)} g</strong> forged carbon ` +
        `(${charge.part_volume_cm3.toFixed(1)} cm³ at 1.5 g/cm³) + a few % for flash<br>` +
        `Loading space: ${charge.loading_volume_cm3.toFixed(0)} cm³ ` +
        `(${charge.loading_ratio.toFixed(1)}× the part — loose fibre needs 2–3×)`;
    }

    const [bMin, bMax] = body.metadata.block.bounds;
    state.blockBounds = [bMin, bMax];
    state.explodeMax = (bMax[2] - bMin[2]) * 1.2;
    $('explode').value = 0;
    applyExplode();
    $('explode-row').classList.toggle('hidden', Object.keys(body.halves).length < 2);
    $('gen-warnings').replaceChildren(
      ...body.warnings.map((w) => Object.assign(document.createElement('li'), { textContent: w })));

    applySection();
    showMoldView();
    fitCamera(bMin, bMax);
    const halves = Object.values(body.metadata.halves);
    const tight = halves.every((h) => h.watertight);
    setStatus(`mold generated — ${halves.length === 1 ? 'tool' : `${halves.length} halves`} ` +
      (tight ? 'watertight' : 'NOT watertight, check the warnings'), !tight);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    endBusy();
    btn.disabled = false;
    btn.textContent = 'Generate mold';
  }
}

// one row per half: STL (with volume and a print-mass estimate for the
// selected process) plus a 3MF alternative with explicit units
function renderDownloads() {
  const halves = state.lastHalves;
  if (!halves) return;
  const rows = Object.entries(halves).map(([name, info]) => {
    const color = '#' + (halfMaterials[name] || halfMaterials.tool).color.getHexString();
    const cm3 = info.volume / 1000;
    const density = PRINT_DENSITY[$('printer').value];
    const mass = density ? ` · ≈${Math.round(cm3 * density)} g solid` : '';

    const stl = document.createElement('a');
    stl.className = 'dl';
    stl.href = info.url;
    stl.download = `moldgen-${name}.stl`;
    const swatch = Object.assign(document.createElement('i'), { className: 'sw' });
    swatch.style.background = color;
    stl.append(
      swatch,
      `${name}.stl — ${cm3.toFixed(0)} cm³${mass}`,
      Object.assign(document.createElement('span'), { className: 'dl-arrow', textContent: '↓' }),
    );
    const row = Object.assign(document.createElement('div'), { className: 'dl-row' });
    row.append(stl);
    if (info.url_3mf) {
      const alt = Object.assign(document.createElement('a'), {
        className: 'dl-alt', href: info.url_3mf, textContent: '3MF',
        title: 'Same half as 3MF — units are explicit in the file, so slicers never guess the scale',
      });
      alt.download = `moldgen-${name}.3mf`;
      row.append(alt);
    }
    return row;
  });
  $('downloads').replaceChildren(...rows);
}

// --- Section view --------------------------------------------------------------

function applySection() {
  const on = $('section').checked && state.blockBounds;
  $('section-row').classList.toggle('hidden', !$('section').checked);
  const planes = on ? [sectionPlane] : [];
  for (const m of [...Object.values(halfMaterials), ghostMaterial]) {
    m.clippingPlanes = planes;
    m.side = on ? THREE.DoubleSide : THREE.FrontSide;
    m.needsUpdate = true;
  }
  if (on) {
    const [bMin, bMax] = state.blockBounds;
    const t = parseFloat($('section-pos').value);
    // keep y >= cut: the camera looks from −Y, so the near part is removed
    sectionPlane.constant = -(bMin[1] + t * (bMax[1] - bMin[1]));
  }
}

// --- Wiring ------------------------------------------------------------------

$('part-upload').addEventListener('change', (e) => {
  if (e.target.files[0]) uploadFile(e.target.files[0]);
  e.target.value = '';
});

const dropHint = $('drop-hint');
for (const ev of ['dragenter', 'dragover']) {
  window.addEventListener(ev, (e) => {
    e.preventDefault();
    dropHint.classList.remove('hidden');
  });
}
window.addEventListener('dragleave', (e) => {
  if (!e.relatedTarget) dropHint.classList.add('hidden');
});
window.addEventListener('drop', (e) => {
  e.preventDefault();
  dropHint.classList.add('hidden');
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

$('parting-z').addEventListener('input', () => {
  updatePlane();
  resetAnalysis();
  showPartView();
});
$('use-suggested').addEventListener('click', () => {
  applySuggestion(true);
  showPartView();
});

// per-mode defaults: forged tools need flange room for pins/bolts (22 mm),
// prepreg tools ~30 mm of bagging flange and >3° draft (Easy Composites),
// a basic open-layup tool just needs a modest flange
const MODE_DEFAULTS = {
  compression: { margin: 22, minDraft: 1 },
  prepreg: { margin: 30, minDraft: 3 },
  open_layup: { margin: 15, minDraft: 1 },
};

function applyMode(mode, { keepValues = false } = {}) {
  const compression = mode === 'compression';
  $('keep-side-field').classList.toggle('hidden', compression);
  $('open-layup-opts').classList.toggle('hidden', mode !== 'open_layup');
  $('prepreg-panel').classList.toggle('hidden', mode !== 'prepreg');
  $('forged-panel').classList.toggle('hidden', !compression);
  $('compression-demold').classList.toggle('hidden', !compression);
  $('hardware-panel').classList.toggle('hidden', !compression);
  if (!keepValues) {
    $('wall-margin').value = MODE_DEFAULTS[mode].margin;
    $('min-draft').value = MODE_DEFAULTS[mode].minDraft;
  }
  resetAnalysis();
  applySuggestion();
}

// sub-settings only shown while their feature is on
const DEPENDENT_FIELDS = [
  ['pins', 'pin-clearance-field'],
  ['bolts', 'bolt-opts'],
  ['jack-screws', 'jack-count-field'],
  ['flange-holes', 'flange-spacing-field'],
];

function syncDependentFields() {
  for (const [toggle, field] of DEPENDENT_FIELDS) {
    $(field).classList.toggle('hidden', !$(toggle).checked);
  }
  $('tape-zone').closest('label').classList.toggle('hidden', !$('tape-line').checked);
}

// --- Settings persistence ------------------------------------------------------

const SETTINGS_KEY = 'moldgen.settings.v1';
// per-session controls that shouldn't be restored
const TRANSIENT = new Set([
  'part-upload', 'parting-z', 'explode', 'section', 'section-pos', 'ghost', 'draft-map',
]);

function settingControls() {
  return [...document.querySelectorAll('#sidebar input[id], #sidebar select[id]')]
    .filter((el) => !TRANSIENT.has(el.id) && el.type !== 'radio');
}

function snapshotSettings() {
  const values = {};
  for (const el of settingControls()) values[el.id] = el.type === 'checkbox' ? el.checked : el.value;
  values['mold-style'] = currentMode();
  return values;
}

function applySettings(values) {
  for (const el of settingControls()) {
    if (!(el.id in values)) continue;
    if (el.type === 'checkbox') el.checked = Boolean(values[el.id]);
    else el.value = values[el.id];
  }
  const radio = document.querySelector(`input[name="mold-style"][value="${values['mold-style']}"]`);
  if (radio) radio.checked = true;
  applyMode(currentMode(), { keepValues: true });
  syncDependentFields();
  renderDownloads();
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(snapshotSettings()));
  } catch {
    /* storage unavailable (private mode, quota): settings just won't persist */
  }
}

const DEFAULT_SETTINGS = snapshotSettings(); // the HTML's values, before restoring

function restoreSettings() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
  } catch {
    saved = null;
  }
  applySettings({ ...DEFAULT_SETTINGS, ...(saved || {}) });
}

$('sidebar').addEventListener('change', (e) => {
  if (e.target.id && TRANSIENT.has(e.target.id)) return;
  syncDependentFields();
  saveSettings();
});

$('reset-settings').addEventListener('click', () => {
  applySettings(DEFAULT_SETTINGS);
  saveSettings();
  setStatus('settings reset to defaults');
});

$('keep-side').addEventListener('change', () => {
  resetAnalysis();
  applySuggestion();
});

for (const radio of document.querySelectorAll('input[name="mold-style"]')) {
  radio.addEventListener('change', () => {
    if (radio.checked) applyMode(radio.value);
  });
}

$('printer').addEventListener('change', () => {
  const preset = PRINTER_PRESETS[$('printer').value];
  $('pin-clearance').value = preset.pin;
  $('plug-clearance').value = preset.plug;
  renderDownloads();
  setStatus(`clearances set for ${$('printer').selectedOptions[0].textContent}`);
});

$('ghost').addEventListener('change', () => {
  if (state.ghostMesh) state.ghostMesh.visible = $('ghost').checked;
});
$('explode').addEventListener('input', applyExplode);
$('draft-map').addEventListener('change', paintAnalysis);
for (const b of document.querySelectorAll('#view-tools .vt')) {
  b.addEventListener('click', () => setView(b.dataset.view));
}
// viewport shortcuts; never while typing in a field
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest('input, select, textarea')) return;
  const view = { 1: 'iso', 2: 'top', 3: 'front', f: 'fit', F: 'fit' }[e.key];
  if (view) {
    setView(view);
    e.preventDefault();
  }
});
$('section').addEventListener('change', applySection);
$('section-pos').addEventListener('input', applySection);
window.addEventListener('resize', drawProfile);
$('analyze').addEventListener('click', analyze);
$('generate').addEventListener('click', generate);
$('lay-flat').addEventListener('click', () => setLayFlat(!layFlat.active));
$('reset-orient').addEventListener('click', () => orientPart({ reset: true }));

fetch('/api/health')
  .then((r) => r.json())
  .then((info) => {
    if (info.step_support) {
      $('part-upload').accept += ',.step,.stp,.iges,.igs';
      $('format-list').textContent = 'STL · 3MF · OBJ · STEP · IGES';
      $('drop-hint').textContent = 'Drop STL · 3MF · OBJ · STEP · IGES';
      $('step-opts').classList.remove('hidden');
    } else {
      $('step-note').classList.remove('hidden');
    }
    setStatus(`ready — backend v${info.version}`);
  })
  .catch(() => setStatus('backend unreachable', true));

restoreSettings();
