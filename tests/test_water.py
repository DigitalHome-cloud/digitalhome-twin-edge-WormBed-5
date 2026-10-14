# SPDX-License-Identifier: GPL-3.0-or-later
"""The guild's water in the twin (ros2/roofbed_twin/roofbed_twin/water.py): rain into the barrel, valves that run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin"))
from roofbed_twin.water import FLOW_L_PER_MIN, WaterSystem  # noqa: E402

from permtek5_twin_edge import engine, habitat as hb  # noqa: E402


def system():
    h = hb.load_citydemo()
    return WaterSystem(hb.water_for(h, h.beds[0])[0])


class Box:                       # the worm box's sump, as the driver's WormBox has it
    def __init__(self, sump_l=10.0):
        self.sump_l, self.sump_cap_l = sump_l, 14.4


def test_citydemo_has_its_water_and_rain_fills_the_barrel_until_it_overflows():
    w = system()
    barrel = w.of_kind("RainBarrel")
    w.set_level(barrel, 0.0)
    w.rain(10.0)                                  # 10 mm on 8 m² at 0.8: 64 l
    assert round(w.litres(barrel)) == 64
    w.rain(40.0)
    assert w.litres(barrel) == 200.0 and barrel in w.overflowing and w.lost_l > 0


def test_the_barrel_tap_refills_the_tank_pipes_at_a_tap_s_pace_and_closes_when_the_barrel_is_empty():
    w = system()
    barrel = w.of_kind("RainBarrel")
    w.set_level(barrel, 30.0)
    tap = w.valve_between("RainBarrel", "TankPipes")
    w.open(tap)
    tank = w.step(2.0, 100.0, 202.0)
    assert tank == 100.0 + 2 * FLOW_L_PER_MIN and w.valves[tap]["open"]
    tank = w.step(60.0, tank, 202.0)
    assert round(tank) == 130 and not w.valves[tap]["open"], "the barrel ran empty: closed again"


def test_the_sump_goes_to_the_garden_tank_and_is_counted_as_worm_liquid():
    w, box = system(), Box(10.0)
    garden = w.of_kind("GardenTank")
    w.set_level(garden, 0.0)
    w.open(w.valve_between("WormBoxSump", "GardenTank"))
    w.step(10.0, 100.0, 202.0, box)
    assert box.sump_l == 0.0 and w.litres(garden) == 10.0 and w.worm_l[garden] == 10.0 and w.to_garden_l == 10.0
    st = w.state()
    assert {s["kind"] for s in st["stores"]} == {"RainBarrel", "TankPipes", "WormBoxSump", "GardenTank"}


def test_the_edge_proposes_the_bed_first_then_the_garden_once_the_bed_had_its_third():
    cfg = {"wormBin": {"cols": 4, "spots": 4}}
    w = system()
    state = {"wormBin": {"harvest": {}, "sumpL": 10.0, "sumpCapacity": 14.4}, "tank": {"litres": 100.0, "capacity": 202.0},
             "hopper": {}, "water": w.state()}
    first = {p["id"] for p in engine._box_care(cfg, state, {}, [])}
    assert "valve-bed" in first and "valve-garden" not in first
    state["water"]["wormToBedL"], state["water"]["wormToGardenL"] = 12.0, 3.0
    later = {p["id"] for p in engine._box_care(cfg, state, {}, [])}
    assert "valve-garden" in later, "the bed has had more than a third"
