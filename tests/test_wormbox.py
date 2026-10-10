# SPDX-License-Identifier: GPL-3.0-or-later
"""The worm box's biology and its sensors, on plain Python."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "roofbed_twin"))
from roofbed_twin.wormbox import LOSS_PER_G_EATEN, Sensors, WormBox  # noqa: E402


def run(box, hours, air=16.0, step=10.0, **kw):
    out = {"liquidL": 0.0, "eatenG": 0.0}
    for _ in range(int(hours * 60 / step)):
        r = box.step(step, air, **kw)
        out = {k: out[k] + r[k] for k in out}
    return out


def test_a_mild_day_the_worms_eat_about_half_their_weight_and_the_box_gets_lighter_by_that():
    box = WormBox()
    box.feed(1000)
    before = box.weight_g()
    out = run(box, 24)
    assert 300 < out["eatenG"] < 420, "800 g of worms, working well"
    assert before - box.weight_g() == pytest.approx(out["eatenG"] * LOSS_PER_G_EATEN, rel=0.05)
    assert out["liquidL"] == pytest.approx(out["eatenG"] * 0.3 / 1000)
    assert box.health == 1.0


def test_cold_stops_them_and_a_frozen_box_kills():
    box = WormBox()
    box.feed(500)
    run(box, 24, air=-8.0)
    assert box.core_c < 0 and box.activity() == 0.0 and box.health < 0.8


def test_insulation_carries_the_box_through_a_cold_night():
    bare, wrapped = WormBox(), WormBox()
    wrapped.insulated = True
    for box in (bare, wrapped):
        run(box, 10, air=-6.0)
    assert bare.core_c < 2.0 and wrapped.core_c > 5.0
    assert wrapped.health == 1.0 and bare.health < 1.0


def test_too_much_food_heats_the_core_and_uses_the_air_up():
    box = WormBox()
    box.feed(4000, by_robot=False)
    run(box, 12)
    assert box.core_c - box.edge_c > 6.0, "it composts"
    assert box.gas() > 60 and box.activity() < 0.6
    assert box.feedings == 0, "a person's bucket is not a robot feeding"
    box.take_out(3500)
    box.add_bedding(800)
    run(box, 12)
    assert box.gas() < 30 and box.core_c - box.edge_c < 3.0


def test_worms_that_may_leave_come_back_a_closed_box_does_not_recover():
    open_box, closed = WormBox(may_leave=True), WormBox(may_leave=False)
    for box in (open_box, closed):
        run(box, 30, air=-10.0)
    assert closed.health < open_box.health < 1.0, "leaving dodges half the harm"
    low_open, low_closed = open_box.health, closed.health
    for box in (open_box, closed):
        run(box, 72, air=16.0)
    assert open_box.health > low_open + 0.3 and closed.health == pytest.approx(low_closed)


def test_an_open_lid_dries_the_box_and_lets_rain_in():
    box = WormBox()
    box.lid_open = True
    run(box, 24)
    assert box.moisture < 60
    run(box, 2, rain_mm_h=20.0)
    assert box.moisture > 90


def test_sensors_add_a_little_noise_repeat_themselves_and_fail_as_told():
    box = WormBox()
    a, b = Sensors(seed=5).read(box), Sensors(seed=5).read(box)
    assert a == b, "the same seed gives the same readings"
    assert abs(a["tempC"] - box.core_c) < 0.5 and abs(a["weightKg"] - box.weight_g() / 1000) < 0.08
    s = Sensors(seed=5)
    dead = s.read(box, lambda p: "dead" if p == "wormBin.weightKg" else None)
    assert dead["weightKg"] is None and dead["tempC"] is not None
    stuck = [s.read(box, lambda p: "stuck" if p == "wormBin.tempC" else None)["tempC"] for _ in range(4)]
    assert len(set(stuck)) == 1
    assert len({s.read(box)["tempC"] for _ in range(8)}) > 1, "and moves again once it is free"


def test_the_sump_catches_the_liquid_fills_in_rain_backs_up_and_drains_by_the_tap():
    box = WormBox(sump_cap_l=14.0, area_m2=0.48)
    box.feed(1000)
    out = run(box, 24)
    assert box.sump_l == pytest.approx(out["liquidL"]) and 0.08 < box.sump_l < 0.15, "a glass a day"
    box.lid_open = True
    wet = box.moisture
    run(box, 1, rain_mm_h=60.0)
    assert box.sump_l == 14.0 and box.moisture > wet + 20, "full, and it backed up into the bin"
    assert Sensors(seed=1).read(box)["sumpL"] == pytest.approx(14.0, abs=0.15)
    assert box.drain(room_l=10.0) == 10.0 and box.sump_l == pytest.approx(4.0), "only as far as the tank has room"
    assert box.drain() == pytest.approx(4.0) and box.sump_l == 0.0


def test_kinds_of_food_differ_citrus_sours_the_box_and_eggshell_and_bedding_bring_it_back():
    plain, sour = WormBox(), WormBox()
    plain.feed(1000)
    sour.feed(1000, wet=1.2, heat=1.0, acid=3.0)                  # a kilo of citrus
    assert plain.ph() > 6.5 and sour.ph() < 5.6
    assert sour.moisture > plain.moisture, "it is wetter too"
    assert sour.stress() > 0.0 == plain.stress(), "a sour box wears the worms down"
    run(sour, 24)
    low = sour.ph()
    sour.feed(100, wet=0.0, heat=0.0, acid=-2.0)                  # crushed eggshell
    sour.add_bedding(1000)
    assert sour.ph() > low + 0.2
    run(sour, 24 * 10)
    assert sour.ph() > 6.4, "and it fades over days"
    hot = WormBox()
    hot.feed(1000, heat=3.0)                                      # meat heats three times as much
    assert hot.heat_c() > 2.5 * plain.heat_c() - 1.0 and hot.heat_c() > plain.heat_c() + 4.0


def test_a_rested_half_is_ready_after_ten_days_and_its_castings_go_out_in_portions():
    box = WormBox()
    assert box.take_castings(100) == 0.0, "nothing is taken from a box that is not rested"
    box.rest(1)
    run(box, 24 * 5)
    assert not box.ready() and box.take_castings(100) == 0.0 and box.harvest()["restDays"] == 5.0
    run(box, 24 * 5 + 1)
    assert box.ready() and box.ready_g == pytest.approx(box.castings_g * 0.5)
    weight, ready = box.weight_g(), box.ready_g
    assert box.take_castings(300) == 300 and box.weight_g() == pytest.approx(weight - 300) and box.taken_g == 300
    assert box.take_castings(10_000) == pytest.approx(ready - 300)
    assert box.harvest() == {"side": None, "restDays": 0.0, "ready": False, "readyAfterDays": 10.0, "nextSide": 0}, "the halves swap: both are fed again"
