#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
app.py — browser front end for engine.py and the simulation (sim.py).

    permtek5-twin-edge-web                       # http://127.0.0.1:8772
    permtek5-twin-edge-web --port 9000
    python3 -m permtek5_twin_edge.app            # the same, from a checkout

The page, stop, restart, update and the About box come from core.server; this
module only adds the tool's own routes, and those only move data between the
browser, engine and the simulation's gateway.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request

from flask import Response, abort, jsonify, render_template, request, send_file

from . import engine as engine
from . import cloudsite
from . import disturbances
from . import feeder
from . import maintenance
from . import operator_view
from . import feeding_guide
from . import film
from . import habitat_store as store
from . import i18n
from .i18n import _
from . import instance
from . import keeper
from . import loop_view
from . import scenarios
from . import sim
from .core import server
from .core import workspace as ws

app = server.create_app(__name__)

from .edge import routes as edge_routes  # edge layer
from .edge import cloud as edge_cloud
edge_routes.install(app)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
# The page and its script change together. Flask caches a template for the
# life of the process but serves static files from disk, so an edited checkout
# would pair an old page with a new script (the page then stops at "loading").
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


@app.before_request
def _language() -> None:
    """Every request speaks the person's language: their choice on the page, else their browser's."""
    i18n.use(i18n.pick(request.cookies.get("lang"), request.headers.get("Accept-Language")))


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object body")
    return data


# ------------------------------------------------------------------ page ----

@app.get("/")
def index() -> str:
    return render_template("index.html", i18n=_i18n_boot())


def _i18n_boot() -> dict:
    """The page's language, the languages there are, and the catalog: in the page, so it draws translated at once."""
    lang = i18n.current()
    return {"lang": lang, "langs": [{"id": l, "name": i18n.NAMES[l]} for l in i18n.LANGS], "catalog": i18n.catalog(lang)}


@app.get("/api/i18n")
def i18n_catalog():
    """The page's language and its catalog (English text -> translation), and the languages there are."""
    return jsonify(_i18n_boot())


@app.get("/how")
def how():
    """How the twin edge works: an Archify drawing (docs/how-it-works.architecture.json)."""
    return app.send_static_file("how.html")


@app.get("/model")
def model():
    """The PermTek-5 data model: the cloud's Archify drawing, copied by scripts/sync-ontology.sh."""
    return app.send_static_file("model.html")


@app.get("/favicon.ico")
def favicon():
    """Browsers ask for this by name whatever the page links to."""
    return app.send_static_file("favicon.svg")


# ------------------------------------------------------------------ twin ----
#
# The page shows what the twin does and proposes jobs; nothing moves until a
# person confirms one (POST /api/twin/job). The simulation runs on the ROS
# side; these routes only pass data between the browser, engine and sim.

_keeper_memory: dict = {}
_feeder_memory: dict = {}
_worm_memory: dict = {}         # the feeder's estimate of the worms, a value a day
_care_guide = maintenance.load()   # the WormBed5 care calendar: PermTek-5's data, followed here
_care_jobs: dict = {}           # job id -> the care task it does, until the job is done
_guide = feeding_guide.load()       # the WormBed5 feeding guide: PermTek-5's data, enforced here
_diary: list = []                   # what was loaded, fed and given as bedding in this simulation (simulated minutes)
_guide_seen = {"now": 0.0, "feedN": 0, "load": None}
_weeds: dict = {"scan": None, "stamp": None, "found": [], "when": ""}   # what the last look over the bed found
_keeper_lock = threading.Lock()     # the page and a scenario ask at the same moment: one of them writes the log
_offline = {"until": 0.0, "queued": 0}


def _is_offline() -> bool:
    return time.time() < _offline["until"]


def _keep(cfg, bed, out, offline: bool) -> dict:
    kept = keeper.evaluate(bed, _keeper_memory, offline=offline)
    if cfg and bed:
        keeper.log_changes(out, kept, _keeper_memory, ((cfg.get("guild") or {}).get("id") or cfg["bed"]["id"]),
                           (bed.get("env") or {}).get("clock", ""))
    if bed and "weightKg" in (bed.get("wormBin") or {}):   # the feeder: how much to feed, from the box's weight (feeder.py)
        feeder.observe(_feeder_memory, bed)
        advice = feeder.advise(_feeder_memory, bed)
        truth = (bed["wormBin"].get("sim") or {}).get("eatsPerDay")
        if advice.get("eatsPerDay") and truth:    # only a simulation can say how far off the feeder is
            advice["offPct"] = round(100.0 * abs(advice["eatsPerDay"] - truth) / truth)
        kept["feeder"] = advice
        est = feeder.worms(_worm_memory, advice, feeder._now(bed), ((cfg or {}).get("wormBin") or {}).get("areaM2"))
        if est:
            kept["worms"] = est
        keeper.population(kept, _keeper_memory, est)
    if bed and cfg and (cfg.get("wormBin") or {}).get("cols") and "lastFeed" in (bed.get("wormBin") or {}):
        _follow_guide(cfg, bed, out, kept)
    return kept


