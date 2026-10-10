# SPDX-License-Identifier: GPL-3.0-or-later
"""The worm box as a living thing, and the sensors that watch it. Plain Python: no ROS, no Webots.

A worm box turns kitchen scraps into castings, a liquid and a little heat.
How fast depends on how the worms are doing. This is a small model of that,
good enough to try rules and a feeder against. Every number is a rule of
thumb from composting practice, not a measurement of a real box.

WHAT IS IN THE BOX (grams, wet):  food not yet eaten, bedding, castings, worms.

EACH SIMULATED MINUTE
  activity   0..1: how hard the worms work. Best at 15-25 °C in the core and
             65-85 % moisture; nothing under 4 °C or over 32 °C; less without air.
  eating     worms eat up to half their own weight a day, times activity and
             health. Of what is eaten, 40 % stays as castings, 30 % drains
             as liquid (to the tank), 30 % leaves as gas and vapour. So the
             box LOSES 0.6 g for every gram eaten: that is what load cells see.
  heat       food that lies uneaten composts and warms the core (about
             2.5 °C per kg). The edge follows the air, slowly; wrapped in
             insulation, five times more slowly.
  air        much uneaten food, or a soaked box, uses the air up. An airless
             box smells (the gas sensor) and harms the worms.
  health     0..1. Frost, heat, drought, a soaked or airless box wear it
             down. When worms may leave into the soil (the guild's setting)
             they dodge half the harm and come back when it is good again; in
             a closed box what is lost stays lost.

THE SENSORS  (`Sensors.read`) give what real ones would: the value plus a
little noise; None from a dead one; the old value from a stuck one.

    box = WormBox(may_leave=True)
    box.feed(300)
    out = box.step(minutes=1.0, air_c=16.0)      # -> {"liquidL": ..., "eatenG": ...}
"""
import math
import random

EATS_OWN_WEIGHT_PER_DAY = 0.5
TO_CASTINGS, TO_LIQUID, TO_AIR = 0.4, 0.3, 0.3
LOSS_PER_G_EATEN = TO_LIQUID + TO_AIR          # what the load cells see for every gram eaten
HEAT_C_PER_KG_FOOD = 2.5
EDGE_LAG_MIN, CORE_LAG_MIN = 180.0, 60.0
INSULATED_LAG_X = 5.0
EDGE_ABOVE_AIR_C = 1.0
FOOD_WETS_PER_G = 1.0 / 200.0                  # percent of moisture a gram of scraps adds
BEDDING_DRIES_PER_G = 1.0 / 60.0               # percent of moisture a gram of dry paper takes up
DRIES_PER_MIN, DRIES_OPEN_PER_MIN = 0.002, 0.01
FOOD_AIRLESS_G = (1500.0, 4000.0)              # uneaten food: air starts to run short, is gone
WET_AIRLESS_PCT = (85.0, 95.0)
FULL_G = 20000.0
MINUTES_PER_DAY = 1440.0


def ramp(x, lo, hi):
    """0 at lo, 1 at hi, straight between (lo may be above hi)."""
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def temp_factor(c):
    """How hard worms work at this core temperature, 0..1."""
    return min(ramp(c, 4.0, 15.0), ramp(c, 32.0, 25.0))


def moisture_factor(pct):
    return min(ramp(pct, 40.0, 65.0), ramp(pct, 95.0, 85.0))


