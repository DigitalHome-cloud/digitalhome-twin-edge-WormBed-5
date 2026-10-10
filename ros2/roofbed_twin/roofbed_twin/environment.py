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
"""
import math
import random

MINUTES_PER_DAY = 1440.0


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
        # slow drift so two days are not twins: three sines with seeded phases
        self._phases = [self.rng.uniform(0, 2 * math.pi) for _ in range(3)]

    # -- the clock ---------------------------------------------------------------

    def step(self, real_seconds):
        """Advance by real time. Returns the simulated minutes that passed."""
        dmin = max(0.0, real_seconds) * self.factor
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
        return self.mean_c + self.swing_c * daily + self._drift() + self.effect("airOffsetC")

    def rain_mm_h(self):
        # a shower in about one hour of twenty, the same hours for the same seed
        slot = int(self.minutes // 60)
        r = random.Random(self.seed * 1000003 + slot).random()
        shower = 1.5 + 30.0 * r if r < 0.05 else 0.0
        return shower + self.effect("rainMmH")

    def sun(self):
        """0 at night, 1 at a clear noon."""
        up = math.sin(math.pi * (self.hour - 6.0) / 12.0) if 6.0 < self.hour < 18.0 else 0.0
        return max(0.0, up) * (0.25 if self.rain_mm_h() > 0 else 1.0)

    def wind_ms(self):
        d = self.minutes / MINUTES_PER_DAY
        return max(0.0, 3.0 + 1.5 * math.sin(2 * math.pi * d / 0.21 + self._phases[2]) + self.effect("windMs"))

    def evaporation(self):
        """How fast soil dries, against a mild still overcast hour (1.0): sun, warmth and wind raise it."""
        return max(0.1, 0.3 + 1.2 * self.sun() + 0.03 * max(0.0, self.air_c() - 10.0) + 0.05 * self.wind_ms())

    def snapshot(self):
        return {"day": self.day, "hour": round(self.hour, 2), "clock": self.clock(), "factor": self.factor,
                "airC": round(self.air_c(), 1), "sun": round(self.sun(), 2), "rainMmH": round(self.rain_mm_h(), 1),
                "windMs": round(self.wind_ms(), 1), "chaosRate": self.chaos_rate,
                "disturbances": [{"id": k, "title": a["title"], "leftMin": round(a["left"])} for k, a in self.active.items()]}
