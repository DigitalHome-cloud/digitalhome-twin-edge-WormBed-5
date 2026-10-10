# SPDX-License-Identifier: GPL-3.0-or-later
"""The mass-balance feeder, run against the worm box model it has never seen the inside of."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "roofbed_twin"))
from roofbed_twin.wormbox import Sensors, WormBox  # noqa: E402

from permtek5_twin_edge import feeder  # noqa: E402


class Rig:
    """A box, its sensors and a clock: what the driver does, without ROS."""

    def __init__(self, air=18.0, **kw):
        self.box, self.probes, self.minutes, self.air, self.hopper = WormBox(air_c=air, **kw), Sensors(seed=3), 600.0, air, 5000
        self.memory: dict = {}
        self.dead = None

    def bed(self):
        mode = lambda p: "dead" if p == self.dead else None   # noqa: E731
        return {"env": {"day": int(self.minutes // 1440) + 1, "hour": (self.minutes % 1440) / 60.0},
                "wormBin": {**self.probes.read(self.box, mode), "fedGrams": round(self.box.fed_g)},
                "hopper": {"grams": self.hopper}}

    def run(self, hours, feed=False):
        fed = []
        for _ in range(int(hours * 2)):                       # a look every half hour
            self.box.step(30.0, self.air)
            self.minutes += 30.0
            feeder.observe(self.memory, self.bed())
            advice = feeder.advise(self.memory, self.bed())
            if feed and advice["grams"] > 0:
                self.hopper -= self.box.feed(advice["grams"])
                fed.append(advice["grams"])
        return advice, fed


def test_it_learns_first_and_proposes_a_small_portion():
    rig = Rig()
    advice, _ = rig.run(3)
    assert advice["state"] == "learning" and advice["grams"] == 150 and "six" in advice["why"]
    rig.box.feed(150)
    rig.box.feed(150)
    assert feeder.advise(rig.memory, rig.bed())["grams"] == 0, "two small portions, then it waits until it knows"


def test_after_a_day_of_readings_it_knows_the_rate_within_a_quarter():
    rig = Rig()
    rig.box.feed(600)
    advice, _ = rig.run(24)
    truth = rig.box.eats_per_day()
    assert advice["state"] == "known" and abs(advice["eatsPerDay"] - truth) / truth < 0.25, (advice, truth)


def test_fed_by_its_advice_the_box_neither_starves_nor_piles_up():
    rig = Rig()
    _, fed = rig.run(24 * 6, feed=True)
    assert rig.box.food_g < 900, "no pile of uneaten food"
    assert rig.box.eaten_g > 6 * 250, "and the worms had enough to eat"
    assert rig.box.health == 1.0 and max(fed) <= 1000


def test_cold_worms_are_fed_less():
    warm, cold = Rig(air=18.0), Rig(air=18.0)
    for rig in (warm, cold):
        rig.box.feed(600)
        rig.run(24)
    cold.air = 6.0
    a_cold, _ = cold.run(12)
    a_warm, _ = warm.run(12)
    assert a_cold["eatsPerDay"] < 0.6 * a_warm["eatsPerDay"]


def test_a_person_changing_the_box_makes_it_start_over_and_a_dead_scale_makes_it_blind():
    rig = Rig()
    rig.box.feed(600)
    rig.run(24)
    rig.box.add_bedding(800)
    advice, _ = rig.run(1)
    assert advice["state"] == "learning"
    rig.dead = "wormBin.weightKg"
    advice, _ = rig.run(1)
    assert advice["state"] == "blind" and advice["grams"] == 0


def test_a_portion_just_dropped_counts_at_once():
    rig = Rig()
    rig.box.feed(600)
    advice, _ = rig.run(30)
    assert advice["grams"] >= 300
    rig.box.feed(advice["grams"])                      # the robot drops it; no new weight sample yet
    again = feeder.advise(rig.memory, rig.bed())
    assert again["grams"] < 100, "it does not propose the same portion twice"
