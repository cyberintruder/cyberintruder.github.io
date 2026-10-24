import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

// ───────────────────────── Astronomy ─────────────────────────
const DEG = Math.PI / 180;
const OBLIQUITY = 23.44 * DEG;
const DAY_MS = 86400000;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const EVENTS = ['March equinox', 'June solstice', 'September equinox', 'December solstice'];

const wrap360 = (d) => ((d % 360) + 360) % 360;
const wrap180 = (d) => wrap360(d + 180) - 180;
const isLeap = (y) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
const daysInYear = (y) => (isLeap(y) ? 366 : 365);

// Low-precision solar ephemeris (Astronomical Almanac), good to ~0.01°.
function solar(ms) {
  const n = ms / DAY_MS + 2440587.5 - 2451545.0;
  const L = 280.46 + 0.9856474 * n;
  const g = (357.528 + 0.9856003 * n) * DEG;
  const lambda = wrap360(L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)); // Sun's ecliptic longitude
  const l = lambda * DEG;
  const decl = Math.asin(Math.sin(OBLIQUITY) * Math.sin(l));
  const ra = Math.atan2(Math.cos(OBLIQUITY) * Math.sin(l), Math.cos(l)) / DEG;
  const gmst = 280.46061837 + 360.98564736629 * n;
  return { lambda, decl, subLon: wrap180(ra - gmst) };
}

// Hours of daylight at latitude (rad) for declination (rad); includes refraction.
function dayLength(lat, decl) {
  const c = (Math.sin(-0.833 * DEG) - Math.sin(lat) * Math.sin(decl)) / (Math.cos(lat) * Math.cos(decl));
  if (c <= -1) return 24;
  if (c >= 1) return 0;
  return (2 * Math.acos(c)) / DEG / 15;
}
const noonAltitude = (latDeg, declDeg) => 90 - Math.abs(latDeg - declDeg);

// Season by the Sun's ecliptic longitude (astronomical seasons).
function season(lambda, north) {
  const q = Math.floor(lambda / 90) % 4; // 0 spring(N) 1 summer 2 autumn 3 winter
  const names = ['spring', 'summer', 'autumn', 'winter'];
  return names[north ? q : (q + 2) % 4];
}
const SEASON_ICON = { spring: '🌱', summer: '☀️', autumn: '🍂', winter: '❄️' };
const cap = (s) => s[0].toUpperCase() + s.slice(1);

function fmtLat(deg) {
  const a = Math.abs(deg).toFixed(1);
  return Math.abs(deg) < 0.05 ? '0.0°' : `${a}° ${deg > 0 ? 'N' : 'S'}`;
}
function fmtLon(deg) {
  return `${Math.abs(deg).toFixed(1)}° ${deg >= 0 ? 'E' : 'W'}`;
}
function fmtHrs(h) {
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

// Precomputed ecliptic longitude at noon for each day of a year, plus equinox/solstice days.
const yearCache = new Map();
function yearInfo(year) {
  if (yearCache.has(year)) return yearCache.get(year);
  const start = Date.UTC(year, 0, 1);
  const n = daysInYear(year);
  const lambdas = [];
  const decls = [];
  for (let d = 0; d <= n + 100; d++) {
    const s = solar(start + d * DAY_MS + DAY_MS / 2);
    lambdas.push(s.lambda);
    decls.push(s.decl / DEG);
  }
  const events = [];
  for (let d = 1; d < lambdas.length; d++) {
    const a = Math.floor(lambdas[d - 1] / 90);
    const b = Math.floor(lambdas[d] / 90);
    if (a !== b) {
      // interpolate the crossing between the two noons to get the calendar day it falls on
      const prev = lambdas[d - 1];
      const next = lambdas[d] < prev ? lambdas[d] + 360 : lambdas[d];
      const target = b * 90 < prev ? b * 90 + 360 : b * 90;
      const frac = (target - prev) / (next - prev);
      events.push({ day: Math.floor(d - 0.5 + frac), kind: b % 4 });
    }
  }
  const info = { start, n, lambdas, decls, events };
  yearCache.set(year, info);
  return info;
}

// ───────────────────────── State ─────────────────────────
const now = new Date();
const state = {
  year: now.getUTCFullYear(),
  day: Math.floor((Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) - Date.UTC(now.getUTCFullYear(), 0, 1)) / DAY_MS),
  hours: now.getUTCHours() + now.getUTCMinutes() / 60,
  playing: false,
  spin: true,
  rays: true,
  tint: true,
  lines: true,
  lights: true,
  pin: null, // { lat, lon } in degrees
};
const SPIN_SECONDS_PER_DAY = 36;
const PLAY_DAYS_PER_SECOND = 18;

function currentDate() {
  return new Date(Date.UTC(state.year, 0, 1) + Math.floor(state.day) * DAY_MS);
}
function setDate(year, day) {
  while (day < 0) { year -= 1; day += daysInYear(year); }
  while (day >= daysInYear(year)) { day -= daysInYear(year); year += 1; }
  state.year = year;
  state.day = day;
  uiDirty = true;
}
function setYMD(y, m, d) {
  const ms = Date.UTC(y, m, d);
  const yy = new Date(ms).getUTCFullYear();
  setDate(yy, Math.round((ms - Date.UTC(yy, 0, 1)) / DAY_MS));
}

// ───────────────────────── Three.js scene ─────────────────────────
const canvas = document.getElementById('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 400);
camera.position.set(-0.9, 0.9, 6.4);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.enablePan = false;
controls.minDistance = 1.9;
controls.maxDistance = 16;
controls.rotateSpeed = 0.6;

// Inside `world` the Sun is fixed along +X and Y is ecliptic north; the Earth's axis tilts beneath it.
// The whole system is rolled a little so the Sun shines down from the upper right.
const SUN_DIR = new THREE.Vector3(1, 0, 0);
const SUN_DIST = 3.7;
const SUN_RADIUS = 0.4;
const ROLL = 20 * DEG;
const world = new THREE.Group();
world.rotation.z = ROLL;
scene.add(world);
const SUN_DIR_WORLD = SUN_DIR.clone().applyAxisAngle(new THREE.Vector3(0, 0, 1), ROLL);

function latLonToVec(latDeg, lonDeg, r = 1) {
  const la = latDeg * DEG, lo = lonDeg * DEG;
  return new THREE.Vector3(Math.cos(la) * Math.cos(lo) * r, Math.sin(la) * r, -Math.cos(la) * Math.sin(lo) * r);
}

// Stars
{
  const N = 3000;
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(120 + Math.random() * 80);
    pos.set([v.x, v.y, v.z], i * 3);
    const t = Math.random();
    const c = new THREE.Color().setHSL(0.58 + (t - 0.5) * 0.25, 0.4, 0.6 + Math.random() * 0.4);
    col.set([c.r, c.g, c.b], i * 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({ size: 0.55, vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false })));
}

