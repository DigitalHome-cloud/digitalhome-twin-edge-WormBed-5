# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
feeding_guide.py — the WormBed5 feeding guide, enforced.

The guide itself is data and belongs to PermTek-5
(packages/ontology/guides/wormbed5-feeding.json; scripts/sync-ontology.sh
copies it here). It says WHAT the worms are fed (yes, a little, never,
browns), HOW MUCH, WHERE and WHEN. This module holds it against what is in
the hopper and against the next feeding:

    check_load(guide, mix, diary, now)        what a person put in the hopper
    check_feed(guide, grams, spot, ...)       the feeding the robot would do next

Both answer {"ok", "rule", "text", ...}: allowed, or the rule that says no, in
the guide's own words. Nothing is fed against the guide: a refused load holds
feeding until the hopper is emptied, a refused feeding is not proposed and a
job sent anyway is refused.

The DIARY is the list of what was loaded, fed and added as bedding, each
with its simulated time in minutes:

    {"t": 1234.0, "what": "feed", "grams": 300, "spot": 1, "mix": {"vegetable": 200, "fruit": 100}}
    {"t": 1300.0, "what": "bedding", "grams": 500}

Pure functions over plain data; standard library only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

GUIDE = Path(__file__).resolve().parent / "data" / "wormbed5-feeding.json"
#: A settled box starts with bedding in it: this much counts as browns before the diary has any.
BROWNS_CREDIT_G = 250.0
DEFAULT_MIX_KIND = "vegetable"


