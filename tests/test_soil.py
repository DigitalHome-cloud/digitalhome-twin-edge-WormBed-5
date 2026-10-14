# SPDX-License-Identifier: GPL-3.0-or-later
"""The bed's soil-life index, on plain Python."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "roofbed_twin"))
from roofbed_twin.soil import START, SoilLife, allows  # noqa: E402


def days(life, n, **kw):
    for _ in range(n * 24):
        life.step(60, **kw)
    return life.value


GOOD = {"moisture": 45, "air_c": 14}


def test_castings_and_a_mulch_feed_it_over_weeks():
    bare, fed = SoilLife(), SoilLife()
    days(bare, 60, covered=0.1, **GOOD)
    days(fed, 60, compost_g_m2=1500, mulch_cm=3, covered=0.8, **GOOD)
    assert fed.value > START + 0.2 and fed.value > 2 * bare.value
    assert days(SoilLife(), 7, compost_g_m2=1500, mulch_cm=3, covered=0.8, **GOOD) < fed.value - 0.1, "it builds up slowly"


def test_drought_and_frost_knock_it_down_within_days():
    for kw in ({"moisture": 8, "air_c": 14}, {"moisture": 45, "air_c": -5}):
        life = SoilLife(0.8)
        assert days(life, 10, compost_g_m2=1500, mulch_cm=3, covered=0.8, **kw) < 0.3


def test_a_soaked_bed_is_bad_and_bare_soil_is_worse_than_covered():
    assert allows(95, 14, 0, 0, 0.5) == 0.0
    assert allows(45, 14, 0, 0, 0.0) < allows(45, 14, 0, 0, 1.0)
    assert allows(45, 14, 0, 4, 0.0) > allows(45, 14, 0, 0, 0.0), "a mulch covers too"