// Textures
const manager = new THREE.LoadingManager();
manager.onLoad = () => document.getElementById('loading').classList.add('done');
manager.onError = () => document.getElementById('loading').classList.add('done');
const loader = new THREE.TextureLoader(manager);
const maxAniso = renderer.capabilities.getMaxAnisotropy();
function tex(url, srgb = true) {
  const t = loader.load(url);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = Math.min(8, maxAniso);
  return t;
}

// Earth
const earth = new THREE.Group();
world.add(earth);

const earthUniforms = {
  dayMap: { value: tex('textures/earth-blue-marble.jpg') },
  nightMap: { value: tex('textures/earth-night.jpg') },
  waterMap: { value: tex('textures/earth-water.png', false) },
  sunDir: { value: SUN_DIR_WORLD.clone() },
  decl: { value: 0 },
  tintAmt: { value: 1 },
  lightsAmt: { value: 1 },
  raysAmt: { value: 1 },
};

const earthMat = new THREE.ShaderMaterial({
  uniforms: earthUniforms,
  vertexShader: /* glsl */`
    varying vec2 vUv;
    varying vec3 vN;
    varying vec3 vLocal;
    varying vec3 vWorld;
    void main() {
      vUv = uv;
      vLocal = position;
      vN = normalize(mat3(modelMatrix) * normal);
      vec4 w = modelMatrix * vec4(position, 1.0);
      vWorld = w.xyz;
      gl_Position = projectionMatrix * viewMatrix * w;
    }`,
  fragmentShader: /* glsl */`
    uniform sampler2D dayMap, nightMap, waterMap;
    uniform vec3 sunDir;
    uniform float decl, tintAmt, lightsAmt, raysAmt;
    varying vec2 vUv;
    varying vec3 vN;
    varying vec3 vLocal;
    varying vec3 vWorld;
    void main() {
      vec3 N = normalize(vN);
      vec3 V = normalize(cameraPosition - vWorld);
      float ndl = dot(N, sunDir);
      float dayMix = smoothstep(-0.06, 0.14, ndl);

      vec3 day = texture2D(dayMap, vUv).rgb;
      vec3 night = texture2D(nightMap, vUv).rgb;
      float water = texture2D(waterMap, vUv).r;

      vec3 lit = day * (0.06 + 1.15 * clamp(ndl * 1.2 + 0.08, 0.0, 1.0));
      vec3 nightCol = day * 0.006 + night * vec3(1.0, 0.8, 0.5) * 1.2 * lightsAmt;
      vec3 col = mix(nightCol, lit, dayMix);

      // sunset band along the terminator
      float dusk = 1.0 - smoothstep(0.0, 0.05, abs(ndl - 0.02));
      col += vec3(1.0, 0.35, 0.08) * dusk * 0.012;

      // ocean glint
      vec3 H = normalize(sunDir + V);
      col += vec3(1.0, 0.93, 0.8) * pow(max(dot(N, H), 0.0), 70.0) * water * 0.55 * dayMix;

      // season colouring: warm where the Sun is overhead, cool in the opposite hemisphere
      float lat = asin(clamp(normalize(vLocal).y, -1.0, 1.0));
      float side = smoothstep(-0.06, 0.06, lat) * 2.0 - 1.0;
      float sunSide = decl >= 0.0 ? 1.0 : -1.0;
      float s = side * sunSide;
      float strength = tintAmt * (0.3 + 0.7 * min(abs(decl) / 0.409, 1.0)) * smoothstep(0.03, 0.35, abs(lat));
      vec3 tint = s > 0.0 ? vec3(1.0, 0.55, 0.16) : vec3(0.32, 0.62, 1.0);
      vec3 tinted = col * (0.55 + tint * 0.8) + tint * (0.004 + 0.03 * dayMix);
      col = mix(col, tinted, strength * 0.6);

      // the spot directly under the Sun
      col += vec3(1.0, 0.86, 0.45) * pow(max(ndl, 0.0), 120.0) * 0.55 * raysAmt;

      // thin atmospheric rim
      float rim = pow(1.0 - max(dot(N, V), 0.0), 3.0);
      col += vec3(0.35, 0.62, 1.0) * rim * (0.03 + 0.45 * dayMix);

      gl_FragColor = vec4(col, 1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
});
const earthMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 96), earthMat);
earth.add(earthMesh);

// Atmosphere glow
const atmo = new THREE.Mesh(
  new THREE.SphereGeometry(1.075, 96, 64),
  new THREE.ShaderMaterial({
    uniforms: { sunDir: earthUniforms.sunDir },
    vertexShader: /* glsl */`
      varying vec3 vNView;
      varying vec3 vNWorld;
      void main() {
        vNView = normalize(normalMatrix * normal);
        vNWorld = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 sunDir;
      varying vec3 vNView;
      varying vec3 vNWorld;
      void main() {
        float i = pow(clamp(-vNView.z * 2.6, 0.0, 1.0), 2.0);
        float lit = smoothstep(-0.35, 0.5, dot(vNWorld, sunDir));
        gl_FragColor = vec4(vec3(0.3, 0.58, 1.0) * i * (0.2 + 1.1 * lit), 1.0);
      }`,
    side: THREE.BackSide,
    blending: THREE.AdditiveBlending,
    transparent: true,
    depthWrite: false,
  })
);
world.add(atmo);

// Latitude circles (children of the Earth so they tilt with it)
const linesGroup = new THREE.Group();
earth.add(linesGroup);
const LATS = [
  { name: 'Arctic Circle', lat: 66.56, kind: 'polar' },
  { name: 'Tropic of Cancer', lat: 23.44, kind: 'tropic' },
  { name: 'Equator', lat: 0, kind: 'equator' },
  { name: 'Tropic of Capricorn', lat: -23.44, kind: 'tropic' },
  { name: 'Antarctic Circle', lat: -66.56, kind: 'polar' },
];
for (const L of LATS) {
  const pts = [];
  for (let i = 0; i <= 256; i++) pts.push(latLonToVec(L.lat, (i / 256) * 360, 1.003));
  const g = new THREE.BufferGeometry().setFromPoints(pts);
  const color = L.kind === 'tropic' ? 0xffc46b : L.kind === 'polar' ? 0x9fd3ff : 0xffffff;
  const m = L.kind === 'equator'
    ? new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.5 })
    : new THREE.LineDashedMaterial({ color, dashSize: 0.03, gapSize: 0.022, transparent: true, opacity: 0.75 });
  const line = new THREE.Line(g, m);
  line.computeLineDistances();
  linesGroup.add(line);
}
// Poles / axis
{
  const axisGeom = new THREE.CylinderGeometry(0.006, 0.006, 2.7, 8);
  const axis = new THREE.Mesh(axisGeom, new THREE.MeshBasicMaterial({ color: 0xbfd4ff, transparent: true, opacity: 0.55 }));
  linesGroup.add(axis);
}

// Glowing ring at the latitude where the Sun is overhead
const subRing = new THREE.Mesh(
  new THREE.TorusGeometry(1, 0.0045, 8, 256),
  new THREE.MeshBasicMaterial({ color: 0xffd75e, transparent: true, opacity: 0.95 })
);
subRing.rotation.x = Math.PI / 2;
const subRingHolder = new THREE.Group();
subRingHolder.add(subRing);
earth.add(subRingHolder);

// Pin marker
const pinMarker = new THREE.Group();
{
  const dot = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.022, 0.03, 32), new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, transparent: true, opacity: 0.85 }));
  pinMarker.add(dot, ring);
  pinMarker.visible = false;
  earth.add(pinMarker);
}

// Sun
function glowTexture(stops) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
  for (const [o, col] of stops) g.addColorStop(o, col);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
function coronaTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const ctx = c.getContext('2d');
  ctx.translate(256, 256);
  for (let i = 0; i < 28; i++) {
    const a = (i / 28) * Math.PI * 2 + Math.random() * 0.1;
    const len = 150 + Math.random() * 100;
    const g = ctx.createLinearGradient(0, 0, Math.cos(a) * len, Math.sin(a) * len);
    g.addColorStop(0, 'rgba(255,220,140,0.55)');
    g.addColorStop(1, 'rgba(255,160,60,0)');
    ctx.strokeStyle = g;
    ctx.lineWidth = 3 + Math.random() * 5;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(a) * len, Math.sin(a) * len);
    ctx.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const sun = new THREE.Group();
sun.position.copy(SUN_DIR).multiplyScalar(SUN_DIST);
world.add(sun);
const sunUniforms = { time: { value: 0 } };
sun.add(new THREE.Mesh(
  new THREE.SphereGeometry(SUN_RADIUS, 64, 48),
  new THREE.ShaderMaterial({
    uniforms: sunUniforms,
    vertexShader: /* glsl */`
      varying vec3 vN; varying vec3 vP;
      void main() { vN = normalize(normalMatrix * normal); vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform float time; varying vec3 vN; varying vec3 vP;
      void main() {
        float mu = clamp(dot(vN, vec3(0.0, 0.0, 1.0)), 0.0, 1.0);
        float n = sin(vP.x * 38.0 + time * 0.7) * sin(vP.y * 41.0 - time * 0.5) * sin(vP.z * 35.0 + time * 0.6);
        vec3 c = mix(vec3(1.0, 0.45, 0.08), vec3(1.0, 0.93, 0.62), pow(mu, 0.55));
        c += n * 0.05;
        gl_FragColor = vec4(c * 1.15, 1.0);
        #include <colorspace_fragment>
      }`,
  })
));
const glow = new THREE.Sprite(new THREE.SpriteMaterial({
  map: glowTexture([[0, 'rgba(255,240,200,1)'], [0.18, 'rgba(255,210,120,0.85)'], [0.4, 'rgba(255,150,50,0.25)'], [1, 'rgba(255,120,30,0)']]),
  blending: THREE.AdditiveBlending, depthWrite: false, transparent: true,
}));
glow.scale.setScalar(3.2);
sun.add(glow);
const corona = new THREE.Sprite(new THREE.SpriteMaterial({ map: coronaTexture(), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.8 }));
corona.scale.setScalar(2.6);
sun.add(corona);

// Sun rays: a bundle aimed at the point where the Sun is overhead (always world +X on the Earth).
const raysGroup = new THREE.Group();
world.add(raysGroup);
const rayUniforms = { time: { value: 0 } };
const rayVS = /* glsl */`
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
function rayMaterial(color, strength) {
  return new THREE.ShaderMaterial({
    uniforms: { time: rayUniforms.time, color: { value: new THREE.Color(color) }, strength: { value: strength } },
    vertexShader: rayVS,
    fragmentShader: /* glsl */`
      uniform float time; uniform vec3 color; uniform float strength;
      varying vec2 vUv;
      void main() {
        float flow = fract(vUv.y * 7.0 + time * 0.9);
        float pulse = smoothstep(0.0, 0.25, flow) * (1.0 - smoothstep(0.35, 0.9, flow));
        float ends = smoothstep(0.0, 0.06, vUv.y) * smoothstep(1.0, 0.85, vUv.y);
        float a = (0.28 + 0.9 * pulse) * ends * strength;
        gl_FragColor = vec4(color * a, a);
      }`,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
}
function cylinderBetween(a, b, r0, r1, mat) {
  const dir = new THREE.Vector3().subVectors(a, b);
  const len = dir.length();
  const m = new THREE.Mesh(new THREE.CylinderGeometry(r0, r1, len, 10, 1, true), mat);
  m.position.copy(a).add(b).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return m;
}
{
  const offsets = [[0, 0]];
  for (const [r, n] of [[0.3, 6], [0.6, 10], [0.86, 14]]) {
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + r * 2.0;
      offsets.push([Math.cos(a) * r, Math.sin(a) * r]);
    }
  }
  const sparkTex = glowTexture([[0, 'rgba(255,245,210,1)'], [0.3, 'rgba(255,200,90,0.6)'], [1, 'rgba(255,160,40,0)']]);
  for (const [oy, oz] of offsets) {
    const r = Math.hypot(oy, oz);
    const end = new THREE.Vector3(Math.sqrt(1 - r * r), oy, oz);
    const start = new THREE.Vector3(SUN_DIST - SUN_RADIUS * 0.6, oy * 0.42, oz * 0.42);
    const central = r === 0;
    const mat = rayMaterial(central ? 0xffe27a : 0xffc860, central ? 1.4 : 0.75 - r * 0.35);
    raysGroup.add(cylinderBetween(start, end, central ? 0.011 : 0.006, central ? 0.016 : 0.009, mat));
    const spark = new THREE.Sprite(new THREE.SpriteMaterial({ map: sparkTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: central ? 1 : 0.6 }));
    spark.position.copy(end).multiplyScalar(1.004);
    spark.scale.setScalar(central ? 0.14 : 0.07);
    raysGroup.add(spark);
  }
  // soft volumetric beam
  const beamMat = new THREE.ShaderMaterial({
    uniforms: { time: rayUniforms.time },
    vertexShader: /* glsl */`
      varying vec2 vUv; varying vec3 vN; varying vec3 vV;
      void main() {
        vUv = uv;
        vN = normalize(normalMatrix * normal);
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      varying vec2 vUv; varying vec3 vN; varying vec3 vV;
      void main() {
        float face = pow(abs(dot(normalize(vN), normalize(vV))), 1.6);
        float ends = smoothstep(0.0, 0.35, vUv.y) * smoothstep(1.0, 0.8, vUv.y);
        float a = face * ends * 0.12;
        gl_FragColor = vec4(vec3(1.0, 0.82, 0.45) * a, a);
      }`,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
  });
  raysGroup.add(cylinderBetween(new THREE.Vector3(SUN_DIST - SUN_RADIUS * 0.6, 0, 0), new THREE.Vector3(1.25, 0, 0), 0.4, 0.8, beamMat));
}

// ───────────────────────── Earth orientation ─────────────────────────
// Sun-fixed frame: Earth's north pole A = (sinε·sinλ, cosε, −sinε·cosλ), so A·Sun = sin(declination).
const _mw = new THREE.Matrix4();
const _ml = new THREE.Matrix4();
function orientEarth(lambdaDeg, subLonDeg) {
  const l = lambdaDeg * DEG;
  const A = new THREE.Vector3(Math.sin(OBLIQUITY) * Math.sin(l), Math.cos(OBLIQUITY), -Math.sin(OBLIQUITY) * Math.cos(l));
  const E1 = SUN_DIR.clone().addScaledVector(A, -SUN_DIR.dot(A)).normalize();
  const E3 = new THREE.Vector3().crossVectors(E1, A);
  const eL = latLonToVec(0, subLonDeg);
  const Y = new THREE.Vector3(0, 1, 0);
  const eL3 = new THREE.Vector3().crossVectors(eL, Y);
  _mw.makeBasis(E1, A, E3);
  _ml.makeBasis(eL, Y, eL3).transpose();
  earth.quaternion.setFromRotationMatrix(_mw.multiply(_ml));
}

// ───────────────────────── Globe labels ─────────────────────────
const labelsEl = document.getElementById('labels');
function makeLabel(cls, html) {
  const el = document.createElement('div');
  el.className = `lbl ${cls}`;
  el.innerHTML = html;
  labelsEl.appendChild(el);
  return el;
}
const lineLabels = LATS.map((L) => ({ ...L, el: makeLabel(`line ${L.kind}`, L.name) }));
const seasonN = makeLabel('season', '');
const seasonS = makeLabel('season', '');
const sunLabel = makeLabel('sunpt', '');

const _v = new THREE.Vector3();
const _n = new THREE.Vector3();
function placeLabel(el, localPoint, dx = 0, dy = 0, minFacing = 0.12) {
  earth.localToWorld(_v.copy(localPoint));
  _n.copy(_v).normalize();
  const toCam = camera.position.clone().sub(_v).normalize();
  const facing = _n.dot(toCam);
  _v.project(camera);
  const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
  const x = (_v.x * 0.5 + 0.5) * w + dx;
  const y = (-_v.y * 0.5 + 0.5) * h + dy;
  const op = THREE.MathUtils.smoothstep(facing, minFacing, minFacing + 0.2);
  el.style.opacity = op.toFixed(3);
  el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
  return { x, y, op };
}

// ───────────────────────── UI ─────────────────────────
const $ = (id) => document.getElementById(id);
const CITIES = [
  { name: 'Tromsø', lat: 69.65, lon: 18.96 },
  { name: 'London', lat: 51.51, lon: -0.13 },
  { name: 'New York', lat: 40.71, lon: -74.0 },
  { name: 'Hyderabad', lat: 17.39, lon: 78.49 },
  { name: 'Singapore', lat: 1.35, lon: 103.82 },
  { name: 'Sydney', lat: -33.87, lon: 151.21 },
  { name: 'Ushuaia', lat: -54.8, lon: -68.3 },
];
$('cities').innerHTML = CITIES.map((c, i) => `
  <li data-i="${i}"><span class="name">${c.name}<small>${fmtLat(c.lat)}</small></span>
  <span class="bar"><i></i></span><span class="hrs"><span></span><small></small></span></li>`).join('');
const cityRows = [...$('cities').children];
if (window.innerWidth <= 820) document.querySelector('.cities').open = false;
cityRows.forEach((li, i) => li.addEventListener('click', () => focusOn(CITIES[i].lat, CITIES[i].lon, true)));

let uiDirty = true;
let lastCalKey = '';
let lastTrackYear = null;

function updateHemi(el, s, north, declDeg, lambda) {
  el.className = `hemi ${s}`;
  el.querySelector('.icon').textContent = SEASON_ICON[s];
  el.querySelector('strong').textContent = cap(s);
  const lengthening = Math.cos(lambda * DEG) > 0 === north;
  const sunHere = north ? declDeg > 0 : declDeg < 0;
  el.querySelector('em').textContent = `${sunHere ? 'more direct sun' : 'slanted sun'} · days ${lengthening ? 'lengthening' : 'shortening'}`;
}

function updateUI() {
  const info = yearInfo(state.year);
  const di = Math.floor(state.day);
  const date = currentDate();
  const sol = solar(date.getTime() + state.hours * 3600000);
  const lambda = sol.lambda;
  const declDeg = sol.decl / DEG;

  $('dDay').textContent = date.getUTCDate();
  $('dMonth').textContent = MONTHS[date.getUTCMonth()];
  $('dYear').textContent = state.year;
  $('subLat').textContent = fmtLat(declDeg);
  const a = Math.abs(declDeg);
  $('subNote').textContent = a > 23.3 ? `right on the Tropic of ${declDeg > 0 ? 'Cancer' : 'Capricorn'} — the solstice`
    : a < 0.5 ? 'right over the Equator — the equinox'
    : `between the Equator and the Tropic of ${declDeg > 0 ? 'Cancer' : 'Capricorn'}`;

  const sN = season(lambda, true), sS = season(lambda, false);
  updateHemi($('hemiN'), sN, true, declDeg, lambda);
  updateHemi($('hemiS'), sS, false, declDeg, lambda);

  const ev = info.events.find((e) => e.day > di);
  if (ev) {
    const dd = ev.day - di;
    const evDate = new Date(info.start + ev.day * DAY_MS);
    $('nextEvent').innerHTML = `Next: <b>${EVENTS[ev.kind]}</b> on ${evDate.getUTCDate()} ${MONTHS[evDate.getUTCMonth()].slice(0, 3)} · in ${dd} day${dd === 1 ? '' : 's'}`;
  }

  CITIES.forEach((c, i) => {
    const h = dayLength(c.lat * DEG, sol.decl);
    const row = cityRows[i];
    row.querySelector('.bar i').style.width = `${(h / 24) * 100}%`;
    row.querySelector('.hrs span').textContent = h >= 24 ? '24h ☀' : h <= 0 ? '0h ☾' : fmtHrs(h);
    row.querySelector('.hrs small').textContent = `noon ${Math.max(0, noonAltitude(c.lat, declDeg)).toFixed(0)}°`;
    row.title = h >= 24 ? 'Midnight sun' : h <= 0 ? 'Polar night' : `${c.name}: ${fmtHrs(h)} of daylight`;
  });

  // Season labels on the globe
  for (const [el, north, s] of [[seasonN, true, sN], [seasonS, false, sS]]) {
    const sunHere = north ? declDeg > 0.5 : declDeg < -0.5;
    const cool = north ? declDeg < -0.5 : declDeg > 0.5;
    el.className = `lbl season ${sunHere ? 'warm' : cool ? 'cool' : 'neutral'}`;
    el.innerHTML = `${SEASON_ICON[s]} ${s}<small>${north ? 'Northern' : 'Southern'} Hemisphere</small>`;
  }
  sunLabel.innerHTML = `☀ Sun overhead · ${fmtLat(declDeg)}`;

  updateCalendar(date);
  updateTrack(info);
  drawOrbit(lambda);
  updatePinTip();
}

// Calendar grid
function updateCalendar(date) {
  const y = date.getUTCFullYear(), m = date.getUTCMonth(), d = date.getUTCDate();
  const key = `${y}-${m}-${d}`;
  if (key === lastCalKey) return;
  lastCalKey = key;
  $('calTitle').textContent = `${MONTHS[m]} ${y}`;
  const first = new Date(Date.UTC(y, m, 1)).getUTCDay(); // 0 = Sunday
  const offset = (first + 6) % 7; // Monday first
  const dim = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  const info = yearInfo(y);
  const doy0 = (Date.UTC(y, m, 1) - info.start) / DAY_MS;
  let html = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((s) => `<span class="dow">${s}</span>`).join('');
  for (let i = 0; i < offset; i++) html += '<span></span>';
  for (let i = 1; i <= dim; i++) {
    const doy = doy0 + i - 1;
    const dec = info.decls[doy];
    const t = Math.min(Math.abs(dec) / 23.44, 1);
    const dot = dec >= 0 ? `rgba(255,165,58,${0.25 + 0.75 * t})` : `rgba(95,180,255,${0.25 + 0.75 * t})`;
    const ev = info.events.find((e) => e.day === doy);
    html += `<button data-d="${i}" class="${i === d ? 'sel' : ''} ${ev ? 'event' : ''}" style="--dot:${dot}" title="${ev ? EVENTS[ev.kind] : `Sun overhead ${fmtLat(dec)}`}">${i}</button>`;
  }
  $('calGrid').innerHTML = html;
}
$('calGrid').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-d]');
  if (!b) return;
  const d = currentDate();
  setYMD(d.getUTCFullYear(), d.getUTCMonth(), +b.dataset.d);
});
function shiftMonth(k) {
  const d = currentDate();
  const y = d.getUTCFullYear(), m = d.getUTCMonth() + k;
  const dim = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  setYMD(y, m, Math.min(d.getUTCDate(), dim));
}
$('calPrev').onclick = () => shiftMonth(-1);
$('calNext').onclick = () => shiftMonth(1);
$('prevMonth').onclick = () => shiftMonth(-1);
$('nextMonth').onclick = () => shiftMonth(1);
$('prevDay').onclick = () => setDate(state.year, Math.floor(state.day) - 1);
$('nextDay').onclick = () => setDate(state.year, Math.floor(state.day) + 1);
$('today').onclick = () => {
  const n = new Date();
  setYMD(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate());
  state.hours = n.getUTCHours() + n.getUTCMinutes() / 60;
};

function wheelStepper(el, pxPerStep, onStep) {
  let acc = 0;
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    const d = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
    acc += e.deltaMode === 1 ? d * 30 : d;
    while (Math.abs(acc) >= pxPerStep) {
      const s = Math.sign(acc);
      acc -= s * pxPerStep;
      onStep(s, e.shiftKey);
    }
  }, { passive: false });
}
wheelStepper($('cal'), 100, (s) => shiftMonth(s));
wheelStepper($('track'), 24, (s, fast) => setDate(state.year, Math.floor(state.day) + s * (fast ? 7 : 1)));
wheelStepper($('orbit'), 24, (s) => setDate(state.year, Math.floor(state.day) + s * 2));

