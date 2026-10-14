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
const S = { running: false, streams: false, bedKey: "", busy: false, tab: "", landed: false, ready: false };

const say = (err) => core.toast((err.errors || [String(err)]).join("; "), true);

/* The page in the person's language (i18n.py): the English text is the key, `{name}` a value filled in. */
const I18N = { lang: "en", catalog: {} };
function tr(text, values) {
  let out = (text && I18N.catalog[text]) || text || "";
  if (values) for (const [k, v] of Object.entries(values)) out = out.split(`{${k}}`).join(String(v));
  return out;
}
const job_ = (word) => tr(String(word || ""));         // a job's kind or state, as the robot names it
Object.assign(I18N, window.I18N_BOOT || {});

// The page's own text and the template's: every text node and title, label and alt that is exactly an English
// text in the catalog. Whitespace inside is folded, as the catalog keys are.
const I18N_ATTRS = ["title", "aria-label", "alt", "placeholder"];
function translateDom(root) {
  if (I18N.lang === "en" || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const key = n.nodeValue.replace(/\s+/g, " ").trim();
    if (key && I18N.catalog[key]) n.nodeValue = n.nodeValue.replace(/\S[\s\S]*\S|\S/, I18N.catalog[key]);
  }
  const els = root.querySelectorAll ? [root, ...root.querySelectorAll("[title],[aria-label],[alt],[placeholder]")] : [];
  for (const el of els) for (const a of I18N_ATTRS) {
    const v = el.getAttribute && el.getAttribute(a);
    if (v && I18N.catalog[v.trim()]) el.setAttribute(a, I18N.catalog[v.trim()]);
  }
}
document.documentElement.lang = I18N.lang;
translateDom(document.body);
new MutationObserver((changes) => {
  for (const c of changes) {
    const el = c.target.nodeType === 1 ? c.target : c.target.parentElement;
    if (el && el.closest("#core-dialog, #core-toast, #edge-connections, #edge-chat")) translateDom(el);
  }
}).observe(document.body, { childList: true, subtree: true });

// The language switch: a cookie the server reads too, then the page again in that language.
document.querySelectorAll("#lang-seg [data-lang]").forEach((b) => {
  const on = b.dataset.lang === I18N.lang;
  b.classList.toggle("app-seg__option--on", on);
  b.setAttribute("aria-pressed", String(on));
  b.addEventListener("click", () => {
    document.cookie = `lang=${b.dataset.lang}; path=/; max-age=31536000; samesite=lax`;
    location.reload();
  });
});

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
  on = on && S.tab === "robot";                 // pictures only while the Robot screen shows them
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
  if (!cfg) { el.innerHTML = ""; S.bedKey = ""; $("#bed-title").textContent = tr("Bed"); return; }
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
  // what the last look over the bed found: the cell each weed stands in
  const weeds = {};
  for (const w of (t.weeds && t.weeds.found) || []) (weeds[`${Math.floor(w.y / cell)}_${Math.floor(w.x / cell)}`] ||= []).push(w);
  const wl = t.weeds, sim = t.twin && t.twin.bed && t.twin.bed.sim;
  $("#weeds-line").textContent = !wl ? "" : (wl.scanned
    ? tr("Weeds, last look ({when}): {small} small ones to stamp, {hand} for your hands.", { when: wl.when, small: wl.small, hand: wl.byHand })
    : tr("Weeds: the bed has not been looked over yet."))
    + (sim ? " " + tr("Simulation only: {n} really stand in the bed, {big} of them big.", { n: sim.weeds, big: sim.weedsLarge }) : "");
  el.querySelectorAll(".cell").forEach((n) => {
    const k = n.dataset.k, c = planned[k], l = live[k];
    const status = (l && l.status) || (c && c.status) || "";
    const inBin = Number(k.split("_")[1]) >= binFrom;
    n.className = "cell" + (inBin ? " bin" : status ? " " + status.replace("Cell", "").toLowerCase() : "") + (k === headKey ? " head" : "")
      + (weeds[k] ? " weed" : "");
    if (inBin) { n.style.background = ""; n.title = tr("worm box"); return; }
    n.style.background = status === "CellGrowing" || status === "CellSown" ? rgb(c && c.rgb) || "var(--accent)" : "";
    const [r, col] = k.split("_").map(Number);
    n.title = tr("row {row}, column {col}", { row: r + 1, col: col + 1 }) + (c ? ` · ${(c.cropNames && c.cropNames[I18N.lang]) || c.cropName}` : "")
      + (status ? ` · ${tr(status.replace("Cell", "").toLowerCase())}` : "")
      + (l && l.moisture !== undefined ? " · " + tr("soil {pct} %", { pct: l.moisture }) : "")
      + (weeds[k] ? " · " + tr("weed") + " " + weeds[k].map((w) => `${w.sizeMm} mm, ${w.do === "stamp" ? tr("to stamp") : tr("by hand")}`).join("; ") : "");
  });
}

