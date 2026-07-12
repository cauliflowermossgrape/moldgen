import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const viewport = document.getElementById('viewport');
const statusEl = document.getElementById('status');

// --- Scene -----------------------------------------------------------------

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1d21);

const camera = new THREE.PerspectiveCamera(
  50,
  viewport.clientWidth / viewport.clientHeight,
  0.1,
  5000
);
camera.position.set(120, 90, 120);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(viewport.clientWidth, viewport.clientHeight);
viewport.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.target.set(0, 0, 0);

// --- Lighting --------------------------------------------------------------

scene.add(new THREE.HemisphereLight(0xbfd4e6, 0x30261a, 1.2));

const keyLight = new THREE.DirectionalLight(0xffffff, 1.6);
keyLight.position.set(80, 140, 60);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight(0x8899bb, 0.5);
fillLight.position.set(-90, 40, -70);
scene.add(fillLight);

// --- Grid & axes (units: millimeters) ---------------------------------------

const grid = new THREE.GridHelper(200, 20, 0x556070, 0x333a44);
scene.add(grid);

const axes = new THREE.AxesHelper(30);
scene.add(axes);

// --- Render loop -----------------------------------------------------------

function onResize() {
  camera.aspect = viewport.clientWidth / viewport.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(viewport.clientWidth, viewport.clientHeight);
}
window.addEventListener('resize', onResize);

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

// --- Backend check ----------------------------------------------------------

fetch('/api/health')
  .then((r) => r.json())
  .then((info) => {
    statusEl.textContent = `backend ok · v${info.version}`;
  })
  .catch(() => {
    statusEl.textContent = 'backend unreachable';
  });