// Timeline track
const track = $('track');
const trackBg = $('trackBg');
function buildTrack(info) {
  const w = trackBg.clientWidth, h = trackBg.clientHeight;
  const dpr = Math.min(window.devicePixelRatio, 2);
  trackBg.width = Math.max(1, Math.round(w * dpr));
  trackBg.height = Math.max(1, Math.round(h * dpr));
  const ctx = trackBg.getContext('2d');
  for (let x = 0; x < trackBg.width; x++) {
    const doy = Math.floor((x / trackBg.width) * info.n);
    const dec = info.decls[doy];
    const t = Math.min(Math.abs(dec) / 23.44, 1);
    ctx.fillStyle = dec >= 0
      ? `rgba(255,${Math.round(190 - 60 * t)},${Math.round(90 - 50 * t)},${0.2 + 0.7 * t})`
      : `rgba(${Math.round(120 - 40 * t)},${Math.round(190 - 10 * t)},255,${0.2 + 0.7 * t})`;
    ctx.fillRect(x, 0, 1, trackBg.height);
  }
  // declination curve
  ctx.strokeStyle = 'rgba(255,255,255,0.55)';
  ctx.lineWidth = dpr;
  ctx.beginPath();
  for (let x = 0; x < trackBg.width; x += 2) {
    const doy = Math.floor((x / trackBg.width) * info.n);
    const y = trackBg.height / 2 - (info.decls[doy] / 23.44) * (trackBg.height / 2 - 3 * dpr);
    x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();

  let months = '';
  const year = new Date(info.start).getUTCFullYear();
  for (let m = 0; m < 12; m++) {
    const doy = (Date.UTC(year, m, 1) - info.start) / DAY_MS;
    const doyNext = (Date.UTC(year, m + 1, 1) - info.start) / DAY_MS;
    months += `<span data-m="${m}" style="left:${(doy / info.n) * 100}%;width:${((doyNext - doy) / info.n) * 100}%">${MONTHS[m].slice(0, 3)}</span>`;
  }
  $('months').innerHTML = months;
  $('events').innerHTML = info.events.filter((e) => e.day < info.n).map((e) =>
    `<span style="left:${(e.day / info.n) * 100}%">${EVENTS[e.kind].replace('September', 'Sep').replace('December', 'Dec').replace('March', 'Mar').replace('June', 'Jun')}</span>`).join('');
}
function updateTrack(info) {
  if (lastTrackYear !== state.year) {
    lastTrackYear = state.year;
    buildTrack(info);
  }
  const di = Math.floor(state.day);
  const pct = ((state.day + 0.5) / info.n) * 100;
  $('playhead').style.left = `${pct}%`;
  const d = currentDate();
  $('playLabel').textContent = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()].slice(0, 3)}`;
  track.setAttribute('aria-valuemax', info.n);
  track.setAttribute('aria-valuenow', di + 1);
  track.setAttribute('aria-valuetext', `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${state.year}`);
  const cm = d.getUTCMonth();
  for (const s of $('months').children) s.classList.toggle('cur', +s.dataset.m === cm);
}
function trackPick(e) {
  const r = trackBg.getBoundingClientRect();
  const t = THREE.MathUtils.clamp((e.clientX - r.left) / r.width, 0, 0.99999);
  setDate(state.year, Math.floor(t * daysInYear(state.year)));
}
track.addEventListener('pointerdown', (e) => {
  track.setPointerCapture(e.pointerId);
  trackPick(e);
  const move = (ev) => trackPick(ev);
  const up = () => { track.removeEventListener('pointermove', move); track.removeEventListener('pointerup', up); };
  track.addEventListener('pointermove', move);
  track.addEventListener('pointerup', up);
});

// Play / toggles / keyboard
function setPlaying(p) {
  state.playing = p;
  $('play').textContent = p ? '❚❚' : '▶';
  $('play').setAttribute('aria-label', p ? 'Pause' : 'Play the year');
  if (!p) state.day = Math.floor(state.day);
}
$('play').onclick = () => setPlaying(!state.playing);
for (const b of $('toggles').children) {
  b.addEventListener('click', () => {
    const k = b.dataset.k;
    state[k] = !state[k];
    b.classList.toggle('on', state[k]);
  });
}
$('drawerBtn').onclick = () => $('side').classList.toggle('open');
window.addEventListener('keydown', (e) => {
  if (e.target.closest && e.target.closest('input, textarea')) return;
  const k = e.key;
  if (k === 'ArrowRight' || k === 'ArrowLeft') {
    e.preventDefault();
    setDate(state.year, Math.floor(state.day) + (k === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 7 : 1));
  } else if (k === 'ArrowUp' || k === 'ArrowDown') {
    e.preventDefault();
    shiftMonth(k === 'ArrowUp' ? 1 : -1);
  } else if (k === ' ' && !(e.target instanceof HTMLButtonElement)) {
    e.preventDefault();
    setPlaying(!state.playing);
  }
});

// Orbit diagram (side view of Earth going around the Sun, axis fixed in space)
const orbitCanvas = $('orbit');
const octx = orbitCanvas.getContext('2d');
function orbitGeom() {
  const w = orbitCanvas.clientWidth, h = orbitCanvas.clientHeight;
  return { w, h, cx: w / 2, cy: h / 2 - 4, rx: w / 2 - 30, ry: h / 2 - 34 };
}
// screen angle t = λ + 90°: June solstice on the left, December on the right, March at the back.
function orbitPos(lambdaDeg, g) {
  const t = (lambdaDeg + 90) * DEG;
  return { x: g.cx + g.rx * Math.cos(t), y: g.cy - g.ry * Math.sin(t), t };
}
function drawOrbit(lambda) {
  const dpr = Math.min(window.devicePixelRatio, 2);
  const g = orbitGeom();
  if (g.w < 80) return;
  if (orbitCanvas.width !== Math.round(g.w * dpr)) {
    orbitCanvas.width = Math.round(g.w * dpr);
    orbitCanvas.height = Math.round(g.h * dpr);
  }
  const c = octx;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, g.w, g.h);

  c.strokeStyle = 'rgba(160,185,255,0.35)';
  c.setLineDash([3, 4]);
  c.beginPath();
  c.ellipse(g.cx, g.cy, g.rx, g.ry, 0, 0, Math.PI * 2);
  c.stroke();
  c.setLineDash([]);

  // equinox / solstice markers
  c.font = '10px "Space Grotesk", sans-serif';
  c.textAlign = 'center';
  const marks = [[0, 'Mar'], [90, 'Jun'], [180, 'Sep'], [270, 'Dec']];
  for (const [lam, txt] of marks) {
    const p = orbitPos(lam, g);
    c.fillStyle = 'rgba(255,215,94,0.9)';
    c.beginPath(); c.arc(p.x, p.y, 2, 0, Math.PI * 2); c.fill();
    c.fillStyle = 'rgba(200,210,235,0.75)';
    const ox = Math.cos(p.t) * 16, oy = -Math.sin(p.t) * 13;
    c.fillText(txt, p.x + ox, p.y + oy + 3);
  }

  const e = orbitPos(lambda, g);
  const front = Math.sin(e.t) < 0; // nearer the viewer than the Sun
  const drawSun = () => {
    const gr = c.createRadialGradient(g.cx, g.cy, 0, g.cx, g.cy, 26);
    gr.addColorStop(0, 'rgba(255,245,200,1)');
    gr.addColorStop(0.35, 'rgba(255,200,90,0.95)');
    gr.addColorStop(0.5, 'rgba(255,150,40,0.35)');
    gr.addColorStop(1, 'rgba(255,120,30,0)');
    c.fillStyle = gr;
    c.beginPath(); c.arc(g.cx, g.cy, 26, 0, Math.PI * 2); c.fill();
  };
  const drawEarth = () => {
    const R = 10;
    // ray from the Sun
    c.strokeStyle = 'rgba(255,215,94,0.45)';
    c.beginPath(); c.moveTo(g.cx, g.cy); c.lineTo(e.x, e.y); c.stroke();
    // dark disc + lit half facing the sun
    c.fillStyle = '#1a2a4a';
    c.beginPath(); c.arc(e.x, e.y, R, 0, Math.PI * 2); c.fill();
    const toSun = Math.atan2(g.cy - e.y, g.cx - e.x);
    c.fillStyle = '#4c9be8';
    c.beginPath(); c.arc(e.x, e.y, R, toSun - Math.PI / 2, toSun + Math.PI / 2); c.closePath(); c.fill();
    // axis tilted 23.4° towards the June side (left)
    const ax = Math.sin(-OBLIQUITY) * 17, ay = -Math.cos(OBLIQUITY) * 17;
    c.strokeStyle = '#fff';
    c.lineWidth = 1.5;
    c.beginPath(); c.moveTo(e.x - ax, e.y - ay); c.lineTo(e.x + ax, e.y + ay); c.stroke();
    c.lineWidth = 1;
    c.fillStyle = '#fff';
    c.font = 'bold 9px "Space Grotesk", sans-serif';
    c.fillText('N', e.x + ax * 1.25, e.y + ay * 1.25 + 3);
  };
  if (front) { drawSun(); drawEarth(); } else { drawEarth(); drawSun(); }

  c.fillStyle = 'rgba(200,210,235,0.6)';
  c.font = '10px "Space Grotesk", sans-serif';
  c.textAlign = 'left';
  c.fillText('Axis always points the same way in space', 6, g.h - 6);
}
orbitCanvas.addEventListener('click', (ev) => {
  const r = orbitCanvas.getBoundingClientRect();
  const g = orbitGeom();
  const x = ev.clientX - r.left, y = ev.clientY - r.top;
  const t = Math.atan2(-(y - g.cy) / g.ry, (x - g.cx) / g.rx) / DEG;
  const lam = wrap360(t - 90);
  const info = yearInfo(state.year);
  let best = 0, bestD = 1e9;
  for (let d = 0; d < info.n; d++) {
    const dd = Math.abs(wrap180(info.lambdas[d] - lam));
    if (dd < bestD) { bestD = dd; best = d; }
  }
  setDate(state.year, best);
});

// ───────────────────────── Picking a place on the globe ─────────────────────────
const raycaster = new THREE.Raycaster();
const pinTip = $('pinTip');
let downAt = null;
canvas.addEventListener('pointerdown', (e) => { downAt = { x: e.clientX, y: e.clientY }; });
canvas.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 5) return;
  const r = canvas.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObject(earthMesh, false)[0];
  if (!hit) return;
  const local = earth.worldToLocal(hit.point.clone()).normalize();
  state.pin = { lat: Math.asin(local.y) / DEG, lon: Math.atan2(-local.z, local.x) / DEG };
  placePin();
  updatePinTip();
});
function placePin() {
  if (!state.pin) { pinMarker.visible = false; pinTip.hidden = true; return; }
  const p = latLonToVec(state.pin.lat, state.pin.lon, 1.002);
  pinMarker.position.copy(p);
  pinMarker.lookAt(p.clone().multiplyScalar(2));
  pinMarker.visible = true;
  pinTip.hidden = false;
}
function updatePinTip() {
  if (!state.pin) return;
  const date = currentDate();
  const sol = solar(date.getTime() + state.hours * 3600000);
  const { lat, lon } = state.pin;
  const north = lat >= 0;
  const s = Math.abs(lat) < 0.5 ? null : season(sol.lambda, north);
  const h = dayLength(lat * DEG, sol.decl);
  const alt = noonAltitude(lat, sol.decl / DEG);
  pinTip.innerHTML = `<button class="close" aria-label="Close">✕</button>
    <b>${fmtLat(lat)}, ${fmtLon(lon)}</b><br>
    ${s ? `<span class="s-${s}">${SEASON_ICON[s]} ${cap(s)}</span>` : '☀ Equator — no real summer or winter'}<br>
    Daylight: <b>${h >= 24 ? '24h (midnight sun)' : h <= 0 ? 'none (polar night)' : fmtHrs(h)}</b><br>
    Noon sun height: <b>${Math.max(0, alt).toFixed(0)}°</b>${alt > 89.5 ? ' — straight overhead!' : ''}`;
  pinTip.querySelector('.close').onclick = () => { state.pin = null; placePin(); };
}

// Smoothly rotate the camera to look at a lat/lon
let camTween = null;
function focusOn(lat, lon, pin) {
  if (pin) { state.pin = { lat, lon }; placePin(); updatePinTip(); }
  const dist = camera.position.length();
  const target = earth.localToWorld(latLonToVec(lat, lon)).setLength(dist);
  camTween = { from: camera.position.clone(), to: target, t: 0 };
}

// ───────────────────────── Resize & render loop ─────────────────────────
let lastMobile = null;
function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  // Wide screens: shift the Earth left of centre so the Sun fits on the right.
  // Phones: lift the Earth into the gap above the info card and pull back so the Sun fits.
  const mobile = w <= 820;
  if (mobile) camera.setViewOffset(w, h, 0, h * 0.12, w, h);
  else camera.setViewOffset(w, h, Math.min(0.1, (w / h - 1) * 0.1) * w, 0, w, h);
  if (mobile !== lastMobile) {
    camera.position.setLength(mobile ? 6.4 / Math.min(1, (w / h) * 1.55) : 6.4);
    lastMobile = mobile;
  }
  camera.updateProjectionMatrix();
  lastTrackYear = null;
  uiDirty = true;
}
window.addEventListener('resize', resize);
resize();

const clock = new THREE.Clock();
let uiTimer = 0;
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1);
  const t = clock.elapsedTime;

  if (state.playing) {
    const before = Math.floor(state.day);
    state.day += dt * PLAY_DAYS_PER_SECOND;
    if (state.day >= daysInYear(state.year)) setDate(state.year, state.day);
    if (Math.floor(state.day) !== before) uiDirty = true;
  }
  if (state.spin) {
    state.hours += (dt * 24) / SPIN_SECONDS_PER_DAY;
    if (state.hours >= 24) { state.hours -= 24; setDate(state.year, Math.floor(state.day) + 1); if (state.playing) uiDirty = true; }
  }

  // Tilt follows the (continuous) date; the spin follows the time of day on the displayed date.
  const base = Date.UTC(state.year, 0, 1);
  const solTilt = solar(base + state.day * DAY_MS + state.hours * 3600000);
  const solSpin = solar(base + Math.floor(state.day) * DAY_MS + state.hours * 3600000);
  orientEarth(solTilt.lambda, solSpin.subLon);
  earth.updateMatrixWorld();

  earthUniforms.decl.value = solTilt.decl;
  earthUniforms.tintAmt.value += ((state.tint ? 1 : 0) - earthUniforms.tintAmt.value) * Math.min(1, dt * 6);
  earthUniforms.lightsAmt.value += ((state.lights ? 1 : 0) - earthUniforms.lightsAmt.value) * Math.min(1, dt * 6);
  earthUniforms.raysAmt.value = state.rays ? 1 : 0;
  raysGroup.visible = state.rays;
  linesGroup.visible = state.lines;

  const dDeg = solTilt.decl / DEG;
  subRingHolder.position.y = Math.sin(solTilt.decl) * 1.004;
  subRingHolder.scale.setScalar(Math.cos(solTilt.decl) * 1.004);
  subRingHolder.visible = state.rays;

  rayUniforms.time.value = t;
  sunUniforms.time.value = t;
  corona.material.rotation = t * 0.03;

  if (camTween) {
    camTween.t = Math.min(1, camTween.t + dt * 1.4);
    const k = 1 - Math.pow(1 - camTween.t, 3);
    const len = THREE.MathUtils.lerp(camTween.from.length(), camTween.to.length(), k);
    camera.position.copy(camTween.from).normalize().lerp(camTween.to.clone().normalize(), k).normalize().multiplyScalar(len);
    if (camTween.t >= 1) camTween = null;
  }
  controls.update();

  uiTimer += dt;
  if (uiDirty || uiTimer > 0.5) {
    uiDirty = false;
    uiTimer = 0;
    updateUI();
  }
  const hh = Math.floor(state.hours), mm = Math.floor((state.hours - hh) * 60);
  $('dTime').textContent = `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;

  // Labels follow the meridian facing the camera
  const camLocal = earth.worldToLocal(camera.position.clone());
  const camLon = Math.atan2(-camLocal.z, camLocal.x) / DEG;
  const showLabels = state.lines;
  for (const L of lineLabels) {
    if (!showLabels) { L.el.style.opacity = 0; continue; }
    placeLabel(L.el, latLonToVec(L.lat, camLon - 32, 1.01), 4, -14);
  }
  placeLabel(seasonN, latLonToVec(Math.max(40, Math.min(52, dDeg + 30)), camLon + 4, 1.01), -40, -12, 0.25);
  placeLabel(seasonS, latLonToVec(Math.min(-40, Math.max(-52, dDeg - 30)), camLon + 4, 1.01), -40, -12, 0.25);
  if (state.rays) placeLabel(sunLabel, earth.worldToLocal(SUN_DIR_WORLD.clone().multiplyScalar(1.02)), 10, -26, 0.2);
  else sunLabel.style.opacity = 0;

  if (state.pin) {
    const p = placeLabel(pinTip, latLonToVec(state.pin.lat, state.pin.lon, 1.0), 14, 10, 0.05);
    pinTip.style.pointerEvents = p.op > 0.5 ? 'auto' : 'none';
  }

  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
