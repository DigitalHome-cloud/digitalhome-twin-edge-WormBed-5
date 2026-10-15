#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
mcp_server.py — MCP access to permtek5-twin-edge.

Exposes the twin over stdio, so an agent sees the same simulation a person has
open in the browser. Everything goes through the web server (core.mcp_bridge),
which is started on demand; changes go through edge.mcp_tools.act(), which in
the chat panel makes them proposals the person confirms.

    permtek5-twin-edge-mcp                               # talks to 127.0.0.1:8772
    permtek5-twin-edge-mcp --port 9000
    permtek5-twin-edge-mcp --no-autostart                # fail instead of starting one
    python3 -m permtek5_twin_edge.mcp_server            # the same, from a checkout

Register it in .mcp.json (a checkout already has one), or install the Claude
Code plugin, which brings this server and the skill together.

Nothing may be written to stdout: that is the MCP transport. Log to stderr.
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import engine as engine
from .core import mcp_bridge as web

server = MCPServer(
    name="permtek5-twin-edge",
    instructions=(
        "wormbed5.edge: a Webots twin of a gantry robot over a habitat's roof bed. "
        "twin() says whether a world is prepared and the simulation runs, where the gantry "
        "is, the bed's cells and the jobs proposed. Jobs (sow, water, probe, photograph "
        "cells) are proposals: twin_confirm sends one, and in the chat panel that is a "
        "proposal the person confirms first. Never say a job ran before twin() shows it "
        "done. Cells are named row, column from 1, as the page shows them."
    ),
)

web.add_core_tools(server)

from .edge.mcp_tools import act, add_edge_tools  # edge layer
add_edge_tools(server)


@server.tool()
def check_habitat(ttl: str = "") -> list[str]:
    """What stops a habitat (its A-Box in Turtle) from being simulated. Empty
    text checks the invented CityDemo. An empty list means it is ready."""
    return engine.check(ttl or engine.habitat_ttl(None))


@server.tool()
def twin() -> dict:
    """The twin now: prepared or not, running or not, what is missing to start
    it, the gantry's position (m), the running job, the last jobs, the bed
    (rows, columns, planted cells) and the proposed jobs with their ids."""
    web.ensure_server()
    t = web.call("GET", "/api/twin")
    t.pop("config", None)                       # every cell with its job: too long to be useful here
    if t.get("twin"):
        cells = (t["twin"].get("bed") or {}).pop("cells", {})
        t["twin"]["cells"] = {"sown or growing": sum(1 for c in cells.values() if c["status"] != "CellPlanned"),
                              "driest %": min((c["moisture"] for c in cells.values()), default=None)}
    return t


@server.tool()
def twin_prepare() -> Any:
    """Write the Webots world from the habitat (CityDemo unless a habitat was synced)."""
    return act("Twin: prepare the world from the habitat", "POST", "/api/twin/prepare", {})


@server.tool()
def twin_start(window: bool = False) -> Any:
    """Start the simulation (Webots, the gantry, the bed). window=True opens the Webots window."""
    return act("Twin: start the simulation", "POST", "/api/twin/start", {"gui": window})


@server.tool()
def twin_stop() -> Any:
    """Stop the simulation."""
    return act("Twin: stop the simulation", "POST", "/api/twin/stop", {})


@server.tool()
def twin_confirm(proposal: str) -> Any:
    """Send one proposed job to the gantry, by the id twin() lists (e.g. "plant-5_13", "water", "photo")."""
    return act(f"Twin: run the proposed job {proposal}", "POST", "/api/twin/job", {"proposal": proposal})


@server.tool()
def twin_estop(on: bool = True) -> Any:
    """Hold the gantry where it is and abort its job (on=False releases it). Never a proposal: stopping is always allowed."""
    web.ensure_server()
    return web.call("POST", "/api/twin/estop", {"on": on})


@server.tool()
def plan_options(graph: str) -> Any:
    """What the cloud's bed planner can plan in an area's A-Box (graph: its name, as knowledge() lists them):
    the beds and farms of beds, and the plant sets (e.g. set-kitchen-herbs) with their crops."""
    web.ensure_server()
    return web.call("POST", "/api/plan", {"graph": graph})


@server.tool()
def plan_bed(graph: str, sets: list[str], bed: str = "", farm: str = "", shares: list[int] | None = None, seed: int = 5,
             same: bool = False) -> Any:
    """Plan a bed (or a farm of beds) from plant sets with the cloud's planner: the biggest plants first, the
    little ones last, low ground covers under tall plants. sets are ids from plan_options(); shares (1..5, one per
    set) say how much room each gets. For a farm, same=True plants every bed from all the sets; otherwise each set
    gets beds by its share. Returns the plan and PROPOSES writing it: the person confirms before any
    cell is planted. Another seed gives another plan."""
    web.ensure_server()
    ask = {"graph": graph, "sets": [{"setId": s, "share": (shares or [])[i] if i < len(shares or []) else 1} for i, s in enumerate(sets)],
           "seed": seed, **({"farmId": farm, "same": same} if farm else {"bedId": bed})}
    plan = web.call("POST", "/api/plan", ask)
    plans = [{"bedId": p["bedId"], "cells": p["cells"]} for p in plan.get("plans") or []]
    n = sum(len(p["cells"]) for p in plans)
    beds = ", ".join(p.get("bed") or "" for p in plan.get("plans") or [])
    proposed = act(f"Plant {n} cells in {beds} from {', '.join(sets)}", "POST", "/api/plan/apply", {"graph": graph, "plans": plans}) if n else None
    return {"plans": [{k: p[k] for k in ("bed", "sets", "crops", "perSet", "goodPairs", "badPairs", "cover")} for p in plan.get("plans") or []],
            "proposed": proposed}


def main() -> int:
    ap = argparse.ArgumentParser(prog="permtek5-twin-edge-mcp",
                                 description=__doc__.strip().splitlines()[0])
    ap.add_argument("--url", help="web server base URL (overrides --port)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-autostart", action="store_true",
                    help="fail rather than start a web server that is not running")
    args = ap.parse_args()
    web.configure(port=args.port, url=args.url, autostart=not args.no_autostart)
    web.log(f"serving MCP over stdio, web server at {web.BASE}")
    web.start_heartbeat()
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
