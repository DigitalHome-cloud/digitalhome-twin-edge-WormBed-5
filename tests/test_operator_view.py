# SPDX-License-Identifier: GPL-3.0-or-later
"""The Operator screen's tiles: readings judged the way the keeper judges them."""

from __future__ import annotations

from permtek5_twin_edge import keeper, operator_view as ov


def bed(air=16.0, wind=3.0, rain=0.0, **box):
    b = {"env": {"airC": air, "windMs": wind, "rainMmH": rain, "day": 1, "hour": 10.0},
         "tank": {"litres": 120.0, "capacity": 202.0, "overflowing": False},
         "cells": {"r0c0": {"status": "CellGrowing", "moisture": 45.0}, "r0c1": {"status": "CellSown", "moisture": 38.0},
                   "r0c2": {"status": "CellPlanned", "moisture": 5.0}},
         "wormBin": {"tempC": 19.0, "edgeC": 18.0, "moisture": 70.0, "pH": 6.8, "gas": 10.0, "weightKg": 30.0,
                     "sumpL": 0.5, "sumpCapacity": 4.0, "fillPct": 30.0, "lidOpen": False, "insulated": False,
                     "fedGrams": 0, "feedings": 0},
         "hopper": {"grams": 500, "brownsGrams": 0, "jammed": False}}
    b["wormBin"].update(box)
    return b


def judged(b):
    """The tiles, keyed, with the keeper run first: as app.py does it."""
    active = keeper.evaluate(b, {})["active"]
    return {t["key"]: t for t in ov.tiles(b, active)}


def test_a_calm_day_is_all_ok_and_shows_only_readings():
    t = judged(bed())
    assert {x["state"] for x in t.values()} == {"ok"}
    assert [g for g, _ in ov.GROUPS] == ["bed", "box", "water", "feeding", "weather"]
    assert t["soilDriest"]["value"] == 38 and t["soilWettest"]["value"] == 45, "only cells that are sown or growing count"
    assert "worms" not in str(t) and "soilLife" not in str(t), "no simulation truth on the operator screen"
    assert ov.summary(list(t.values()))["alarm"] == 0


def test_a_frosty_box_is_an_alarm_on_its_temperature_and_on_the_air():
    t = judged(bed(air=-4.0, tempC=4.0, edgeC=3.0))
    assert t["tempC"]["state"] == "alarm" and t["tempC"]["rule"] == "frost"
    assert t["airC"]["state"] == "alarm"
    assert t["insulated"]["state"] == "watch", "an unwrapped box in frost wants wrapping"


def test_a_missing_reading_is_an_alarm():
    t = judged(bed(pH=None))
    assert t["pH"]["state"] == "alarm" and t["pH"]["value"] is None


def test_close_to_a_threshold_is_watch():
    t = judged(bed(moisture=keeper.DRY_ON + 2))
    assert t["moisture"]["state"] == "watch" and t["moisture"]["rule"] is None
    assert judged(bed(wind=0.8 * keeper.STORM_ON))["windMs"]["state"] == "watch"


def test_a_note_rule_is_watch_and_an_alert_rule_is_alarm():
    assert judged(bed(lidOpen=True))["lid"]["state"] == "watch", "the open lid is a note"
    assert judged(bed(gas=keeper.GAS_ON + 5))["gas"]["state"] == "alarm", "airless is an alert"


def test_no_bed_no_tiles():
    assert ov.tiles(None) == [] and ov.tiles({}, []) == []


def test_frost_in_the_air_leaves_a_warm_box_ok():
    t = judged(bed(air=-4.0, tempC=18.0, edgeC=17.0))
    assert t["airC"]["state"] == "alarm"
    assert t["tempC"]["state"] == "ok" and t["edgeC"]["state"] == "ok", "the box is warm: the air tripped the rule"
    assert t["insulated"]["hint"] == "frost: wrap the box"


def test_every_tile_says_what_stands_behind_it():
    t = judged(bed())
    for key in t:
        info = ov.sensor(key)
        assert info and all(info.get(k) is not None for k in ("sensor", "where", "method", "every", "limits", "twin")), key
    assert str(keeper.FROST_BOX_ON) .split(".")[0] in " ".join(ov.sensor("tempC")["limits"]), "the limits come from the keeper"


def test_the_water_shows_as_tiles_and_a_full_barrel_or_a_low_tank_with_rain_in_it_is_watch():
    b = bed()
    b["water"] = {"stores": [{"id": "b", "kind": "RainBarrel", "capacity": 200.0, "litres": 200.0, "wormL": 0.0, "overflowing": True},
                             {"id": "g", "kind": "GardenTank", "capacity": 300.0, "litres": 60.0, "wormL": 15.0},
                             {"id": "p", "kind": "TankPipes", "capacity": 202.0}],
                  "valves": [{"id": "v", "name": "Barrel tap", "kind": "ManualTap", "from": "b", "to": "p", "open": False}]}
    b["tank"]["litres"] = 40.0
    t = judged(b)
    assert t["barrel"]["state"] == "watch" and t["barrel"]["rule"] in ("barrel-full", "barrel-refill")
    assert t["gardenTank"]["value"] == 60 and "25 %" in t["gardenTank"]["unit"], "a quarter of it is worm liquid"
    assert t["valve-0"]["value"] == "closed" and t["valve-0"]["label"] == "Barrel tap"
    assert ov.sensor("valve-0")["sensor"] == "Valve"
