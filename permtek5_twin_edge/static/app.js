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
  $("#bed-title").textContent = b.name;
  if (key !== S.bedKey) {
    S.bedKey = key;
    el.style.gridTemplateColumns = `repeat(${b.cols}, 1.15rem)`;
    el.innerHTML = Array.from({ length: b.rows * b.cols }, (_, i) =>
      `<span class="cell" data-k="${Math.floor(i / b.cols)}_${i % b.cols}"></span>`).join("");
  }
  const planned = Object.fromEntries(cfg.cells.map((c) => [`${c.row}_${c.col}`, c]));
  const live = (t.twin && t.twin.bed && t.twin.bed.cells) || {};
  const g = t.twin && t.twin.gantry, cell = cfg.world.cellM;
  const binFrom = cfg.wormBin && cfg.wormBin.cols ? cfg.wormBin.fromCol : b.cols;
  const headKey = g ? `${Math.floor(g.y / cell)}_${Math.floor(g.x / cell)}` : "";
  el.querySelectorAll(".cell").forEach((n) => {
    const k = n.dataset.k, c = planned[k], l = live[k];
    const status = (l && l.status) || (c && c.status) || "";
    const inBin = Number(k.split("_")[1]) >= binFrom;
    n.className = "cell" + (inBin ? " bin" : status ? " " + status.replace("Cell", "").toLowerCase() : "") + (k === headKey ? " head" : "");
    if (inBin) { n.style.background = ""; n.title = "worm box"; return; }
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
      : r.grams ? `${r.grams} g`
      : r.photos ? `${r.photos.length} pictures` : "";
    return `<div class="${j.state === "done" ? "" : "bad"}">${core.esc(j.kind)} · ${core.esc(j.state)}`
      + `${j.message ? " · " + core.esc(j.message) : ""}${what ? " · " + core.esc(what) : ""}</div>`;
  }).join("") : "nothing yet";
}

function stats(t) {
  const cells = (t.twin && t.twin.bed && t.twin.bed.cells) || null;
  const by = { ...(t.byStatus || {}) };
  if (cells) {                                  // the running bed knows better than the prepared plan
    for (const k of Object.keys(by)) by[k] = 0;
    for (const c of Object.values(cells)) by[c.status] = (by[c.status] || 0) + 1;
  }
  const driest = cells ? Math.min(...Object.values(cells).filter((c) => c.status !== "CellPlanned").map((c) => c.moisture)) : null;
  const g = t.twin && t.twin.gantry;
  const rows = !t.prepared ? [["–", "bed"]] : [
    [`${t.bed.lengthCm} × ${t.bed.widthCm}`, "bed, cm"],
    [`${t.bed.rows} × ${t.bed.cols}`, "rows × columns"],
    [by.CellPlanned || 0, "planned"],
    [by.CellSown || 0, "sown"],
    [by.CellGrowing || 0, "growing"],
    [driest === null || !isFinite(driest) ? "–" : `${driest.toFixed(0)} %`, "driest soil"],
    [g ? `${(g.x * 1000).toFixed(0)} · ${(g.y * 1000).toFixed(0)}` : "–", "tool x · y, mm"],
  ];
  // the bed as a system: the tank under it and the worm box at its end, when it has them
  const bedState = (t.twin && t.twin.bed) || {};
  if (t.prepared && t.bed.tankLitres) {
    const tank = bedState.tank;
    rows.push([tank ? `${tank.litres.toFixed(0)} / ${tank.capacity.toFixed(0)} l` : `${t.bed.tankLitres} l`, "tank"]);
  }
  if (t.prepared && t.bed.wormBinCm) {
    const box = bedState.wormBin, hopper = bedState.hopper;
    rows.push([box ? `${box.tempC.toFixed(0)} °C · ${box.moisture.toFixed(0)} %` : `${t.bed.wormBinCm} cm`, "worm box"]);
    if (hopper) rows.push([`${hopper.grams} g`, "hopper"]);
  }
  $("#overview").innerHTML = rows.map(([v, k]) => `<div class="app-stat"><dt>${core.esc(String(v))}</dt><dd>${core.esc(k)}</dd></div>`).join("");
}

