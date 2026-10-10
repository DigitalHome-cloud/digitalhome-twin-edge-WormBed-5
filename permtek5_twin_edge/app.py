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
import json
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
from . import feeding_guide
from . import film
from . import habitat_store as store
from . import instance
from . import keeper
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


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object body")
    return data


# ------------------------------------------------------------------ page ----

@app.get("/")
def index() -> str:
    return render_template("index.html")


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
    load = feeding_guide.check_load(_guide, mix, _diary, now)
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
                                    eats_per_day=advice.get("eatsPerDay"), uneaten_g=advice.get("uneatenG"), spots=spots)
    if advice and advice.get("grams"):
        advice["grams"] = min(int(advice["grams"]), int(feed.get("maxGrams", advice["grams"])))
    kept["guide"] = {"load": load, "feed": feed, "week": feeding_guide.week_line(_guide, _diary, now),
                     "brownsDue": feeding_guide.browns_due(_guide, float(grams or 0), _diary, now)}
    if not load["ok"]:
        keeper.add(kept, "guide-load", f'Feeding guide: {load["text"]} Feeding is on hold until the hopper is emptied.', ["feed"], "alert")
    if not feed["ok"]:
        if feed["rule"] == "browns":                # the person can do something about this one
            keeper.add(kept, "guide-browns", f'Feeding guide: {feed["text"]} Feeding is on hold; add dry bedding.', ["feed"])
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
        keeper.add(kept, "weeds", f"{len(by_hand)} weed(s) are for your hands: "
                   + ", ".join(p for p in (f"{big} too big for the stamp" if big else "", f"{close} too close to a plant" if close else "") if p)
                   + ". Only weeds without seed and without creeping roots go to the worm box.")


def _event(event: dict) -> dict:
    """Send what a person did to the twin; dry bedding given by hand also goes into the feeding diary."""
    out = sim.gateway("/event", event)
    if event.get("op") == "box" and event.get("do") == "bedding":
        _note(engine.workspace(), {"t": _guide_seen["now"], "what": "bedding", "grams": float(event.get("grams", 500))})
    return out


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
            "feeder": kept.get("feeder"), "guide": kept.get("guide"), "weeds": kept.get("weeds"),
            "env": (bed or {}).get("env"),
            "habitats": store.habitats(), "recorded": recorded,
            "proposals": (engine.proposals(cfg, bed, kept["holds"], kept.get("feeder"), kept.get("guide"), kept["active"], kept.get("weeds"))
                          if not kept["lockout"] else [])}


@app.get("/api/twin")
def twin_status():
    return jsonify(_twin_state())


@app.post("/api/twin/prepare")
def twin_prepare():
    """Write the world for a habitat: CityDemo, or Turtle sent in the body."""
    if sim.running():
        return jsonify({"errors": ["stop the simulation before preparing another world"]}), 409
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
        return jsonify({"errors": ["prepare a world first"]}), 400
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
            return jsonify({"errors": ["that proposal is no longer current"]}), 409
    if not isinstance(spec, dict):
        return jsonify({"errors": ["name a proposal or give a job spec"]}), 400
    kept = _twin_state()["keeper"]
    guide = (kept.get("guide") or {}).get("feed") or {}
    if spec.get("kind") == "feed" and guide.get("ok") is False:
        return jsonify({"errors": [f'Feeding guide: {guide["text"]}']}), 409
    why_not = keeper.refuses(kept, str(spec.get("kind")))
    if why_not:                                   # a hold or a lockout: the keeper's word stands over a click
        return jsonify({"errors": [why_not]}), 409
    try:
        return jsonify(_run(spec))
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409


def _run(spec: dict) -> dict:
    """Carry out a confirmed job. Most are the gantry's; the tap and the resting half are switches, not movements."""
    if spec.get("kind") == "tap":
        return {**_event({"op": "box", "do": "tap"}), "done": "the tap was opened"}
    if spec.get("kind") == "rest":
        return {**_event({"op": "box", "do": "rest", "side": spec.get("side")}), "done": "one half of the box now rests"}
    return sim.gateway("/job", spec)


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
        return jsonify({"errors": [str(exc)]}), 409


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
        return jsonify({"errors": [str(exc)]}), 409