def _note(out, entry: dict) -> None:
    """One line in the feeding diary: in memory for the guide's week, and in diary.jsonl for the record."""
    _diary.append(entry)
    try:
        with open(Path(out) / "diary.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({**entry, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _follow_guide(cfg, bed, out, kept: dict) -> None:
    """Hold what is in the hopper and the next feeding against the feeding guide (feeding_guide.py)."""
    env, box, hopper = bed.get("env") or {}, bed["wormBin"], bed.get("hopper") or {}
    now = (env.get("day", 1) - 1) * 1440.0 + env.get("hour", 0.0) * 60.0
    if now + 1.0 < _guide_seen["now"]:              # the simulation started again: a new diary
        _diary.clear()
        _guide_seen.update(feedN=0, load=None)
    _guide_seen["now"] = now
    last = box.get("lastFeed")
    if last and last["n"] != _guide_seen["feedN"]:  # the robot fed: into the diary
        _guide_seen["feedN"] = last["n"]
        _note(out, {"t": last.get("atMin", now), "what": "feed", "grams": last["grams"], "spot": last["spot"], "mix": last.get("mix") or {}})
    elif not last:
        _guide_seen["feedN"] = 0
    mix = hopper.get("mix") or {}
    load = feeding_guide.check_load(_guide, mix, _diary, now, lang=i18n.current())
    sig = json.dumps(mix, sort_keys=True) if mix else None
    if sig is None:
        _guide_seen["load"] = None
    elif _guide_seen["load"] is None:               # a fresh load: its verdict goes into the diary once
        _guide_seen["load"] = "seen"
        _note(out, {"t": now, "what": "load", "mix": mix, "verdict": "ok" if load["ok"] else load["rule"]})
    advice = kept.get("feeder") or {}
    care = box.get("lastCare")
    if care and care["n"] != _guide_seen.get("careN") and care.get("what") == "bedding":   # the robot added bedding
        _note(out, {"t": care.get("atMin", now), "what": "bedding", "grams": care["amount"], "by": "robot"})
    _guide_seen["careN"] = care["n"] if care else 0
    spots = int(cfg["wormBin"].get("spots", 1))
    spot = engine.next_spot(cfg, bed)
    grams = advice.get("grams") if advice else engine.PORTION_G
    feed = feeding_guide.check_feed(_guide, float(grams or 0), spot, _diary, now, core_c=box.get("tempC"),
                                    eats_per_day=advice.get("eatsPerDay"), uneaten_g=advice.get("uneatenG"), spots=spots,
                                    lang=i18n.current())
    if advice and advice.get("grams"):
        advice["grams"] = min(int(advice["grams"]), int(feed.get("maxGrams", advice["grams"])))
    kept["guide"] = {"load": load, "feed": feed, "week": feeding_guide.week_line(_guide, _diary, now),
                     "brownsDue": feeding_guide.browns_due(_guide, float(grams or 0), _diary, now)}
    if not load["ok"]:
        keeper.add(kept, "guide-load", _("Feeding guide: {text} Feeding is on hold until the hopper is emptied.", text=load["text"]), ["feed"], "alert")
    if not feed["ok"]:
        if feed["rule"] == "browns":                # the person can do something about this one
            keeper.add(kept, "guide-browns", _("Feeding guide: {text} Feeding is on hold; add dry bedding.", text=feed["text"]), ["feed"])
        else:                                       # rest, spot, cold, amount: nothing to do but wait; no notice
            kept["holds"] = sorted(set(kept["holds"]) | {"feed"})


def _follow_weeds(cfg, bed, twin: dict, kept: dict) -> None:
    """Keep what the last look over the bed found: stamped ones go off the list; the rest are a person's."""
    done = [j for j in (twin.get("history") or []) if j.get("state") == "done"]
    scan = next((j for j in reversed(done) if j["kind"] == "scan"), None)
    if scan and scan["id"] != _weeds["scan"]:
        _weeds.update(scan=scan["id"], stamp=None, when=(bed.get("env") or {}).get("clock", ""),
                      found=engine.sort_weeds(cfg, bed, (scan.get("result") or {}).get("weeds") or []))
    elif not scan and _weeds["scan"]:
        _weeds.update(scan=None, stamp=None, found=[], when="")          # the simulation started again
    stamp = next((j for j in reversed(done) if j["kind"] == "stamp"), None)
    if stamp and scan and stamp["id"] != _weeds["stamp"] and stamp.get("started", 0) > scan.get("started", 0):
        _weeds["stamp"] = stamp["id"]
        pressed = {(round(p[0], 3), round(p[1], 3)) for p in (stamp.get("spec") or {}).get("places") or []}
        _weeds["found"] = [w for w in _weeds["found"] if (round(w["x"], 3), round(w["y"], 3)) not in pressed]
    by_hand = [w for w in _weeds["found"] if w["do"] == "hand"]
    kept["weeds"] = {"found": _weeds["found"], "when": _weeds["when"], "small": sum(1 for w in _weeds["found"] if w["do"] == "stamp"),
                     "byHand": len(by_hand), "scanned": bool(_weeds["scan"])}
    if by_hand:
        big, close = sum(1 for w in by_hand if w["why"] == "large"), sum(1 for w in by_hand if w["why"] == "close")
        keeper.add(kept, "weeds", _("{n} weed(s) are for your hands: ", n=len(by_hand))
                   + ", ".join(p for p in (_("{n} too big for the stamp", n=big) if big else "", _("{n} too close to a plant", n=close) if close else "") if p)
                   + _(". Only weeds without seed and without creeping roots go to the worm box."))


def _event(event: dict) -> dict:
    """Send what a person did to the twin; dry bedding given by hand also goes into the feeding diary.
    {"op": "care", "id"} is a care task done (it stays on this edge: the care record)."""
    if event.get("op") == "care":
        return _care_done(str(event.get("id") or ""))
    out = sim.gateway("/event", event)
    if event.get("op") == "box" and event.get("do") == "bedding":
        _note(engine.workspace(), {"t": _guide_seen["now"], "what": "bedding", "grams": float(event.get("grams", 500))})
    return out


def _reading(bed, key):
    """An edge reading by its key (a measure's perma:reading): wormBin.sumpL, water.barrelL, ..."""
    if not bed or not key:
        return None
    if key == "water.barrelL":
        return next((x.get("litres") for x in (bed.get("water") or {}).get("stores") or [] if x.get("kind") == "RainBarrel"), None)
    v = bed
    for part in key.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _learning(cfg, bed):
    """What the guild is learning (PermTek-5 ADR 0026): its cycles that are measuring, and their measures due today
    (the cloud's rule: never checked, or as long ago as their rhythm), with what this edge reads for them now."""
    import datetime as _dt
    today = _dt.date.today()
    lang = i18n.current()
    out = []
    for c in (cfg or {}).get("learning") or []:
        if c.get("phase") not in ("DoPhase", "CheckPhase"):
            continue
        due = []
        for m in c.get("measures") or []:
            last = m.get("lastOn")
            since = _dt.date.fromisoformat(last) + _dt.timedelta(days=int(m.get("everyDays") or 30)) if last else \
                _dt.date.fromisoformat(c.get("startedOn") or today.isoformat())
            if since <= today:
                label = m.get("label") or {}
                due.append({"code": m.get("code"), "label": label.get(lang) or label.get("en") or "", "dueOn": since.isoformat(),
                            "lastOn": last, "reading": m.get("reading") or None, "now": _reading(bed, m.get("reading"))})
        name = c.get("name") or {}
        out.append({"id": c["id"], "name": name.get(lang) or name.get("en") or "", "phase": c["phase"], "due": due})
    return out


def _env_view(env):
    """The weather for the page, its disturbances named in the person's language."""
    if not env:
        return env
    return {**env, "disturbances": [{**d, "title": _(d.get("title", ""))} for d in env.get("disturbances") or []]}


_window_seen = {"n": 0}


def _follow_window(bed, out) -> None:
    """What a person did in Webots' robot window (ros2/.../robot_window.py) reached the twin already; this edge
    keeps the same records it keeps for the page's buttons: dry bedding by hand goes into the feeding diary."""
    acts = bed.get("acts") or []
    if acts and acts[-1]["n"] < _window_seen["n"]:            # the simulation started again: its count too
        _window_seen["n"] = 0
    for a in acts:
        if a["n"] <= _window_seen["n"]:
            continue
        _window_seen["n"] = a["n"]
        e = a.get("event") or {}
        if e.get("op") == "box" and e.get("do") == "bedding":
            _note(out, {"t": _guide_seen["now"], "what": "bedding", "grams": float(e.get("grams", 500))})


def _twin_state() -> dict:
    status = sim.status()
    cfg = engine.config()
    twin = status.get("twin") or {}
    bed = twin.get("bed") if status["running"] else None
    out = engine.workspace()
    src = store.source(out)
    offline = _is_offline()
    # the keeper: the rules that hold jobs back, lock the gantry out, and tell the person what to do
    with _keeper_lock:
        kept = _keep(cfg, bed, out, offline)
    if cfg and bed:
        _follow_weeds(cfg, bed, twin, kept)
        _follow_care(cfg, bed, twin, out, kept)
        _follow_window(bed, out)
    recorded = []
    if cfg and src.get("graph") and twin.get("history"):
        # What the gantry sowed goes into the habitat it was prepared from; the next sync takes it to the cloud.
        recorded = store.record_results(out, cfg["bed"]["id"], twin["history"])
        _offline["queued"] += len(recorded) if offline else 0
    if cfg and src.get("graph") and not offline and (recorded or _offline["queued"]) and edge_cloud.linked_profiles():
        try:
            edge_routes.JOBS.submit("knowledge-sync", {"graph": src["graph"]})
            _offline["queued"] = 0
        except Exception:                         # busy: the person syncs from Connections
            pass
    return {**status, **engine.summary(cfg), "config": cfg, "source": src.get("graph") or "citydemo",
            "instance": instance.describe(),
            "keeper": {**kept, "queued": _offline["queued"]}, "notices": [n["text"] for n in kept["notices"]],
            "feeder": kept.get("feeder"), "worms": kept.get("worms"), "guide": kept.get("guide"), "weeds": kept.get("weeds"),
            "care": kept.get("care"),
            "env": _env_view((bed or {}).get("env")),
            "operator": _operator(bed, kept["active"]),
            "learning": _learning(cfg, bed),
            "habitats": store.habitats(), "recorded": recorded,
            "proposals": (engine.proposals(cfg, bed, kept["holds"], kept.get("feeder"), kept.get("guide"), kept["active"], kept.get("weeds"),
                                           [t["id"] for t in (kept.get("care") or {}).get("tasks") or [] if t.get("due")])
                          if not kept["lockout"] else [])}


def _operator(bed, active) -> dict:
    """The Operator screen: every reading as a tile, judged as the keeper judges it (operator_view.py)."""
    tiles = operator_view.tiles(bed, active)
    return {"groups": [{"id": g, "title": _(title)} for g, title in operator_view.GROUPS], "tiles": tiles,
            "count": operator_view.summary(tiles)}


@app.get("/api/twin/sensors")
def twin_sensors():
    """What stands behind each Operator tile: the device, where, how it measures, how often, its limits, the twin's way."""
    return jsonify({key: operator_view.sensor(key) for key in operator_view.SENSORS})


@app.get("/api/twin")
def twin_status():
    return jsonify(_twin_state())


@app.post("/api/twin/prepare")
def twin_prepare():
    """Write the world for a habitat: CityDemo, or Turtle sent in the body."""
    if sim.running():
        return jsonify({"errors": [_("stop the simulation before preparing another world")]}), 409
    data = body()
    graph = data.get("graph") or None
    try:
        ttl = store.ttl_of(graph) if graph else (data.get("ttl") or engine.habitat_ttl(None))
        out = engine.prepare(ttl)           # the guild this instance is for; ValueError names the guilds there are
    except ValueError as exc:
        return jsonify({"errors": str(exc).split("; ")}), 400
    store.remember_source(engine.workspace(), graph)
    return jsonify(out)


@app.post("/api/twin/start")
def twin_start():
    if engine.config() is None:
        return jsonify({"errors": [_("prepare a world first")]}), 400
    try:
        sim.start(engine.workspace(), gui=bool(body().get("gui")))
    except RuntimeError as exc:
        return jsonify({"errors": str(exc).split("; ")}), 400
    return jsonify(_twin_state())


@app.post("/api/twin/stop")
def twin_stop():
    scenarios.stop()
    sim.stop()
    return jsonify(_twin_state())


@app.post("/api/twin/job")
def twin_job():
    """Confirm one job: a proposal by its id, or a full spec (plant, water, probe, photo)."""
    data = body()
    spec = data.get("spec")
    if data.get("proposal"):
        spec = next((p["spec"] for p in _twin_state()["proposals"] if p["id"] == data["proposal"]), None)
        if spec is None:
            return jsonify({"errors": [_("that proposal is no longer current")]}), 409
    if not isinstance(spec, dict):
        return jsonify({"errors": ["name a proposal or give a job spec"]}), 400
    kept = _twin_state()["keeper"]
    guide = (kept.get("guide") or {}).get("feed") or {}
    if spec.get("kind") == "feed" and guide.get("ok") is False:
        return jsonify({"errors": [_("Feeding guide: {text}", text=guide["text"])]}), 409
    hold = "moisten" if spec.get("what") in ("moisten", "mist") else str(spec.get("kind"))    # watering the worm box
    why_not = keeper.refuses(kept, str(spec.get("kind"))) or keeper.refuses(kept, hold)
    if why_not:                                   # a hold or a lockout: the keeper's word stands over a click
        return jsonify({"errors": [why_not]}), 409
    try:
        return jsonify(_run(spec))
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


def _run(spec: dict) -> dict:
    """Carry out a confirmed job. Most are the gantry's; the tap and the resting half are switches, not movements.
    A job that does a care task marks it done when it is done (a switch at once)."""
    task = maintenance.task_for_job(_care_guide, spec)
    if spec.get("kind") == "tap":
        out = {**_event({"op": "box", "do": "tap"}), "done": "the tap was opened"}
        if task:
            _care_done(task)
        return out
    if spec.get("kind") == "valve":                   # an edge valve: open it; it closes itself when done (water.py)
        valve = {"op": "valve", "from": spec.get("from"), "to": spec.get("to"), "open": True}
        if spec.get("litres"):                         # a dose: the mist line
            valve["litres"] = float(spec["litres"])
        out = {**_event(valve), "done": "the valve was opened"}
        if spec.get("from") == "WormBoxSump":
            task = task or maintenance.task_for_job(_care_guide, {"kind": "tap"})
        if task:
            _care_done(task)
        return out
    if spec.get("kind") == "rest":
        return {**_event({"op": "box", "do": "rest", "side": spec.get("side")}), "done": "one half of the box now rests"}
    out = sim.gateway("/job", spec)
    if task and isinstance(out, dict) and out.get("id"):
        _care_jobs[out["id"]] = task
    return out


def _care_today() -> Optional[str]:
    bed = (sim.status().get("twin") or {}).get("bed") if sim.running() else None
    record = maintenance.read(engine.workspace())
    return maintenance.sim_date(record, (bed or {}).get("env") or {}).isoformat() if record else None


def _care_done(task_id: str) -> dict:
    if not any(t["id"] == task_id for t in _care_guide["tasks"]):
        raise ValueError("no such care task: " + task_id)
    today = _care_today()
    if today:
        maintenance.done(engine.workspace(), task_id, today)
    return {"ok": True, "done": task_id, "on": today}


def _follow_care(cfg, bed, twin, out, kept: dict) -> None:
    """The care calendar of a WormBed5 guild: its record, the robot's finished jobs, and what is due."""
    if not (cfg.get("wormBin") or {}).get("cols"):
        return
    env = bed.get("env") or {}
    record = maintenance.read(out)
    if not record or env.get("day", 1) < record.get("seenDay", 0):      # a new record, or the simulation started again
        record = maintenance.begin(out, _care_guide)
    if record.get("seenDay") != env.get("day", 1):
        record["seenDay"] = env.get("day", 1)
        maintenance._write(out, record)
    today = maintenance.sim_date(record, env)
    for job in twin.get("history") or []:
        task = _care_jobs.get(job.get("id"))
        if task and job.get("state") in ("done", "failed", "cancelled"):
            _care_jobs.pop(job["id"], None)
            if job["state"] == "done":
                record = maintenance.done(out, task, today)
    tasks = maintenance.due(_care_guide, record, today, kept.get("active"), lang=i18n.current())
    # the mist line's routine is a look first: when the probe reads the box moist enough, the look is the task done
    moisture = (bed.get("wormBin") or {}).get("moisture")
    mist = next((t for t in tasks if t["id"] == "mist" and t["due"] and t["reason"] != "brought"), None)
    if mist and isinstance(moisture, (int, float)) and moisture >= engine.MIST_BELOW_PCT:
        record = maintenance.done(out, "mist", today)
        tasks = maintenance.due(_care_guide, record, today, kept.get("active"), lang=i18n.current())
    kept["care"] = {"today": today.isoformat(), "tasks": tasks,
                    "due": {"robot": sum(1 for t in tasks if t["due"] and t["who"] == "robot"),
                            "person": sum(1 for t in tasks if t["due"] and t["who"] == "person")}}


@app.get("/api/twin/care/guide")
def care_guide():
    """The care calendar itself (synced from PermTek-5), for the Care screen's year."""
    return jsonify(maintenance.load())


@app.post("/api/twin/care/done")
def care_done():
    """A person did a care task: {"id"}. It is kept in this edge's care record (care.json in the guild's folder)."""
    try:
        return jsonify(_care_done(str(body().get("id") or "")))
    except ValueError as exc:
        return jsonify({"errors": [_(str(exc))]}), 400


@app.post("/api/twin/hopper")
def twin_hopper():
    """A person filled the hopper, and says with what: {"mix": {kind: grams}} (the feeding guide's kinds),
    or just {"grams"} of vegetable scraps. {"grams": 0} empties it. {"browns": grams} fills the second
    compartment with dry bedding. The guide's verdict comes with the twin's state."""
    try:
        if "browns" in body():                    # the second compartment: dry bedding for the robot to add
            return jsonify(sim.gateway("/event", {"op": "hopper", "browns": max(0.0, min(float(body()["browns"]), 3000.0))}))
        mix = body().get("mix")
        if isinstance(mix, dict):
            mix = {str(k): max(0.0, float(g)) for k, g in mix.items() if float(g) > 0}
            return jsonify(sim.gateway("/event", {"op": "hopper", "mix": mix, "grams": min(sum(mix.values()), 5000.0)}))
        grams = max(0.0, min(float(body().get("grams", 1000)), 5000.0))
        return jsonify(sim.gateway("/event", {"op": "hopper", "grams": grams}))
    except (TypeError, ValueError):
        return jsonify({"errors": ["grams are numbers"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


@app.post("/api/twin/valve")
def twin_valve():
    """A person opened (or closed) a tap of the guild's water: {"id"} or the kinds it joins {"from", "to"}, and "open"."""
    data = body()
    event = {"op": "valve", "open": bool(data.get("open", True)),
             **({"id": str(data["id"])} if data.get("id") else {"from": str(data.get("from") or ""), "to": str(data.get("to") or "")})}
    try:
        return jsonify(_event(event))
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


@app.post("/api/twin/tank")
def twin_tank():
    """A person refilled the tank: to the brim, or to the litres given."""
    cfg = engine.config() or {}
    full = float((cfg.get("tank") or {}).get("litres") or 0)
    try:
        litres = float(body().get("litres", full))
        return jsonify(sim.gateway("/event", {"op": "tank", "litres": max(0.0, min(litres, full))}))
    except (TypeError, ValueError):
        return jsonify({"errors": ["litres is a number"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


@app.get("/api/twin/guide")
def twin_guide():
    """The feeding guide for the page: the kinds a hopper load is made of, and the rules in words."""
    g, r, x = _guide, _guide["rules"], _guide["ruleTexts"]

    def fill(text, **values):
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text

    l = i18n.current()
    say = lambda texts: texts.get(l) or texts["en"]       # noqa: E731  the guide carries its own EN, DE and FR
    return jsonify({"about": say(g["about"]), "classes": {k: say(v) for k, v in g["classes"].items()},
                    "kinds": [{"id": k, "label": say(v["label"]), "class": v["class"], "weeklySharePct": v.get("weeklySharePct")}
                              for k, v in g["kinds"].items()],
                    "rules": [say(x["browns"]), say(x["amount"]), fill(say(x["spot"]), g=r["maxGramsPerSpot"]),
                              fill(say(x["rest"]), h=r["minHoursBetweenFeedings"]), fill(say(x["cold"]), c=r["minCoreC"])],
                    "preparation": [say(p) for p in g["preparation"]]})


BOX_DOES = {"insulate": ("on", bool, True), "bedding": ("grams", float, 500.0), "moisten": ("ml", float, 500.0),
            "takeout": ("grams", float, 1000.0), "dump": ("grams", float, 1000.0), "lid": ("open", bool, True), "tap": ("on", bool, True)}


@app.post("/api/twin/box")
def twin_box():
    """What a person did at the worm box: {"do": "insulate", "on"} | {"do": "bedding", "grams"} |
    {"do": "moisten", "ml"} | {"do": "takeout", "grams"} | {"do": "dump", "grams"} | {"do": "lid", "open"} | {"do": "tap"} (drain the sump)."""
    do = str(body().get("do") or "")
    if do not in BOX_DOES:
        return jsonify({"errors": ["do is one of " + ", ".join(BOX_DOES)]}), 400
    key, kind, default = BOX_DOES[do]
    try:
        return jsonify(_event({"op": "box", "do": do, key: kind(body().get(key, default))}))
    except (TypeError, ValueError):
        return jsonify({"errors": [f"{key} is a number"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


# ----------------------------------------------------------------- chaos ----
#
# Chaos monkeys (disturbances.py): trouble laid over the simulated weather and
# devices, so the keeper has something to react to. The simulation's clock can
# run faster than the wall clock.

@app.get("/api/twin/chaos")
def chaos_catalogue():
    return jsonify({"disturbances": disturbances.listing(), "offline": _is_offline()})


def _factor() -> float:
    env = ((sim.status().get("twin") or {}).get("bed") or {}).get("env") or {}
    return float(env.get("factor") or 1.0)


def _disturb(key: str, minutes=None) -> None:
    d = disturbances.get(key)
    if d.get("app"):                              # network loss is this app's, not the simulation's
        _offline["until"] = time.time() + float(minutes or d["minutes"]) / max(_factor(), 0.1)   # simulated minutes
        return
    if d.get("event"):                            # something that happens at the bed, not weather
        sim.gateway("/event", d["event"])
        return
    sim.gateway("/event", disturbances.event(key, minutes))


def _calm() -> None:
    _offline["until"] = 0.0
    sim.gateway("/event", {"op": "calm"})


@app.post("/api/twin/disturb")
def twin_disturb():
    try:
        _disturb(str(body().get("id") or ""), body().get("minutes"))
    except ValueError as exc:
        return jsonify({"errors": [_(str(exc))]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409
    return jsonify({"ok": True})


@app.post("/api/twin/calm")
def twin_calm():
    try:
        _calm()
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409
    return jsonify({"ok": True})


@app.post("/api/twin/time")
def twin_time():
    """How many simulated minutes pass in a real second (1: a day in 24 minutes; 0: paused)."""
    try:
        return jsonify(sim.gateway("/event", {"op": "time", "factor": max(0.0, min(float(body().get("factor", 1)), 600.0))}))
    except (TypeError, ValueError):
        return jsonify({"errors": ["factor is a number"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


@app.post("/api/twin/chaos")
def twin_chaos():
    """Random disturbances, so many per simulated day; 0 turns them off."""
    try:
        return jsonify(sim.gateway("/event", disturbances.chaos_event(float(body().get("rate", 0)))))
    except (TypeError, ValueError):
        return jsonify({"errors": ["rate is a number"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


@app.post("/api/twin/cancel")
def twin_cancel():
    try:
        return jsonify(sim.gateway("/cancel", {}))
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


@app.post("/api/twin/estop")
def twin_estop():
    try:
        return jsonify(sim.gateway("/estop", {"on": bool(body().get("on", True))}))
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409


# ------------------------------------------------------------------ loop ----
#
# The home screen: Prepare, then Run, Observe, Control and back to Run, with the
# Chaos Monkey in the middle (loop_view.py). "Prepare and start" does the whole
# start in its own thread (the job queue may be busy with a film for hours):
# prepare the world, start the twin, wait until the gantry and its cameras are
# there, then begin the scenario with its run profile and month.

_go: dict = {"state": "idle", "phase": "", "error": None, "started": None}


def _go_run(graph, gui: bool, scenario_id: str, month) -> None:
    def phase(p):
        _go.update(phase=p)
    try:
        if not sim.running():
            phase("world")
            ttl = store.ttl_of(graph) if graph else engine.habitat_ttl(None)
            engine.prepare(ttl)
            store.remember_source(engine.workspace(), graph)
            phase("webots")
            sim.start(engine.workspace(), gui=gui)
        phase("gantry")
        if not sim.wait_ready(180.0):
            ready = loop_view.readiness(_twin_state(), sim.log_tail(40), time.time())
            raise RuntimeError(ready.get("failed") or ready.get("slow") or _("the gantry did not connect"))
        if scenario_id:
            phase("scenario")
            _scenario_begin(scenario_id, month)
        _go.update(state="done", phase="ready")
    except (RuntimeError, ValueError) as exc:
        _go.update(state="failed", error=_(str(exc)))


@app.post("/api/twin/go")
def twin_go():
    """Prepare and start: {"graph", "gui", "scenario", "month"}; follow it on /api/twin/loop."""
    if _go["state"] == "running":
        return jsonify({"errors": [_("the twin is already starting")]}), 409
    data = body()
    _go.update(state="running", phase="", error=None, started=time.time(),
               scenario=str(data.get("scenario") or ""), month=data.get("month"))
    threading.Thread(target=_go_run, args=(data.get("graph") or None, bool(data.get("gui")), str(data.get("scenario") or ""),
                                           data.get("month")), name="go", daemon=True).start()
    return jsonify(_go), 202


@app.get("/api/twin/loop")
def twin_loop():
    t = _twin_state()
    ready = loop_view.readiness(t, sim.log_tail(40), time.time())
    view = _scenario_view()
    return jsonify({**loop_view.loop(t, view, disturbances.CATALOGUE, ready), "ready": ready, "go": _go,
                    "scenarios": view["scenarios"], "rendering": any(j["kind"] == "film" and j["state"] == "running" for j in edge_routes.JOBS.list()),
                    "source": t.get("source"), "habitats": t.get("habitats"), "pool": disturbances.RANDOM_POOL})


@app.post("/api/twin/chaos/now")
def chaos_now():
    """The Chaos Monkey throws one at random from its pool."""
    key = random.choice(disturbances.RANDOM_POOL)
    try:
        _disturb(key)
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409
    return jsonify({"id": key, "title": _(disturbances.CATALOGUE[key]["title"])})


# ------------------------------------------------------------- scenarios ----
#
# Guided walkthroughs (scenarios.py, scenarios/*.json). Starting one and doing
# a step are a person's clicks; a step's robot job is the same proposal the
# page lists, sent the same way.

def _scenario_view() -> dict:
    t = _twin_state()
    cfg = t.get("config")
    twin = t.get("twin") or {}
    return {"running": bool(t["running"] and twin.get("gantry")),
            "scenarios": [{"id": k, "title": _(s["title"]), "about": _(s["about"]), "steps": len(s["steps"]),
                           "seasons": bool((s.get("run") or {}).get("seasons")), "startMonth": (s.get("run") or {}).get("startMonth"),
                           "unavailable": _(scenarios.available(s, cfg) or "") or None} for k, s in scenarios.load_all().items()],
            "run": scenarios.state(twin, t["proposals"], t.get("notices") or [], t.get("keeper")),
            "score": _translated_score(scenarios.last_score())}


def _translated_score(score):
    return score and {**score, "title": _(score["title"]), "failed": [_(t) for t in score.get("failed", [])]}


@app.get("/api/twin/scenarios")
def scenario_list():
    return jsonify(_scenario_view())


@app.post("/api/twin/scenario")
def scenario_start():
    if not sim.running():
        return jsonify({"errors": [_("start the simulation first")]}), 409
    try:
        _scenario_begin(str(body().get("id") or ""), body().get("month"))
    except ValueError as exc:
        return jsonify({"errors": [_(str(exc))]}), 400
    return jsonify(_scenario_view())


def _scenario_begin(scenario_id: str, month=None) -> dict:
    """Start a scenario and set the twin up for it: a fresh box when it asks, and its run profile (clock, random
    trouble, the seasons from a starting month). A guided scenario without one runs in the mild, seasonless weather
    it was written for. Raises ValueError (no such scenario, or the bed cannot run it)."""
    s = scenarios.start(scenario_id, engine.config())
    run = s.get("run") or {}
    if s.get("fresh") or run.get("fresh"):       # a scenario about the worm box starts from a settled box and calm weather
        for event in ({"op": "calm"}, {"op": "time", "factor": 1}, {"op": "box", "do": "reset"}):
            sim.gateway("/event", event)
        _feeder_memory.clear()
        _worm_memory.clear()
        _diary.clear()
        _guide_seen.update(feedN=0, load=None, careN=0)
        _offline["until"] = 0.0
    # the seasons: today's simulated day becomes the 1st of the month asked for; the care calendar keeps the same date
    month = month or run.get("startMonth")
    day = int((((sim.status().get("twin") or {}).get("bed") or {}).get("env") or {}).get("day") or 1)
    if run.get("seasons") and month:
        first = dt.date(dt.date.today().year, max(1, min(12, int(month))), 1)
        start = first - dt.timedelta(days=day - 1)
        sim.gateway("/event", {"op": "season", "start": start.isoformat()})
        maintenance.begin(engine.workspace(), _care_guide, start)
    else:
        sim.gateway("/event", {"op": "season", "start": None})
        if s.get("fresh") or run.get("fresh"):
            maintenance.begin(engine.workspace(), _care_guide)
    if "factor" in run:
        sim.gateway("/event", {"op": "time", "factor": float(run["factor"])})
    if "chaos" in run:
        sim.gateway("/event", disturbances.chaos_event(float(run["chaos"])))
    return s


@app.post("/api/twin/scenario/do")
def scenario_do():
    """Carry out the current step's one action."""
    t = _twin_state()
    try:
        act = scenarios.action(t["proposals"])
        if act.get("calm"):
            _calm()
        if "disturb" in act:
            _disturb(act["disturb"], act.get("minutes"))
        if "factor" in act:
            sim.gateway("/event", {"op": "time", "factor": float(act["factor"])})
        for event in act.get("events", []):
            if event.get("op") == "tank" and "litres" not in event:           # "refill": to the brim
                event = {**event, "litres": float(((t.get("config") or {}).get("tank") or {}).get("litres") or 0)}
            _event(event)
        if "estop" in act:
            sim.gateway("/estop", {"on": act["estop"]})
        if "job" in act:
            _run(act["job"])
    except ValueError as exc:
        return jsonify({"errors": [_(str(exc))]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [_(str(exc))]}), 409
    scenarios.mark_done()
    return jsonify(_scenario_view())


@app.post("/api/twin/scenario/next")
def scenario_next():
    view = _scenario_view()
    run = view["run"]
    if run is None:
        return jsonify({"errors": [_("no scenario is running")]}), 409
    if not run["ready"] and not body().get("skip"):
        return jsonify({"errors": [_("this step is not complete yet")]}), 409
    finished = not scenarios.advance(ok=bool(run["ready"]))
    return jsonify({**_scenario_view(), "finished": finished})


@app.post("/api/twin/scenario/stop")
def scenario_stop():
    scenarios.stop()
    return jsonify(_scenario_view())


# ------------------------------------------------------------------ link ----

@app.get("/api/twin/cloud")
def twin_cloud():
    """Where a PermTek-5 site's edge API is: ?site=stage | prod | https://… (cloudsite.py).
    The page then links through the edge layer's own /api/cloud/link."""
    try:
        return jsonify(cloudsite.discover(request.args.get("site") or "stage"))
    except cloudsite.NotFound as exc:
        return jsonify({"errors": [_(str(exc))]}), 404


# ------------------------------------------------------------------ film ----
#
# Films of the prepared guild, made with Blender in its own environment
# (film.py, assets/demo.py, assets/wormlife.py): the bed, the worm box close
# up, a flight into it, and the three as one whole film. A background job with progress, one at a time.

def _film_out():
    return engine.workspace() / "demo"


def _film_job(spec, progress, cancelled):
    return film.render(engine.workspace() / "bed.json", _film_out(), spec.get("shot", "bed"), spec.get("quality", "good"),
                       progress, cancelled, adults=spec.get("adults"))


def _adults():
    """The simulated adult worms now, so the film shows about as many as the box holds; None when not running."""
    try:
        bed = (sim.status().get("twin") or {}).get("bed") or {}
        return ((bed.get("wormBin") or {}).get("sim") or {}).get("population", {}).get("adults")
    except Exception:
        return None


edge_routes.JOBS.register("film", _film_job)


@app.get("/api/twin/film")
def film_status():
    films = [j for j in edge_routes.JOBS.list() if j["kind"] == "film"]
    waiting = [j for j in films if j["state"] == "queued"]
    job = next((j for j in films if j["state"] == "running"), None) or (films[0] if films else None)
    return jsonify({**film.status(_film_out()), "prepared": engine.config() is not None, "job": job,
                    "waiting": [j["spec"].get("shot") for j in waiting]})


@app.post("/api/twin/film")
def film_start():
    if engine.config() is None:
        return jsonify({"errors": [_("prepare a world first: the film shows the prepared bed")]}), 400
    bad = film.missing()
    if bad:
        return jsonify({"errors": bad}), 400
    shot, quality = str(body().get("shot") or "bed"), str(body().get("quality") or "good")
    if shot not in film.SHOTS or quality not in film.QUALITY:
        return jsonify({"errors": [f"shot is one of {', '.join(film.SHOTS)}; quality one of {', '.join(film.QUALITY)}"]}), 400
    if any(j["kind"] == "film" and j["state"] in ("queued", "running") and j["spec"].get("shot") == shot for j in edge_routes.JOBS.list()):
        return jsonify({"errors": [_("this film is already being rendered or waits its turn")]}), 409
    try:
        return jsonify(edge_routes.JOBS.submit("film", {"shot": shot, "quality": quality, "adults": _adults()})), 202
    except Exception as exc:                      # the same film is already queued or running
        return jsonify({"errors": [_(str(exc))]}), 409


@app.get("/api/twin/film/<name>")
def film_file(name: str):
    if name not in {f for shot in film.SHOTS for f in film.files(shot).values()} | {film.WHOLE}:
        abort(404)
    path = _film_out() / name
    if not path.exists():
        abort(404)
    return send_file(path, conditional=True, max_age=0)      # conditional: the player seeks with Range


@app.get("/api/twin/camera/<name>.<ext>")
def twin_camera(name: str, ext: str):
    """A robot view, passed through from the gateway: <name>.jpg is one picture, <name>.mjpg a live stream."""
    if name not in ("tool", "overview") or ext not in ("jpg", "mjpg"):
        abort(404)
    try:
        upstream = urllib.request.urlopen(sim.gateway_url(f"/camera/{name}.{ext}"), timeout=5)
    except (urllib.error.URLError, OSError):
        abort(503, "the simulation sends no pictures")

    def chunks():
        try:
            while True:
                data = upstream.read1(65536)
                if not data:
                    break
                yield data
        finally:
            upstream.close()

    return Response(chunks(), mimetype=upstream.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ main ----

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="permtek5-twin-edge-web",
                                 description=__doc__.strip().splitlines()[0])
    server.add_server_args(ap)
    ap.add_argument("--dir", default=ws.FOLDERS[ws.USER].name,
                    help="your own folder: generated worlds and run data go to its twin/ (default: ./mydocs)")
    ap.add_argument("--guild", default=None,
                    help="the Perma5Guild this edge runs, by name (default: the habitat's first). One edge per guild: "
                         "start another with its own --port")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    ws.configure(user_dir=args.dir)
    instance.configure(guild=args.guild, port=args.port)
    taken = instance.claim(engine.workspace())
    if taken:
        print(f"This guild's edge is already running on port {taken}. One edge per guild: "
              f"name another guild with --guild, and give it its own --port.", file=sys.stderr)
        return 2
    lines = [f"  {f['label'].lower():<13} {f['path']}" for f in ws.folders()]
    edge_routes.start_background()  # edge layer
    sim.stop_leftover(engine.workspace())      # a simulation an earlier run of this app left behind
    try:
        return server.serve(app, args, lines=lines)
    finally:
        sim.stop()                     # never leave Webots running behind a stopped page


if __name__ == "__main__":
    raise SystemExit(main())