function draw(t) {
  S.running = t.running;
  const g = t.twin && t.twin.gantry;
  const ready = t.running && t.twin && (t.twin.cameras || []).length >= 2;
  const stopped = !!(t.twin && t.twin.bed && t.twin.bed.stopped);
  S.busy = !!(t.twin && t.twin.job && t.twin.job.state === "running");
  const hero = $("#twin-status");
  hero.textContent = !t.prepared ? "Prepare a world from the habitat."
    : !t.running ? "The world is ready. Start the simulation."
    : !ready ? "Starting Webots and the gantry…"
    : stopped ? "E-stop: the gantry holds its position."
    : S.busy ? "The gantry is working." : "The gantry waits for a job you confirm.";
  hero.className = "app-hero__title edge-hero__title " + (stopped ? "hero--next" : !t.prepared || !t.running ? "hero--next" : S.busy || !ready ? "hero--working" : "hero--done");
  $("#hero-eyebrow").textContent = `Roof-bed twin · ${t.source === "citydemo" ? "CityDemo (invented)" : t.source}${t.meshes ? " · Blender meshes" : ""}`;
  $("#shell-meta").textContent = t.prepared ? `${t.bed.name} · ${t.cells} planted cells` : "";
  $("#badge-sim").hidden = !ready;
  $("#badge-estop").hidden = !stopped;
  const miss = $("#twin-missing");
  miss.hidden = !t.missing.length;
  miss.innerHTML = t.missing.map((m) => `<li>${core.esc(m)}</li>`).join("");
  if (!t.running && t.exit !== undefined && t.exit !== 0 && t.log) {
    miss.hidden = false;
    miss.innerHTML += `<li>the simulation ended (exit ${t.exit}): <code>${core.esc(t.log.slice(-3).join(" | "))}</code></li>`;
  }
  const notes = $("#twin-notices");
  notes.hidden = !(t.notices || []).length;
  notes.innerHTML = (t.notices || []).map((m) => `<li>${core.esc(m)}</li>`).join("");
  $("#btn-hopper").disabled = !ready || !(t.bed && t.bed.wormBinCm);
  $("#btn-tank").disabled = !ready || !(t.bed && t.bed.tankLitres);
  $("#btn-start").disabled = t.running || !t.prepared || t.missing.length > 0;
  $("#btn-stop").disabled = !t.running;
  $("#chk-window").disabled = t.running;
  $("#btn-prepare").disabled = t.running;
  $("#btn-estop").disabled = !ready;
  $("#btn-estop").textContent = stopped ? "Release e-stop" : "E-stop";
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
  stats(t);
  drawBed(t);
  drawJobs(t);
  const job = t.twin && t.twin.job;
  $("#rail-job").textContent = job ? `${job.kind} · ${job.state}${job.feedback && job.state === "running" ? " · " + job.feedback : ""}` : "none";
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
$("#btn-start").addEventListener("click", async () => {
  const gui = $("#chk-window").checked;
  try { localStorage.setItem("permtek5-twin-edge-window", gui ? "1" : ""); } catch (_) { /* private window */ }
  await post("/api/twin/start", { gui });
  refresh();
});
try { $("#chk-window").checked = localStorage.getItem("permtek5-twin-edge-window") === "1"; } catch (_) { /* private window */ }
$("#btn-stop").addEventListener("click", async () => { views(false); await post("/api/twin/stop"); refresh(); });
$("#btn-hopper").addEventListener("click", async () => { if (await post("/api/twin/hopper", { grams: 1000 })) core.toast("Hopper: 1 kg of scraps"); refresh(); });
$("#btn-tank").addEventListener("click", async () => { if (await post("/api/twin/tank", {})) core.toast("Tank refilled"); refresh(); });
$("#btn-estop").addEventListener("click", async () => {
  await post("/api/twin/estop", { on: $("#btn-estop").textContent === "E-stop" });
  refresh();
});

/* ---------------------------------------------------------------- shell -- */
// The rail's own buttons call core's (its header stays in the page, hidden).

const TABS = ["twin", "film", "connections", "chat"];

function showTab(name) {
  const tab = TABS.includes(name) ? name : "twin";
  for (const t of TABS) $(`#tab-${t}`).hidden = t !== tab;
  document.querySelectorAll(".app-rail__item.tab").forEach((a) => {
    const on = a.dataset.tab === tab;
    a.classList.toggle("app-rail__item--on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}

function railOpen(open, remember = false) {
  $("#rail").classList.toggle("app-rail--closed", !open);
  $("#rail-toggle").setAttribute("aria-expanded", String(open));
  $("#rail-toggle").setAttribute("aria-label", open ? "Hide menu" : "Show menu");
  if (remember) try { localStorage.setItem("permtek5-twin-edge-rail", open ? "open" : "closed"); } catch (_) { /* private window */ }
}

function shownTheme() {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function markTheme() {
  const now = shownTheme();
  document.querySelectorAll("#theme-seg [data-theme-pick]").forEach((b) => {
    b.classList.toggle("app-seg__option--on", b.dataset.themePick === now);
    b.setAttribute("aria-pressed", String(b.dataset.themePick === now));
  });
}

/* ----------------------------------------------------------------- link -- */
// Pick Stage or Production; the edge looks the edge API up (/api/twin/cloud),
// then pairs through the edge layer's own route. A site admin approves the code.

async function startLink() {
  const site = $("#link-site").value;
  let found;
  try { found = await core.api("GET", `/api/twin/cloud?site=${encodeURIComponent(site)}`); }
  catch (err) { say(err); return; }
  const made = await post("/api/cloud/link", { cloud: found.edgeApi, profile: site });
  if (made) { S.linkSite = found.site; refreshLink(); }
}

async function refreshLink() {
  let c;
  try { c = await core.api("GET", "/api/cloud"); } catch (_) { return; }
  const link = c.link || {}, box = $("#link-state");
  const linked = Object.entries(c.profiles || {}).filter(([, p]) => p && (p.linked || p.edge_id));
  $("#dot-linked").hidden = !linked.length;
  $("#btn-sync").hidden = !linked.length;
  if (link.state === "waiting") {
    // the approval page is the site's own /link, whatever host the cloud printed
    const url = S.linkSite ? `${S.linkSite}/link?user_code=${encodeURIComponent(link.user_code)}` : link.url;
    box.hidden = false;
    box.innerHTML = `<span>Approve this code as an admin of your site:</span>
      <span class="link-code">${core.esc(link.user_code || "")}</span>
      <span><a href="${core.esc(url)}" target="_blank" rel="noopener noreferrer">Open the approval page ↗</a>
      <span class="note"> · ${core.esc(link.status || "waiting for approval")}</span></span>`;
  } else if (link.state === "failed") {
    box.hidden = false;
    box.innerHTML = `<span class="bad">Linking failed: ${core.esc(link.error || "")}</span>`;
  } else if (linked.length) {
    box.hidden = false;
    box.innerHTML = linked.map(([name, p]) =>
      `<span><strong>${core.esc(name)}</strong> · linked to ${core.esc((p.tenant && p.tenant.name) || p.site || "a site")}`
      + `${(p.spaces || []).length ? " · " + p.spaces.length + " group(s)" : ""}</span>`).join("")
      + `<span class="note">Sync, then choose the site as the twin's source on the Simulation tab.</span>`;
  } else {
    box.hidden = true;
  }
}

$("#btn-link").addEventListener("click", startLink);
$("#btn-sync").addEventListener("click", async () => { if (await post("/api/knowledge/sync", {})) core.toast("Syncing the site's knowledge…"); });
refreshLink();
setInterval(refreshLink, 3000);

async function pollShell() {
  try {
    const info = await core.api("GET", "/api/agent");
    $("#badge-agent").hidden = !(info && info.status && info.status.connected);
  } catch (_) { $("#badge-agent").hidden = true; }
}

/* ------------------------------------------------------------ scenarios -- */
// A guided walk: pick one, then do its steps. Each step has at most one action
// (your click, or "run every step for me" as one confirmation for all of it)
// and the readings to watch. An older server without scenarios hides the panel.

const SC = { acting: false };

async function refreshScenarios() {
  let v;
  try { v = await core.api("GET", "/api/twin/scenarios"); }
  catch (_) { $("#scenario-panel").hidden = true; return; }
  $("#scenario-panel").hidden = false;
  const run = v.run;
  $("#scenario-pick").hidden = !!run;
  $("#scenario-run").hidden = !run;
  if (!run) {
    $("#scenario-list").innerHTML = v.scenarios.map((s) => `
      <button class="scenario-item" data-id="${core.esc(s.id)}" ${v.running && !s.unavailable ? "" : "disabled"}>
        <strong>${core.esc(s.title)}</strong>
        <span class="note">${s.steps} steps${s.unavailable ? " · " + core.esc(s.unavailable) : v.running ? "" : " · start the simulation first"}</span>
      </button>`).join("");
    $("#scenario-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
      $("#chk-scenario-auto").checked = false;
      await post("/api/twin/scenario", { id: b.dataset.id });
      refreshScenarios();
    }));
    return;
  }
  $("#scenario-title").textContent = run.title;
  $("#scenario-count").textContent = `step ${run.index + 1} of ${run.total}`;
  $("#scenario-step").textContent = run.step.title;
  $("#scenario-why").textContent = run.index === 0 ? `${run.about} ${run.step.why}` : run.step.why;
  $("#scenario-checks").innerHTML = run.checks.map((c) => `<li class="${c.ok ? "ok" : ""}">${core.esc(c.say)}</li>`).join("");
  const doBtn = $("#btn-scenario-do"), nextBtn = $("#btn-scenario-next");
  doBtn.hidden = run.step.type === "none";
  doBtn.textContent = run.step.label || "";
  doBtn.disabled = run.did || SC.acting;
  nextBtn.textContent = run.last ? "Finish" : "Next step";
  nextBtn.disabled = !run.ready || SC.acting;
  if ($("#chk-scenario-auto").checked && !SC.acting) {             // your one confirmation for the whole scenario
    if (!run.did && run.step.type !== "none") scenarioDo();
    else if (run.ready) scenarioNext();
  }
}

async function scenarioDo() {
  SC.acting = true;
  await post("/api/twin/scenario/do");
  SC.acting = false;
  refreshScenarios();
}

async function scenarioNext() {
  SC.acting = true;
  const r = await post("/api/twin/scenario/next");
  SC.acting = false;
  if (r && r.finished) core.toast("Scenario finished");
  refreshScenarios();
}

$("#btn-scenario-do").addEventListener("click", scenarioDo);
$("#btn-scenario-next").addEventListener("click", scenarioNext);
$("#btn-scenario-stop").addEventListener("click", async () => { await post("/api/twin/scenario/stop"); refreshScenarios(); });
refreshScenarios();
setInterval(refreshScenarios, 1000);

/* ----------------------------------------------------------------- film -- */

const F = { shown: "", job: null };

async function refreshFilm() {
  let f;
  try { f = await core.api("GET", "/api/twin/film"); } catch (_) { return; }
  const job = f.job && ["queued", "running"].includes(f.job.state) ? f.job : null;
  F.job = job;
  const miss = $("#film-missing");
  miss.hidden = !f.missing.length;
  miss.innerHTML = f.missing.map((m) => `<li>${core.esc(m)}</li>`).join("");
  $("#btn-film").disabled = !!job || f.missing.length > 0 || !f.prepared;
  $("#btn-film").textContent = f.film ? "Render again" : "Render film";
  $("#btn-film-cancel").hidden = !job;
  $("#film-progress").hidden = !job;
  if (job) $("#film-progress").value = job.progress || 0;
  const last = f.job && !job ? f.job : null;
  $("#film-status").textContent = job ? `rendering · ${job.phase}`
    : last && last.state === "failed" ? `failed: ${last.error}`
    : last && last.state === "cancelled" ? "cancelled"
    : f.film ? `rendered ${new Date(f.film.modified * 1000).toLocaleString()}`
    : !f.prepared ? "prepare a world first" : "not rendered yet";
  // show the film when there is one, else the still; reload only when the file changed
  const want = f.film ? `film:${f.film.modified}` : f.poster ? `poster:${f.poster.modified}` : "";
  if (want === F.shown) return;
  F.shown = want;
  const video = $("#film-video"), poster = $("#film-poster");
  video.hidden = !f.film; poster.hidden = !!f.film || !f.poster;
  if (f.film) {
    video.poster = f.poster ? `/api/twin/film/poster.png?t=${f.poster.modified}` : "";
    video.src = `/api/twin/film/film.mp4?t=${f.film.modified}`;
    video.play().catch(() => { /* autoplay refused: the controls are there */ });
  } else if (f.poster) {
    poster.src = `/api/twin/film/poster.png?t=${f.poster.modified}`;
  }
}

$("#btn-film").addEventListener("click", async () => { await post("/api/twin/film", {}); refreshFilm(); });
$("#btn-film-cancel").addEventListener("click", async () => { if (F.job) await post(`/api/jobs/${F.job.id}/cancel`); refreshFilm(); });
refreshFilm();
setInterval(refreshFilm, 2000);

window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
$("#rail-toggle").addEventListener("click", () => railOpen($("#rail").classList.contains("app-rail--closed"), true));
$("#rail-agent").addEventListener("click", () => core.connectDialog());
$("#rail-about").addEventListener("click", () => core.aboutDialog());
$("#rail-restart").addEventListener("click", () => core.restartServer());
$("#rail-stop").addEventListener("click", () => core.stopServer());
document.querySelectorAll("#theme-seg [data-theme-pick]").forEach((b) =>
  b.addEventListener("click", () => { core.applyTheme(b.dataset.themePick); markTheme(); }));
document.addEventListener("core:theme", markTheme);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", markTheme);
if (document.body.dataset.version) $("#rail-about-label").textContent = `About · v${document.body.dataset.version}`;
{
  let saved = null;
  try { saved = localStorage.getItem("permtek5-twin-edge-rail"); } catch (_) { /* private window */ }
  railOpen(saved ? saved === "open" : window.matchMedia("(min-width: 52rem)").matches);
}
markTheme();
showTab(location.hash.slice(1));
pollShell();
setInterval(pollShell, 30000);

refresh();
setInterval(refresh, 800);
