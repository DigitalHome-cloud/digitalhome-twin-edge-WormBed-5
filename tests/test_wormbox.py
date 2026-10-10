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
