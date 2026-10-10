#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end check of the twin on the invented CityDemo: needs ROS 2, Webots and a built ros2/.

    .venv/bin/python scripts/selftest-twin.py [--gui]

Generates the world, starts the simulation, sows, waters, probes and
photographs cells through the gateway, checks what came back, stops it.
Not part of ./test.sh (which must pass on a machine without ROS or Webots).
"""
import sys
import tempfile
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
        job = sim.run_job({"kind": "plant", "row": 40, "col": 2, "crop": "crop-rocket"})
        check("a cell outside the bed is refused (soft limit)", job["state"] == "failed" and "outside the bed" in job["message"], job)
    finally:
        sim.stop()
    check("the simulation stopped", not sim.running())
    print("twin self-test passed")


if __name__ == "__main__":
    main()
