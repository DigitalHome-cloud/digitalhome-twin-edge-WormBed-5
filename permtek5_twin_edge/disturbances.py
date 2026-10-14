# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
disturbances.py — the chaos monkeys: what can go wrong around a guild, and what the guild owes.

A guild lives outdoors (PermTek-5 docs/specs/perma5guild.md, section 4). Each
entry says what it does to the simulated environment or devices (`effect`,
laid over the weather for `minutes` of simulated time) and which reaction of
the keeper it must bring about (`owes`: ids from keeper.py). A chaos run
injects one and checks that the reaction came.

Injected by hand from the page, as a scenario step, or at random at a rate.
Network loss is this app's own (the simulation knows nothing of the cloud).
Standard library only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .i18n import _

CATALOGUE: Dict[str, Dict[str, Any]] = {
    "cold-snap": {"title": "Cold snap", "about": "The air drops far below freezing within hours.",
                  "effect": {"airOffsetC": -22.0}, "minutes": 600, "owes": ["frost"]},
    "cool-spell": {"title": "Cool fortnight", "about": "Two weeks some 9 °C colder than usual, without frost: the worms stop breeding.",
                   "effect": {"airOffsetC": -9.0}, "minutes": 14 * 1440, "owes": []},
    "heat-wave": {"title": "Heat wave", "about": "Days well above 30 °C: the worm box overheats first.",
                  "effect": {"airOffsetC": 19.0}, "minutes": 1440, "owes": ["heat"]},
    "cloudburst": {"title": "Cloudburst", "about": "Heavy rain fills the tank to its overflow.",
                   "effect": {"rainMmH": 60.0}, "minutes": 90, "owes": ["overflow"]},
    "storm": {"title": "Storm wind", "about": "Gusts above what the gantry may move in.",
              "effect": {"windMs": 20.0}, "minutes": 180, "owes": ["storm"]},
    "tank-leak": {"title": "Tank leak", "about": "The tank loses water although nothing draws on it.",
                  "effect": {"tankLeakLPerMin": 3.0}, "minutes": 240, "owes": ["tank-falling"]},
    "sensor-dead": {"title": "Dead sensor", "about": "The worm box's temperature probe stops answering.",
                    "effect": {"sensor": {"path": "wormBin.tempC", "mode": "dead"}}, "minutes": 720, "owes": ["sensor"]},
    "sensor-stuck": {"title": "Stuck sensor", "about": "The worm box's temperature probe repeats one value.",
                     "effect": {"sensor": {"path": "wormBin.tempC", "mode": "stuck"}}, "minutes": 720, "owes": ["sensor"]},
    "scale-dead": {"title": "Dead load cells", "about": "The worm box's weight reading is gone: the feeder has nothing to go by.",
                   "effect": {"sensor": {"path": "wormBin.weightKg", "mode": "dead"}}, "minutes": 720, "owes": ["sensor"]},
    "lid-open": {"title": "Lid left open", "about": "The worm box's lid stands open: it dries, cools faster, and rain gets in.",
                 "effect": {"lidOpen": True}, "minutes": 600, "owes": ["lid"]},
    "wrong-food": {"title": "Wrong food in the hopper", "about": "Somebody tips cooked leftovers and a lot of citrus into the hopper.",
                   "effect": {}, "minutes": 1, "owes": ["guide-load"],
                   "event": {"op": "hopper", "mix": {"vegetable": 500, "cooked": 200, "citrus": 300}}},
    "weed-flush": {"title": "A flush of weeds", "about": "After wet days a dozen weeds come up between the plants, a few of them already big.",
                   "effect": {}, "minutes": 1, "owes": ["weeds"], "event": {"op": "weeds", "count": 12, "large": 3}},
    "hopper-jam": {"title": "Hopper jam", "about": "The scoop comes back empty although the hopper holds scraps.",
                   "effect": {"hopperJam": True}, "minutes": 720, "owes": ["hopper-jam"]},
    "network-loss": {"title": "Network loss", "about": "No link to the cloud: the guild works on, and queues what it would write back.",
                     "effect": {}, "minutes": 240, "owes": ["offline"], "app": True},
}

#: what a random chaos draw may pick: weather and device trouble, one at a time
RANDOM_POOL = ["cold-snap", "heat-wave", "cloudburst", "storm", "tank-leak", "sensor-dead", "hopper-jam"]


def get(key: str) -> Dict[str, Any]:
    if key not in CATALOGUE:
        raise ValueError("no such disturbance: " + ", ".join(CATALOGUE))
    return CATALOGUE[key]


def event(key: str, minutes: Optional[float] = None) -> Dict[str, Any]:
    """The bed event that puts a disturbance in force in the simulation."""
    d = get(key)
    return {"op": "disturb", "id": key, "title": d["title"], "effect": d["effect"], "minutes": float(minutes or d["minutes"])}


def chaos_event(rate_per_day: float) -> Dict[str, Any]:
    """Random disturbances at a rate (per simulated day); 0 turns them off."""
    pool = [{"id": k, "title": CATALOGUE[k]["title"], "effect": CATALOGUE[k]["effect"], "minutes": CATALOGUE[k]["minutes"]} for k in RANDOM_POOL]
    return {"op": "chaos", "rate": max(0.0, min(float(rate_per_day), 24.0)), "pool": pool}


def listing() -> List[Dict[str, Any]]:
    return [{"id": k, "title": _(d["title"]), "about": _(d["about"]), "owes": d["owes"]} for k, d in CATALOGUE.items()]
