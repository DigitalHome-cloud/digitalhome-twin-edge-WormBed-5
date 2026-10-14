# SPDX-License-Identifier: GPL-3.0-or-later
"""The weather around a guild, and what disturbs it. Plain Python: no ROS, no Webots.

The simulation's own clock runs faster than the wall clock: `factor` is how
many simulated minutes pass in one real second (1: a day in 24 minutes; 30: a
day in 48 seconds). From that clock come the day and the night, an air
temperature that swings around a mean, the sun, showers, and the wind. The
same seed gives the same weather.

A DISTURBANCE is an effect laid over that weather for a number of simulated
minutes: colder or hotter air, heavy rain, storm wind, a leaking tank, a
sensor that died or froze, a jammed hopper. The driver applies what reaches
the bed; this module only says what is in force.

    env = Environment(seed=5)
    env.step(0.5)                         # half a real second later
    env.apply("cold-snap", {"airOffsetC": -18}, minutes=600)
    env.air_c(), env.rain_mm_h(), env.effect("tankLeakLPerMin"), env.sensor("wormBin.tempC")

THE SEASONS. Without a start date the weather is one mild day after another (16 °C on
average, sun from 6 to 18): what the guided scenarios are written for. With one
(`set_start("2026-04-01")`, a normal year's run) the day runs through a generic
temperate year, YEAR: the mean and the day's swing, how long the sun is up and how
high it climbs, and how much rain falls follow the month, eased from one month's
middle to the next. YEAR is not a site; it is the same year the PermTek-5 core's
water tests use.
"""
import datetime as dt
import math
import random

MINUTES_PER_DAY = 1440.0

# a generic temperate year (about 48° N, not a site), one row a month:
#   mean °C, the day's swing ± °C, rain mm in the month, daylight hours, the noon sun (1: a clear June noon)
YEAR = [
    (2.5, 3.0, 60, 8.6, 0.20), (3.5, 3.5, 50, 10.0, 0.30), (7.0, 4.5, 55, 11.8, 0.50), (10.5, 5.5, 50, 13.6, 0.75),
    (14.5, 6.0, 65, 15.2, 0.92), (17.5, 6.0, 70, 16.0, 1.00), (19.5, 6.5, 75, 15.6, 1.00), (19.0, 6.0, 70, 14.2, 0.87),
    (15.5, 5.5, 60, 12.4, 0.62), (11.0, 4.5, 65, 10.6, 0.37), (6.5, 3.5, 65, 9.0, 0.19), (3.5, 3.0, 65, 8.2, 0.12),
]
SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
           6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
SHOWER_MM_H = 4.0            # a seasonal shower's mean rate: the month's rain comes as so many such hours


