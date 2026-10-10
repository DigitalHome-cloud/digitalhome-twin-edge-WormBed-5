# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
keeper.py — the rules that keep a guild safe: the bottom rung of the algorithm ladder.

Every look at the twin's state runs these rules (PermTek-5
docs/specs/perma5guild.md, sections 3 and 4). A rule that holds gives a
REACTION with an id:

    frost         cold air or a cold worm box   hold feeding and watering; the person insulates
    heat          a hot worm box or hot air     hold feeding; the person shades
    storm         wind above the gantry's limit LOCK THE GANTRY OUT until it passes
    overflow      rain on a full tank           hold watering; the person checks the overflow
    tank-low      under 15 %                    the person refills
    tank-empty    0                             the person refills; the soil is no longer watered from below
    tank-falling  the level drops with no use   the person looks for a leak
    sensor        a reading is missing or frozen  distrust it; hold feeding (the safe side)
    hopper-jam    the scoop comes back empty    hold feeding; the person clears it
    offline       no link to the cloud          work on; queue what is to be written back
    box-full      the worm box is nearly full   the person harvests the compost

Reactions are notices to the person, HOLDS on kinds of job (no proposal, and
a job of that kind is refused) or a LOCKOUT of the gantry (every job
refused). Thresholds have hysteresis, so a reading at the edge does not
flicker. Rules need a little memory between looks (`memory`, a plain dict the
caller keeps): what is on, and recent tank and sensor values.

Pure functions over plain data; standard library only. Higher rungs
(mass-balance feeding, forecasts, anomaly detection, vision) add reactions
beside these; they never take one of these away.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

FROST_AIR_ON, FROST_AIR_OFF = 1.0, 3.0
FROST_BOX_ON, FROST_BOX_OFF = 5.0, 7.0
HEAT_BOX_ON, HEAT_BOX_OFF = 30.0, 28.0
HEAT_AIR_ON, HEAT_AIR_OFF = 32.0, 30.0
STORM_ON, STORM_OFF = 12.0, 9.0          # m/s
TANK_LOW_PCT = 15.0
TANK_FALL_L, TANK_FALL_MIN = 5.0, 60.0   # this much lost within this many simulated minutes, with nothing drawing on it
STUCK_MIN, STUCK_AIR_C = 120.0, 3.0      # a box temperature unchanged this long while the air moved this much
BOX_FULL_PCT = 90.0

TEXT = {
    "frost": "Frost: feeding and watering are on hold. Insulate the worm box; in a hard frost take the worms indoors.",
    "heat": "Heat: feeding is on hold. Shade the worm box and keep its top moist.",
    "storm": "Storm wind: the gantry is parked and locked out until the wind drops.",
    "overflow": "Heavy rain on a full tank: watering is on hold. Check that the overflow runs free.",
    "tank-low": "The tank is low: refill it.",
    "tank-empty": "The tank is empty: the soil is no longer watered from below. Refill it.",
    "tank-falling": "The tank is losing water although nothing draws on it: look for a leak.",
    "sensor": "The worm box's temperature probe gives no trustworthy reading: feeding is on hold until it does.",
    "hopper-jam": "The scoop comes back empty: the hopper is jammed. Feeding is on hold; clear it.",
    "offline": "No link to the cloud: the guild works on, and what it would write back is queued.",
    "box-full": "The worm box is nearly full: harvest the finished compost.",
}
HOLDS = {"frost": ["feed", "water"], "heat": ["feed"], "overflow": ["water"], "sensor": ["feed"], "hopper-jam": ["feed"]}
LOCKS = {"storm"}
ALERTS = {"frost", "heat", "storm", "tank-empty", "tank-falling", "sensor"}


def _switch(memory: Dict[str, Any], key: str, on: bool, off: bool) -> bool:
    """A rule with hysteresis: turns on at `on`, stays until `off`."""
    state = memory.setdefault("on", {})
    if on:
        state[key] = True
    elif off:
        state[key] = False
    return bool(state.get(key))


