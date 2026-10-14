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
    overfed       the core runs far hotter than the edge   hold feeding; the person takes food out, adds bedding
    airless       the gas sensor smells rot     hold feeding; the person adds dry bedding
    sump-full     the lower bin is full of liquid  hold feeding; the tap is opened
    sour          the pH probe reads acid       hold feeding; the person adds eggshell and bedding
    box-wet       the box is soaked             hold feeding; the person adds dry bedding
    box-dry       the box is drying out         the person sprinkles water
    lid           the lid stands open           the person closes it
    hopper-jam    the scoop comes back empty    hold feeding; the person clears it
    offline       no link to the cloud          work on; queue what is to be written back
    box-full      the worm box is nearly full   the person harvests the compost
    crowded       the worms eat like a crowded box  the person harvests, or starts a second box
    dying-back    they eat a third less than a week ago, with food there  the person checks food, moisture, air
                  (these two go by the feeder's estimate of the worms: `population`)

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

from .i18n import _

FROST_AIR_ON, FROST_AIR_OFF = 1.0, 3.0
FROST_BOX_ON, FROST_BOX_OFF = 5.0, 7.0
HEAT_BOX_ON, HEAT_BOX_OFF = 30.0, 28.0
HEAT_AIR_ON, HEAT_AIR_OFF = 32.0, 30.0
STORM_ON, STORM_OFF = 12.0, 9.0          # m/s
TANK_LOW_PCT = 15.0
TANK_DEAD_L = 1.0                        # under a litre the wick does not reach the water: empty
TANK_FALL_L, TANK_FALL_MIN = 5.0, 60.0   # this much lost within this many simulated minutes, with nothing drawing on it
STUCK_MIN, STUCK_AIR_C = 120.0, 3.0      # a box temperature unchanged this long while the air moved this much
BOX_FULL_PCT = 90.0
OVERFED_ON, OVERFED_OFF = 6.0, 4.5       # °C the core is above the edge: uneaten food is composting (at dusk the core lags a cooling edge by a degree or two)
GAS_ON, GAS_OFF = 60.0, 30.0             # the gas sensor's 0..100
WET_ON, WET_OFF = 90.0, 85.0             # % moisture
DRY_ON, DRY_OFF = 50.0, 55.0
SOUR_ON, SOUR_OFF = 5.6, 6.2             # pH
SUMP_ON, SUMP_OFF = 0.9, 0.5             # of the sump's capacity
BOX_SENSORS = {"tempC": "core temperature", "edgeC": "edge temperature", "moisture": "moisture", "weightKg": "weight", "gas": "gas",
               "sumpL": "sump level", "pH": "acidity"}

TEXT = {
    "frost": "Frost: feeding and watering are on hold. Insulate the worm box; in a hard frost take the worms indoors.",
    "heat": "Heat: feeding is on hold. Shade the worm box and keep its top moist.",
    "storm": "Storm wind: the gantry is parked and locked out until the wind drops.",
    "overflow": "Heavy rain on a full tank: watering is on hold. Check that the overflow runs free.",
    "tank-low": "The tank is low: refill it.",
    "tank-empty": "The tank is empty: the soil is no longer watered from below. Refill it.",
    "tank-falling": "The tank is losing water although nothing draws on it: look for a leak.",
    "sensor": "A worm box sensor gives no trustworthy reading: feeding is on hold until it does.",
    "overfed": "The worm box's core runs much hotter than its edge: too much food is composting. Feeding is on hold; take some out and add dry bedding.",
    "airless": "The worm box smells: it is short of air. Feeding is on hold; mix in dry bedding.",
    "sump-full": "The worm box's sump is full and backs up into the bin. Feeding is on hold; open the tap to let it into the tank.",
    "sour": "The worm box is turning sour. Feeding is on hold; mix in crushed eggshell and dry bedding, and no citrus for a while.",
    "box-wet": "The worm box is soaked. Feeding is on hold; add dry bedding and check that it drains.",
    "box-dry": "The worm box is drying out: sprinkle water over it.",
    "lid": "The worm box's lid stands open: close it.",
    "hopper-jam": "The scoop comes back empty: the hopper is jammed. Feeding is on hold; clear it.",
    "offline": "No link to the cloud: the guild works on, and what it would write back is queued.",
    "box-full": "The worm box is nearly full: harvest the finished compost.",
    "crowded": "The worms eat like a crowded box (about {kg} kg of worms per m²): they will stop breeding. Harvest the compost with some of the worms in it, or start a second box.",
    "dying-back": "The worms eat {pct} % less than a week ago although food lies there: fewer are working. Check the food, the moisture and the air.",
}
HOLDS = {"frost": ["feed", "water"], "heat": ["feed"], "overflow": ["water"], "sensor": ["feed"], "hopper-jam": ["feed"],
         "overfed": ["feed"], "airless": ["feed"], "box-wet": ["feed"], "sump-full": ["feed"], "sour": ["feed"]}
LOCKS = {"storm"}
CROWDED_ON, CROWDED_OFF = 4.0, 3.5      # kg of worms per m² of box, as the feeder estimates them
DYING_ON = 30                          # % less eating than a week ago, with food there
ALERTS = {"frost", "heat", "storm", "tank-empty", "tank-falling", "sensor", "overfed", "airless", "sour"}


def add(result: Dict[str, Any], rule: str, text: str, holds: Optional[List[str]] = None, level: str = "note") -> None:
    """A reaction from a higher rung (the feeding guide): put it beside the keeper's own."""
    result["active"].append(rule)
    result["holds"] = sorted(set(result["holds"]) | set(holds or []))
    result["notices"].append({"id": rule, "text": text, "level": level, "holds": holds or []})


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
            if litres < TANK_DEAD_L:
                active.append("tank-empty")
            elif 100.0 * litres / cap < TANK_LOW_PCT:
                active.append("tank-low")
            # falling with nothing drawing on it: compare with the level a while ago
            seen = [s for s in memory.setdefault("tank", []) if now_min - s[0] <= TANK_FALL_MIN and s[0] <= now_min]
            seen.append((now_min, litres))
            memory["tank"] = seen[-240:]
            lost = max(l for _, l in seen) - litres
            if _switch(memory, "tank-falling", lost >= TANK_FALL_L and litres >= TANK_DEAD_L, lost < 1.0):
                active.append("tank-falling")

        if box:
            dead = [name for key, name in BOX_SENSORS.items() if key in box and box[key] is None]
            if dead:
                active.append("sensor")                         # dead
            elif air is not None:
                last = memory.get("box")
                if last is None or last["value"] != box_c:
                    memory["box"] = {"value": box_c, "since": now_min, "air": air}
                elif now_min - last["since"] >= STUCK_MIN and abs(air - last["air"]) >= STUCK_AIR_C:
                    active.append("sensor")                     # stuck: the air moved, the box "did not"
            edge_c, gas, wet = box.get("edgeC"), box.get("gas"), box.get("moisture")
            if box_c is not None and edge_c is not None and \
                    _switch(memory, "overfed", box_c - edge_c >= OVERFED_ON, box_c - edge_c <= OVERFED_OFF):
                active.append("overfed")
            if gas is not None and _switch(memory, "airless", gas >= GAS_ON, gas <= GAS_OFF):
                active.append("airless")
            if wet is not None and "edgeC" in box:              # (the first twin's box had a moisture number but no sensors)
                if _switch(memory, "box-wet", wet >= WET_ON, wet <= WET_OFF):
                    active.append("box-wet")
                if _switch(memory, "box-dry", wet <= DRY_ON, wet >= DRY_OFF):
                    active.append("box-dry")
            sump, cap = box.get("sumpL"), box.get("sumpCapacity")
            if sump is not None and cap and _switch(memory, "sump-full", sump >= SUMP_ON * cap, sump <= SUMP_OFF * cap):
                active.append("sump-full")
            ph = box.get("pH")
            if ph is not None and _switch(memory, "sour", ph <= SOUR_ON, ph >= SOUR_OFF):
                active.append("sour")
            if box.get("lidOpen"):
                active.append("lid")
            if (box.get("fillPct") or 0) >= BOX_FULL_PCT:
                active.append("box-full")
        if (bed.get("hopper") or {}).get("jammed"):
            active.append("hopper-jam")
    if offline:
        active.append("offline")

    holds = sorted({kind for rule in active for kind in HOLDS.get(rule, [])})
    lockout = next((_(TEXT[rule]) for rule in active if rule in LOCKS), None)
    return {"active": active, "holds": holds, "lockout": lockout,
            "notices": [{"id": r, "text": _(TEXT[r]), "level": "alert" if r in ALERTS else "note"} for r in active]}


def population(result: Dict[str, Any], memory: Dict[str, Any], est: Optional[Dict[str, Any]]) -> None:
    """The two rules about the worms themselves. No sensor counts worms: `est` is the feeder's estimate
    from the eating rate (feeder.worms), or None while it has none."""
    if not est:
        return
    dense = est.get("kgPerM2")
    if dense is not None and _switch(memory, "crowded", dense >= CROWDED_ON, dense <= CROWDED_OFF):
        add(result, "crowded", _(TEXT["crowded"], kg=f"{dense:.1f}"))
    fall = est.get("fallPct")
    if fall is not None and fall >= DYING_ON:
        add(result, "dying-back", _(TEXT["dying-back"], pct=fall), level="alert")


def refuses(result: Dict[str, Any], kind: str) -> Optional[str]:
    """Why a job of this kind may not run now, or None."""
    if result.get("lockout"):
        return result["lockout"]
    if kind in result.get("holds", []):
        rule = next((r for r in result["active"] if kind in HOLDS.get(r, [])), None)
        return _(TEXT[rule]) if rule else next((n["text"] for n in result["notices"] if kind in n.get("holds", [])),
                                               _("Jobs of the kind '{kind}' are on hold.", kind=kind))
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