class Environment:
    def __init__(self, seed=5, start_hour=10.0, mean_c=16.0, swing_c=5.0, factor=1.0):
        self.seed = seed
        self.rng = random.Random(seed)
        self.minutes = start_hour * 60.0
        self.mean_c, self.swing_c = mean_c, swing_c
        self.factor = factor
        self.active = {}                 # id -> {"effect": {...}, "left": minutes, "title": str}
        self.chaos_rate = 0.0            # random disturbances per simulated day
        self.chaos_pool = []             # [(id, title, effect, minutes)] to draw from
        self._chaos_hour = int(self.minutes // 60)
        self.start = None                # the date of day 1, or None: no seasons (see the module's text)
        # slow drift so two days are not twins: three sines with seeded phases
        self._phases = [self.rng.uniform(0, 2 * math.pi) for _ in range(3)]

    # -- the clock ---------------------------------------------------------------

    def step(self, real_seconds):
        """Advance by real time. Returns the simulated minutes that passed."""
        dmin = max(0.0, real_seconds) * self.factor                  # factor 0: paused
        self.minutes += dmin
        for key in list(self.active):
            self.active[key]["left"] -= dmin
            if self.active[key]["left"] <= 0:
                del self.active[key]
        hour = int(self.minutes // 60)
        while self._chaos_hour < hour:                        # once per simulated hour, a chance of trouble
            self._chaos_hour += 1
            if self.chaos_pool and self.rng.random() < self.chaos_rate / 24.0:
                key, title, effect, minutes = self.rng.choice(self.chaos_pool)
                self.apply(key, effect, minutes, title)
        return dmin

    @property
    def day(self):
        return int(self.minutes // MINUTES_PER_DAY) + 1

    @property
    def hour(self):
        return (self.minutes % MINUTES_PER_DAY) / 60.0

    def clock(self):
        h = self.hour
        return f"day {self.day} · {int(h):02d}:{int((h % 1) * 60):02d}"

    # -- the seasons -------------------------------------------------------------------

    def set_start(self, start):
        """Day 1 is `start` (a date or "YYYY-MM-DD"): the weather follows the year from there. None: no seasons."""
        self.start = None if start in (None, "") else start if isinstance(start, dt.date) else dt.date.fromisoformat(str(start))

    @property
    def date(self):
        return None if self.start is None else self.start + dt.timedelta(days=self.day - 1)

    def _year(self, col):
        """One of YEAR's columns today, eased between the middles of this month and the next or last."""
        d = self.date
        days = 31 if d.month in (1, 3, 5, 7, 8, 10, 12) else 30 if d.month != 2 else 28
        at = (d.month - 1) + (d.day - 0.5 + self.hour / 24.0) / days - 0.5        # in months; 0 = mid-January
        i = math.floor(at)
        f = at - i
        return YEAR[i % 12][col] * (1 - f) + YEAR[(i + 1) % 12][col] * f

    def daylight(self):
        """(sunrise, sunset) in hours."""
        if self.start is None:
            return 6.0, 18.0
        half = self._year(3) / 2.0
        return 12.5 - half, 12.5 + half

    # -- disturbances --------------------------------------------------------------

    def apply(self, key, effect, minutes, title=None):
        self.active[key] = {"effect": dict(effect), "left": float(minutes), "title": title or key}

    def calm(self, key=None):
        if key is None:
            self.active.clear()
        else:
            self.active.pop(key, None)

    def effect(self, name, default=0.0):
        """The sum of one effect over everything in force (offsets and rates add up)."""
        found = [a["effect"][name] for a in self.active.values() if name in a["effect"]]
        return sum(found) if found else default

    def flag(self, name):
        return any(a["effect"].get(name) for a in self.active.values())

    def sensor(self, path):
        """None, "dead" or "stuck" for one reading."""
        for a in self.active.values():
            s = a["effect"].get("sensor")
            if s and s.get("path") == path:
                return s.get("mode", "dead")
        return None

    # -- the weather -----------------------------------------------------------------

    def _drift(self):
        d = self.minutes / MINUTES_PER_DAY
        return 0.9 * math.sin(2 * math.pi * d / 3.1 + self._phases[0]) + 0.5 * math.sin(2 * math.pi * d / 0.37 + self._phases[1])

    def air_c(self):
        daily = math.sin(2 * math.pi * (self.hour - 9.0) / 24.0)          # warmest mid-afternoon, coldest before dawn
        mean, swing = (self.mean_c, self.swing_c) if self.start is None else (self._year(0), self._year(1))
        return mean + swing * daily + self._drift() + self.effect("airOffsetC")

    def rain_mm_h(self):
        # a shower in about one hour of twenty, the same hours for the same seed
        slot = int(self.minutes // 60)
        r = random.Random(self.seed * 1000003 + slot).random()
        if self.start is None:
            shower = 1.5 + 30.0 * r if r < 0.05 else 0.0
        else:                                                         # the month's rain in hours of SHOWER_MM_H on average
            chance = self._year(2) / (30.4 * 24.0 * SHOWER_MM_H)
            shower = 0.5 + 2.0 * (SHOWER_MM_H - 0.5) * (r / chance) if r < chance else 0.0
        return shower + self.effect("rainMmH")

    def sun(self):
        """0 at night, 1 at a clear noon."""
        rise, sets = self.daylight()
        up = math.sin(math.pi * (self.hour - rise) / (sets - rise)) if rise < self.hour < sets else 0.0
        high = 1.0 if self.start is None else self._year(4)
        return max(0.0, up) * high * (0.25 if self.rain_mm_h() > 0 else 1.0)

    def wind_ms(self):
        d = self.minutes / MINUTES_PER_DAY
        return max(0.0, 3.0 + 1.5 * math.sin(2 * math.pi * d / 0.21 + self._phases[2]) + self.effect("windMs"))

    def evaporation(self):
        """How fast soil dries, against a mild still overcast hour (1.0): sun, warmth and wind raise it."""
        return max(0.1, 0.3 + 1.2 * self.sun() + 0.03 * max(0.0, self.air_c() - 10.0) + 0.05 * self.wind_ms())

    def snapshot(self):
        rise, sets = self.daylight()
        seasons = {} if self.start is None else {"date": self.date.isoformat(), "month": self.date.month, "season": SEASONS[self.date.month]}
        return {"day": self.day, "hour": round(self.hour, 2), "clock": self.clock(), "factor": self.factor, **seasons,
                "sunrise": round(rise, 2), "sunset": round(sets, 2), "night": not (rise < self.hour < sets),
                "airC": round(self.air_c(), 1), "sun": round(self.sun(), 2), "rainMmH": round(self.rain_mm_h(), 1),
                "windMs": round(self.wind_ms(), 1), "chaosRate": self.chaos_rate,
                "disturbances": [{"id": k, "title": a["title"], "leftMin": round(a["left"])} for k, a in self.active.items()]}