class WormBox:
    def __init__(self, may_leave=True, air_c=16.0, worms_g=800.0, food_g=400.0, bedding_g=4000.0, castings_g=1000.0):
        self.may_leave = may_leave
        self.worms_g, self.food_g, self.bedding_g, self.castings_g = worms_g, food_g, bedding_g, castings_g
        self.moisture = 70.0
        self.edge_c = air_c + EDGE_ABOVE_AIR_C
        self.core_c = self.edge_c + self.heat_c()
        self.air = 1.0                   # 1: fresh, 0: airless
        self.health = 1.0
        self.insulated = False
        self.lid_open = False
        self.fed_g = 0.0                 # what the robot dropped in, in all
        self.feedings = 0
        self.eaten_g = 0.0

    # -- what a robot or a person does ---------------------------------------------

    def feed(self, grams, by_robot=True):
        grams = max(0.0, min(grams, FULL_G - self.weight_g()))
        self.food_g += grams
        self.moisture = min(98.0, self.moisture + grams * FOOD_WETS_PER_G)
        if by_robot:
            self.fed_g += grams
            self.feedings += 1
        return grams

    def add_bedding(self, grams):
        """Dry shredded paper: takes up moisture and lets air in."""
        grams = max(0.0, min(grams, FULL_G - self.weight_g()))
        self.bedding_g += grams
        self.moisture = max(40.0, self.moisture - grams * BEDDING_DRIES_PER_G)
        self.air = min(1.0, self.air + grams / 1000.0)

    def moisten(self, ml):
        self.moisture = min(98.0, self.moisture + max(0.0, ml) / 100.0)

    def take_out(self, grams):
        """A person takes uneaten food out again."""
        self.food_g -= max(0.0, min(grams, self.food_g))

    # -- readings of the truth -------------------------------------------------------

    def weight_g(self):
        return self.worms_g + self.food_g + self.bedding_g + self.castings_g

    def heat_c(self):
        return HEAT_C_PER_KG_FOOD * self.food_g / 1000.0

    def activity(self):
        return temp_factor(self.core_c) * moisture_factor(self.moisture) * (0.3 + 0.7 * self.air)

    def eats_per_day(self):
        """Grams a day at this moment, if there is that much food."""
        return self.worms_g * EATS_OWN_WEIGHT_PER_DAY * self.activity() * self.health

    def stress(self):
        """0: fine. 1: conditions that kill within about a day."""
        c, m = self.core_c, self.moisture
        return max(ramp(c, 5.0, 0.0), ramp(c, 30.0, 35.0), ramp(m, 45.0, 35.0), ramp(m, 92.0, 98.0), ramp(self.air, 0.4, 0.0))

    def gas(self):
        """The smell a gas sensor reads, 0..100: 0 fresh, 100 airless and rotting."""
        return 100.0 * (1.0 - self.air)

    # -- one step ----------------------------------------------------------------------

    def step(self, minutes, air_c, rain_mm_h=0.0):
        if minutes <= 0:
            return {"liquidL": 0.0, "eatenG": 0.0}
        open_x = 3.0 if self.lid_open else 1.0
        lag = EDGE_LAG_MIN * (INSULATED_LAG_X if self.insulated else 1.0) / open_x
        self.edge_c += (air_c + EDGE_ABOVE_AIR_C - self.edge_c) * (1.0 - math.exp(-minutes / lag))
        self.core_c += (self.edge_c + self.heat_c() - self.core_c) * (1.0 - math.exp(-minutes / CORE_LAG_MIN))

        self.moisture = max(30.0, self.moisture - (DRIES_OPEN_PER_MIN if self.lid_open else DRIES_PER_MIN) * minutes)
        if self.lid_open and rain_mm_h > 0:
            self.moisture = min(98.0, self.moisture + rain_mm_h * minutes / 60.0)

        wants = 1.0 - max(ramp(self.food_g, *FOOD_AIRLESS_G), ramp(self.moisture, *WET_AIRLESS_PCT))
        self.air += (wants - self.air) * (1.0 - math.exp(-minutes / 240.0))

        eaten = min(self.food_g, self.eats_per_day() * minutes / MINUTES_PER_DAY)
        self.food_g -= eaten
        self.castings_g += eaten * TO_CASTINGS
        self.eaten_g += eaten

        days = minutes / MINUTES_PER_DAY
        harm = self.stress() * (0.5 if self.may_leave else 1.0)
        if harm > 0:
            self.health = max(0.0, self.health - harm * days)
        elif self.may_leave:
            self.health = min(1.0, self.health + 0.2 * days)       # they come back from the soil
        if eaten > 0:
            self.worms_g *= 1.0 + 0.008 * days * self.activity()   # a well-fed population doubles in about three months
        return {"liquidL": eaten * TO_LIQUID / 1000.0, "eatenG": eaten}

    def truth(self):
        """What is really going on: only a simulation can show this, a real box cannot."""
        return {"activity": round(self.activity(), 2), "health": round(self.health, 2), "wormsG": round(self.worms_g),
                "foodG": round(self.food_g), "castingsG": round(self.castings_g), "eatsPerDay": round(self.eats_per_day()),
                "eatenG": round(self.eaten_g), "air": round(self.air, 2)}


NOISE = {"wormBin.tempC": 0.1, "wormBin.edgeC": 0.1, "wormBin.moisture": 0.3, "wormBin.weightKg": 0.015, "wormBin.gas": 1.0}
DIGITS = {"wormBin.weightKg": 3}
FLOOR = {"wormBin.gas": 0.0}


class Sensors:
    """The box's fixed sensors: two temperature probes (core, edge), moisture, load cells, gas, a lid switch."""

    def __init__(self, seed=5):
        self.rng = random.Random(seed * 7919 + 1)
        self.frozen = {}

    def one(self, path, value, mode=None):
        if mode == "dead":
            return None
        if mode == "stuck" and path in self.frozen:
            return self.frozen[path]
        value = round(max(FLOOR.get(path, -1e9), value + self.rng.gauss(0.0, NOISE.get(path, 0.0))), DIGITS.get(path, 1))
        if mode == "stuck":
            self.frozen[path] = value
        else:
            self.frozen.pop(path, None)
        return value

    def read(self, box, mode_of=lambda path: None):
        """`mode_of(path)` -> None | "dead" | "stuck" (environment.Environment.sensor)."""
        s = lambda key, v: self.one(f"wormBin.{key}", v, mode_of(f"wormBin.{key}"))   # noqa: E731
        return {"tempC": s("tempC", box.core_c), "edgeC": s("edgeC", box.edge_c), "moisture": s("moisture", box.moisture),
                "weightKg": s("weightKg", box.weight_g() / 1000.0), "gas": s("gas", max(0.0, box.gas())),
                "lidOpen": box.lid_open}