@app.get("/api/twin/guide")
def twin_guide():
    """The feeding guide for the page: the kinds a hopper load is made of, and the rules in words."""
    g, r, x = _guide, _guide["rules"], _guide["ruleTexts"]

    def fill(text, **values):
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text

    return jsonify({"about": g["about"]["en"], "classes": {k: v["en"] for k, v in g["classes"].items()},
                    "kinds": [{"id": k, "label": v["label"]["en"], "class": v["class"], "weeklySharePct": v.get("weeklySharePct")}
                              for k, v in g["kinds"].items()],
                    "rules": [x["browns"]["en"], x["amount"]["en"], fill(x["spot"]["en"], g=r["maxGramsPerSpot"]),
                              fill(x["rest"]["en"], h=r["minHoursBetweenFeedings"]), fill(x["cold"]["en"], c=r["minCoreC"])],
                    "preparation": [p["en"] for p in g["preparation"]]})


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
        return jsonify({"errors": [str(exc)]}), 409


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
        return jsonify({"errors": [str(exc)]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409
    return jsonify({"ok": True})


@app.post("/api/twin/calm")
def twin_calm():
    try:
        _calm()
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409
    return jsonify({"ok": True})


@app.post("/api/twin/time")
def twin_time():
    """How many simulated minutes pass in a real second (1: a day in 24 minutes)."""
    try:
        return jsonify(sim.gateway("/event", {"op": "time", "factor": max(0.1, min(float(body().get("factor", 1)), 600.0))}))
    except (TypeError, ValueError):
        return jsonify({"errors": ["factor is a number"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409


@app.post("/api/twin/chaos")
def twin_chaos():
    """Random disturbances, so many per simulated day; 0 turns them off."""
    try:
        return jsonify(sim.gateway("/event", disturbances.chaos_event(float(body().get("rate", 0)))))
    except (TypeError, ValueError):
        return jsonify({"errors": ["rate is a number"]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409


@app.post("/api/twin/cancel")
def twin_cancel():
    try:
        return jsonify(sim.gateway("/cancel", {}))
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409


@app.post("/api/twin/estop")
def twin_estop():
    try:
        return jsonify(sim.gateway("/estop", {"on": bool(body().get("on", True))}))
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409


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
            "scenarios": [{"id": k, "title": s["title"], "about": s["about"], "steps": len(s["steps"]),
                           "unavailable": scenarios.available(s, cfg)} for k, s in scenarios.load_all().items()],
            "run": scenarios.state(twin, t["proposals"], t.get("notices") or [], t.get("keeper")),
            "score": scenarios.last_score()}


@app.get("/api/twin/scenarios")
def scenario_list():
    return jsonify(_scenario_view())


@app.post("/api/twin/scenario")
def scenario_start():
    if not sim.running():
        return jsonify({"errors": ["start the simulation first"]}), 409
    try:
        fresh = scenarios.start(str(body().get("id") or ""), engine.config()).get("fresh")
    except ValueError as exc:
        return jsonify({"errors": [str(exc)]}), 400
    if fresh:                                     # a scenario about the worm box starts from a settled box and calm weather
        for event in ({"op": "calm"}, {"op": "time", "factor": 1}, {"op": "box", "do": "reset"}):
            sim.gateway("/event", event)
        _feeder_memory.clear()
        _diary.clear()
        _guide_seen.update(feedN=0, load=None, careN=0)
        _offline["until"] = 0.0
    return jsonify(_scenario_view())


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
        return jsonify({"errors": [str(exc)]}), 400
    except RuntimeError as exc:
        return jsonify({"errors": [str(exc)]}), 409
    scenarios.mark_done()
    return jsonify(_scenario_view())


@app.post("/api/twin/scenario/next")
def scenario_next():
    view = _scenario_view()
    run = view["run"]
    if run is None:
        return jsonify({"errors": ["no scenario is running"]}), 409
    if not run["ready"] and not body().get("skip"):
        return jsonify({"errors": ["this step is not complete yet"]}), 409
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
        return jsonify({"errors": [str(exc)]}), 404


# ------------------------------------------------------------------ film ----
#
# A rendered turntable of the prepared bed, made with Blender in its own
# environment (film.py, assets/demo.py). A background job with progress.

def _film_out():
    return engine.workspace() / "demo"


def _film_job(spec, progress, cancelled):
    return film.render(engine.workspace() / "bed.json", _film_out(), spec.get("frames", 72), progress, cancelled)


edge_routes.JOBS.register("film", _film_job)


@app.get("/api/twin/film")
def film_status():
    job = next((j for j in edge_routes.JOBS.list() if j["kind"] == "film"), None)
    return jsonify({**film.status(_film_out()), "prepared": engine.config() is not None, "job": job})


@app.post("/api/twin/film")
def film_start():
    if engine.config() is None:
        return jsonify({"errors": ["prepare a world first: the film shows the prepared bed"]}), 400
    bad = film.missing()
    if bad:
        return jsonify({"errors": bad}), 400
    frames = max(24, min(int(body().get("frames") or 72), film.MAX_FRAMES))
    try:
        return jsonify(edge_routes.JOBS.submit("film", {"frames": frames})), 202
    except Exception as exc:                      # the same film is already queued or running
        return jsonify({"errors": [str(exc)]}), 409


@app.get("/api/twin/film/<name>")
def film_file(name: str):
    if name not in ("poster.png", "film.mp4"):
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
