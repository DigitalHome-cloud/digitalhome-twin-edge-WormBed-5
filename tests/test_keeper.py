# SPDX-License-Identifier: GPL-3.0-or-later
"""The keeper's rules: one test per reaction, on plain state."""

from __future__ import annotations

import json

import pytest

from permtek5_twin_edge import keeper


def bed(air=16.0, wind=3.0, rain=0.0, box=18.0, tank=120.0, hour=10.0, day=1, **more):
    b = {"env": {"airC": air, "windMs": wind, "rainMmH": rain, "day": day, "hour": hour},
         "tank": {"litres": tank, "capacity": 202.0, "overflowing": False},
         "wormBin": {"tempC": box, "moisture": 70.0, "fillPct": 30.0}, "hopper": {"grams": 500, "jammed": False}}
    for k, v in more.items():
        b[k].update(v)
    return b


def ids(b, memory=None, **kw):
    return keeper.evaluate(b, {} if memory is None else memory, **kw)["active"]


def test_a_calm_day_needs_nothing():
    r = keeper.evaluate(bed(), {})
    assert r == {"active": [], "holds": [], "lockout": None, "notices": []}
    assert keeper.evaluate(None, {})["active"] == []


def test_frost_holds_feeding_and_watering_and_lets_go_only_when_clearly_warmer():
    m = {}
    r = keeper.evaluate(bed(air=-6.0, box=9.0), m)
    assert r["active"] == ["frost"] and r["holds"] == ["feed", "moisten", "water"] and r["lockout"] is None
    assert ids(bed(air=2.0, box=9.0), m) == ["frost"], "between the two thresholds it holds"
    assert ids(bed(air=4.0, box=6.0), m) == ["frost"], "the air recovered, the box has not"
    assert ids(bed(air=4.0, box=8.0), m) == []
    assert ids(bed(air=10.0, box=4.5)) == ["frost"], "a cold box alone is enough"


def test_heat_holds_feeding():
    m = {}
    assert keeper.evaluate(bed(air=26.0, box=31.0), m)["holds"] == ["feed"]
    assert ids(bed(air=26.0, box=29.0), m) == ["heat"] and ids(bed(air=26.0, box=27.0), m) == []
    assert ids(bed(air=33.0)) == ["heat"]


def test_storm_locks_the_gantry_out_until_the_wind_really_drops():
    m = {}
    r = keeper.evaluate(bed(wind=21.0), m)
    assert r["active"] == ["storm"] and "locked out" in r["lockout"]
    assert keeper.refuses(r, "photo") and keeper.refuses(r, "plant"), "every job is refused"
    assert ids(bed(wind=10.0), m) == ["storm"] and ids(bed(wind=8.0), m) == []


def test_a_hold_refuses_its_kind_of_job_and_no_other():
    r = keeper.evaluate(bed(air=-6.0), {})
    assert "Frost" in keeper.refuses(r, "feed") and "Frost" in keeper.refuses(r, "water")
    assert keeper.refuses(r, "photo") is None and keeper.refuses(r, "probe") is None


def test_the_tank_low_empty_overflowing_and_leaking():
    assert ids(bed(tank=20.0)) == ["tank-low"] and ids(bed(tank=0.0)) == ["tank-empty"]
    full = bed(tank=202.0, rain=60.0)
    full["tank"]["overflowing"] = True
    r = keeper.evaluate(full, {})
    assert r["active"] == ["overflow"] and r["holds"] == ["water"]
    m = {}
    assert ids(bed(tank=120.0, hour=10.0), m) == []
    assert ids(bed(tank=118.5, hour=10.2), m) == [], "a little is the soil drinking"
    assert ids(bed(tank=112.0, hour=10.5), m) == ["tank-falling"]
    assert ids(bed(tank=112.0, hour=12.0), m) == [], "an hour and a half later the old level is forgotten and it holds steady"


def test_a_dead_sensor_and_a_stuck_one_are_distrusted_and_feeding_waits():
    r = keeper.evaluate(bed(box=None), {})
    assert r["active"] == ["sensor"] and r["holds"] == ["feed"]
    m = {}
    assert ids(bed(air=16.0, box=18.0, hour=10.0), m) == []
    assert ids(bed(air=15.0, box=18.0, hour=12.5), m) == [], "the same value, but the air hardly moved"
    assert ids(bed(air=9.0, box=18.0, hour=13.0), m) == ["sensor"], "the air fell six degrees and the box 'did not'"
    assert ids(bed(air=9.0, box=17.2, hour=13.1), m) == [], "it moves again"


def test_hopper_jam_offline_and_a_full_box():
    assert keeper.evaluate(bed(hopper={"jammed": True}), {})["holds"] == ["feed"]
    assert ids(bed(), offline=True) == ["offline"] and ids(None, offline=True) == ["offline"]
    assert ids(bed(wormBin={"fillPct": 93.0})) == ["box-full"]


def test_several_at_once_add_up():
    b = bed(air=-8.0, wind=22.0, box=None, tank=0.0)
    r = keeper.evaluate(b, {})
    assert set(r["active"]) == {"frost", "storm", "tank-empty", "sensor"} and r["lockout"] and r["holds"] == ["feed", "moisten", "water"]
    assert [n["level"] for n in r["notices"]] == ["alert"] * 4


