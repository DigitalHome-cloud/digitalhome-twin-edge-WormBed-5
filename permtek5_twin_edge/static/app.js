// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 D-LAB-5
//
// app.js — the twin's page. core.js has already wired the header;
// window.core offers api(), dialog(), toast() and friends.
//
// The page polls /api/twin: the simulation's state, the bed, the robot's views
// and the proposed jobs. A job is sent only by a Confirm click.

"use strict";

const $ = (sel) => document.querySelector(sel);
const S = { running: false, streams: false, bedKey: "", busy: false };

const say = (err) => core.toast((err.errors || [String(err)]).join("; "), true);

async function post(path, body) {
  try { return await core.api("POST", path, body || {}); }
  catch (err) { say(err); return null; }
}

function rgb(c) { return c ? `rgb(${c.map((v) => Math.round(v * 255)).join(",")})` : ""; }

/* The robot's views: a fresh picture a few times a second while the
   simulation runs. Plain pictures rather than an MJPEG stream: a stream holds
   a connection per view and not every browser paints one reliably. The next
   picture is asked for only when the last one has arrived. */
function views(on) {
  if (on === S.streams) return;
  S.streams = on;
  for (const name of ["overview", "tool"]) {
    const img = $(`#view-${name}`), off = $(`#off-${name}`);
    img.hidden = !on; off.hidden = on;
    if (!on) { img.onload = img.onerror = null; img.removeAttribute("src"); continue; }
    const next = () => { if (S.streams) img.src = `/api/twin/camera/${name}.jpg?t=${Date.now()}`; };
    img.onload = () => setTimeout(next, 220);
    img.onerror = () => setTimeout(next, 1000);
    next();
  }
}

function drawBed(t) {
  const cfg = t.config, el = $("#bed");
  if (!cfg) { el.innerHTML = ""; S.bedKey = ""; $("#bed-title").textContent = "Bed"; return; }
  const b = cfg.bed, key = `${b.id}|${b.rows}|${b.cols}`;
  $("#bed-title").textContent = `${b.name} · ${b.length_cm} × ${b.width_cm} cm · ${b.rows} rows × ${b.cols} columns`;
  if (key !== S.bedKey) {
    S.bedKey = key;
    el.style.gridTemplateColumns = `repeat(${b.cols}, 1.15rem)`;
    el.innerHTML = Array.from({ length: b.rows * b.cols }, (_, i) =>
      `<span class="cell" data-k="${Math.floor(i / b.cols)}_${i % b.cols}"></span>`).join("");
  }
  const planned = Object.fromEntries(cfg.cells.map((c) => [`${c.row}_${c.col}`, c]));
  const live = (t.twin && t.twin.bed && t.twin.bed.cells) || {};
  const g = t.twin && t.twin.gantry, cell = cfg.world.cellM;
  const headKey = g ? `${Math.floor(g.y / cell)}_${Math.floor(g.x / cell)}` : "";
  el.querySelectorAll(".cell").forEach((n) => {
    const k = n.dataset.k, c = planned[k], l = live[k];
    const status = (l && l.status) || (c && c.status) || "";
    n.className = "cell" + (status ? " " + status.replace("Cell", "").toLowerCase() : "") + (k === headKey ? " head" : "");
    n.style.background = status === "CellGrowing" || status === "CellSown" ? rgb(c && c.rgb) || "var(--accent)" : "";
    const [r, col] = k.split("_").map(Number);
    n.title = `row ${r + 1}, column ${col + 1}` + (c ? ` · ${c.cropName}` : "") + (status ? ` · ${status.replace("Cell", "").toLowerCase()}` : "")
      + (l && l.moisture !== undefined ? ` · soil ${l.moisture} %` : "");
  });
}

