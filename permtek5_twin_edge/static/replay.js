// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 D-LAB-5
//
// replay.js — the last run in 3D, on the Film tab: the bed, the worm box, the gantry travelling, and every plant
// coming up, growing and harvested, along a time slider. It reads /api/twin/replay (the run's timeline, the same
// one its OpenUSD stage is written from) and draws with three.js, vendored (scripts/vendor-three.sh): the edge
// runs offline. Nothing leaves this PC.

import {
  BoxGeometry, Color, DirectionalLight, HemisphereLight, Mesh, MeshStandardMaterial, OrbitControls, PerspectiveCamera,
  Scene, SphereGeometry, WebGLRenderer,
} from "./vendor/three-replay.js";

const R = { data: null, t: 0, playing: false, scene: null, stop: null };
const $ = (s) => document.querySelector(s);
const tr = (text, values) => {          // the page's own catalogue (app.js keeps it on window.I18N_BOOT)
  let out = ((window.I18N_BOOT || {}).catalog || {})[text] || text;
  for (const [k, v] of Object.entries(values || {})) out = out.split(`{${k}}`).join(String(v));
  return out;
};

/** A key list's value at time t: step for shown, straight between keys for sizes and positions. */
function at(keys, t, step) {
  if (!keys.length) return 0;
  if (t <= keys[0][0]) return keys[0].slice(1);
  for (let i = 1; i < keys.length; i++) {
    if (t < keys[i][0]) {
      if (step) return keys[i - 1].slice(1);
      const [a, b] = [keys[i - 1], keys[i]], f = (t - a[0]) / Math.max(1e-9, b[0] - a[0]);
      return a.slice(1).map((v, j) => v + (b[j + 1] - v) * f);
    }
  }
  return keys[keys.length - 1].slice(1);
}

