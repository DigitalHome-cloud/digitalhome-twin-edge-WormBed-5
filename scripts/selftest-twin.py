#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end check of the twin on the invented CityDemo: needs ROS 2, Webots and a built ros2/.

    .venv/bin/python scripts/selftest-twin.py [--gui]

Generates the world, starts the simulation, sows, waters, probes and
photographs cells through the gateway, feeds the worm box, gives it bedding,
takes compost back to cells, drains the sump, watches the tank and the soil,
checks what came back, stops it. It uses ports of its own (8790 and up), so it
can run beside an edge that is in use.
Not part of ./test.sh (which must pass on a machine without ROS or Webots).
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from permtek5_twin_edge import engine, habitat as hb, instance, sim, world  # noqa: E402


def check(what, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {what}{'' if ok or not detail else '  ' + str(detail)}")
    if not ok:
        raise SystemExit(1)


def main():
    instance.configure(port=8790)          # its own ports, so it can run beside an edge that is in use
    out = Path(tempfile.mkdtemp(prefix="twin-selftest-"))
    world.generate(hb.load_citydemo(), hb.load_library(), out, assets=engine.shared_assets())
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
        # the box's care: bedding from the second compartment, the sump's tap, and compost back to the bed
        job = sim.run_job({"kind": "care", "what": "bedding", "grams": 200, "spot": 0})
        check("an empty bedding compartment is refused", job["state"] == "failed" and "compartment is empty" in job["message"], job)
        sim.gateway("/event", {"op": "hopper", "browns": 500})
        time.sleep(1.2)
        job = sim.run_job({"kind": "care", "what": "bedding", "grams": 200, "spot": 0})
        check("care_box adds dry bedding", job["state"] == "done" and job["result"]["amount"] == 200, job)
        time.sleep(1.2)
        bed = sim.gateway("/state")["bed"]
        check("the compartment is lighter and the robot knows what it added",
              bed["hopper"]["brownsGrams"] == 300 and bed["wormBin"]["beddingGrams"] == 200, bed["hopper"])
        job = sim.run_job({"kind": "topdress", "cells": [[1, 1]], "grams": 50})
        check("no compost is taken from a box that has not rested", job["state"] == "failed" and "rested" in job["message"], job)
        sim.gateway("/event", {"op": "box", "do": "rest", "side": 1})
        sim.gateway("/event", {"op": "time", "factor": 600})     # ten days at ten hours a second
        deadline = time.time() + 60
        while time.time() < deadline and not sim.gateway("/state")["bed"]["wormBin"]["harvest"]["ready"]:
            time.sleep(1.0)
        sim.gateway("/event", {"op": "time", "factor": 1})
        check("the rested half is ready after ten days", sim.gateway("/state")["bed"]["wormBin"]["harvest"]["ready"])
        job = sim.run_job({"kind": "topdress", "cells": [[1, 1], [1, 4]], "grams": 50})
        check("top_dress carries compost to the cells", job["state"] == "done" and job["result"]["givenG"] == [50, 50], job)
        time.sleep(1.2)
        bed = sim.gateway("/state")["bed"]
        check("the cells have it and the box is lighter by it",
              bed["cells"]["1_1"]["compostG"] == 50 and bed["cells"]["1_4"]["compostG"] == 50 and bed["wormBin"]["takenGrams"] == 100, bed["wormBin"])
        sim.gateway("/event", {"op": "tank", "litres": 100})
        sim.gateway("/event", {"op": "box", "do": "lid", "open": True})
        sim.gateway("/event", {"op": "disturb", "id": "cloudburst", "effect": {"rainMmH": 60.0}, "minutes": 60})
        sim.gateway("/event", {"op": "time", "factor": 30})
        time.sleep(4)
        sim.gateway("/event", {"op": "calm"})
        sim.gateway("/event", {"op": "time", "factor": 1})
        sim.gateway("/event", {"op": "box", "do": "lid", "open": False})
        sim.gateway("/event", {"op": "tank", "litres": 100})
        time.sleep(1.2)
        full = sim.gateway("/state")["bed"]["wormBin"]["sumpL"]
        sim.gateway("/event", {"op": "box", "do": "tap"})
        time.sleep(1.2)
        bed = sim.gateway("/state")["bed"]
        check("rain through an open lid fills the sump, and the tap lets it into the tank",
              full > 10 and bed["wormBin"]["sumpL"] < 1 and bed["tank"]["litres"] > 108, (full, bed["tank"]))
        # weeds: the camera finds what the simulation grew, the stamp presses the small ones under
        sim.gateway("/event", {"op": "weeds", "pull": "all"})
        time.sleep(1.2)
        job = sim.run_job({"kind": "scan"}, timeout=400)
        check("a clean bed has no weeds", job["state"] == "done" and job["result"]["weeds"] == [], job["message"])
        sim.gateway("/event", {"op": "weeds", "count": 8, "large": 2})
        time.sleep(1.5)
        truth = sim.gateway("/state")["bed"]["sim"]["weedsAt"]
        job = sim.run_job({"kind": "scan"}, timeout=400)
        found = job["result"]["weeds"]
        near = lambda w: min(((w["x"] - x) ** 2 + (w["y"] - y) ** 2) ** 0.5 for x, y, _ in truth)   # noqa: E731
        check("scan_weeds finds every weed, within 15 mm, and nothing else",
              job["state"] == "done" and len(found) == len(truth) and all(near(w) < 0.015 for w in found), (job["message"], len(truth)))
        small = [[w["x"], w["y"]] for w in found if w["sizeMm"] < 30]
        job = sim.run_job({"kind": "stamp", "places": small}, timeout=400)
        check("stamp_weeds presses the small ones under", job["state"] == "done" and job["result"]["stamped"] + job["result"]["skipped"] == len(small), job)
        time.sleep(1.5)
        left = sim.gateway("/state")["bed"]["sim"]
        check("the big ones stand, for a person", left["weedsLarge"] == 2 and left["weeds"] == 2 + job["result"]["skipped"], left["weeds"])
        sim.gateway("/event", {"op": "weeds", "pull": "all"})
        sim.gateway("/event", {"op": "tank", "litres": 100})
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
