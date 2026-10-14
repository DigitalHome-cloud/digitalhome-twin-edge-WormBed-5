# SPDX-License-Identifier: GPL-3.0-or-later
"""How alive the bed's soil is: one index, not a count. Plain Python: no ROS, no Webots.

A roof bed's soil life (topsoil worms, springtails, fungi, bacteria) cannot be
counted, and a 15 cm bed on a roof is a poor home for most of it: it dries
through in summer and freezes through in winter, and a lightweight substrate
holds little organic matter. So the twin keeps an INDEX, 0..1, that says how
good the bed has been for soil life lately:

  food      organic matter: compost top-dressed from the worm box, and a mulch
  water     soil moisture in range (too dry or soaked is bad)
  warmth    above freezing; it stops in frost
  cover     plants and mulch over the soil; bare soil bakes and washes out

The index moves towards what the conditions allow: up slowly (weeks, life
builds up), down faster (days, a drought or frost kills). Every number is a
rule of thumb, chosen to show the direction, not measured.

    life = SoilLife()
    life.step(minutes=60, moisture=40, air_c=12, compost_g_m2=200, mulch_cm=2, covered=0.6)
    life.value  # -> 0..1
"""
import math

START = 0.35                     # a new bed of bought substrate
RISE_DAYS, FALL_DAYS = 20.0, 6.0
COMPOST_FULL_G_M2 = 1500.0       # top-dressed castings per m² that count as a rich soil
MINUTES_PER_DAY = 1440.0


def ramp(x, lo, hi):
    """0 at lo, 1 at hi, straight between (lo may be above hi)."""
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def allows(moisture, air_c, compost_g_m2, mulch_cm, covered):
    """What the conditions allow now, 0..1."""
    food = min(1.0, 0.3 + 0.5 * compost_g_m2 / COMPOST_FULL_G_M2 + 0.08 * mulch_cm)
    water = min(ramp(moisture, 12.0, 30.0), ramp(moisture, 90.0, 75.0))
    warmth = ramp(air_c, -2.0, 6.0)
    cover = 0.4 + 0.6 * max(0.0, min(1.0, max(covered, 0.25 * mulch_cm)))
    return food * water * warmth * cover


class SoilLife:
    def __init__(self, value=START):
        self.value = value

    def step(self, minutes, moisture, air_c, compost_g_m2=0.0, mulch_cm=0.0, covered=0.0):
        target = allows(moisture, air_c, compost_g_m2, mulch_cm, covered)
        days = RISE_DAYS if target > self.value else FALL_DAYS
        self.value += (target - self.value) * (1.0 - math.exp(-minutes / MINUTES_PER_DAY / days))
        return self.value
