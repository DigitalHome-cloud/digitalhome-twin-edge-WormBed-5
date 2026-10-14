# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
operator_view.py — the guild's readings as an operator sees them: one tile per reading.

The Operator screen shows what a REAL guild could show: sensor readings, never
the simulation's own truth (worm counts, soil life). Each tile says how the
reading stands, judged the way the keeper judges it (keeper.py), so the page
adds no thresholds of its own:

    alarm   a keeper's rule that is an alert holds on it, or the sensor gives no reading
    watch   a keeper's rule that is a note holds on it, or it is close to a threshold
    ok      otherwise

    tiles(bed, active)  -> [{"group", "key", "label", "value", "unit", "state", "rule"}]

`bed` is the twin's /bed/state "bed"; `active` the keeper's reactions now (its
"active" list). Pure functions over plain data. Standard library only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import keeper
from .engine import DRY_BELOW_PCT
from .i18n import _

GROUPS = [("bed", "Bed"), ("box", "Worm box"), ("feeding", "Feeding"), ("weather", "Weather")]
# a keeper's rule as a tile names it, in a few words
RULES = {"sensor": "no trustworthy reading", "frost": "frost", "heat": "heat", "storm": "storm", "overflow": "overflow",
         "tank-low": "tank low", "tank-empty": "tank empty", "tank-falling": "tank falling", "overfed": "overfed",
         "airless": "airless", "sump-full": "sump full", "sour": "sour", "box-wet": "box wet", "box-dry": "box dry",
         "lid": "lid open", "hopper-jam": "hopper jammed", "box-full": "box full", "guide-load": "refused load",
         "guide-browns": "bedding due"}

# how close to a keeper's threshold counts as "watch" (rules of thumb)
NEAR_C = 2.0          # °C, air and box
NEAR_MOIST = 3.0      # % box moisture
NEAR_PH = 0.3
NEAR_SHARE = 0.75     # of the gas, sump and wind limits
NEAR_TANK = 1.5       # times the low-tank share


def _state(value: Any, rules: List[str], active: List[str], near: bool = False) -> Dict[str, Any]:
    on = next((r for r in rules if r in active), None)
    if value is None:
        return {"state": "alarm", "rule": on or "sensor"}
    if on:
        return {"state": "alarm" if on in keeper.ALERTS else "watch", "rule": on}
    return {"state": "watch" if near else "ok", "rule": None}


def _tile(group: str, key: str, label: str, value: Any, unit: str = "", rules: Optional[List[str]] = None,
          active: Optional[List[str]] = None, near: bool = False, digits: int = 0, hint: str = "") -> Dict[str, Any]:
    shown = round(value, digits) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    if digits == 0 and isinstance(shown, float):
        shown = int(shown)
    if isinstance(shown, str):
        shown = _(shown)
    out = {"group": group, "key": key, "label": _(label), "value": shown, "unit": _(unit), **_state(value, rules or [], active or [], near)}
    if hint and out["state"] != "ok":
        out["hint"] = _(hint)
    # in a few words, why it stands as it does: the hint, else the keeper's rule, else how close it is
    out["why"] = out.get("hint") or (_(RULES.get(out["rule"], out["rule"])) if out["rule"] else _("close to a limit") if out["state"] == "watch" else "")
    return out


def _temp_rules(c: Optional[float], frost_off: float, heat_off: float, more: List[str]) -> List[str]:
    """Frost and heat count on a temperature only when it stands on their side: a warm box is not
    "frost" because the air froze (the keeper's rule holds on whichever reading tripped it)."""
    if c is None:
        return more
    return (["frost"] if c < frost_off else []) + (["heat"] if c > heat_off else []) + more