function drawJobs(t) {
  const job = t.twin && t.twin.job;
  S.busy = !!(job && job.state === "running");
  $("#job").innerHTML = job
    ? `<strong>${core.esc(job_(job.kind))}</strong> · ${core.esc(job_(job.state))}${job.feedback ? " · " + core.esc(job.feedback) : ""}`
      + (job.message ? `<br>${core.esc(tr(job.message))}` : "")
      + (S.busy ? ` <button id="btn-cancel">${core.esc(tr("Cancel"))}</button>` : "")
    : core.esc(tr("none"));
  const cancel = $("#btn-cancel");
  if (cancel) cancel.addEventListener("click", () => post("/api/twin/cancel"));

  const list = t.proposals || [];
  $("#proposals").innerHTML = list.length ? list.map((p) => `
    <div class="proposal">
      <div><strong>${core.esc(p.title)}</strong><br><span class="note">${core.esc(p.detail)}</span></div>
      <button data-id="${core.esc(p.id)}" ${t.running && t.twin && !S.busy ? "" : "disabled"}>${core.esc(tr("Confirm"))}</button>
    </div>`).join("") : `<p class="note">${core.esc(tr("nothing to propose"))}</p>`;
  $("#proposals").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", async () => { b.disabled = true; await post("/api/twin/job", { proposal: b.dataset.id }); refresh(); }));

  const hist = ((t.twin && t.twin.history) || []).slice().reverse();
  $("#history").innerHTML = hist.length ? hist.map((j) => {
    const r = j.result || {};
    const what = r.moisture ? tr("soil {pct} %", { pct: r.moisture.join(", ") }) : r.givenMl ? `${r.givenMl.reduce((a, b) => a + b, 0)} ml`
      : r.weeds ? tr("{n} weeds in {p} pictures", { n: r.weeds.length, p: r.pictures }) : r.stamped !== undefined ? tr("{n} stamped", { n: r.stamped })
      : r.grams ? `${r.grams} g` : r.givenG ? tr("{g} g of compost", { g: r.givenG.reduce((a, b) => a + b, 0) })
      : r.photos ? tr("{n} pictures", { n: r.photos.length }) : "";
    return `<div class="${j.state === "done" ? "" : "bad"}">${core.esc(job_(j.kind))} · ${core.esc(job_(j.state))}`
      + `${j.message ? " · " + core.esc(tr(j.message)) : ""}${what ? " · " + core.esc(what) : ""}</div>`;
  }).join("") : core.esc(tr("nothing yet"));
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
  const rows = !t.prepared ? [["–", tr("bed")]] : [
    [`${t.bed.lengthCm} × ${t.bed.widthCm}`, tr("bed, cm")],
    [`${t.bed.rows} × ${t.bed.cols}`, tr("rows × columns")],
    [by.CellPlanned || 0, tr("planned")],
    [by.CellSown || 0, tr("sown")],
    [by.CellGrowing || 0, tr("growing")],
    [driest === null || !isFinite(driest) ? "–" : `${driest.toFixed(0)} %`, tr("driest soil")],
    [g ? `${(g.x * 1000).toFixed(0)} · ${(g.y * 1000).toFixed(0)}` : "–", tr("tool x · y, mm")],
  ];
  // the bed as a system: the tank under it and the worm box at its end, when it has them
  const bedState = (t.twin && t.twin.bed) || {};
  if (t.prepared && t.bed.tankLitres) {
    const tank = bedState.tank;
    rows.push([tank ? `${tank.litres.toFixed(0)} / ${tank.capacity.toFixed(0)} l` : `${t.bed.tankLitres} l`, tr("tank")]);
  }
  if (t.prepared && t.bed.wormBinCm) {
    const box = bedState.wormBin, hopper = bedState.hopper;
    // a dead probe gives no reading: say so instead of showing a number
    const temp = box && typeof box.tempC === "number" ? `${box.tempC.toFixed(0)} °C` : tr("no reading");
    rows.push([box ? `${temp} · ${box.moisture.toFixed(0)} %` : `${t.bed.wormBinCm} cm`, tr("worm box")]);
    if (hopper) rows.push([`${hopper.grams} g${hopper.brownsGrams ? ` · ${hopper.brownsGrams} g` : ""}`, hopper.brownsGrams ? tr("hopper: scraps · bedding") : tr("hopper")]);
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
  hero.textContent = tr(!t.running ? "Choose the habitat and a scenario, then start."
    : !ready ? "Starting Webots and the gantry…"
    : stopped ? "E-stop: the gantry holds its position."
    : S.busy ? "The gantry is working." : "The gantry waits for a job you confirm.");
  hero.className = "app-hero__title edge-hero__title " + (stopped ? "hero--next" : !t.prepared || !t.running ? "hero--next" : S.busy || !ready ? "hero--working" : "hero--done");
  // one edge runs one Perma5Guild: its name, its type (WormBed5) and its robot (Gantry5-gen1)
  const guild = t.guild;
  $("#hero-eyebrow").textContent = (guild ? `${guild.name} · ${guild.type} · ${guild.robotName}` : tr("Roof-bed twin"))
    + ` · ${t.source === "citydemo" ? tr("CityDemo (invented)") : t.source}${t.meshes ? " · " + tr("Blender meshes") : ""}`;
  $("#shell-meta").textContent = t.prepared ? `${guild ? "Perma5Guild · " : ""}${t.bed.name} · ${tr("{n} planted cells", { n: t.cells })}` : "";
  $("#badge-sim").hidden = !ready;
  $("#badge-estop").hidden = !stopped;
  const miss = $("#twin-missing");
  miss.hidden = !t.missing.length;
  miss.innerHTML = t.missing.map((m) => `<li>${core.esc(tr(m))}</li>`).join("");
  if (!t.running && t.exit !== undefined && t.exit !== 0 && t.log) {
    miss.hidden = false;
    miss.innerHTML += `<li>${core.esc(tr("the simulation ended (exit {code}):", { code: t.exit }))} <code>${core.esc(t.log.slice(-3).join(" | "))}</code></li>`;
  }
  drawAttention(t);
  const assumed = $("#twin-assumed");               // parts a WormBed5 guild has that the map does not show yet
  assumed.hidden = !(t.assumed || []).length;
  assumed.textContent = assumed.hidden ? "" : tr("Not on your map yet, so assumed here: {parts}.", { parts: t.assumed.map((a) => tr(a)).join("; ") })
    + " " + tr("Add them to the bed on the Map in PermTek-5 to set your own sizes.");
  drawEnv(t);
  drawBox(t, ready);
  drawCare(t);
  drawLearning(t);
  drawOperator(t);
  drawTruth(t);
  drawStrip(t, ready);
  S.ready = !!ready;
  $("#btn-go").classList.toggle("pulse", !t.running && !t.missing.length);
  land(t);
  $("#btn-hopper").disabled = !ready || !(t.bed && t.bed.wormBinCm);
  $("#btn-hopper-empty").disabled = $("#btn-hopper-mix").disabled = $("#btn-browns").disabled = $("#btn-hopper").disabled;
  $("#btn-tank").disabled = !ready || !(t.bed && t.bed.tankLitres);
  const barrel = ((t.twin && t.twin.bed && t.twin.bed.water && t.twin.bed.water.stores) || []).find((x) => x.kind === "RainBarrel");
  $("#btn-barrel").hidden = !barrel;
  $("#btn-barrel").disabled = !ready || !barrel || (barrel.litres || 0) < 1;
  $("#btn-start").disabled = t.running || !t.prepared || t.missing.length > 0;
  $("#btn-stop").disabled = !t.running;
  $("#chk-window").disabled = t.running;
  $("#btn-prepare").disabled = t.running;
  $("#btn-estop").disabled = !ready;
  $("#btn-estop").textContent = stopped ? tr("Release e-stop") : tr("E-stop");
  $("#btn-estop").dataset.on = stopped ? "" : "1";
  $("#axes").textContent = g ? `x ${(g.x * 1000).toFixed(0)} · y ${(g.y * 1000).toFixed(0)} · z ${(g.z * 1000).toFixed(0)} mm` : "";
  const sel = $("#sel-source"), want = (t.habitats || []).map((h) => h.graph).join("|");
  if (sel.dataset.have !== want) {            // the areas this edge holds that have a growing bed
    sel.dataset.have = want;
    sel.innerHTML = `<option value="">${core.esc(tr("CityDemo (invented)"))}</option>` + (t.habitats || []).map((h) =>
      `<option value="${core.esc(h.graph)}">${core.esc(h.graph)} · ${core.esc(tr("{n} cells", { n: h.cells }))}</option>`).join("");
    sel.value = t.source === "citydemo" ? "" : t.source;
  }
  sel.disabled = t.running;
  if ((t.recorded || []).length) core.toast(tr("{n} sown cell(s) written to the habitat", { n: t.recorded.length }));
  views(!!ready);
  stats(t);
  drawBed(t);
  drawJobs(t);
  const job = t.twin && t.twin.job;
  $("#strip-job").textContent = !t.running ? "" : job ? tr("job: {kind} · {state}", { kind: job_(job.kind), state: job_(job.state) })
    + (job.feedback && job.state === "running" ? " · " + job.feedback : "") : tr("no job");
}

async function refresh() {
  try { draw(await core.api("GET", "/api/twin")); }
  catch (err) { $("#twin-status").textContent = (err.errors || [tr("the page lost its server")])[0]; }
}

$("#btn-prepare").addEventListener("click", async () => {
  const graph = $("#sel-source").value;
  if (await post("/api/twin/prepare", graph ? { graph } : {})) core.toast(tr("World written from the habitat"));
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
const fillHopper = async (mix, said) => { if (await post("/api/twin/hopper", mix ? { mix } : { grams: 0 })) core.toast(tr(said)); refresh(); };
$("#btn-hopper").addEventListener("click", () => fillHopper({ vegetable: 600, fruit: 300, coffee: 100 }, "Hopper: 1 kg, the usual mix"));
$("#btn-browns").addEventListener("click", async () => { if (await post("/api/twin/hopper", { browns: 1000 })) core.toast(tr("Bedding compartment: 1 kg")); refresh(); });
$("#btn-hopper-empty").addEventListener("click", () => fillHopper(null, "Hopper emptied"));
$("#btn-hopper-mix").addEventListener("click", () => {
  const mix = {};
  $("#hopper-kinds").querySelectorAll("input").forEach((i) => { if (Number(i.value) > 0) mix[i.dataset.kind] = Number(i.value); });
  fillHopper(mix, "Hopper filled");
});

// The feeding guide's kinds, for saying what went into the hopper. An older server without it keeps the form hidden.
(async function loadGuide() {
  let g;
  try { g = await core.api("GET", "/api/twin/guide"); } catch (_) { return; }
  $("#hopper-form").hidden = false;
  $("#guide-about").textContent = g.about;
  $("#hopper-kinds").innerHTML = g.kinds.map((k) => `
    <label class="kind kind--${core.esc(k.class)}"><span>${core.esc(k.label)} <em>${core.esc(g.classes[k.class])}${k.weeklySharePct ? `, ≤ ${k.weeklySharePct} %` : ""}</em></span>
      <input type="number" min="0" step="50" value="0" data-kind="${core.esc(k.id)}" aria-label="${core.esc(tr("{kind}, grams", { kind: k.label }))}"> g</label>`).join("");
  $("#guide-rules").innerHTML = [...g.rules, ...g.preparation].map((r) => `<li>${core.esc(r)}</li>`).join("");
})();
$("#btn-tank").addEventListener("click", async () => { if (await post("/api/twin/tank", {})) core.toast(tr("Tank refilled")); refresh(); });
$("#btn-barrel").addEventListener("click", async () => {
  if (await post("/api/twin/valve", { from: "RainBarrel", to: "TankPipes", open: true })) core.toast(tr("The barrel's tap is open"));
  refresh();
});
$("#btn-estop").addEventListener("click", async () => {
  await post("/api/twin/estop", { on: !!$("#btn-estop").dataset.on });
  refresh();
});

/* ----------------------------------------------------------------- loop -- */
// The home screen: Prepare, then Run, Observe, Control and back to Run, with the
// Chaos Monkey in the middle (loop_view.py on the server). The stage the twin is
// in now glows; each card shows its part and opens the screen with the detail.

const L = { scenarios: "", months: false, gui: false };
const MONTHS = () => Array.from({ length: 12 }, (_, i) => new Date(2026, i, 1).toLocaleString(I18N.lang, { month: "long" }));
const READY_SAY = { world: tr("the world"), webots: tr("Webots"), gantry: tr("the gantry connected"), cameras: tr("the cameras") };
const SEASON = { winter: tr("winter"), spring: tr("spring"), summer: tr("summer"), autumn: tr("autumn") };

function li(html, cls = "") { return `<li${cls ? ` class="${cls}"` : ""}>${html}</li>`; }

function drawLoop(v) {
  // which stage is now
  for (const st of ["prepare", "run", "observe", "control"]) $(`#stage-${st}`).classList.toggle("loop-card--now", v.now === st);
  $("#stage-chaos").classList.toggle("loop-card--now", (v.chaos.active || []).length > 0);
  $("#dot-loop").hidden = v.now !== "control";

  // Prepare: the choices (filled once), the checklist, what failed
  if (!L.months) {
    L.months = true;
    $("#sel-month").innerHTML = MONTHS().map((m, i) => `<option value="${i + 1}">${core.esc(m)}</option>`).join("");
    $("#sel-month").value = String(new Date().getMonth() + 1);
  }
  L.list = v.scenarios || [];
  const want = JSON.stringify((v.scenarios || []).map((x) => [x.id, x.unavailable]));
  if (L.scenarios !== want) {
    L.scenarios = want;
    const sel = $("#sel-scenario"), had = sel.value;
    const list = (v.scenarios || []).slice().sort((a, b) => (b.seasons - a.seasons) || a.title.localeCompare(b.title));
    sel.innerHTML = `<option value="">${core.esc(tr("No scenario: just run"))}</option>` + list.map((x) =>
      `<option value="${core.esc(x.id)}" ${x.unavailable ? "disabled" : ""}>${core.esc(x.title)}${x.unavailable ? " · " + core.esc(x.unavailable) : ""}</option>`).join("");
    sel.value = had || (list.find((x) => x.seasons && !x.unavailable) || {}).id || "";
    pickScenario(v);
  }
  const r = v.ready || {}, go = v.go || {};
  const starting = go.state === "running";
  $("#ready-steps").innerHTML = (r.steps || []).map((x) => li(core.esc(READY_SAY[x.id] || x.id),
    x.done ? "ready-step--done" : starting && r.next === x.id ? "ready-step--now" : "")).join("");
  $("#ready-steps").hidden = !starting && !r.running && go.state !== "failed" && !r.failed;
  const bad = go.state === "failed" ? go.error : r.failed;
  const msg = bad || r.slow || "";
  $("#ready-msg").hidden = !msg;
  $("#ready-msg").textContent = msg;
  $("#ready-msg").classList.toggle("bad", !!bad);
  $("#ready-log").hidden = !(msg && (r.log || []).length);
  $("#ready-log").textContent = (r.log || []).join("\n");
  $("#btn-go-nowindow").hidden = !(msg && $("#chk-window").checked);
  $("#ready-rendering").hidden = !(v.rendering && (starting || !r.running));
  $("#btn-go").disabled = starting;
  $("#btn-go").textContent = tr(starting ? "Starting…" : r.running ? "Begin this scenario" : "Prepare and start");
  $("#sel-source").disabled = $("#chk-window").disabled = r.running || starting;

  // Run: the clock, day or night, the season, the weather
  const run = v.run || {};
  $("#loop-sky").textContent = !r.running ? "" : run.night ? "☾" : "☀";
  $("#loop-sky").classList.toggle("loop-sky--night", !!run.night);
  $("#loop-clock").textContent = !r.running ? tr("not running") : [clock(run.clock), run.date && new Date(run.date).toLocaleDateString(I18N.lang, { day: "numeric", month: "long" }),
    run.season && SEASON[run.season]].filter(Boolean).join(" · ");
  $("#loop-weather").textContent = !r.running ? "" : [run.airC != null && `${run.airC} °C`, run.rainMmH ? tr("rain {mm} mm/h", { mm: run.rainMmH }) : tr("dry"),
    run.sunrise != null && tr("sun {from}–{to}", { from: hm(run.sunrise), to: hm(run.sunset) }), run.paused && tr("paused")].filter(Boolean).join(" · ");
  const f = $("#loop-factor");
  if (document.activeElement !== f && run.factor != null) f.value = String([0, 1, 10, 30, 120].reduce((a, b) => Math.abs(b - run.factor) < Math.abs(a - run.factor) ? b : a));
  f.disabled = !r.ready;

  // Observe: the readings at a glance, what needs a look, the scenario's step
  const c = (r.running && (v.observe || {}).count) || {};
  $("#loop-counts").innerHTML = !r.running ? "" : [["ok", c.ok], ["watch", c.watch], ["alarm", c.alarm]].map(([k, n]) =>
    `<span class="chip chip--${k}">${core.esc(tr(k))} ${n || 0}</span>`).join(" ");
  const obs = r.running ? v.observe || {} : {};
  $("#loop-attention").innerHTML = (obs.attention || []).map((n) => li(core.esc(n.text), n.level === "alert" ? "bad" : "")).join("");
  const ACT_SAY = { lid: tr("the lid"), wrap: tr("the wrapping"), tap: tr("the sump's tap opened"), bedding: tr("dry bedding added"),
    moisten: tr("water sprinkled"), hopper: tr("hopper filled"), tank: tr("tank refilled"), barrel: tr("the barrel's tap opened"), estop: tr("E-stop") };
  $("#loop-webots").innerHTML = (obs.inWebots || []).slice().reverse().map((a) => li(core.esc(tr("In Webots, {when}: {what}", { when: clock(a.clock), what: ACT_SAY[a.act] || a.act })), "note")).join("");
  const step = obs.step;
  $("#loop-step").hidden = !step;
  if (step) {
    $("#loop-step-title").textContent = `${step.title} · ${step.index + 1}/${step.total}: ${step.step.title}`;
    $("#loop-step-checks").innerHTML = (step.checks || []).map((k) => li(core.esc(k.say), k.ok ? "ok" : "")).join("");
    $("#btn-loop-next").hidden = !step.ready || step.last;
  }

  // Control: maintenance that is due, and what needs a person (nothing while the twin does not run)
  const ctl = r.running ? v.control || {} : {};
  $("#loop-maintenance").innerHTML = (ctl.maintenance || []).map((x) => li(`${core.esc(tr(x.label))} <span class="tag">${core.esc(tr(x.who === "person" ? "you" : "robot"))}</span>`
    + (x.overdueDays ? ` <span class="note">${core.esc(tr("{n} day(s) late", { n: x.overdueDays }))}</span>` : "")
    + (x.who === "person" ? ` <button data-loop-care="${core.esc(x.id)}">${core.esc(tr("Done"))}</button>` : ""))).join("")
    || li(core.esc(tr(r.running ? "nothing due" : "–")), "note");
  $("#loop-holds").innerHTML = (ctl.lockout ? `<span class="chip chip--alarm">${core.esc(tr("gantry locked out"))}</span> ` : "")
    + (ctl.holds || []).map((h) => `<span class="chip chip--watch">${core.esc(tr("{kind} on hold", { kind: job_(h) }))}</span>`).join(" ");
  $("#loop-issues").innerHTML = (ctl.issues || []).map((n) => li(core.esc(n.text) + (n.proposal
    ? `<span class="loop-fix">${core.esc(tr("Proposed: {what}", { what: n.proposal.title }))} <button class="primary" data-loop-job="${core.esc(n.proposal.id)}">${core.esc(tr("Confirm"))}</button></span>` : ""),
    n.level === "alert" ? "bad" : "")).join("");
  $("#loop-proposals").innerHTML = (ctl.proposals || []).map((p) => li(`${core.esc(p.title)} <button class="primary" data-loop-job="${core.esc(p.id)}">${core.esc(tr("Confirm"))}</button>`)).join("")
    + (ctl.proposalCount > 3 ? li(`<a href="#robot">${core.esc(tr("{n} more on the Robot screen", { n: ctl.proposalCount - 3 }))}</a>`, "note") : "")
    + (!(ctl.issues || []).length && !(ctl.proposals || []).length && r.running ? li(core.esc(tr("nothing waits for you")), "note") : "");

  // the Chaos Monkey
  const ch = v.chaos || {}, cs = $("#loop-chaos");
  if (document.activeElement !== cs) cs.value = String(ch.rate >= 4 ? 6 : ch.rate >= 1 ? 2 : 0);
  cs.disabled = $("#btn-chaos-now").disabled = !r.ready;
  $("#loop-chaos-active").innerHTML = (ch.active || []).map((d) => li(`<strong>${core.esc(d.title)}</strong> <span class="note">${core.esc(tr("{h} h left", { h: Math.round((d.leftMin || 0) / 6) / 10 }))}</span>`
    + (d.owes || []).map((o) => ` <span class="chip chip--${o.came ? "ok" : "watch"}">${o.came ? "✓" : "…"} ${core.esc(o.say || o.rule)}</span>`).join(""))).join("")
    || (r.running ? li(core.esc(tr(ch.rate ? "quiet for now" : "asleep")), "note") : "");
  translateDom($("#loop"));
}

const hm = (h) => `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.round((h % 1) * 60)).padStart(2, "0")}`;

function pickScenario() {
  const x = (L.list || []).find((s) => s.id === $("#sel-scenario").value);
  $("#lbl-month").hidden = !(x && x.seasons);
  $("#scenario-about-pick").textContent = x ? x.about : tr("The twin runs in mild weather without seasons; you can begin a scenario later.");
}

async function refreshLoop() {
  if (S.tab !== "loop") return;
  try { drawLoop(await core.api("GET", "/api/twin/loop")); } catch (_) { /* the main refresh says the server is gone */ }
}

// The ring behind the cards, as on the portal's home: a dotted circle through Run, Observe and Control, its arrows
// between them, and a line from Prepare into Run. Drawn from where the cards are; hidden on a narrow screen.
function ringLoop() {
  const box = $("#loop"), svg = $("#loop-ring");
  if (!box || box.offsetParent === null) return;
  const at = (id) => { const r = $(id).getBoundingClientRect(), b = box.getBoundingClientRect(); return { x: r.left - b.left + r.width / 2, y: r.top - b.top + r.height / 2, r }; };
  const c = at("#stage-chaos"), run = at("#stage-run"), obs = at("#stage-observe"), ctl = at("#stage-control"), prep = at("#stage-prepare");
  const narrow = Math.abs(run.x - ctl.x) > 4 || Math.abs(c.y - obs.y) > 40;
  svg.setAttribute("width", box.clientWidth); svg.setAttribute("height", box.clientHeight);
  if (narrow) { svg.innerHTML = ""; return; }
  const rx = Math.max(60, obs.x - c.x), ry = Math.max(60, (ctl.y - run.y) / 2);
  const arrow = (deg) => {
    const a = deg * Math.PI / 180, x = c.x + rx * Math.cos(a), y = c.y + ry * Math.sin(a);
    const tx = -rx * Math.sin(a), ty = ry * Math.cos(a), turn = Math.atan2(ty, tx) * 180 / Math.PI;
    return `<path d="M -8 -7 L 3 0 L -8 7" transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${turn.toFixed(1)})"/>`;
  };
  const px = prep.r.right - box.getBoundingClientRect().left;
  svg.innerHTML = `<ellipse class="loop__ellipse" cx="${c.x}" cy="${c.y}" rx="${rx}" ry="${ry}"/>`
    + `<g class="loop__arrows">${[-45, 45, 135, 225].map(arrow).join("")}</g>`
    + `<path class="loop__lead" d="M ${px} ${run.y} L ${run.r.left - box.getBoundingClientRect().left - 6} ${run.y}"/>`
    + `<path class="loop__arrows" d="M -8 -7 L 3 0 L -8 7" transform="translate(${run.r.left - box.getBoundingClientRect().left - 6} ${run.y})"/>`;
}
window.addEventListener("resize", ringLoop);
new ResizeObserver(() => ringLoop()).observe($("#loop"));

async function go(gui) {
  const month = $("#lbl-month").hidden ? undefined : Number($("#sel-month").value);
  const graph = $("#sel-source").value || undefined, scenario = $("#sel-scenario").value;
  try { localStorage.setItem("permtek5-twin-edge-window", gui ? "1" : ""); } catch (_) { /* private window */ }
  if (await post("/api/twin/go", { graph, gui, scenario, month })) core.toast(tr("Starting: the checklist shows how far it is"));
  refreshLoop();
}
$("#btn-go").addEventListener("click", () => go($("#chk-window").checked));
$("#btn-go-nowindow").addEventListener("click", () => { $("#chk-window").checked = false; go(false); });
$("#sel-scenario").addEventListener("change", pickScenario);
$("#loop-factor").addEventListener("change", async (e) => { await post("/api/twin/time", { factor: Number(e.target.value) }); refreshLoop(); });
$("#loop-chaos").addEventListener("change", async (e) => { await post("/api/twin/chaos", { rate: Number(e.target.value) }); refreshLoop(); });
$("#btn-chaos-now").addEventListener("click", async () => {
  const d = await post("/api/twin/chaos/now", {});
  if (d) core.toast(tr("The Chaos Monkey throws: {what}", { what: d.title }));
  refreshLoop();
});
$("#btn-loop-next").addEventListener("click", async () => { await post("/api/twin/scenario/next", {}); refreshLoop(); });
$("#loop").addEventListener("click", async (e) => {
  const care = e.target.dataset && e.target.dataset.loopCare, job = e.target.dataset && e.target.dataset.loopJob;
  if (!care && !job) return;
  e.target.disabled = true;
  if (care && await post("/api/twin/care/done", { id: care })) core.toast(tr("Noted as done"));
  if (job) await post("/api/twin/job", { proposal: job });
  refreshLoop();
  refresh();
});
setInterval(refreshLoop, 1500);

/* ---------------------------------------------------------------- shell -- */
// The rail's own buttons call core's (its header stays in the page, hidden).

const TABS = ["loop", "operator", "robot", "care", "sim", "film", "connections", "chat"];

function showTab(name) {
  const tab = TABS.includes(name) ? name : "loop";          // "#setup", the old home, lands on the loop too
  S.tab = tab;
  for (const t of TABS) $(`#tab-${t}`).hidden = t !== tab;
  if (tab === "loop") requestAnimationFrame(ringLoop);
  document.querySelectorAll(".app-rail__item.tab").forEach((a) => {
    const on = a.dataset.tab === tab;
    a.classList.toggle("app-rail__item--on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  views(S.ready);
}

function railOpen(open, remember = false) {
  $("#rail").classList.toggle("app-rail--closed", !open);
  $("#rail-toggle").setAttribute("aria-expanded", String(open));
  $("#rail-toggle").setAttribute("aria-label", open ? tr("Hide menu") : tr("Show menu"));
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
    box.innerHTML = `<span>${core.esc(tr("Approve this code as an admin of your site:"))}</span>
      <span class="link-code">${core.esc(link.user_code || "")}</span>
      <span><a href="${core.esc(url)}" target="_blank" rel="noopener noreferrer">${core.esc(tr("Open the approval page"))} ↗</a>
      <span class="note"> · ${core.esc(tr(link.status || "waiting for approval"))}</span></span>`;
  } else if (link.state === "failed") {
    box.hidden = false;
    box.innerHTML = `<span class="bad">${core.esc(tr("Linking failed: {why}", { why: link.error || "" }))}</span>`;
  } else if (linked.length) {
    box.hidden = false;
    box.innerHTML = linked.map(([name, p]) =>
      `<span><strong>${core.esc(name)}</strong> · ${core.esc(tr("linked to {site}", { site: (p.tenant && p.tenant.name) || p.site || tr("a site") }))}`
      + `${(p.spaces || []).length ? " · " + core.esc(tr("{n} group(s)", { n: p.spaces.length })) : ""}</span>`).join("")
      + `<span class="note">${core.esc(tr("Sync, then choose the site as the twin's source on the Loop screen."))}</span>`;
  } else {
    box.hidden = true;
  }
}

$("#btn-link").addEventListener("click", startLink);
$("#btn-sync").addEventListener("click", async () => { if (await post("/api/knowledge/sync", {})) core.toast(tr("Syncing the site's knowledge…")); });
refreshLink();
setInterval(refreshLink, 3000);

async function pollShell() {
  try {
    const info = await core.api("GET", "/api/agent");
    $("#badge-agent").hidden = !(info && info.status && info.status.connected);
  } catch (_) { $("#badge-agent").hidden = true; }
}

/* ------------------------------------------------------------- worm box -- */

// The box's sensors as they read (a dead one says so), the feeder's advice, and
// what only the simulation knows. An older server without them hides the panel.
function drawBox(t, ready) {
  const box = t.twin && t.twin.bed && t.twin.bed.wormBin;
  const has = !!(box && "weightKg" in box);
  $("#op-feeding").hidden = !has;
  for (const id of ["#btn-insulate", "#btn-bedding", "#btn-moisten", "#btn-lid", "#btn-tap"]) $(id).disabled = !ready || !has;
  if (!has) { $("#box-sim").textContent = ""; drawPopulation(t, null); return; }
  const f = t.feeder;
  $("#box-feeder").textContent = !f ? "" : tr("Feeder: {why}.", { why: f.why }) + (f.state === "known" ? " " + tr("It would feed {g} g now.", { g: f.grams }) : "");
  // the feeding guide: why the next feeding waits, and the week on record
  const guide = t.guide, week = guide && guide.week;
  $("#box-guide").textContent = !guide ? "" : tr("Feeding guide:") + " "
    + (!guide.load.ok ? `${guide.load.text} ` : "")
    + (!guide.feed.ok ? tr("no feeding now.") + ` ${guide.feed.text}${guide.feed.againInMin ? " " + tr("({h} h to go)", { h: Math.ceil(guide.feed.againInMin / 60) }) : ""} `
      : guide.load.ok ? tr("the next feeding is in order.") + " " : "")
    + tr("This week: {scraps} g of scraps in {n} feeding(s), {browns} g of dry bedding", { scraps: week.scrapsG, n: week.feedings, browns: week.brownsG })
    + (week.refusedLoads ? ", " + tr("{n} hopper load(s) refused", { n: week.refusedLoads }) : "") + "."
    + (guide.brownsDue ? " " + tr("Dry bedding due with the next feeding: {g} g.", { g: guide.brownsDue }) : "");
  // the compost harvest: which half rests, and what has gone back to the bed
  const h = box.harvest, cells = Object.values((t.twin.bed.cells) || {}).filter((c) => c.compostG > 0).length;
  $("#box-harvest").textContent = !h ? "" : tr("Compost:") + " "
    + (h.side === null ? tr("both halves are fed.") : h.ready ? tr("half {half} has rested {days} days: its castings are ready to go to the bed.", { half: h.side + 1, days: h.restDays })
      : tr("half {half} rests ({days} of {of} days); the worms are moving to the other half.", { half: h.side + 1, days: h.restDays, of: h.readyAfterDays })) + " "
    + (box.takenGrams ? tr("{g} g taken so far, onto {n} cell(s).", { g: box.takenGrams, n: cells }) : "");
  const sim = box.sim;
  $("#box-sim").textContent = !sim ? "" : tr("The box: worms {g} g, health {health} %, working at {work} %, eat {eat} g a day, {food} g uneaten, {castings} g castings",
      { g: sim.wormsG, health: Math.round(sim.health * 100), work: Math.round(sim.activity * 100), eat: sim.eatsPerDay, food: sim.foodG, castings: sim.castingsG })
    + (f && f.offPct !== undefined ? ". " + tr("The feeder's estimate is off by {pct} %.", { pct: f.offPct }) : ".")
    + " " + (box.wormsMayLeave ? tr("Worms may leave into the soil.") : tr("The box is closed."));
  drawPopulation(t, sim && sim.population);
  $("#btn-insulate").textContent = tr(box.insulated ? "Wrapping taken off" : "Box wrapped");
  $("#btn-insulate").dataset.on = box.insulated ? "" : "1";
  $("#btn-lid").textContent = tr(box.lidOpen ? "Lid closed" : "Lid left open");
  $("#btn-lid").dataset.open = box.lidOpen ? "" : "1";
}
// The worms by life stage, and a trend of the last 90 simulated days (kept in the page: a reload starts it again).
const POP = { days: [] };
function drawPopulation(t, p) {
  $("#box-pop").hidden = !p;
  if (!p) return;
  const env = (t.twin.bed && t.twin.bed.env) || {};
  const day = (env.day || 1) + (env.hour || 0) / 24;
  if (POP.days.length && day < POP.days[POP.days.length - 1][0]) POP.days = [];   // the simulation started again
  if (!POP.days.length || day - POP.days[POP.days.length - 1][0] >= 0.25) POP.days.push([day, p.wormsG, p.cocoons]);
  POP.days = POP.days.filter((d) => day - d[0] <= 90);
  const n = (v) => v >= 10000 ? `${Math.round(v / 1000)}k` : String(v);
  const w = t.worms;
  $("#box-pop-line").textContent = tr("Worms: {adults} adults, {young} young, {cocoons} cocoons", { adults: n(p.adults), young: n(p.young), cocoons: n(p.cocoons) })
    + (p.inBed ? ", " + tr("{n} out in the bed", { n: n(p.inBed) }) : "") + ` · ${p.wormsG} g, ${p.kgPerM2} kg/m²`
    + (p.crowding > 0 ? ", " + tr("crowded {pct} %", { pct: Math.round(p.crowding * 100) }) : "") + (p.nourish < 0.7 ? ", " + tr("hungry") : "")
    + (w ? ". " + tr("The feeder reckons about {kg} kg of worms", { kg: w.kg })
      + (w.fallPct !== null && w.fallPct !== undefined ? ", " + tr("{pct} % less than a week ago", { pct: w.fallPct }) : "") + "." : ".");
  const pts = POP.days, svg = $("#box-pop-trend");
  if (pts.length < 2) { svg.innerHTML = ""; return; }
  const x0 = pts[0][0], dx = Math.max(1, pts[pts.length - 1][0] - x0);
  const top = Math.max(...pts.map((d) => d[1]), 1);
  const line = pts.map((d) => `${(300 * (d[0] - x0) / dx).toFixed(1)},${(58 - 54 * d[1] / top).toFixed(1)}`).join(" ");
  svg.innerHTML = `<polyline points="${line}" fill="none" stroke="var(--app-accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>`
    + `<text x="2" y="10" class="box-pop-label">${top} g</text>`;
}
/* ------------------------------------------------------- operator view -- */

// A first look lands on the loop, the home screen. A #screen in the address wins.
function land() {
  if (S.landed) return;
  S.landed = true;
  if (!TABS.includes(location.hash.slice(1))) showTab("loop");
}

// The keeper's notices, alerts first, and what they hold back.
function drawAttention(t) {
  const kept = t.keeper || {}, list = (kept.notices || []).slice().sort((a, b) => (a.level === "alert" ? 0 : 1) - (b.level === "alert" ? 0 : 1));
  $("#twin-notices").innerHTML = list.map((n) => `<li class="notice notice--${n.level === "alert" ? "alarm" : "watch"}">`
    + `<span class="op-word">${core.esc(tr(n.level === "alert" ? "alarm" : "note"))}</span> ${core.esc(n.text)}</li>`).join("");
  const alerts = list.filter((n) => n.level === "alert").length;
  const notes = list.length - alerts, n = (k, one, many) => k ? tr(k > 1 ? many : one, { n: k }) : "";
  $("#attention-count").textContent = [n(alerts, "{n} alarm", "{n} alarms"), n(notes, "{n} note", "{n} notes")].filter(Boolean).join(" · ");
  $("#attention-none").hidden = list.length > 0;
  const holds = kept.holds || [];
  $("#attention-holds").hidden = !holds.length && !kept.lockout;
  $("#attention-holds").innerHTML = (kept.lockout ? `<span class="chip chip--alarm">${core.esc(tr("gantry locked out"))}</span> ` : "")
    + holds.map((h) => `<span class="chip">${core.esc(tr("{kind} on hold", { kind: job_(h) }))}</span>`).join(" ");
  $("#attention").classList.toggle("attention--alarm", alerts > 0 || !!kept.lockout);
}

// Every reading as a tile: its value, and how it stands as the keeper judges it (operator_view.py).
function drawOperator(t) {
  const op = t.operator, tiles = (op && op.tiles) || [];
  SENS.tiles = Object.fromEntries(tiles.map((x) => [x.key, x]));
  $("#op-empty").hidden = tiles.length > 0;
  const html = !tiles.length ? "" : op.groups.map((g) => {
    const mine = tiles.filter((x) => x.group === g.id);
    if (!mine.length) return "";
    return `<section class="app-panel op-group"><h2 class="app-panel__title">${core.esc(g.title)}</h2><div class="op-grid">`
      + mine.map((x) => {
        const v = x.value === null || x.value === undefined ? tr("no reading") : String(x.value);
        const why = x.why || "";
        return `<button type="button" class="op-tile op-tile--${x.state}" data-key="${core.esc(x.key)}" title="${core.esc(why || tr("the sensor and how it measures"))}">`
          + `<span class="op-label">${core.esc(x.label)}</span>`
          + `<span class="op-value${typeof x.value === "number" ? "" : " op-value--word"}">${core.esc(v)} <span class="op-unit">${core.esc(x.unit || "")}</span></span>`
          + `<span class="op-state"><span class="op-word">${core.esc(tr(x.state))}</span>${why ? ` · ${core.esc(why)}` : ""}</span></button>`;
      }).join("") + "</div></section>";
  }).join("");
  const el = $("#op-groups");
  if (S.opHtml !== html) {                      // redraw only on a change: a focused tile keeps its focus
    const focused = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.key;
    el.innerHTML = html;
    S.opHtml = html;
    if (focused) { const again = el.querySelector(`[data-key="${focused}"]`); if (again) again.focus(); }
  }
}

// A tile's details: the sensor behind it and how it measures (operator_view.SENSORS), with its reading now.
const SENS = { info: null, tiles: {} };
(async function loadSensors() { try { SENS.info = await core.api("GET", "/api/twin/sensors"); } catch (_) { /* an older server: no details */ } })();
$("#op-groups").addEventListener("click", (e) => {
  const tile = e.target.closest(".op-tile");
  if (!tile) return;
  const x = SENS.tiles[tile.dataset.key], info = SENS.info && SENS.info[tile.dataset.key];
  if (!x) return;
  const v = x.value === null || x.value === undefined ? tr("no reading") : `${x.value} ${x.unit || ""}`;
  const row = (k, html) => `<dt>${k}</dt><dd>${html}</dd>`;
  core.dialog(x.label[0].toUpperCase() + x.label.slice(1),
    `<p class="sensor-now op-tile--${x.state}"><strong>${core.esc(v)}</strong> · <span class="op-word">${core.esc(tr(x.state))}</span>`
    + `${x.why ? ` · ${core.esc(x.why)}` : ""}</p>`
    + (!info ? `<p class="note">${core.esc(tr("No details for this reading."))}</p>` : `<dl class="sensor-facts">`
      + row(tr("Sensor"), core.esc(info.sensor)) + row(tr("Where"), core.esc(info.where))
      + row(tr("How it measures"), core.esc(info.method)) + row(tr("How often"), core.esc(info.every))
      + row(tr("Limits"), info.limits.length ? `<ul>${info.limits.map((l) => `<li>${core.esc(l)}</li>`).join("")}</ul>` : core.esc(tr("none set")))
      + row(tr("In this twin"), core.esc(info.twin)) + "</dl>")
    + `<menu class="sensor-menu"><button value="close" class="primary">${core.esc(tr("Close"))}</button></menu>`);
});

// What only the simulation knows: the worms by life stage (drawPopulation), the box's insides, the soil life.
function drawTruth(t) {
  const bed = t.twin && t.twin.bed, life = bed && bed.sim && bed.sim.soilLife;
  $("#soil-life").textContent = typeof life === "number"
    ? tr("Soil life in the bed: {pct} %. An index of how good the bed has been for soil life lately, not a count.", { pct: Math.round(life * 100) }) : "";
  $("#truth-none").hidden = !!(t.running && bed);
}

// The strip in the header: the simulated clock, the job, and how many readings or notices are at alarm.
function drawStrip(t, ready) {
  const env = t.env, count = (t.operator && t.operator.count) || {}, kept = t.keeper || {};
  // the keeper's alerts are what an operator acts on; a reading at alarm without one (a dead sensor) still counts
  const alerts = (kept.notices || []).filter((n) => n.level === "alert").length, alarms = alerts || count.alarm || 0;
  const notes = (kept.notices || []).length - alerts;
  $("#strip-clock").textContent = env ? clock(env.clock) : t.running ? tr("starting…") : t.prepared ? tr("stopped") : "";
  const a = $("#strip-alerts");
  a.hidden = !ready || (!alarms && !notes);
  a.textContent = alarms ? "⚠ " + tr(alarms > 1 ? "{n} alarms" : "{n} alarm", { n: alarms }) : tr(notes > 1 ? "{n} notes" : "{n} note", { n: notes });
  a.classList.toggle("strip__alerts--alarm", alarms > 0);
  $("#dot-operator").hidden = !alarms;
  $("#dot-robot").hidden = !ready || !(t.proposals || []).length;
  $("#dot-care").hidden = !((t.care && t.care.tasks) || []).some((x) => x.due && x.who === "person");
}

// The simulated clock as the robot says it ("day 3 · 14:05"), in the person's language.
const clock = (text) => String(text || "").replace(/^day (\d+)/, (_, d) => tr("day {d}", { d }));

// The care calendar (maintenance.py): what is due now, by whom, and what comes next. An older server hides it.
const CARE_WHY = { never: "not done yet", rhythm: "its time has come", season: "its months have begun", brought: "the keeper brings it forward" };
function drawCare(t) {
  const care = t.care;
  $("#care-panel").hidden = !care;
  if (!care) return;
  $("#care-today").textContent = tr("simulated date {date}", { date: care.today });
  const due = care.tasks.filter((x) => x.due), next = care.tasks.filter((x) => !x.due && x.nextOn).sort((a, b) => a.nextOn.localeCompare(b.nextOn));
  $("#care-due").innerHTML = !due.length ? `<li class="note">${core.esc(tr("Nothing is due."))}</li>` : due.map((x) => `<li class="care-${x.who}">
      <span class="care-who">${core.esc(tr(x.who === "robot" ? "robot" : "you"))}</span> <strong>${core.esc(x.label)}</strong>
      <span class="note">· ${core.esc(tr(CARE_WHY[x.reason] || ""))}${x.overdueDays ? ", " + core.esc(tr("{n} day(s) late", { n: x.overdueDays })) : ""}</span>
      ${x.who === "person" ? `<button data-care="${core.esc(x.id)}">${core.esc(tr("Done"))}</button>` : `<span class="note"> · ${core.esc(tr("done when the robot's job is"))}</span>`}
      <div class="note">${core.esc(x.why)}</div></li>`).join("");
  $("#care-next").innerHTML = next.slice(0, 12).map((x) => `<li class="care-${x.who}"><span class="care-who">${core.esc(tr(x.who === "robot" ? "robot" : "you"))}</span>
      ${core.esc(x.label)} <span class="note">· ${x.nextOn}</span></li>`).join("");
}
// What the guild is learning (PermTek-5's Learn view): the measures due, with this edge's reading for each where it has one.
function drawLearning(t) {
  const cycles = (t.learning || []).filter((c) => c.due.length);
  $("#learning-panel").hidden = !(t.learning || []).length;
  $("#learning-due").innerHTML = !cycles.length ? `<li class="note">${core.esc(tr("No measure is due."))}</li>`
    : cycles.map((c) => c.due.map((d) => `<li><span class="care-who">${core.esc(d.code || "")}</span> <strong>${core.esc(d.label)}</strong>
      <span class="note">· ${core.esc(c.name)} · ${core.esc(tr("due since {date}", { date: d.dueOn }))}${d.lastOn ? " · " + core.esc(tr("last {date}", { date: d.lastOn })) : ""}</span>
      ${d.now !== null && d.now !== undefined ? `<div class="note">${core.esc(tr("this edge reads {value} now ({key})", { value: d.now, key: d.reading }))}</div>` : ""}</li>`).join("")).join("");
}

// The year: the seasonal tasks month by month, from the care guide the edge holds. Once; an older server hides it.
(async function loadCareYear() {
  let g;
  try { g = await core.api("GET", "/api/twin/care/guide"); } catch (_) { return; }
  const MONTHS = Array.from({ length: 12 }, (_, i) => new Date(2026, i, 1).toLocaleString(I18N.lang, { month: "short" }));
  const now = new Date().getMonth() + 1;
  const en = (x) => (x && (x[I18N.lang] || x.en || Object.values(x)[0])) || "";     // the guide carries EN, DE and FR
  const seasonal = g.tasks.filter((x) => (x.months || []).length);
  $("#care-about").textContent = en(g.about);
  $("#care-year").innerHTML = `<thead><tr><th scope="col">${core.esc(tr("Task"))}</th>${MONTHS.map((m, i) => `<th scope="col" class="${i + 1 === now ? "now" : ""}">${m}</th>`).join("")}</tr></thead><tbody>`
    + seasonal.map((x) => `<tr><th scope="row" title="${core.esc(en(x.why))}">${core.esc(en(x.label))} <span class="note">· ${core.esc(tr(x.who === "robot" ? "robot" : "you"))}</span></th>`
      + MONTHS.map((_, i) => `<td class="${i + 1 === now ? "now" : ""}">${x.months.includes(i + 1) ? `<span class="care-mark care-mark--${x.who}"></span>` : ""}</td>`).join("") + "</tr>").join("")
    + "</tbody>";
})();
$("#care-due").addEventListener("click", async (e) => {
  const id = e.target.dataset && e.target.dataset.care;
  if (!id) return;
  if (await post("/api/twin/care/done", { id })) core.toast(tr("Noted as done"));
  refresh();
});
const boxDo = async (body, said) => { if (await post("/api/twin/box", body)) core.toast(tr(said)); refresh(); };
$("#btn-insulate").addEventListener("click", (e) => boxDo({ do: "insulate", on: !!e.target.dataset.on }, "Noted"));
$("#btn-bedding").addEventListener("click", () => boxDo({ do: "bedding", grams: 500 }, "500 g of dry bedding"));
$("#btn-moisten").addEventListener("click", () => boxDo({ do: "moisten", ml: 500 }, "Half a litre sprinkled"));
$("#btn-tap").addEventListener("click", () => boxDo({ do: "tap" }, "The sump ran into the tank"));
$("#btn-lid").addEventListener("click", (e) => boxDo({ do: "lid", open: !!e.target.dataset.open }, "Noted"));

/* ---------------------------------------------------------------- chaos -- */

// The simulated weather, and the trouble you can throw at the guild. The keeper's
// reactions show as notices at the top. An older server without them hides the panel.
const CH = { loaded: false };

function drawEnv(t) {
  const env = t.env, panel = $("#chaos-panel");
  if (!CH.loaded) return;
  panel.hidden = false;
  $("#env-line").textContent = env
    ? `${clock(env.clock)} · ` + tr("air {c} °C · wind {w} m/s · rain {r} mm/h", { c: env.airC, w: env.windMs, r: env.rainMmH })
    : tr("The weather runs with the simulation.");
  const kept = t.keeper || {};
  const lines = (env ? env.disturbances : []).map((d) => tr("{what}, {min} min left", { what: d.title, min: d.leftMin }));
  if (kept.lockout) lines.push(tr("the gantry is locked out"));
  if ((kept.holds || []).length) lines.push(tr("on hold: {kinds}", { kinds: kept.holds.map(job_).join(", ") }));
  if (kept.queued) lines.push(tr("{n} result(s) wait for the network", { n: kept.queued }));
  $("#env-active").innerHTML = lines.map((l) => `<li>${core.esc(l)}</li>`).join("");
  for (const [sel, v] of [["#sel-factor", env && env.factor], ["#sel-chaos", env && env.chaosRate]]) {
    const el = $(sel);
    el.disabled = !env;
    if (env && document.activeElement !== el && [...el.options].some((o) => Number(o.value) === Number(v))) el.value = String(v);
  }
  $("#btn-calm").disabled = !env;
  $("#chaos-list").querySelectorAll("button").forEach((b) => { b.disabled = !env; });
}

async function loadChaos() {
  let v;
  try { v = await core.api("GET", "/api/twin/chaos"); } catch (_) { return; }
  CH.loaded = true;
  $("#chaos-list").innerHTML = v.disturbances.map((d) => `
    <button class="scenario-item" data-id="${core.esc(d.id)}" disabled>
      <strong>${core.esc(d.title)}</strong><span>${core.esc(d.about)}</span></button>`).join("");
  $("#chaos-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    if (await post("/api/twin/disturb", { id: b.dataset.id })) core.toast(tr("Sent"));
    refresh();
  }));
}
$("#btn-calm").addEventListener("click", async () => { await post("/api/twin/calm"); refresh(); });
$("#sel-factor").addEventListener("change", (e) => post("/api/twin/time", { factor: Number(e.target.value) }));
$("#sel-chaos").addEventListener("change", (e) => post("/api/twin/chaos", { rate: Number(e.target.value) }));
loadChaos();

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
  const score = $("#scenario-score");                    // a chaos run ends with a score
  score.hidden = !!run || !v.score;
  if (v.score) score.textContent = tr("{title}: {passed} of {of} reactions as expected", { title: v.score.title, passed: v.score.passed, of: v.score.of })
    + (v.score.failed.length ? ". " + tr("Missed: {what}", { what: v.score.failed.join(", ") }) : "");
  $("#scenario-pick").hidden = !!run;
  $("#scenario-run").hidden = !run;
  if (!run) {
    $("#scenario-list").innerHTML = v.scenarios.map((s) => `
      <button class="scenario-item" data-id="${core.esc(s.id)}" ${v.running && !s.unavailable ? "" : "disabled"}>
        <strong>${core.esc(s.title)}</strong>
        <span class="note">${core.esc(tr("{n} steps", { n: s.steps }))}${s.unavailable ? " · " + core.esc(s.unavailable) : v.running ? "" : " · " + core.esc(tr("start the simulation first"))}</span>
      </button>`).join("");
    $("#scenario-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
      $("#chk-scenario-auto").checked = false;
      await post("/api/twin/scenario", { id: b.dataset.id });
      refreshScenarios();
    }));
    return;
  }
  $("#scenario-title").textContent = run.title;
  $("#scenario-count").textContent = tr("step {i} of {n}", { i: run.index + 1, n: run.total });
  $("#scenario-step").textContent = run.step.title;
  $("#scenario-about").hidden = run.index !== 0;          // what the scenario is about, on its first step
  $("#scenario-about").textContent = run.about;
  $("#scenario-why").textContent = run.step.why;
  $("#scenario-checks").innerHTML = run.checks.map((c) => `<li class="${c.ok ? "ok" : ""}">${core.esc(c.say)}</li>`).join("");
  const doBtn = $("#btn-scenario-do"), nextBtn = $("#btn-scenario-next");
  doBtn.hidden = run.step.type === "none";
  doBtn.textContent = run.step.label || "";
  // done and as expected: nothing more to do. Done but not as expected (something else changed the
  // bed meanwhile): the action can be repeated, or the step skipped.
  doBtn.disabled = SC.acting || run.busy || (run.did && run.ready);
  if (run.did && !run.ready && !run.busy && run.step.label) doBtn.textContent = tr("{action} again", { action: run.step.label });
  $("#btn-scenario-skip").hidden = run.ready || run.last;
  nextBtn.textContent = tr(run.last ? "Finish" : "Next step");
  nextBtn.disabled = !run.ready || SC.acting;
  if ($("#chk-scenario-auto").checked && !SC.acting) {             // your one confirmation for the whole scenario
    if (!run.did && run.step.type !== "none" && !run.busy) scenarioDo();
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
  if (r && r.finished) core.toast(tr("Scenario finished"));
  refreshScenarios();
}

$("#btn-scenario-do").addEventListener("click", scenarioDo);
$("#btn-scenario-next").addEventListener("click", scenarioNext);
$("#btn-scenario-skip").addEventListener("click", async () => { await post("/api/twin/scenario/next", { skip: true }); refreshScenarios(); });
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
  const q = $("#sel-film-quality");
  if (!q.options.length && f.quality) {
    q.innerHTML = Object.entries(f.quality).map(([k, v]) => `<option value="${k}">${core.esc(v)}</option>`).join("");
    q.value = "good";
  }
  $("#btn-film-cancel").hidden = !job;
  $("#film-progress").hidden = !job;
  if (job) $("#film-progress").value = job.progress || 0;
  const last = f.job && !job ? f.job : null, shotOf = (j) => (j && j.spec && j.spec.shot) || "";
  const title = (id) => ((f.shots || []).find((x) => x.id === id) || {}).title || id;
  const phase = (p) => String(p || "").replace(/^frame (\d+) of (\d+)$/, (_, i, n) => tr("frame {i} of {n}", { i, n })).replace(/^the still$/, tr("the still")).replace(/^the whole film$/, tr("the whole film"));
  $("#film-status").textContent = job ? tr("rendering “{title}” · {phase}", { title: title(shotOf(job)), phase: phase(job.phase) })
      + ((f.waiting || []).length ? " · " + tr("{n} more waiting", { n: f.waiting.length }) : "")
    : last && last.state === "failed" ? tr("the last film failed: {why}", { why: last.error })
    : last && last.state === "cancelled" ? tr("the last film was cancelled")
    : !f.prepared ? tr("prepare a world first") : "";
  // one card per shot: its film (or still), and a button; redrawn only when something changed
  const busy = f.missing.length > 0 || !f.prepared, waiting = f.waiting || [];
  const want = JSON.stringify([busy, shotOf(job), waiting, f.whole && f.whole.modified, (f.shots || []).map((x) => [x.id, x.film && x.film.modified, x.poster && x.poster.modified])]);
  if (want === F.shown) return;
  F.shown = want;
  // the whole film: the shots one after another, made anew whenever one is rendered
  const bed = (f.shots || []).find((x) => x.id === "bed") || {};
  const whole = f.whole ? `<section class="app-panel film-card film-card--whole"><h3>${core.esc(tr("The whole film"))}</h3>
      <video class="film" controls loop muted playsinline preload="metadata" ${bed.poster ? `poster="/api/twin/film/${bed.poster.name}?t=${bed.poster.modified}"` : ""} src="/api/twin/film/${f.whole.name}?t=${f.whole.modified}"></video>
      <div class="hero-controls"><span class="note">${core.esc(tr("the shots one after another, made anew whenever one of them is rendered"))}</span></div></section>` : "";
  $("#film-shots").innerHTML = whole + (f.shots || []).map((x) => {
    const when = x.film ? tr("rendered {when}", { when: new Date(x.film.modified * 1000).toLocaleString(I18N.lang) }) : tr(x.poster ? "only the still so far" : "not rendered yet");
    const media = x.film
      ? `<video class="film" controls loop muted playsinline preload="metadata" ${x.poster ? `poster="/api/twin/film/${x.poster.name}?t=${x.poster.modified}"` : ""} src="/api/twin/film/${x.film.name}?t=${x.film.modified}"></video>`
      : x.poster ? `<img class="film" alt="${core.esc(tr("A still of {what}", { what: x.title }))}" src="/api/twin/film/${x.poster.name}?t=${x.poster.modified}">`
      : `<div class="film film--none">${core.esc(tr("no film yet"))}</div>`;
    const doing = shotOf(job) === x.id ? tr("rendering now") : waiting.includes(x.id) ? tr("waits its turn") : "";
    return `<section class="app-panel film-card"><h3>${core.esc(x.title)}${doing ? ` <span class="tag">${core.esc(doing)}</span>` : ""}</h3>${media}
      <div class="hero-controls"><button data-shot="${x.id}" class="${x.film ? "" : "primary"}" ${busy || doing ? "disabled" : ""}>${core.esc(tr(x.film ? "Render again" : "Render"))}</button>
      <span class="note">${when}</span></div></section>`;
  }).join("");
}
$("#film-shots").addEventListener("click", async (e) => {
  const shot = e.target.dataset && e.target.dataset.shot;
  if (!shot) return;
  e.target.disabled = true;
  if (await post("/api/twin/film", { shot, quality: $("#sel-film-quality").value })) core.toast(tr("Rendering: it takes a while"));
  F.shown = "";
  refreshFilm();
});
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
if (document.body.dataset.version) $("#rail-about-label").textContent = `${tr("About")} · v${document.body.dataset.version}`;
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
refreshLoop();
setInterval(refresh, 800);