def load(path: Path = GUIDE) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def kinds_for_twin(guide: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """What the simulated box needs to know about each kind of food: how wet, how heating, how sour."""
    return {k: {"wet": v["wet"], "heat": v["heat"], "acid": v["acid"]} for k, v in guide["kinds"].items()}


def _say(guide: Dict[str, Any], rule: str, lang: str, **values: Any) -> str:
    text = guide["ruleTexts"][rule]
    out = text.get(lang) or text["en"]
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def _label(guide: Dict[str, Any], kind: str, lang: str) -> str:
    label = (guide["kinds"].get(kind) or {}).get("label") or {}
    return label.get(lang) or label.get("en") or kind


def _class(guide: Dict[str, Any], kind: str) -> str:
    return (guide["kinds"].get(kind) or {}).get("class", "never")       # what the guide does not know is not fed


def _window(guide: Dict[str, Any], diary: List[Dict[str, Any]], now: float) -> List[Dict[str, Any]]:
    since = now - float(guide["rules"].get("windowDays", 7)) * 1440.0
    return [e for e in diary if since <= e.get("t", 0) <= now]


def totals(guide: Dict[str, Any], diary: List[Dict[str, Any]], now: float) -> Dict[str, float]:
    """The week on record: grams of scraps fed, of browns given, and per limited kind."""
    out: Dict[str, float] = {"scraps": 0.0, "browns": 0.0}
    for e in _window(guide, diary, now):
        if e.get("what") == "bedding":
            out["browns"] += float(e.get("grams", 0))
        elif e.get("what") == "feed":
            for kind, grams in (e.get("mix") or {DEFAULT_MIX_KIND: e.get("grams", 0)}).items():
                cls = _class(guide, kind)
                out["browns" if cls == "browns" else "scraps"] += float(grams)
                if cls == "limited":
                    out[kind] = out.get(kind, 0.0) + float(grams)
    return out


def check_load(guide: Dict[str, Any], mix: Optional[Dict[str, float]], diary: List[Dict[str, Any]], now: float,
               lang: str = "en") -> Dict[str, Any]:
    """Is what lies in the hopper allowed? `mix` is {kind: grams}."""
    mix = {k: float(g) for k, g in (mix or {}).items() if float(g) > 0}
    if not mix:
        return {"ok": True}
    never = [k for k in mix if _class(guide, k) == "never"]
    if never:
        return {"ok": False, "rule": "never", "text": _say(guide, "never", lang, kinds=", ".join(_label(guide, k, lang) for k in never))}
    week = totals(guide, diary, now)
    scraps = week["scraps"] + sum(g for k, g in mix.items() if _class(guide, k) != "browns")
    for kind, grams in mix.items():
        if _class(guide, kind) != "limited":
            continue
        pct = float(guide["kinds"][kind]["weeklySharePct"])
        if scraps > 0 and 100.0 * (week.get(kind, 0.0) + grams) / scraps > pct + 1e-9:
            return {"ok": False, "rule": "limited", "text": _say(guide, "limited", lang, kind=_label(guide, kind, lang), pct=int(pct))}
    return {"ok": True}


def check_feed(guide: Dict[str, Any], grams: float, spot: int, diary: List[Dict[str, Any]], now: float,
               core_c: Optional[float] = None, eats_per_day: Optional[float] = None, uneaten_g: Optional[float] = None,
               spots: int = 1, with_browns_g: float = 0.0, lang: str = "en") -> Dict[str, Any]:
    """May the robot feed `grams` at `spot` now? Also gives the most the guide allows ("maxGrams")."""
    r = guide["rules"]
    cap = float(r["maxGramsPerSpot"])
    if eats_per_day is not None:                       # never more than so many days' eating in the box
        cap = min(cap, max(0.0, float(r["maxDaysOfFoodInBox"]) * eats_per_day - (uneaten_g or 0.0)))
    feeds = [e for e in diary if e.get("what") == "feed" and e.get("t", 0) <= now]
    last = feeds[-1] if feeds else None
    no = lambda rule, **v: {"ok": False, "rule": rule, "text": _say(guide, rule, lang, **v), "maxGrams": int(cap)}   # noqa: E731
    if core_c is not None and core_c < float(r["minCoreC"]):
        return no("cold", c=r["minCoreC"])
    if last and now - last["t"] < float(r["minHoursBetweenFeedings"]) * 60.0:
        return {**no("rest", h=r["minHoursBetweenFeedings"]),
                "againInMin": round(float(r["minHoursBetweenFeedings"]) * 60.0 - (now - last["t"]))}
    if last and r.get("noSameSpotTwice") and spots > 1 and int(last.get("spot", -1)) == int(spot):
        return no("spot", g=r["maxGramsPerSpot"])
    if eats_per_day is not None and cap < 50:
        return no("amount")
    week = totals(guide, diary, now)
    browns = BROWNS_CREDIT_G + week["browns"] + with_browns_g
    if browns + 1e-9 < float(r["brownsPerScraps"]) * (week["scraps"] + min(grams, cap)):
        return no("browns")
    return {"ok": True, "maxGrams": int(cap)}


def browns_due(guide: Dict[str, Any], grams: float, diary: List[Dict[str, Any]], now: float) -> int:
    """Grams of dry bedding that should go in with a feeding of `grams`, to keep the week's balance."""
    week = totals(guide, diary, now)
    need = float(guide["rules"]["brownsPerScraps"]) * (week["scraps"] + grams) - (BROWNS_CREDIT_G + week["browns"])
    return int(max(0.0, -(-need // 50) * 50))


def week_line(guide: Dict[str, Any], diary: List[Dict[str, Any]], now: float) -> Dict[str, Any]:
    """The week for the page: what was fed and given, and how many loads the guide refused."""
    week = totals(guide, diary, now)
    refused = sum(1 for e in _window(guide, diary, now) if e.get("what") == "load" and e.get("verdict") not in (None, "ok"))
    feeds = sum(1 for e in _window(guide, diary, now) if e.get("what") == "feed")
    return {"scrapsG": round(week["scraps"]), "brownsG": round(week["browns"]), "feedings": feeds, "refusedLoads": refused,
            "brownsOk": BROWNS_CREDIT_G + week["browns"] + 1e-9 >= float(guide["rules"]["brownsPerScraps"]) * week["scraps"]}
