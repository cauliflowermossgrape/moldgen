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
};

// --- Scene (Z-up, millimeters) ----------------------------------------------

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1d21);

const camera = new THREE.PerspectiveCamera(
  50, viewport.clientWidth / viewport.clientHeight, 0.1, 5000);
camera.up.set(0, 0, 1);
camera.position.set(120, -120, 90);

const renderer = new THREE.WebGLRenderer({ antialias: true });
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

const grid = new THREE.GridHelper(200, 20, 0x556070, 0x333a44);
grid.rotation.x = Math.PI / 2; // lie in the XY plane, Z up
scene.add(grid);
scene.add(new THREE.AxesHelper(30));

window.addEventListener('resize', () => {
  camera.aspect = viewport.clientWidth / viewport.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(viewport.clientWidth, viewport.clientHeight);
});

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
const LOW_DRAFT = [0.98, 0.72, 0.25];

// --- Helpers -----------------------------------------------------------------

function setStatus(text, isError = false) {
  $('status-text').textContent = text;
  $('status').classList.toggle('error', isError);
  $('status').classList.toggle('busy', state.busy && !isError);
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `request failed (${res.status})`);
  return body;
}

function disposeMesh(mesh) {
  if (!mesh) return;
  scene.remove(mesh);
  mesh.geometry.dispose();
}

function fitCamera(min, max) {
  const box = new THREE.Box3(
    new THREE.Vector3(...min), new THREE.Vector3(...max));
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3()).length();
  controls.target.copy(center);
  const dir = new THREE.Vector3(1, -1, 0.65).normalize();
  camera.position.copy(center).addScaledVector(dir, size * 1.15);
  camera.near = Math.max(size / 100, 0.1);
  camera.far = size * 20;
  camera.updateProjectionMatrix();
}

function fillColors(geometry, rgb) {
  const n = geometry.attributes.position.count;
  const colors = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) colors.set(rgb, i * 3);
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
}

function resetAnalysis() {
  if (state.partMesh) {
    const colors = state.partMesh.geometry.attributes.color;
    for (let i = 0; i < colors.count; i++) colors.setXYZ(i, ...NEUTRAL);
    colors.needsUpdate = true;
  }
  $('verdicts').classList.add('hidden');
  $('legend').classList.add('hidden');
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
  $('parting-z-out').textContent = `${(+$('parting-z').value).toFixed(1)} mm`;
}

function initPartingControls(stats) {
  const [lo, hi] = [stats.bounds[0][2], stats.bounds[1][2]];
  const slider = $('parting-z');
  slider.min = lo.toFixed(1);
  slider.max = hi.toFixed(1);
  slider.step = Math.max((hi - lo) / 400, 0.01).toFixed(2);
  slider.value = stats.suggested_parting_z_mm.toFixed(1);
  slider.disabled = false;

  const btn = $('use-suggested');
  btn.textContent = `suggested: ${stats.suggested_parting_z_mm.toFixed(1)} mm`;
  btn.classList.remove('hidden');

  const [minB, maxB] = stats.bounds;
  const w = (maxB[0] - minB[0]) * 1.6 + 30;
  const h = (maxB[1] - minB[1]) * 1.6 + 30;
  disposeMesh(state.planeMesh);
  state.planeMesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), planeMaterial);
  state.planeMesh.position.set((minB[0] + maxB[0]) / 2, (minB[1] + maxB[1]) / 2, 0);
  scene.add(state.planeMesh);
  updatePlane();
}

// --- Upload ------------------------------------------------------------------

async function uploadFile(file) {
  if (state.busy) return;
  state.busy = true;
  setStatus(`uploading ${file.name}…`);
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    if (!$('step-opts').classList.contains('hidden')) {
      fd.append('linear_deflection_mm', $('lin-defl').value);
      fd.append('angular_deflection_deg', $('ang-defl').value);
    }
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `upload failed (${res.status})`);

    setStatus('loading preview…');
    const geometry = await new STLLoader().loadAsync(body.mesh_url);
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
    $('info-name').textContent = body.filename;
    $('info-dims').textContent =
      body.extents.map((e) => e.toFixed(1)).join(' × ') + ' mm';
    $('info-tris').textContent = body.triangles.toLocaleString();
    const wt = $('info-watertight');
    wt.textContent = body.watertight ? 'yes' : 'no — will attempt repair';
    wt.className = body.watertight ? 'good' : 'bad';
    const warn = $('part-warnings');
    warn.replaceChildren(
      ...body.warnings.map((w) => Object.assign(document.createElement('li'), { textContent: w })));

    initPartingControls(body);
    resetAnalysis();
    $('analyze').disabled = false;
    $('generate').disabled = false;
    showPartView();
    fitCamera(body.bounds[0], body.bounds[1]);
    setStatus(`loaded ${body.filename} — parting plane at suggested height`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    state.busy = false;
    $('status').classList.remove('busy');
  }
}

// --- Analyze -----------------------------------------------------------------

