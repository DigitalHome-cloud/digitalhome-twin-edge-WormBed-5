#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
wormbox-year.py — a year of the twin's worm box, for the sizing hypothesis (PermTek-5 docs/specs/wormbed5-sizing.md).

    python3 scripts/wormbox-year.py                 # the model as it is
    python3 scripts/wormbox-year.py --dormant       # worms rest between 0 and 5 °C instead of dying slowly

Runs ros2/roofbed_twin/roofbed_twin/wormbox.py (plain Python, no ROS) through two years of a generic
Central European climate (monthly mean air and day-night swing; not any real site) and prints the
second year month by month: food eaten, liquid to the sump, castings made, worms in the box, and the
core's temperature at the month's end. A helper does what a person and the robot would:
  - keeps a little over the worms' best day of food in the box (half their weight, plus a fifth)
  - adds dry bedding once a week, one part to three of scraps (half of it, as the box's paper counts too)
  - sprinkles water when the bedding dries under 62 %
  - wraps the box from November to March
  - harvests half the box's castings and bedding whenever it is 85 % full (the model never lets the
    worms eat paper, so without this the box fills with bedding)

Its numbers are the MODEL's: they are what the Check of the plan compares the real box against, and
what the Act changes when the real box disagrees. Standard library only.
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin"))
from roofbed_twin import wormbox as wb  # noqa: E402

MEAN = [2.5, 3.5, 7.0, 10.5, 14.5, 17.5, 19.5, 19.0, 15.5, 11.0, 6.5, 3.5]   # monthly mean air, °C (generic temperate)
SWING = [3.0, 3.5, 4.5, 5.5, 6.0, 6.0, 6.0, 6.0, 5.5, 4.5, 3.5, 3.0]         # half the day-night range, °C
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


class Dormant(wb.WormBox):
    """Cold makes the worms rest down to 0 °C and harms them only when the box freezes; else as the model."""

    def stress(self):
        c, m = self.core_c, self.moisture
        return max(wb.ramp(c, 0.0, -3.0), wb.ramp(c, 30.0, 35.0), wb.ramp(m, 45.0, 35.0), wb.ramp(m, 92.0, 98.0),
                   wb.ramp(self.air, 0.4, 0.0), wb.ramp(self.acid, 2.5, 5.5))


def year(dormant=False, worms_g=800.0):
    box = (Dormant if dormant else wb.WormBox)(worms_g=worms_g)
    rows, harvested = [], 0.0
    for y in range(2):
        for m in range(12):
            box.insulated = m in (10, 11, 0, 1, 2)
            eaten = liquid = 0.0
            for d in range(DAYS[m]):
                want = max(50.0, 0.6 * box.worms_g)
                if box.food_g < want:
                    box.feed(want - box.food_g)
                if d % 7 == 0:
                    box.add_bedding(want * 7 / 3.0 * 0.5)
                if box.moisture < 62:
                    box.moisten(500)
                if box.weight_g() > 0.85 * wb.FULL_G:
                    harvested += (box.castings_g + box.bedding_g) * 0.5 if y == 1 else 0.0
                    box.castings_g *= 0.5
                    box.bedding_g *= 0.5
                for h in range(24):
                    r = box.step(60, MEAN[m] + SWING[m] * math.sin((h - 9) / 24 * 2 * math.pi))
                    eaten += r["eatenG"]
                    liquid += r["liquidL"]
            if y == 1:
                rows.append({"month": m + 1, "eatenKg": eaten / 1000, "perDayG": eaten / DAYS[m], "liquidL": liquid,
                             "castingsKg": eaten * wb.TO_CASTINGS / 1000, "wormsKg": box.worms_g / 1000, "coreC": box.core_c})
    return rows, harvested / 1000


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--dormant", action="store_true", help="worms rest between 0 and 5 °C instead of dying slowly")
    args = ap.parse_args()
    rows, harvested = year(args.dormant)
    print("month  eaten kg  g/day  liquid l  castings kg  worms kg  core °C")
    for r in rows:
        print(f"{r['month']:5d}  {r['eatenKg']:8.1f}  {r['perDayG']:5.0f}  {r['liquidL']:8.1f}  {r['castingsKg']:11.1f}  {r['wormsKg']:8.2f}  {r['coreC']:7.1f}")
    print(f"year   {sum(r['eatenKg'] for r in rows):8.1f}         {sum(r['liquidL'] for r in rows):8.1f}  "
          f"{sum(r['castingsKg'] for r in rows):11.1f}   (harvested, castings with paper: {harvested:.1f} kg)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