K = keeper
# What stands behind each tile: the device, where it sits, how it measures, how often, the limits the keeper
# applies to it, and how this twin makes the value (PermTek-5 docs/specs/roofbed-system.md, section 6). The
# devices are the usual choices for a hobby build (rules of thumb, to confirm on the real guild).
SENSORS: Dict[str, Dict[str, Any]] = {
    "soilDriest": {"sensor": "Soil moisture probes", "where": "two or three fixed at depth along the bed, plus the gantry's probe pushed into each cell",
                   "method": "Capacitive: the probe's electric field changes with the water in the soil around it (its dielectric constant); "
                             "read as volumetric water content, %. No bare metal in the soil, so it does not corrode.",
                   "every": "fixed probes every few minutes; the gantry's probe on each probe pass",
                   "limits": [f"under {DRY_BELOW_PCT:.0f} %: the cell is proposed for watering"],
                   "twin": "the simulated water content of every sown or growing cell: the tank pipes wick water up, the plants and the sun take it"},
    "soilMean": {"same": "soilDriest"}, "soilWettest": {"same": "soilDriest"},
    "soilTempC": {"sensor": "Soil temperature", "where": "on the soil probe, a few centimetres deep",
                  "method": "A digital thermometer chip (DS18B20 type) sealed in the probe's tip, ±0.5 °C.",
                  "every": "with the soil moisture", "limits": [],
                  "twin": "a fixed value for now: the twin does not model soil temperature yet"},
    "tank": {"sensor": "Tank level", "where": "in the fill pipe that reaches the tank pipes from above",
             "method": "A level probe: a float on a rod, or a pressure sensor at the bottom (the water column's weight gives its height); "
                       "the pipes' size turns height into litres.",
             "every": "every few minutes",
             "limits": [f"under {K.TANK_LOW_PCT:.0f} %: refill (note)", f"under {K.TANK_DEAD_L:.0f} l: empty, the soil is no longer watered from below (alarm)",
                        f"{K.TANK_FALL_L:.0f} l lost within {K.TANK_FALL_MIN:.0f} min with nothing drawing: a leak (alarm)", "rain on a full tank: check the overflow"],
             "twin": "the simulated water balance: what the wick gives the soil, rain, refills and leaks; read without noise"},
    "tempC": {"sensor": "Compost temperature, core", "where": "a probe in the middle of the working bin, 10–15 cm deep",
              "method": "A stainless-steel probe with a digital thermometer chip (DS18B20 type), ±0.5 °C.",
              "every": "every minute",
              "limits": [f"at or under {K.FROST_BOX_ON:.0f} °C: frost, until it is back over {K.FROST_BOX_OFF:.0f} °C (alarm)",
                         f"at or over {K.HEAT_BOX_ON:.0f} °C: heat, until under {K.HEAT_BOX_OFF:.0f} °C (alarm)",
                         f"{K.OVERFED_ON:.0f} °C or more above the edge: too much food is composting (note)",
                         f"unchanged for {K.STUCK_MIN:.0f} min while the air moved {K.STUCK_AIR_C:.0f} °C: a stuck sensor (alarm)"],
              "twin": "the simulated core: air, sun, the wrapping, and the heat of food composting; plus noise of ±0.1 °C"},
    "edgeC": {"sensor": "Compost temperature, edge", "where": "a second probe near a wall of the working bin",
              "method": "The same kind of probe as the core. Core and edge together tell food heating up from weather: weather changes both.",
              "every": "every minute",
              "limits": [f"frost and heat as the core: {K.FROST_BOX_ON:.0f} °C and {K.HEAT_BOX_ON:.0f} °C"],
              "twin": "the simulated edge, closer to the air than the core; plus noise of ±0.1 °C"},
    "moisture": {"sensor": "Compost moisture", "where": "a probe in the working bin's bedding",
                 "method": "Capacitive, like the soil probes, calibrated for compost. Red wigglers do best at about 70–85 %.",
                 "every": "every few minutes",
                 "limits": [f"at or under {K.DRY_ON:.0f} %: drying out, sprinkle water (note)", f"at or over {K.WET_ON:.0f} %: soaked, add dry bedding (note)"],
                 "twin": "the simulated water in the bedding: food brings water, paper takes it up, the sump drains it, the air dries it; plus noise of ±0.3 %"},
    "pH": {"sensor": "Acidity (pH)", "where": "a probe in the bedding",
           "method": "A glass electrode: a small voltage across a thin glass bulb that follows the hydrogen ions around it. "
                     "Needs cleaning and calibrating with buffer solutions (a monthly care task).",
           "every": "every few minutes",
           "limits": [f"at or under pH {K.SOUR_ON}: turning sour, until back over {K.SOUR_OFF} (alarm)"],
           "twin": "the simulated acidity: citrus and fresh scraps lower it, eggshell and bedding buffer it; plus noise of ±0.03"},
    "gas": {"sensor": "Gas (smell)", "where": "under the lid, over the bedding",
            "method": "A metal-oxide gas sensor: its resistance falls as gases from rotting (ammonia, hydrogen sulphide, alcohols) reach it. "
                      "Shown 0 fresh to 100 rotting; it gives a trend, not exact amounts.",
            "every": "every few minutes",
            "limits": [f"at or over {K.GAS_ON:.0f}: short of air, until under {K.GAS_OFF:.0f} (alarm)"],
            "twin": "the simulated smell: wet, airless, overfed bedding rots; plus noise of ±1"},
    "weightKg": {"sensor": "Weight of the box", "where": "load cells under the working bin's corners",
                 "method": "Strain-gauge load cells: a metal beam bends a little under the weight and the gauges glued on it change their resistance; "
                           "an amplifier (HX711 type) reads it. Averaged over a day, the slope of the weight gives how much the worms eat.",
                 "every": "every minute", "limits": ["a missing or frozen reading: feeding is held (alarm)"],
                 "twin": "the simulated mass of bedding, food, castings and worms; plus noise of ±15 g"},
    "sumpL": {"sensor": "Sump level", "where": "in the lower bin, which catches what drains from the working bin",
              "method": "A float switch for 'full', and a level probe (float on a rod, or pressure) for the litres in between.",
              "every": "every few minutes",
              "limits": [f"at {K.SUMP_ON * 100:.0f} % of its capacity: full, open the tap (note); clear again under {K.SUMP_OFF * 100:.0f} %"],
              "twin": "the simulated liquid that drains through; the tap lets it on to the tank; plus noise of ±0.03 l"},
    "fillPct": {"sensor": "How full the box is", "where": "worked out on the edge",
                "method": "From the weight and what was fed and harvested: castings build up as food is eaten. "
                          "A distance sensor under the lid could check it.",
                "every": "with the weight", "limits": [f"at {K.BOX_FULL_PCT:.0f} %: harvest the compost (note)"],
                "twin": "the simulated castings and bedding in the working bin"},
    "lid": {"sensor": "Lid switch", "where": "on the lid's edge",
            "method": "A reed switch and a magnet: closed lid, closed contact.",
            "every": "at once, when it changes", "limits": ["open: close it (note); the camera sees the worms only then"],
            "twin": "what you said with the Lid button, or a disturbance"},
    "insulated": {"sensor": "Wrapping", "where": "said by a person",
                  "method": "No sensor: you say it with the Box wrapped button. The keeper asks for it in frost.",
                  "every": "when you say so", "limits": ["frost while bare: wrap the box (watch)"],
                  "twin": "what you said with the button; wrapped, the box loses its warmth five times more slowly"},
    "hopper": {"sensor": "Weight of the hopper", "where": "a load cell under the hopper on the frame",
               "method": "A strain-gauge load cell, like the box's. Each scoop the robot takes shows as a step down.",
               "every": "every minute and around each feeding", "limits": ["empty: fill it (watch)", "the scoop comes back empty: jammed (alarm)"],
               "twin": "what you said went in, less what the robot fed"},
    "browns": {"sensor": "Weight of the bedding compartment", "where": "a second load cell, under the hopper's other compartment",
               "method": "As the hopper: a strain-gauge load cell.", "every": "every minute",
               "limits": ["the feeding guide asks for bedding with the next feeding"], "twin": "what you said went in, less what the robot added"},
    "fed": {"sensor": "Fed so far", "where": "counted on the edge",
            "method": "The sum of what each feeding job gave, from the hopper's weight before and after.",
            "every": "after each feeding", "limits": [], "twin": "the robot's feeding jobs"},
    "airC": {"sensor": "Air temperature", "where": "on the gantry's frame, in a small radiation shield (out of direct sun)",
             "method": "A digital temperature and humidity sensor (SHT type), ±0.3 °C.",
             "every": "every minute",
             "limits": [f"at or under {K.FROST_AIR_ON:.0f} °C: frost, until over {K.FROST_AIR_OFF:.0f} °C (alarm)",
                        f"at or over {K.HEAT_AIR_ON:.0f} °C: heat, until under {K.HEAT_AIR_OFF:.0f} °C (alarm)"],
             "twin": "the simulated weather: a day's warming and cooling, plus cold snaps and heat waves when you throw them in"},
    "windMs": {"sensor": "Wind", "where": "on top of the frame",
               "method": "A cup anemometer: the cups turn with the wind and a magnet passes a reed switch on each turn; pulses per second give m/s.",
               "every": "every few seconds, gusts kept",
               "limits": [f"at or over {K.STORM_ON:.0f} m/s: storm, the gantry is locked out until under {K.STORM_OFF:.0f} m/s (alarm)"],
               "twin": "the simulated weather, with storms when you throw them in"},
    "rainMmH": {"sensor": "Rain", "where": "on the frame (or the local weather service until one is fitted)",
                "method": "A tipping-bucket rain gauge: each tip of its little seesaw is 0.2 mm of rain.",
                "every": "each tip; shown per hour", "limits": ["20 mm/h or more on a full tank: check the overflow (note)"],
                "twin": "the simulated weather, with cloudbursts when you throw them in"},
}