async function analyze() {
  if (!state.meshId || state.busy) return;
  state.busy = true;
  setStatus('analyzing undercuts…');
  try {
    const body = await postJSON('/api/analyze', {
      mesh_id: state.meshId,
      parting_z_mm: parseFloat($('parting-z').value),
    });
    resetAnalysis();
    const colors = state.partMesh.geometry.attributes.color;
    const paint = (faces, rgb) => {
      for (const f of faces) {
        colors.setXYZ(3 * f, ...rgb);
        colors.setXYZ(3 * f + 1, ...rgb);
        colors.setXYZ(3 * f + 2, ...rgb);
      }
    };
    paint(body.low_draft_faces, LOW_DRAFT);
    paint(body.undercut_faces, UNDERCUT);
    colors.needsUpdate = true;

    for (const half of ['bottom', 'top']) {
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
    $('legend').classList.toggle(
      'hidden', body.undercut_faces.length + body.low_draft_faces.length === 0);
    showPartView();
    const n = body.undercut_faces.length + body.low_draft_faces.length;
    setStatus(n
      ? `${body.undercut_faces.length} undercut (red) + ${body.low_draft_faces.length} low-draft (amber) faces highlighted`
      : 'no undercuts or low-draft walls at this parting height');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    state.busy = false;
    $('status').classList.remove('busy');
  }
}

// --- Generate ----------------------------------------------------------------

function currentParams() {
  return {
    mesh_id: state.meshId,
    mode: document.querySelector('input[name="mold-style"]:checked').value,
    parting_z_mm: parseFloat($('parting-z').value),
    wall_margin_mm: parseFloat($('wall-margin').value),
    base_thickness_mm: parseFloat($('base-thickness').value),
    cavity_offset_mm: parseFloat($('cavity-offset').value),
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
  state.busy = true;
  const btn = $('generate');
  btn.disabled = true;
  btn.textContent = 'Generating…';
  setStatus('running booleans — this can take a moment on dense meshes…');
  try {
    const body = await postJSON('/api/generate', currentParams());
    if (!body.ok) throw new Error(body.errors.join('; '));

    for (const m of Object.values(state.halfMeshes)) disposeMesh(m);
    state.halfMeshes = {};
    const loader = new STLLoader();
    const downloads = $('downloads');
    downloads.replaceChildren();
    for (const [name, info] of Object.entries(body.halves)) {
      const geometry = await loader.loadAsync(info.url);
      const mesh = new THREE.Mesh(geometry, halfMaterials[name] || halfMaterials.tool);
      state.halfMeshes[name] = mesh;
      scene.add(mesh);

      const a = document.createElement('a');
      a.className = 'dl';
      a.href = info.url;
      a.download = `moldgen-${name}.stl`;
      const swatch = document.createElement('i');
      swatch.className = 'sw';
      swatch.style.background =
        '#' + (halfMaterials[name] || halfMaterials.tool).color.getHexString();
      a.append(
        swatch,
        `${name}.stl — ${(info.volume / 1000).toFixed(0)} cm³`,
        Object.assign(document.createElement('span'), { className: 'dl-arrow', textContent: '↓' }),
      );
      downloads.append(a);
    }

    const [bMin, bMax] = body.metadata.block.bounds;
    state.explodeMax = (bMax[2] - bMin[2]) * 1.2;
    $('explode').value = 0;
    applyExplode();
    $('explode-row').classList.toggle('hidden', Object.keys(body.halves).length < 2);
    $('gen-warnings').replaceChildren(
      ...body.warnings.map((w) => Object.assign(document.createElement('li'), { textContent: w })));

    showMoldView();
    fitCamera(bMin, bMax);
    setStatus('mold generated — both halves watertight');
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    state.busy = false;
    $('status').classList.remove('busy');
    btn.disabled = false;
    btn.textContent = 'Generate mold';
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
  $('parting-z').value = state.stats.suggested_parting_z_mm.toFixed(1);
  updatePlane();
  resetAnalysis();
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

function applyMode(mode) {
  const compression = mode === 'compression';
  $('keep-side-field').classList.toggle('hidden', compression);
  $('open-layup-opts').classList.toggle('hidden', mode !== 'open_layup');
  $('prepreg-panel').classList.toggle('hidden', mode !== 'prepreg');
  $('forged-panel').classList.toggle('hidden', !compression);
  $('demold-panel').classList.toggle('hidden', !compression);
  $('hardware-panel').classList.toggle('hidden', !compression);
  $('wall-margin').value = MODE_DEFAULTS[mode].margin;
  $('min-draft').value = MODE_DEFAULTS[mode].minDraft;
}

for (const radio of document.querySelectorAll('input[name="mold-style"]')) {
  radio.addEventListener('change', () => {
    if (radio.checked) applyMode(radio.value);
  });
}

$('printer').addEventListener('change', () => {
  const preset = PRINTER_PRESETS[$('printer').value];
  $('pin-clearance').value = preset.pin;
  $('plug-clearance').value = preset.plug;
  setStatus(`clearances set for ${$('printer').selectedOptions[0].textContent}`);
});
$('pins').addEventListener('change', () => {
  $('pin-clearance-field').classList.toggle('hidden', !$('pins').checked);
});
$('bolts').addEventListener('change', () => {
  $('bolt-opts').classList.toggle('hidden', !$('bolts').checked);
});
$('ghost').addEventListener('change', () => {
  if (state.ghostMesh) state.ghostMesh.visible = $('ghost').checked;
});
$('explode').addEventListener('input', applyExplode);
$('analyze').addEventListener('click', analyze);
$('generate').addEventListener('click', generate);

fetch('/api/health')
  .then((r) => r.json())
  .then((info) => {
    if (info.step_support) {
      $('part-upload').accept += ',.step,.stp,.iges,.igs';
      $('format-list').textContent = 'STL · 3MF · OBJ · STEP · IGES — millimeters';
      $('step-opts').classList.remove('hidden');
    } else {
      $('step-note').classList.remove('hidden');
    }
    setStatus(`ready — backend v${info.version}`);
  })
  .catch(() => setStatus('backend unreachable', true));
