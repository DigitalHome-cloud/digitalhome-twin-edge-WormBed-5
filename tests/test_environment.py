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
        assert d["title"] and d["minutes"] > 0, key
        # what chaos may pick must call for a reaction; a cool spell only slows the worms down and calls for none
        assert d["owes"] or key not in disturbances.RANDOM_POOL, key
    assert disturbances.CATALOGUE["cool-spell"]["owes"] == []
    with pytest.raises(ValueError):
        disturbances.get("locusts")
    assert disturbances.chaos_event(99)["rate"] == 24.0


def _at(month, hour, seed=5):
    env = Environment(seed=seed, start_hour=hour, factor=1.0)
    env.set_start(f"2026-{month:02d}-15")
    return env


def test_without_a_start_there_are_no_seasons_as_the_guided_scenarios_expect():
    env = Environment(seed=5, start_hour=12.0)
    snap = env.snapshot()
    assert env.date is None and "season" not in snap and (snap["sunrise"], snap["sunset"]) == (6.0, 18.0)


def test_january_is_colder_and_its_nights_longer_than_july():
    jan, jul = _at(1, 15.0), _at(7, 15.0)
    assert jul.air_c() > jan.air_c() + 12
    (r1, s1), (r7, s7) = jan.daylight(), jul.daylight()
    assert s1 - r1 < 9.5 and s7 - r7 > 15
    assert _at(1, 18.0).sun() == 0 and _at(7, 18.0).sun() > 0, "at six in the evening: dark in January, sun in July"
    assert _at(1, 12.5).sun() < _at(7, 12.5).sun() or _at(7, 12.5).rain_mm_h() > 0, "a lower noon sun in winter"


def test_the_date_runs_from_the_start_and_names_the_season():
    env = Environment(seed=5, start_hour=0.0, factor=60.0)
    env.set_start("2026-04-01")
    assert env.snapshot()["date"] == "2026-04-01" and env.snapshot()["season"] == "spring"
    env.step(24 * 40)                                  # 40 simulated days at 60 min a second
    assert env.date.isoformat() == "2026-05-11" and env.snapshot()["month"] == 5


def test_a_month_of_rain_is_about_the_years_figure_and_the_same_seed_repeats_it():
    def july(seed):
        env = Environment(seed=seed, start_hour=0.0, factor=60.0)
        env.set_start("2026-07-01")
        total = 0.0
        for _ in range(31 * 24):
            total += env.rain_mm_h()
            env.step(60)
        return total
    a = july(5)
    assert 25 < a < 160, f"July's rain {a:.0f} mm against the year's 75"
    assert a == july(5)


def test_a_paused_clock_stands_still():
    env = Environment(seed=5, start_hour=10.0, factor=0.0)
    env.step(120)
    assert env.clock() == "day 1 · 10:00"