def evaluate(bed: Optional[Dict[str, Any]], memory: Dict[str, Any], offline: bool = False) -> Dict[str, Any]:
    """The reactions in force now. `bed` is the twin's /bed/state; None when nothing runs."""
    active: List[str] = []
    if bed:
        env = bed.get("env") or {}
        tank = bed.get("tank") or {}
        box = bed.get("wormBin")
        air, wind, rain = env.get("airC"), env.get("windMs"), env.get("rainMmH") or 0.0
        now_min = (env.get("day", 1) - 1) * 1440.0 + env.get("hour", 0.0) * 60.0
        box_c = box.get("tempC") if box else None

        if air is not None:
            cold_box = box_c is not None and box_c <= FROST_BOX_ON
            warm_box = box_c is None or box_c >= FROST_BOX_OFF
            if _switch(memory, "frost", air <= FROST_AIR_ON or cold_box, air >= FROST_AIR_OFF and warm_box):
                active.append("frost")
            hot_box = box_c is not None and box_c >= HEAT_BOX_ON
            cool_box = box_c is None or box_c <= HEAT_BOX_OFF
            if _switch(memory, "heat", air >= HEAT_AIR_ON or hot_box, air <= HEAT_AIR_OFF and cool_box):
                active.append("heat")
        if wind is not None and _switch(memory, "storm", wind >= STORM_ON, wind <= STORM_OFF):
            active.append("storm")

        if tank.get("capacity"):
            litres, cap = tank.get("litres", 0.0), tank["capacity"]
            if tank.get("overflowing") or (rain >= 20.0 and litres >= 0.98 * cap):
                active.append("overflow")
            if litres <= 0:
                active.append("tank-empty")
            elif 100.0 * litres / cap < TANK_LOW_PCT:
                active.append("tank-low")
            # falling with nothing drawing on it: compare with the level a while ago
            seen = [s for s in memory.setdefault("tank", []) if now_min - s[0] <= TANK_FALL_MIN and s[0] <= now_min]
            seen.append((now_min, litres))
            memory["tank"] = seen[-240:]
            lost = max(l for _, l in seen) - litres
            if _switch(memory, "tank-falling", lost >= TANK_FALL_L and litres > 0, lost < 1.0):
                active.append("tank-falling")

        if box:
            if box_c is None:
                active.append("sensor")                         # dead
            elif air is not None:
                last = memory.get("box")
                if last is None or last["value"] != box_c:
                    memory["box"] = {"value": box_c, "since": now_min, "air": air}
                elif now_min - last["since"] >= STUCK_MIN and abs(air - last["air"]) >= STUCK_AIR_C:
                    active.append("sensor")                     # stuck: the air moved, the box "did not"
            if (box.get("fillPct") or 0) >= BOX_FULL_PCT:
                active.append("box-full")
        if (bed.get("hopper") or {}).get("jammed"):
            active.append("hopper-jam")
    if offline:
        active.append("offline")

    holds = sorted({kind for rule in active for kind in HOLDS.get(rule, [])})
    lockout = next((TEXT[rule] for rule in active if rule in LOCKS), None)
    return {"active": active, "holds": holds, "lockout": lockout,
            "notices": [{"id": r, "text": TEXT[r], "level": "alert" if r in ALERTS else "note"} for r in active]}


def refuses(result: Dict[str, Any], kind: str) -> Optional[str]:
    """Why a job of this kind may not run now, or None."""
    if result.get("lockout"):
        return result["lockout"]
    if kind in result.get("holds", []):
        rule = next(r for r in result["active"] if kind in HOLDS.get(r, []))
        return TEXT[rule]
    return None


def log_changes(folder: Path, result: Dict[str, Any], memory: Dict[str, Any], about: str, sim_clock: str = "") -> List[Dict[str, Any]]:
    """Append what turned on or off since the last look to observations.jsonl (SOSA-shaped). Returns what was written."""
    before, now = set(memory.get("logged", [])), set(result["active"])
    written = []
    for rule, value in [(r, "on") for r in sorted(now - before)] + [(r, "off") for r in sorted(before - now)]:
        written.append({"@type": "sosa:Observation", "madeBySensor": "keeper", "observedProperty": f"keeper:{rule}",
                        "hasSimpleResult": value, "hasFeatureOfInterest": about, "simClock": sim_clock,
                        "resultTime": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
    if written:
        Path(folder).mkdir(parents=True, exist_ok=True)
        with open(Path(folder) / "observations.jsonl", "a", encoding="utf-8") as fh:
            for row in written:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    memory["logged"] = sorted(now)
    return written
