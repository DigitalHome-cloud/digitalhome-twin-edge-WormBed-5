# SPDX-License-Identifier: GPL-3.0-or-later
"""The simulated weather and its disturbances: plain Python from the ROS package, no ROS needed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin"))
from roofbed_twin.environment import Environment  # noqa: E402

from permtek5_twin_edge import disturbances  # noqa: E402


def day_of(env, step_s=60.0):
    """(hour, air, sun) every simulated hour for one day."""
    out = []
    for _ in range(24):
        out.append((round(env.hour, 1), env.air_c(), env.sun()))
        env.step(step_s)
    return out


def test_the_same_seed_gives_the_same_day_and_another_seed_another():
    a, b, c = (day_of(Environment(seed=s, factor=1.0)) for s in (5, 5, 6))
    assert a == b and a != c


def test_night_is_colder_and_dark_and_the_clock_reads_right():
    env = Environment(seed=5, start_hour=10.0, factor=1.0)
    assert env.clock() == "day 1 · 10:00"
    by_hour = {round(h): (air, sun) for h, air, sun in day_of(env)}
    assert by_hour[15][0] > by_hour[4][0] + 5, "mid-afternoon is warmer than before dawn"
    assert by_hour[3][1] == 0 and by_hour[12][1] > 0.2
    assert env.day == 2, "24 hours later it is the next day"


def test_the_time_factor_makes_simulated_time_run_faster():
    slow, fast = Environment(factor=1.0), Environment(factor=30.0)
    assert slow.step(10) == 10 and fast.step(10) == 300
    assert fast.minutes - slow.minutes == 290


def test_a_disturbance_lays_its_effect_over_the_weather_and_ends_by_itself():
    env = Environment(seed=5, factor=1.0)
    before = env.air_c()
    cold = disturbances.event("cold-snap")
    env.apply(cold["id"], cold["effect"], cold["minutes"], cold["title"])
    assert env.air_c() == pytest.approx(before - 22.0) and env.air_c() < 0
    assert env.snapshot()["disturbances"][0]["title"] == "Cold snap"
    env.step(601 * 60 / 1.0 / 60)                         # its 600 simulated minutes pass
    assert env.snapshot()["disturbances"] == [] and env.air_c() > 0


def test_effects_add_up_and_sensors_and_flags_are_reported():
    env = Environment(seed=5)
    for key in ("storm", "tank-leak", "sensor-dead", "hopper-jam"):
        e = disturbances.event(key)
        env.apply(key, e["effect"], e["minutes"])
    assert env.wind_ms() > 20 and env.effect("tankLeakLPerMin") == 3.0
    assert env.sensor("wormBin.tempC") == "dead" and env.sensor("tank.litres") is None
    assert env.flag("hopperJam") is True
    env.calm("storm")
    assert env.wind_ms() < 10
    env.calm()
    assert not env.flag("hopperJam") and env.effect("tankLeakLPerMin") == 0.0


def test_random_chaos_draws_from_the_pool_at_a_rate_and_repeats_with_the_seed():
    def run(seed):
        env = Environment(seed=seed, factor=60.0)
        chaos = disturbances.chaos_event(12)
        env.chaos_rate, env.chaos_pool = chaos["rate"], [(p["id"], p["title"], p["effect"], p["minutes"]) for p in chaos["pool"]]
        seen = []
        for _ in range(48):                                   # two simulated days, an hour a step
            env.step(60)
            seen.append(tuple(sorted(env.active)))
        return seen
    assert run(5) == run(5)
    assert any(run(5)), "at twelve a day something happens within two days"
    quiet = Environment(seed=5, factor=60.0)
    for _ in range(48):
        quiet.step(60)
    assert quiet.active == {}, "no rate, no chaos"


def test_the_catalogue_names_what_each_disturbance_owes():
    assert {"cold-snap", "heat-wave", "cloudburst", "storm", "tank-leak", "sensor-dead", "hopper-jam", "network-loss"} <= set(disturbances.CATALOGUE)
    for key, d in disturbances.CATALOGUE.items():
        assert d["owes"] and d["title"] and d["minutes"] > 0, key
    with pytest.raises(ValueError):
        disturbances.get("locusts")
    assert disturbances.chaos_event(99)["rate"] == 24.0