def sensor(key: str) -> Optional[Dict[str, Any]]:
    """What stands behind a tile: {"sensor", "where", "method", "every", "limits", "twin"}, or None. In the
    current language: the limits are written out with the keeper's numbers, and the English text is the key."""
    info = SENSORS.get(key)
    if info and "same" in info:
        info = SENSORS[info["same"]]
    if not info:
        return None
    return {k: [_(x) for x in v] if isinstance(v, list) else _(v) for k, v in info.items()}


def tiles(bed: Optional[Dict[str, Any]], active: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Every reading the guild has, as tiles in GROUPS order. No bed (not running): no tiles."""
    if not bed:
        return []
    act = list(active or [])
    out: List[Dict[str, Any]] = []
    env, tank, box, hopper = bed.get("env") or {}, bed.get("tank") or {}, bed.get("wormBin"), bed.get("hopper")

    # the bed: soil moisture over the planted cells, the tank under it
    soil = [c["moisture"] for c in (bed.get("cells") or {}).values()
            if c.get("status") != "CellPlanned" and isinstance(c.get("moisture"), (int, float))]
    if soil:
        driest = min(soil)
        out.append(_tile("bed", "soilDriest", "driest soil", driest, "%", near=driest < DRY_BELOW_PCT))
        out.append(_tile("bed", "soilMean", "mean soil moisture", sum(soil) / len(soil), "%"))
        out.append(_tile("bed", "soilWettest", "wettest soil", max(soil), "%"))
    if isinstance(bed.get("soilTempC"), (int, float)):
        out.append(_tile("bed", "soilTempC", "soil temperature", bed["soilTempC"], "°C", digits=1))
    if tank.get("capacity"):
        pct = 100.0 * tank.get("litres", 0.0) / tank["capacity"]
        out.append(_tile("bed", "tank", "tank", tank.get("litres"), _("of {n} l", n=f"{tank['capacity']:.0f}"),
                         ["tank-empty", "tank-falling", "overflow", "tank-low"], act, near=pct < NEAR_TANK * keeper.TANK_LOW_PCT))

    # the worm box: its sensors, and what a person sees at it
    if box and "edgeC" in box:
        core, edge = box.get("tempC"), box.get("edgeC")
        near_t = lambda c: c is not None and (c <= keeper.FROST_BOX_ON + NEAR_C or c >= keeper.HEAT_BOX_ON - NEAR_C)
        box_rules = lambda c, more: _temp_rules(c, keeper.FROST_BOX_OFF, keeper.HEAT_BOX_OFF, more)
        out.append(_tile("box", "tempC", "core temperature", core, "°C", box_rules(core, ["overfed", "sensor"]), act, near_t(core), 1))
        out.append(_tile("box", "edgeC", "edge temperature", edge, "°C", box_rules(edge, ["sensor"]), act, near_t(edge), 1))
        wet = box.get("moisture")
        out.append(_tile("box", "moisture", "moisture", wet, "%", ["box-wet", "box-dry", "sensor"], act,
                         wet is not None and (wet >= keeper.WET_ON - NEAR_MOIST or wet <= keeper.DRY_ON + NEAR_MOIST)))
        ph = box.get("pH")
        out.append(_tile("box", "pH", "acidity", ph, "pH", ["sour", "sensor"], act, ph is not None and ph <= keeper.SOUR_ON + NEAR_PH, 1))
        gas = box.get("gas")
        out.append(_tile("box", "gas", "gas", gas, "0 fresh … 100", ["airless", "sensor"], act, gas is not None and gas >= NEAR_SHARE * keeper.GAS_ON))
        out.append(_tile("box", "weightKg", "weight", box.get("weightKg"), "kg", ["sensor"], act, digits=2))
        sump, cap = box.get("sumpL"), box.get("sumpCapacity")
        out.append(_tile("box", "sumpL", "sump", sump, _("of {n} l", n=cap) if cap else "l", ["sump-full", "sensor"], act,
                         sump is not None and bool(cap) and sump >= NEAR_SHARE * keeper.SUMP_ON * cap, 1))
        if isinstance(box.get("fillPct"), (int, float)):
            out.append(_tile("box", "fillPct", "full", box["fillPct"], "%", ["box-full"], act, box["fillPct"] >= keeper.BOX_FULL_PCT - 10))
        out.append(_tile("box", "lid", "lid", "open" if box.get("lidOpen") else "closed", "", ["lid"], act))
        out.append(_tile("box", "insulated", "wrapping", "wrapped" if box.get("insulated") else "bare", "",
                         [], act, near="frost" in act and not box.get("insulated"), hint="frost: wrap the box"))

    # feeding: the hopper a person fills
    if hopper:
        out.append(_tile("feeding", "hopper", "hopper, scraps", hopper.get("grams", 0), "g", ["hopper-jam", "guide-load"], act,
                         near=not hopper.get("grams"), hint="empty: fill it"))
        if "brownsGrams" in hopper:
            out.append(_tile("feeding", "browns", "hopper, dry bedding", hopper.get("brownsGrams", 0), "g", ["guide-browns"], act))
    if box and "fedGrams" in box:
        out.append(_tile("feeding", "fed", "fed so far", box["fedGrams"], _("g in {n} portion(s)", n=box.get('feedings', 0))))

    # the weather at the guild
    if "airC" in env:
        air = env.get("airC")
        out.append(_tile("weather", "airC", "air", air, "°C", _temp_rules(air, keeper.FROST_AIR_OFF, keeper.HEAT_AIR_OFF, []), act,
                         air is not None and (air <= keeper.FROST_AIR_ON + NEAR_C or air >= keeper.HEAT_AIR_ON - NEAR_C), 1))
    if "windMs" in env:
        wind = env.get("windMs")
        out.append(_tile("weather", "windMs", "wind", wind, "m/s", ["storm"], act, wind is not None and wind >= NEAR_SHARE * keeper.STORM_ON, 1))
    if "rainMmH" in env:
        out.append(_tile("weather", "rainMmH", "rain", env.get("rainMmH") or 0.0, "mm/h", ["overflow"], act, digits=1))
    return out


def summary(tiles_: List[Dict[str, Any]]) -> Dict[str, int]:
    """How many tiles stand at each state: for the status strip and the rail's dot."""
    out = {"ok": 0, "watch": 0, "alarm": 0}
    for t in tiles_:
        out[t["state"]] += 1
    return out
