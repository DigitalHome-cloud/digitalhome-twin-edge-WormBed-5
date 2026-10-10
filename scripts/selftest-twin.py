#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end check of the twin on the invented CityDemo: needs ROS 2, Webots and a built ros2/.

    .venv/bin/python scripts/selftest-twin.py [--gui]

Generates the world, starts the simulation, sows, waters, probes and
photographs cells through the gateway, feeds the worm box, watches the tank
and the soil, checks what came back, stops it.
Not part of ./test.sh (which must pass on a machine without ROS or Webots).
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from permtek5_twin_edge import habitat as hb, sim, world  # noqa: E402


def check(what, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {what}{'' if ok or not detail else '  ' + str(detail)}")
    if not ok:
        raise SystemExit(1)


def main():
    out = Path(tempfile.mkdtemp(prefix="twin-selftest-"))
    world.generate(hb.load_citydemo(), hb.load_library(), out)
    check("nothing missing to start", sim.missing() == [], sim.missing())
    sim.start(out, gui="--gui" in sys.argv)
    try:
        check("gantry and both cameras report", sim.wait_ready(90), sim.log_tail(8))
        job = sim.run_job({"kind": "plant", "row": 5, "col": 13, "crop": "crop-rocket", "depthMm": 5, "seeds": 3})
        check("plant_cell succeeds", job["state"] == "done" and job["result"]["sown"], job)
        s = sim.gateway("/state")
        g = s["gantry"]
        check("the head is over the cell (5 mm)", abs(g["x"] - 1.35) < 0.005 and abs(g["y"] - 0.55) < 0.005, g)
        check("the cell is sown", s["bed"]["cells"]["5_13"]["status"] == "CellSown")
        before = s["bed"]["cells"]["5_13"]["moisture"]
        job = sim.run_job({"kind": "water", "cells": [[5, 13, 200]]})
        check("water_cells gives the amount asked", job["state"] == "done" and job["result"]["givenMl"] == [200.0], job)
        job = sim.run_job({"kind": "probe", "cells": [[5, 13]]})
        check("the probe reads the wetter soil", job["state"] == "done" and job["result"]["moisture"][0] > before + 10, job)
        job = sim.run_job({"kind": "photo", "cells": [[1, 4]]})
        photos = (job.get("result") or {}).get("photos", [])
        check("photo_pass returns one picture per cell",
              job["state"] == "done" and len(photos) == 1 and Path(photos[0]["file"]).stat().st_size > 1000, job)
        # the bed as a system: the worm box, the tank under the bed
        job = sim.run_job({"kind": "feed", "grams": 300, "spot": 0})
        check("an empty hopper is refused", job["state"] == "failed" and "hopper is empty" in job["message"], job)
        sim.gateway("/event", {"op": "hopper", "grams": 1000})
        sim.gateway("/event", {"op": "tank", "litres": 100})
        time.sleep(1.2)
        before_feed = sim.gateway("/state")["bed"]
        job = sim.run_job({"kind": "feed", "grams": 300, "spot": 1})
        check("feed_worms drops a portion in the worm box", job["state"] == "done" and job["result"]["grams"] == 300, job)
        time.sleep(1.2)
        bed = sim.gateway("/state")["bed"]
        check("the hopper is lighter, the worm box fed", bed["hopper"]["grams"] == 700 and bed["wormBin"]["feedings"] == 1, bed["hopper"])
        check("the load cells see the portion", 0.2 < bed["wormBin"]["weightKg"] - before_feed["wormBin"]["weightKg"] < 0.4, bed["wormBin"])
        sim.gateway("/event", {"op": "box", "do": "insulate", "on": True})
        sim.gateway("/event", {"op": "disturb", "id": "storm", "effect": {"windMs": 20.0}, "minutes": 30})
        time.sleep(1.2)
        bed = sim.gateway("/state")["bed"]
        check("a person's wrapping and a disturbance reach the twin", bed["wormBin"]["insulated"] and bed["env"]["windMs"] > 15, bed["env"])
        sim.gateway("/event", {"op": "calm"})
        sim.gateway("/event", {"op": "box", "do": "insulate", "on": False})
        g = sim.gateway("/state")["gantry"]
        check("the head is over the worm box", abs(g["x"] - 2.8) < 0.005, g)
        job = sim.run_job({"kind": "plant", "row": 5, "col": 27, "crop": "crop-rocket"})
        check("nothing is sown in the worm box", job["state"] == "failed" and "worm box" in job["message"], job)
        # with water in the tank the soil is kept moist from below; an empty tank lets it dry
        time.sleep(6)
        wet = sim.gateway("/state")["bed"]
        check("the wick holds a grown cell's moisture up", wet["cells"]["1_1"]["moisture"] >= 36.0, wet["cells"]["1_1"])
        check("and that draws on the tank", wet["tank"]["litres"] < 100.1, wet["tank"])
        sim.gateway("/event", {"op": "tank", "litres": 0})
        sim.gateway("/event", {"op": "time", "factor": 60})     # drying takes hours: an hour a second
        time.sleep(1.2)
        level = sim.gateway("/state")["bed"]["cells"]["1_1"]["moisture"]
        time.sleep(5)
        check("an empty tank lets the soil dry", sim.gateway("/state")["bed"]["cells"]["1_1"]["moisture"] < level, level)
        sim.gateway("/event", {"op": "time", "factor": 1})
        job = sim.run_job({"kind": "plant", "row": 40, "col": 2, "crop": "crop-rocket"})
        check("a cell outside the bed is refused (soft limit)", job["state"] == "failed" and "outside the bed" in job["message"], job)
    finally:
        sim.stop()
    check("the simulation stopped", not sim.running())
    print("twin self-test passed")


if __name__ == "__main__":
    main()