def test_changes_are_logged_once_as_observations(tmp_path):
    m = {}
    on = keeper.evaluate(bed(wind=21.0), m)
    first = keeper.log_changes(tmp_path, on, m, "guild-g-1", "day 1 · 10:00")
    assert [(o["observedProperty"], o["hasSimpleResult"]) for o in first] == [("keeper:storm", "on")]
    assert keeper.log_changes(tmp_path, on, m, "guild-g-1") == [], "nothing changed, nothing written"
    off = keeper.evaluate(bed(wind=3.0), m)
    assert keeper.log_changes(tmp_path, off, m, "guild-g-1")[0]["hasSimpleResult"] == "off"
    rows = [json.loads(l) for l in (tmp_path / "observations.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["@type"] == "sosa:Observation" and rows[0]["hasFeatureOfInterest"] == "guild-g-1"


def box(**readings):
    b = bed()
    b["wormBin"].update({"edgeC": 17.0, "gas": 5.0, "weightKg": 6.2, "lidOpen": False, **readings})
    return b


def test_an_overfed_box_is_not_fed_more():
    m = {}
    r = keeper.evaluate(box(tempC=27.0, edgeC=17.0), m)
    assert r["active"] == ["overfed"] and r["holds"] == ["feed"]
    assert "overfed" in keeper.evaluate(box(tempC=22.0, edgeC=17.0), m)["active"], "still composting"
    assert keeper.evaluate(box(tempC=19.0, edgeC=17.0), m)["active"] == []


def test_a_smelling_or_soaked_box_is_not_fed_and_a_dry_one_is_only_reported():
    assert keeper.evaluate(box(gas=70.0), {})["holds"] == ["feed"]
    assert keeper.evaluate(box(moisture=93.0), {})["active"] == ["box-wet"]
    dry = keeper.evaluate(box(moisture=45.0), {})
    assert dry["active"] == ["box-dry"] and dry["holds"] == []
    assert keeper.evaluate(box(lidOpen=True), {})["active"] == ["lid"]


def test_any_dead_box_sensor_is_distrusted():
    assert keeper.evaluate(box(weightKg=None), {})["active"] == ["sensor"]
    assert keeper.evaluate(box(gas=None), {})["holds"] == ["feed"]


def test_a_full_sump_holds_feeding_until_it_is_well_drained():
    m = {}
    r = keeper.evaluate(box(sumpL=13.2, sumpCapacity=14.4), m)
    assert r["active"] == ["sump-full"] and r["holds"] == ["feed"]
    assert keeper.evaluate(box(sumpL=9.0, sumpCapacity=14.4), m)["active"] == ["sump-full"], "not yet"
    assert keeper.evaluate(box(sumpL=3.0, sumpCapacity=14.4), m)["active"] == []


def test_the_worms_rules_go_by_the_feeders_estimate_crowded_and_dying_back():
    from permtek5_twin_edge import feeder
    est_mem, kmem = {}, {}
    advice = {"state": "known", "eatsPerDay": 1000, "bestPerDay": 1000, "uneatenG": 300}
    est = feeder.worms(est_mem, advice, 0.0, 0.48)
    assert est["kg"] == 2.0 and est["kgPerM2"] == pytest.approx(4.17, abs=0.01) and est["fallPct"] is None
    result = keeper.evaluate(None, kmem)
    keeper.population(result, kmem, est)
    assert "crowded" in result["active"] and result["holds"] == [], "a notice, nothing held"
    for day in range(1, 8):                                       # a week later they eat 40 % less, with food there
        est = feeder.worms(est_mem, {**advice, "eatsPerDay": 600, "bestPerDay": 600}, day * 1440.0, 0.48)
    assert est["fallPct"] == 40
    result = keeper.evaluate(None, kmem)
    keeper.population(result, kmem, est)
    assert "dying-back" in result["active"] and "crowded" not in result["active"]
    assert feeder.worms({}, {**advice, "uneatenG": 0}, 0.0, 0.48) is None, "an empty box says nothing about the worms"


def water(barrel_l=150.0, overflowing=False, garden_l=50.0, tap="ManualTap"):
    return {"stores": [{"id": "b", "kind": "RainBarrel", "capacity": 200.0, "litres": barrel_l, "overflowing": overflowing},
                       {"id": "g", "kind": "GardenTank", "capacity": 300.0, "litres": garden_l},
                       {"id": "p", "kind": "TankPipes", "capacity": 202.0}],
            "valves": [{"id": "v", "kind": tap, "from": "b", "to": "p", "open": False}]}


def test_a_low_tank_with_rain_in_the_barrel_asks_for_the_tap_and_a_full_barrel_or_garden_tank_says_so():
    b = bed(tank=50.0)
    b["water"] = water()
    assert "barrel-refill" in ids(b)
    b["water"] = water(tap="EdgeValve")
    assert "barrel-refill" not in ids(b), "an edge valve is proposed as a job instead"
    b = bed()
    b["water"] = water(barrel_l=200.0, overflowing=True, garden_l=290.0)
    assert {"barrel-full", "garden-full"} <= set(ids(b))
    m = {}
    b["water"] = water(garden_l=290.0)
    assert "garden-full" in ids(b, m)
    b["water"] = water(garden_l=270.0)
    assert "garden-full" in ids(b, m), "it lets go only clearly below full"
