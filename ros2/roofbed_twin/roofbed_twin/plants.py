# SPDX-License-Identifier: GPL-3.0-or-later
"""The plants of the bed, as the twin grows them (PermTek-5 ADR 0029). Plain Python: no ROS, no Webots.

A sown cell's plant comes up after its crop's germination days, counted only on days warm and moist enough;
some do not come up at all. Then it grows, faster when it is warm, and is ready after the crop's days to
harvest. It is lost to frost (a tender crop under 0 °C, any crop under -6 °C), to drought (two days below the
wilting point), or when it stays down too long. A person harvests and clears; the cell is free again.

    plants = Plants(crops, seed)          crops: {crop id: {germinationDays, daysToHarvest, sowMinTempC, frostTender, perennial}}
    plants.sow(key, crop)                 a sowing at a cell ("row_col")
    plants.step(cells, dmin, mean_c, air_c)    simulated minutes; returns the cells whose status changed
    plants.state(key)                     {"growth" 0..1, "up", "ready", "why"} for /bed/state

Rules of thumb, deterministic for a seed: the same run gives the same bed.
"""
import random

GERM_MOIST = 22.0          # % soil moisture a seed needs to swell
WILT_BELOW = 12.0          # % under which a plant wilts
DRY_DAYS_LOST = 2.0        # wilting this long kills it
STAY_DOWN = 3.0            # not up after this many times its germination days: lost
DOWN_SHARE = 0.08          # of sowings that never come up, whatever the weather
GROW_FROM_C = 5.0          # no growth under this day mean
GROW_FULL_C = 18.0         # full speed from this day mean
FROST_TENDER_C = 0.0
FROST_HARDY_C = -6.0
DEFAULT_GERM_DAYS = 10
DEFAULT_HARVEST_DAYS = 70
MIN = 1440.0               # minutes in a day


class Plants:
    def __init__(self, crops=None, seed=5):
        self.crops = dict(crops or {})
        self.rng = random.Random(seed * 71 + 3)
        self.p = {}

    def _crop(self, crop):
        return self.crops.get(crop) or {}

    def sow(self, key, crop):
        """A new sowing at the cell: whether it will come up is decided now, so a run can be repeated."""
        self.p[key] = {"crop": crop, "germ": 0.0, "waited": 0.0, "growth": 0.0, "dry": 0.0, "up": False,
                       "dud": self.rng.random() < DOWN_SHARE, "why": None}

    def grown(self, key, crop, growth=0.6):
        """A plant already standing when the run begins (a bed started as it is)."""
        self.p[key] = {"crop": crop, "germ": 0.0, "waited": 0.0, "growth": growth, "dry": 0.0, "up": True, "dud": False, "why": None}

    def drop(self, key):
        self.p.pop(key, None)

    def state(self, key):
        x = self.p.get(key)
        if not x:
            return {}
        return {"growth": round(x["growth"], 2), "up": x["up"], "ready": x["up"] and x["growth"] >= 1.0, "why": x["why"]}

    def step(self, cells, dmin, mean_c, air_c):
        """Advance every plant by `dmin` simulated minutes. `cells` are the driver's ("row_col" -> {"status", "moisture"});
        the status of a cell whose plant came up or was lost is set here. Returns the keys that changed."""
        changed = []
        days = dmin / MIN
        warmth = max(0.0, min(1.0, (mean_c - GROW_FROM_C) / (GROW_FULL_C - GROW_FROM_C)))
        for key, x in self.p.items():
            c = cells.get(key)
            if c is None or c["status"] not in ("CellSown", "CellGrowing"):
                continue
            crop = self._crop(x["crop"])
            # frost
            if air_c < (FROST_TENDER_C if crop.get("frostTender") else FROST_HARDY_C):
                c["status"], x["why"] = "CellLost", "frost"
                changed.append(key)
                continue
            # drought, once it is up
            if x["up"]:
                x["dry"] = x["dry"] + days if c["moisture"] < WILT_BELOW else max(0.0, x["dry"] - days)
                if x["dry"] >= DRY_DAYS_LOST:
                    c["status"], x["why"] = "CellLost", "dry"
                    changed.append(key)
                    continue
                if crop.get("perennial"):
                    x["growth"] = min(1.0, x["growth"] + days * warmth / (crop.get("daysToHarvest") or DEFAULT_HARVEST_DAYS))
                else:
                    x["growth"] += days * warmth / (crop.get("daysToHarvest") or DEFAULT_HARVEST_DAYS)
                continue
            # coming up: only warm, moist days count
            need = crop.get("germinationDays") or DEFAULT_GERM_DAYS
            x["waited"] += days
            if c["moisture"] >= GERM_MOIST and mean_c >= (crop.get("sowMinTempC") or GROW_FROM_C) - 2.0:
                x["germ"] += days
            if x["germ"] >= need and not x["dud"]:
                x["up"] = True
                c["status"] = "CellGrowing"
                changed.append(key)
            elif x["waited"] >= STAY_DOWN * need:
                c["status"], x["why"] = "CellLost", "cold" if mean_c < (crop.get("sowMinTempC") or GROW_FROM_C) - 2.0 else "notUp"
                changed.append(key)
        return changed
