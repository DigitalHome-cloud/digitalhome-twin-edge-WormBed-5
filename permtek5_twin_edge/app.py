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
import sys
import urllib.error
import urllib.request

from flask import Response, abort, jsonify, render_template, request

from . import engine as engine
from . import habitat_store as store
from . import sim
from .core import server
from .core import workspace as ws

app = server.create_app(__name__)

from .edge import routes as edge_routes  # edge layer
from .edge import cloud as edge_cloud
edge_routes.install(app)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "expected a JSON object body")
    return data


# ------------------------------------------------------------------ page ----

@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    """Browsers ask for this by name whatever the page links to."""
    return app.send_static_file("favicon.svg")


# ------------------------------------------------------------------ twin ----
#
# The page shows what the twin does and proposes jobs; nothing moves until a
# person confirms one (POST /api/twin/job). The simulation runs on the ROS
# side; these routes only pass data between the browser, engine and sim.

def _twin_state() -> dict:
    status = sim.status()
    cfg = engine.config()
    twin = status.get("twin") or {}
    out = engine.workspace()
    src = store.source(out)
    recorded = []
    if cfg and src.get("graph") and twin.get("history"):
        # What the gantry sowed goes into the habitat it was prepared from; the next sync takes it to the cloud.
        recorded = store.record_results(out, cfg["bed"]["id"], twin["history"])
        if recorded and edge_cloud.linked_profiles():
            try:
                edge_routes.JOBS.submit("knowledge-sync", {"graph": src["graph"]})
            except Exception:                     # busy, or offline: the person syncs from Connections
                pass
    return {**status, **engine.summary(cfg), "config": cfg, "source": src.get("graph") or "citydemo",
            "habitats": store.habitats(), "recorded": recorded,
            "proposals": engine.proposals(cfg, twin.get("bed")) if status["running"] else engine.proposals(cfg)}


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
        out = engine.prepare(ttl)
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
    sim.stop()
    return jsonify(_twin_state())


@app.post("/api/twin/job")
def twin_job():
    """Confirm one job: a proposal by its id, or a full spec (plant, water, probe, photo)."""
    data = body()
    spec = data.get("spec")
    if data.get("proposal"):
        bed_state = (sim.status().get("twin") or {}).get("bed")
        spec = next((p["spec"] for p in engine.proposals(engine.config(), bed_state) if p["id"] == data["proposal"]), None)
        if spec is None:
            return jsonify({"errors": ["that proposal is no longer current"]}), 409
    if not isinstance(spec, dict):
        return jsonify({"errors": ["name a proposal or give a job spec"]}), 400
    try:
        return jsonify(sim.gateway("/job", spec))
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
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    ws.configure(user_dir=args.dir)
    lines = [f"  {f['label'].lower():<13} {f['path']}" for f in ws.folders()]
    edge_routes.start_background()  # edge layer
    try:
        return server.serve(app, args, lines=lines)
    finally:
        sim.stop()                     # never leave Webots running behind a stopped page


if __name__ == "__main__":
    raise SystemExit(main())