function build(el, d) {
  const b = d.bed, L = b.lengthM, W = b.widthM, soil = b.soilM, rail = b.railM;
  const scene = new Scene();
  scene.background = new Color(getComputedStyle(document.body).getPropertyValue("--app-bg-sunken").trim() || "#eef2ef");
  const camera = new PerspectiveCamera(40, 1, 0.05, 100);
  camera.position.set(L * 0.5 - 1.6, 2.2, W + 2.3);
  const renderer = new WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  el.appendChild(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(L / 2, soil, W / 2);
  controls.maxPolarAngle = Math.PI * 0.49;
  scene.add(new HemisphereLight(0xf2f6ff, 0x6b5a45, 1.4));
  const sun = new DirectionalLight(0xffffff, 2.0);
  sun.position.set(L * 0.2, 6, -2);
  scene.add(sun);
  const owned = [];
  const mat = (c, rough = 0.85) => { const m = new MeshStandardMaterial({ color: c, roughness: rough }); owned.push(m); return m; };
  const block = (sx, sy, sz, m, x, y, z) => {
    const g = new BoxGeometry(sx, sy, sz); owned.push(g);
    const mesh = new Mesh(g, m); mesh.position.set(x + sx / 2, y + sy / 2, z + sz / 2); scene.add(mesh); return mesh;
  };
  const wood = mat(0x8a6a44), alu = mat(0xc8ccd0, 0.35), t = 0.03;
  block(L + 3, 0.02, W + 3, mat(0x9a9a9e, 0.95), -1.5, -0.02, -1.5);
  block(b.plantedM, soil - 0.02, W, mat(0x3b2a1c, 1), 0, 0, 0);
  block(L + 2 * t, soil + 0.03, t, wood, -t, 0, -t);
  block(L + 2 * t, soil + 0.03, t, wood, -t, 0, W);
  block(t, soil + 0.03, W, wood, -t, 0, 0);
  block(t, soil + 0.03, W, wood, L, 0, 0);
  if (L - b.plantedM > 0.01) block(L - b.plantedM, b.boxM, W, wood, b.plantedM, 0, 0);
  for (const z of [-b.railOutM, W + b.railOutM]) {
    block(L + 0.4, 0.04, 0.04, alu, -0.2, rail, z - 0.02);
    for (const x of [-0.2, L + 0.16]) block(0.04, rail, 0.04, alu, x, 0, z - 0.02);
  }
  const bridge = block(0.06, 0.04, W + 2 * b.railOutM + 0.04, alu, 0, rail + 0.04, -b.railOutM - 0.02);
  const carriage = block(0.1, 0.08, 0.1, mat(0x1d1f22), 0, rail - 0.04, 0);
  const tool = block(0.024, 0.2, 0.024, mat(0xc9944f, 0.4), 0, rail - 0.24, 0);
  const shape = new SphereGeometry(0.5, 16, 10); owned.push(shape);
  const looks = new Map();
  const plants = d.cells.map((c) => {
    if (!looks.has(c.crop)) looks.set(c.crop, mat(new Color(...(c.rgb || [0.3, 0.6, 0.3]).map((v) => v * 0.85)), 0.7));
    const m = new Mesh(shape, looks.get(c.crop));
    m.position.set((c.col + 0.5) * b.cellM, soil, (c.row + 0.5) * b.cellM);
    scene.add(m);
    return { c, m, w: Math.max(b.cellM * 0.8, c.spacingM * 0.9), h: c.heightM };
  });
  const pose = (time) => {
    for (const p of plants) {
      const show = at(p.c.shown, time, true)[0] > 0, s = Math.max(0, at(p.c.sizes, time, false)[0]);
      p.m.visible = show && s > 0.001;
      const w = Math.max(0.01, p.w * Math.min(1, s * 1.15)), h = Math.max(0.01, p.h * s);
      p.m.scale.set(w, h * 2, w);
      p.m.position.y = soil + h * 0.5;
    }
    const [x, y, z] = at(d.gantry, time, false);
    if (x !== undefined) {
      bridge.position.x = x;
      carriage.position.set(x, rail, y);
      tool.position.set(x, rail - 0.14 - z, y);
    }
  };
  const resize = () => {
    const w = el.clientWidth || 640, h = Math.round(Math.min(460, Math.max(260, w * 0.5)));
    renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  resize();
  const watch = new ResizeObserver(resize); watch.observe(el);
  let frame = 0, last = performance.now();
  const loop = (now) => {
    if (R.playing) {                          // about ten simulated days a second
      R.t = Math.min(d.end, R.t + (now - last) / 1000 * 240);
      if (R.t >= d.end) R.playing = false;
      show();
    }
    last = now;
    pose(R.t);
    controls.update();
    renderer.render(scene, camera);
    frame = requestAnimationFrame(loop);
  };
  frame = requestAnimationFrame(loop);
  return () => { cancelAnimationFrame(frame); watch.disconnect(); controls.dispose(); owned.forEach((o) => o.dispose()); renderer.dispose(); renderer.domElement.remove(); };
}

function show() {
  const d = R.data;
  $("#replay-time").value = String(R.t);
  const date = at(d.dates.map(([t, s]) => [t, s]), R.t, true)[0] || "";
  const up = d.cells.filter((c) => at(c.shown, R.t, true)[0] > 0).length;
  $("#replay-when").textContent = tr("{date} · day {day} · {n} plant(s) in the bed", { date, day: Math.floor(R.t / 24) + 1, n: up });
  $("#replay-play").textContent = R.playing ? tr("Pause") : tr("Play");
}

async function open() {
  const box = $("#replay-view");
  let d;
  try { d = await (await fetch("/api/twin/replay")).json(); } catch (_) { return; }
  if (!d || d.errors || !d.gantry.length) { $("#replay-when").textContent = tr("No run recorded yet: start one on the Loop."); return; }
  if (R.stop) R.stop();
  R.data = d; R.t = d.start; R.playing = false;
  const slider = $("#replay-time");
  slider.min = String(d.start); slider.max = String(d.end); slider.step = "1";
  R.stop = build(box, d);
  show();
}

$("#replay").addEventListener("toggle", (e) => { if (e.target.open) open(); else if (R.stop) { R.stop(); R.stop = null; } });
$("#replay-time").addEventListener("input", (e) => { R.t = Number(e.target.value); R.playing = false; show(); });
$("#replay-play").addEventListener("click", () => {
  if (!R.data) return;
  if (R.t >= R.data.end) R.t = R.data.start;
  R.playing = !R.playing; show();
});
$("#replay-reload").addEventListener("click", open);