function drawJobs(t) {
  const job = t.twin && t.twin.job;
  S.busy = !!(job && job.state === "running");
  $("#job").innerHTML = job
    ? `<strong>${core.esc(job.kind)}</strong> · ${core.esc(job.state)}${job.feedback ? " · " + core.esc(job.feedback) : ""}`
      + (job.message ? `<br>${core.esc(job.message)}` : "")
      + (S.busy ? ' <button id="btn-cancel">Cancel</button>' : "")
    : "none";
  const cancel = $("#btn-cancel");
  if (cancel) cancel.addEventListener("click", () => post("/api/twin/cancel"));

  const list = t.proposals || [];
  $("#proposals").innerHTML = list.length ? list.map((p) => `
    <div class="proposal">
      <div><strong>${core.esc(p.title)}</strong><br><span class="note">${core.esc(p.detail)}</span></div>
      <button data-id="${core.esc(p.id)}" ${t.running && t.twin && !S.busy ? "" : "disabled"}>Confirm</button>
    </div>`).join("") : '<p class="note">nothing to propose</p>';
  $("#proposals").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", async () => { b.disabled = true; await post("/api/twin/job", { proposal: b.dataset.id }); refresh(); }));

  const hist = ((t.twin && t.twin.history) || []).slice().reverse();
  $("#history").innerHTML = hist.length ? hist.map((j) => {
    const r = j.result || {};
    const what = r.moisture ? `soil ${r.moisture.join(", ")} %` : r.givenMl ? `${r.givenMl.reduce((a, b) => a + b, 0)} ml`
      : r.photos ? `${r.photos.length} pictures` : "";
    return `<div class="${j.state === "done" ? "" : "bad"}">${core.esc(j.kind)} · ${core.esc(j.state)}`
      + `${j.message ? " · " + core.esc(j.message) : ""}${what ? " · " + core.esc(what) : ""}</div>`;
  }).join("") : "nothing yet";
}

function draw(t) {
  S.running = t.running;
  const g = t.twin && t.twin.gantry;
  const ready = t.running && t.twin && (t.twin.cameras || []).length >= 2;
  $("#twin-status").textContent = !t.prepared ? "No world yet: prepare one from the habitat."
    : !t.running ? `World ready (${t.cells} planted cells${t.meshes ? ", Blender meshes" : ""}). Start the simulation.`
    : !ready ? "Starting Webots and the gantry…"
    : (t.twin.bed && t.twin.bed.stopped) ? "E-STOP: the gantry holds its position." : "Simulation running.";
  const miss = $("#twin-missing");
  miss.hidden = !t.missing.length;
  miss.innerHTML = t.missing.map((m) => `<li>${core.esc(m)}</li>`).join("");
  if (!t.running && t.exit !== undefined && t.exit !== 0 && t.log) {
    miss.hidden = false;
    miss.innerHTML += `<li>the simulation ended (exit ${t.exit}): <code>${core.esc(t.log.slice(-3).join(" | "))}</code></li>`;
  }
  $("#btn-start").disabled = t.running || !t.prepared || t.missing.length > 0;
  $("#btn-stop").disabled = !t.running;
  $("#btn-prepare").disabled = t.running;
  $("#btn-estop").disabled = !ready;
  $("#btn-estop").textContent = t.twin && t.twin.bed && t.twin.bed.stopped ? "Release e-stop" : "E-stop";
  $("#axes").textContent = g ? `x ${(g.x * 1000).toFixed(0)} · y ${(g.y * 1000).toFixed(0)} · z ${(g.z * 1000).toFixed(0)} mm` : "";
  const sel = $("#sel-source"), want = (t.habitats || []).map((h) => h.graph).join("|");
  if (sel.dataset.have !== want) {            // the areas this edge holds that have a growing bed
    sel.dataset.have = want;
    sel.innerHTML = '<option value="">CityDemo (invented)</option>' + (t.habitats || []).map((h) =>
      `<option value="${core.esc(h.graph)}">${core.esc(h.graph)} · ${h.cells} cells</option>`).join("");
    sel.value = t.source === "citydemo" ? "" : t.source;
  }
  sel.disabled = t.running;
  if ((t.recorded || []).length) core.toast(`${t.recorded.length} sown cell(s) written to the habitat`);
  views(!!ready);
  drawBed(t);
  drawJobs(t);
}

async function refresh() {
  try { draw(await core.api("GET", "/api/twin")); }
  catch (err) { $("#twin-status").textContent = (err.errors || ["the page lost its server"])[0]; }
}

$("#btn-prepare").addEventListener("click", async () => {
  const graph = $("#sel-source").value;
  if (await post("/api/twin/prepare", graph ? { graph } : {})) core.toast("World written from the habitat");
  refresh();
});
$("#btn-start").addEventListener("click", async () => { await post("/api/twin/start"); refresh(); });
$("#btn-stop").addEventListener("click", async () => { views(false); await post("/api/twin/stop"); refresh(); });
$("#btn-estop").addEventListener("click", async () => {
  await post("/api/twin/estop", { on: $("#btn-estop").textContent === "E-stop" });
  refresh();
});

refresh();
setInterval(refresh, 800);
