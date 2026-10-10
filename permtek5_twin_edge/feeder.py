# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
feeder.py — how much to feed the worms, from what the load cells show: the second rung of the algorithm ladder.

Worms do not say when they are hungry, but the box gets lighter as they eat:
of every gram eaten about 0.6 g leaves it, as liquid to the tank and as gas
and vapour (a rule of thumb; PermTek-5 docs/specs/perma5guild.md). So:

    1. Take the box's weight at every look, minus what the robot has put in
       so far (food, bedding) and plus the compost it took out. That series only falls, and it falls as fast as the worms eat.
    2. Fit a straight line through the last day of it (least squares, so the
       load cells' noise averages out). Slope / 0.6 = grams eaten per day.
    3. Worms eat less when it is cold: scale that rate from the temperatures
       it was measured at to the box's temperature now.
    4. Keep count of what was fed and what was eaten since: the rest still
       lies in the box. Propose what brings that up to one day's eating.

Until there are six hours of readings the feeder is LEARNING and proposes a
small, safe portion (two of them at most). When the weight jumps for a reason it does not know (a
person added bedding, took food out), it starts over. A person still fills
the hopper and confirms every feeding; the keeper's holds stand above this.

Pure functions over plain data; `memory` is a dict the caller keeps.
Standard library only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

LOSS_PER_G_EATEN = 0.6
SAMPLE_EVERY_MIN = 10.0
FIT_MIN, MIN_SPAN_MIN, MIN_SAMPLES = 1440.0, 360.0, 4
KEEP_MIN = 3 * 1440.0
JUMP_G, JUMP_G_PER_MIN = 150.0, 0.5       # a change no eating explains
LEARNING_G, LEARNING_MAX_G, MAX_G, STEP_G = 150, 300, 1000, 50


def temp_factor(c: float) -> float:
    """How hard worms work at this temperature, 0..1: best at 15-25 °C, nothing under 4 or over 32."""
    up = max(0.0, min(1.0, (c - 4.0) / 11.0))
    down = max(0.0, min(1.0, (32.0 - c) / 7.0))
    return min(up, down)


def _now(bed: Dict[str, Any]) -> float:
    env = bed.get("env") or {}
    return (env.get("day", 1) - 1) * 1440.0 + env.get("hour", 0.0) * 60.0


def _known(box: Dict[str, Any]) -> float:
    """Weight the robot itself put in or took out: food and bedding in, finished compost out. Not eating."""
    return float(box.get("fedGrams") or 0) + float(box.get("beddingGrams") or 0) - float(box.get("takenGrams") or 0)


def observe(memory: Dict[str, Any], bed: Optional[Dict[str, Any]]) -> None:
    """Take one reading of the box into the memory (at most one every ten simulated minutes)."""
    box = (bed or {}).get("wormBin") or {}
    weight, temp = box.get("weightKg"), box.get("tempC")
    if weight is None or temp is None:
        return                                        # a dead sensor: nothing to learn from
    now = _now(bed)
    samples: List[List[float]] = memory.setdefault("samples", [])
    if samples and now < samples[-1][0]:              # the simulation started again
        memory.clear()
        samples = memory.setdefault("samples", [])
    if samples and now - samples[-1][0] < SAMPLE_EVERY_MIN:
        return
    fed = float(box.get("fedGrams") or 0)
    y = weight * 1000.0 - _known(box)
    if samples:
        gap = now - samples[-1][0]
        change = y - samples[-1][1]
        if change > JUMP_G or change < -(JUMP_G + JUMP_G_PER_MIN * gap):
            memory.clear()                            # somebody changed the box: start over
            samples = memory.setdefault("samples", [])
    samples.append([now, y, fed, temp])
    memory["samples"] = [s for s in samples if now - s[0] <= KEEP_MIN]
    memory.setdefault("anchor", [y, fed])


def _fit(samples: List[List[float]]) -> Optional[float]:
    """Grams the series loses per minute (least squares), or None when there is too little of it."""
    pts = [s for s in samples if samples[-1][0] - s[0] <= FIT_MIN]
    if len(pts) < MIN_SAMPLES or pts[-1][0] - pts[0][0] < MIN_SPAN_MIN:
        return None
    n = len(pts)
    mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    return -sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else None


def advise(memory: Dict[str, Any], bed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What to feed now: {"state", "grams", "why", "eatsPerDay", "uneatenG", "hours"}.
    state: "learning" (too few readings), "known", or "blind" (no weight or temperature reading)."""
    box = (bed or {}).get("wormBin") or {}
    hopper = int(((bed or {}).get("hopper") or {}).get("grams") or 0)
    if box.get("weightKg") is None or box.get("tempC") is None:
        return {"state": "blind", "grams": 0, "eatsPerDay": None, "uneatenG": None, "hours": 0,
                "why": "no weight or temperature reading: nothing to go by"}
    samples = memory.get("samples") or []
    hours = round((samples[-1][0] - samples[0][0]) / 60.0, 1) if samples else 0
    loss = _fit(samples)
    if loss is not None:
        pts = [s for s in samples if samples[-1][0] - s[0] <= FIT_MIN]
        at = sum(temp_factor(s[3]) for s in pts) / len(pts)
        ref = max(0.0, loss) * 1440.0 / LOSS_PER_G_EATEN / max(at, 0.3)     # the rate at the best temperature
        # eating also slows when the food runs out: go by the best rate of the last days, not the latest
        seen = [r for r in memory.setdefault("rates", []) if samples[-1][0] - r[0] <= KEEP_MIN]
        seen.append([samples[-1][0], ref])
        memory["rates"] = seen[-400:]
    rates = memory.get("rates") or []
    if not rates:
        given = float(box.get("fedGrams") or 0) - (samples[0][2] if samples else 0.0)
        grams = int(max(0.0, min(LEARNING_G, hopper, LEARNING_MAX_G - given)))
        return {"state": "learning", "grams": grams, "eatsPerDay": None, "uneatenG": None, "hours": hours,
                "why": f"learning: {hours} h of weight readings so far, six are needed. Until then small portions only, {LEARNING_MAX_G} g in all"}
    per_day = max(r[1] for r in rates) * temp_factor(box["tempC"])
    # what was fed since the anchor, minus what the weight says was eaten since
    # (the fed total is read now, not from the last sample: a portion just dropped must count at once)
    y0, fed0 = memory["anchor"]
    fed = float(box.get("fedGrams") or 0)
    last = samples[-3:]
    y_now = sum(s[1] for s in last) / len(last)
    uneaten = (fed - fed0) - (y0 - y_now) / LOSS_PER_G_EATEN
    if uneaten < 0:                                    # they ate food the feeder never counted: move the anchor
        memory["anchor"] = [y_now + (fed - fed0) * LOSS_PER_G_EATEN, fed0]
        uneaten = 0.0
    want = per_day - uneaten
    grams = int(max(0.0, min(want, MAX_G, hopper)) // STEP_G * STEP_G)
    if want >= STEP_G and uneaten == 0 and grams < min(LEARNING_G, hopper):
        grams = min(LEARNING_G, hopper)
    why = (f"the box gets lighter by about {per_day * LOSS_PER_G_EATEN:.0f} g a day, so the worms eat about {per_day:.0f} g a day "
           f"at {box['tempC']:.0f} °C; about {uneaten:.0f} g still lie uneaten")
    return {"state": "known", "grams": grams, "eatsPerDay": round(per_day), "uneatenG": round(uneaten), "hours": hours, "why": why}
