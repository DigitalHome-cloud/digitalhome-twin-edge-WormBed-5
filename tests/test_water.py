# SPDX-License-Identifier: GPL-3.0-or-later
"""The guild's water in the twin (ros2/roofbed_twin/roofbed_twin/water.py): rain into the barrel, valves that run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin"))
from roofbed_twin.water import FLOW_L_PER_MIN, MIST_L_PER_MIN, MIST_STOP_PCT, WaterSystem  # noqa: E402

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
    assert {s["kind"] for s in st["stores"]} == {"RainBarrel", "TankPipes", "WormBoxSump", "WormBoxBin", "GardenTank"}


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


class WetBox(Box):              # and its bedding's moisture, as WormBox.moisten changes it
    def __init__(self, moisture):
        super().__init__(0.0)
        self.moisture = moisture

    def moisten(self, ml):
        self.moisture = min(98.0, self.moisture + ml / 100.0)


def test_the_mist_line_waters_the_box_from_the_barrel_one_dose_at_a_mists_pace():
    w, box = system(), WetBox(48.0)
    barrel, mist = w.of_kind("RainBarrel"), w.valve_between("RainBarrel", "WormBoxBin")
    assert mist and w.valves[mist]["kind"] == "EdgeValve", "a WormBed5 has a mist line, and the edge opens it"
    before = w.litres(barrel)
    w.open(mist, True, litres=1.0)
    w.step(1.0, 100.0, 202.0, box)
    assert round(before - w.litres(barrel), 2) == MIST_L_PER_MIN and w.valves[mist]["open"], "half a litre a minute"
    w.step(10.0, 100.0, 202.0, box)
    assert round(before - w.litres(barrel), 2) == 1.0 and not w.valves[mist]["open"], "the dose given: closed"
    assert round(box.moisture) == 58 and w.state()["mistedL"] == 1.0


def test_the_mist_line_stops_at_a_wet_box_whatever_is_left_of_its_dose():
    w, box = system(), WetBox(MIST_STOP_PCT - 1.0)
    mist = w.valve_between("RainBarrel", "WormBoxBin")
    w.open(mist, True, litres=1.5)
    w.step(10.0, 100.0, 202.0, box)
    w.step(1.0, 100.0, 202.0, box)
    assert not w.valves[mist]["open"] and box.moisture < MIST_STOP_PCT + 6


def box_state(moisture, barrel_l=100.0, browns=0):
    w = system()
    w.set_level(w.of_kind("RainBarrel"), barrel_l)
    return {"wormBin": {"harvest": {}, "moisture": moisture, "sumpL": 1.0, "sumpCapacity": 14.4}, "tank": {"litres": 150.0, "capacity": 202.0},
            "hopper": {"brownsGrams": browns}, "water": w.state()}


CFG = {"wormBin": {"cols": 4, "spots": 4}}


def test_a_dry_box_gets_the_mist_line_and_without_rain_in_the_barrel_the_gantry():
    mist = next(p for p in engine._box_care(CFG, box_state(48.0), {}, ["box-dry"]) if p["id"] == "valve-mist")
    assert mist["spec"] == {"kind": "valve", "what": "mist", "from": "RainBarrel", "to": "WormBoxBin", "litres": 1.5} and mist["hold"] == "moisten"
    ids = {p["id"] for p in engine._box_care(CFG, box_state(48.0, barrel_l=0.0), {}, ["box-dry"])}
    assert "moisten" in ids and "valve-mist" not in ids, "the barrel is dry: the gantry sprinkles from the tank"


def test_the_routine_tops_up_only_under_seventy_and_a_hot_box_gets_a_little():
    assert not engine._box_care(CFG, box_state(75.0), {}, [], ["mist"]), "moist enough: the look is all"
    top_up = next(p for p in engine._box_care(CFG, box_state(66.0), {}, [], ["mist"]) if p["id"] == "valve-mist")
    assert top_up["spec"]["litres"] == 0.6
    hot = next(p for p in engine._box_care(CFG, box_state(76.0), {}, ["heat"]) if p["id"] == "valve-mist")
    assert hot["spec"]["litres"] == 0.3
    assert not engine._box_care(CFG, {**box_state(60.0), "wormBin": {"harvest": {}, "moisture": None}}, {}, ["box-dry"], ["mist"]), "no reading, no water"


def test_frost_and_a_soaked_box_hold_the_box_watering_and_a_wet_box_gets_the_robots_bedding():
    from permtek5_twin_edge import keeper
    assert "moisten" in keeper.HOLDS["frost"] and "moisten" in keeper.HOLDS["box-wet"]
    cfg = {**CFG, "cells": [], "world": {"cellM": 0.1}, "bed": {"rows": 1, "cols": 1}}
    held = {p["id"] for p in engine.proposals(cfg, box_state(48.0), ["feed", "water", "moisten"], active=["box-dry", "frost"])}
    assert not held & {"valve-mist", "moisten"}
    wet = next(p for p in engine._box_care(CFG, box_state(92.0, browns=800), {}, ["box-wet"]) if p["id"] == "bedding")
    assert wet["spec"]["grams"] == 500 and wet["kind"] == "care"
    assert "bedding" not in {p["id"] for p in engine._box_care(CFG, box_state(92.0, browns=0), {}, ["box-wet"])}, "nothing in the compartment"
