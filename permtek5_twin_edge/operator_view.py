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

GROUPS = [("bed", "Bed"), ("box", "Worm box"), ("feeding", "Feeding"), ("weather", "Weather")]

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
    out = {"group": group, "key": key, "label": label, "value": shown, "unit": unit, **_state(value, rules or [], active or [], near)}
    if hint and out["state"] != "ok":
        out["hint"] = hint
    return out


def _temp_rules(c: Optional[float], frost_off: float, heat_off: float, more: List[str]) -> List[str]:
    """Frost and heat count on a temperature only when it stands on their side: a warm box is not
    "frost" because the air froze (the keeper's rule holds on whichever reading tripped it)."""
    if c is None:
        return more
    return (["frost"] if c < frost_off else []) + (["heat"] if c > heat_off else []) + more


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
        out.append(_tile("bed", "tank", "tank", tank.get("litres"), f"of {tank['capacity']:.0f} l",
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
        out.append(_tile("box", "sumpL", "sump", sump, f"of {cap} l" if cap else "l", ["sump-full", "sensor"], act,
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
        out.append(_tile("feeding", "fed", "fed so far", box["fedGrams"], f"g in {box.get('feedings', 0)} portion(s)"))

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
