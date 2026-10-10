#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
engine.py — the engine of permtek5-twin-edge, and its command line.

A habitat's roof bed as a Webots world, and the jobs a gantry would do on it.
The web page and the MCP server call into this module; neither re-implements
what it does. Pure functions over plain data; the simulation itself is
started by sim.py.

    permtek5-twin-edge check [habitat.ttl]        # what stops a habitat from being simulated
    permtek5-twin-edge world [habitat.ttl]        # write roofbed.wbt and bed.json into the workspace
    permtek5-twin-edge jobs                        # the jobs proposed for the prepared bed
    python3 -m permtek5_twin_edge.engine world     # the same, from a checkout

Without a file, the invented CityDemo habitat that ships with the tool is used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DRY_BELOW_PCT = 30.0        # a planted cell under this is proposed for watering
TANK_DEAD_L = 1.0           # under a litre the wick does not reach the water: the tank counts as empty
PORTION_G = 300             # what one feeding of the worm box carries


def workspace() -> Path:
    """Where this guild's generated world and run data go: the person's folder, never the repository."""
    from . import instance
    from .core import workspace as ws
    return instance.workspace(ws.base_dir(ws.USER))


def shared_assets() -> Path:
    """The shape library that ships with the app (shapes/library.json): the same for every guild.
    Made once with Blender (assets/build.py); nothing is built when the twin runs."""
    return Path(__file__).resolve().parent / "shapes"


def habitat_ttl(source: Optional[str] = None) -> str:
    """The habitat's Turtle: a file, or CityDemo when none is named."""
    from . import habitat as hb
    path = hb.CITYDEMO if not source or source == "citydemo" else Path(source)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror}") from None


def check(ttl: str) -> List[str]:
    """Everything that stops this habitat from being simulated. Empty is fine."""
    from . import habitat as hb
    try:
        h = hb.load(ttl)
    except Exception as exc:                      # rdflib raises many kinds for bad Turtle
        return [f"not valid Turtle: {exc}"]
    errors = hb.problems(h, hb.load_library())
    if not h.beds:
        errors.append("the habitat has no growing bed")
    return errors


def prepare(ttl: str, out: Optional[Path] = None, assets: Optional[Path] = None, guild: Optional[str] = None) -> Dict[str, Any]:
    """Write the world and bed.json for one Perma5Guild of the habitat: the one this edge instance is for,
    or the habitat's first. Raises ValueError with what is wrong."""
    from . import habitat as hb
    from . import world
    errors = check(ttl)
    if errors:
        raise ValueError("; ".join(errors))
    from . import instance
    out = Path(out) if out else workspace()
    if assets is None:
        assets = next((d for d in (out / "assets", shared_assets()) if d.is_dir()), None)
    paths = world.generate(hb.load(ttl), hb.load_library(), out, guild=guild or instance.guild(), assets=assets)
    return {**paths, **summary(config(out))}


def config(out: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The prepared bed (bed.json), or None when no world has been prepared yet."""
    path = (Path(out) if out else workspace()) / "bed.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summary(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cfg:
        return {"prepared": False}
    by: Dict[str, int] = {}
    for c in cfg["cells"]:
        by[c["status"]] = by.get(c["status"], 0) + 1
    b = cfg["bed"]
    return {"prepared": True, "bed": {"name": b["name"], "lengthCm": b["length_cm"], "widthCm": b["width_cm"],
                                      "cellCm": b["cell_cm"], "rows": b["rows"], "cols": b["cols"],
                                      "depthCm": b.get("depth_cm"), "tankLitres": (cfg.get("tank") or {}).get("litres", 0),
                                      "wormBinCm": b.get("worm_bin_cm", 0)},
            "assumed": cfg.get("assumed") or [],
            "guild": cfg.get("guild"),
            "cells": len(cfg["cells"]), "byStatus": by, "meshes": cfg["world"].get("meshes", False)}


def proposals(cfg: Optional[Dict[str, Any]], bed_state: Optional[Dict[str, Any]] = None,
              holds: Optional[List[str]] = None, advice: Optional[Dict[str, Any]] = None,
              guide: Optional[Dict[str, Any]] = None, active: Optional[List[str]] = None,
              weeds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The jobs the gantry could do now. Proposals only: a person confirms each before anything moves.

    Sowing: every planned cell not sown yet. Watering: planted cells whose soil
    is dry, each with what its crop wants for a day. A probe pass and a photo
    pass over what is planted. `bed_state` is the running twin's /bed/state.
    `holds` are the kinds of job the keeper has on hold (keeper.py): they are not proposed.
    `advice` is the feeder's (feeder.py): how much to feed; without it a fixed portion.
    `guide` is the feeding guide's view (app._follow_guide): how much dry bedding is due.
    `active` are the keeper's reactions in force. From these come the box's care: bedding,
    water, draining the sump, resting one half for harvest, and compost back to the cells.
    Whether this is the right MONTH to sow is PermTek-5's calendar, not decided here.
    """
    if not cfg:
        return []
    found = _box_care(cfg, bed_state or {}, guide or {}, active or []) + _proposals(cfg, bed_state, advice)
    if bed_state and "sim" in bed_state:           # a twin that grows weeds: look for them, and press the small ones under
        stampable = [w for w in (weeds or {}).get("found", []) if w["do"] == "stamp"]
        if stampable:
            found.append({"id": "stamp", "kind": "stamp", "title": f"Press {len(stampable)} small weed(s) into the soil",
                          "detail": "with the stamp; they feed soil life where they stood. Places come from the last look over the bed",
                          "spec": {"kind": "stamp", "places": [[w["x"], w["y"]] for w in stampable][:30]}})
        found.append({"id": "scan", "kind": "scan", "title": "Look over the bed for weeds",
                      "detail": "the tool camera, picture by picture: green that is not at a planted point is a weed"
                                + (f'; last look: {(weeds or {}).get("when")}' if (weeds or {}).get("when") else ""),
                      "spec": {"kind": "scan"}})
    return [p for p in found if p["kind"] not in (holds or [])]


SMALL_WEED_BELOW_MM = 30    # under this the stamp presses a weed into the soil; above, a person pulls it
STAMP_CLEAR_M = 0.03        # and never this close to where a plant reaches


def sort_weeds(cfg: Dict[str, Any], bed_state: Optional[Dict[str, Any]], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What to do with each weed a look over the bed found: "stamp" the small ones that stand clear of
    every plant, pull the others by "hand" (too big, or too close to a plant for the stamp)."""
    cell_m = cfg["world"]["cellM"]
    live = (bed_state or {}).get("cells") or {}
    reach = []
    for c in cfg["cells"]:
        status = (live.get(f'{c["row"]}_{c["col"]}') or {}).get("status", c["status"])
        if status in ("CellSown", "CellGrowing"):
            spacing = ((c.get("job") or {}).get("spacingMm") or cell_m * 1000) / 1000.0
            r = max(0.03, spacing * 0.38) + 0.015 if status == "CellGrowing" else 0.03
            reach.append(((c["col"] + 0.5) * cell_m, (c["row"] + 0.5) * cell_m, r + STAMP_CLEAR_M))
    out = []
    for w in found:
        close = any(((w["x"] - x) ** 2 + (w["y"] - y) ** 2) ** 0.5 < r for x, y, r in reach)
        out.append({**w, "do": "stamp" if w["sizeMm"] < SMALL_WEED_BELOW_MM and not close else "hand",
                    "why": "large" if w["sizeMm"] >= SMALL_WEED_BELOW_MM else "close" if close else ""})
    return out


REST_FROM_KG = 6.0          # a box this heavy has castings worth harvesting
TOPDRESS_CELLS, TOPDRESS_G = 6, 50


def next_spot(cfg: Dict[str, Any], bed_state: Optional[Dict[str, Any]]) -> int:
    """Where the next portion goes: the spots in rotation, leaving out the half that rests for harvest."""
    box = (bed_state or {}).get("wormBin") or {}
    spots = int((cfg.get("wormBin") or {}).get("spots", 1))
    side = (box.get("harvest") or {}).get("side")
    fed = [s for s in range(spots) if side is None or spots < 2 or (s * 2 // spots) != side] or [0]
    return fed[int(box.get("feedings", 0)) % len(fed)]


def _box_care(cfg: Dict[str, Any], state: Dict[str, Any], guide: Dict[str, Any], active: List[str]) -> List[Dict[str, Any]]:
    """What the worm box needs besides food. The robot does the routine part; a person confirms."""
    box, hopper, tank = state.get("wormBin") or {}, state.get("hopper") or {}, state.get("tank") or {}
    if not (cfg.get("wormBin") or {}).get("cols") or "harvest" not in box:
        return []
    out: List[Dict[str, Any]] = []
    spot = next_spot(cfg, state)
    due, have = int(guide.get("brownsDue") or 0), int(hopper.get("brownsGrams") or 0)
    if due > 0 and have > 0:
        grams = min(due, have, 500)
        out.append({"id": "bedding", "kind": "care", "title": f"Add {grams} g of dry bedding to the worm box",
                    "detail": f"the feeding guide wants one part of browns to three of scraps; {have} g in the bedding compartment",
                    "spec": {"kind": "care", "what": "bedding", "grams": grams, "spot": spot}})
    if "box-dry" in active:
        out.append({"id": "moisten", "kind": "care", "title": "Sprinkle half a litre of water over the worm box",
                    "detail": f'its moisture reads {box.get("moisture")} %; worms stop in a dry box',
                    "spec": {"kind": "care", "what": "moisten", "ml": 500}})
    sump, cap = box.get("sumpL"), box.get("sumpCapacity")
    room = float(tank.get("capacity") or 0) - float(tank.get("litres") or 0)
    if sump is not None and cap and sump >= 0.5 * cap and room >= 1.0:
        out.append({"id": "tap", "kind": "tap", "title": f"Open the tap: let the sump's {sump:.0f} l into the tank",
                    "detail": f"the sump is over half full; the tank has room for {room:.0f} l", "spec": {"kind": "tap"}})
    harvest = box.get("harvest") or {}
    if harvest.get("side") is None and (box.get("weightKg") or 0) >= REST_FROM_KG and "box-full" not in active:
        side = int(harvest.get("nextSide", 1))
        out.append({"id": "rest", "kind": "rest", "title": "Start a compost harvest: rest one half of the worm box",
                    "detail": f'no food goes to that half; the worms move to the other, and in about {harvest.get("readyAfterDays", 10):.0f} days '
                              "its castings are finished and go back to the bed",
                    "spec": {"kind": "rest", "side": side}})
    if harvest.get("ready"):
        live = state.get("cells") or {}
        growing = sorted(((c.get("compostG", 0), k) for k, c in live.items() if c.get("status") in ("CellSown", "CellGrowing")))
        cells = [[int(v) for v in k.split("_")] for _, k in growing[:TOPDRESS_CELLS]]
        if cells:
            out.append({"id": "topdress", "kind": "topdress",
                        "title": f"Top-dress {len(cells)} cells with finished compost, {TOPDRESS_G} g each",
                        "detail": "from the rested half of the worm box, to the cells that have had least",
                        "spec": {"kind": "topdress", "cells": cells, "grams": TOPDRESS_G}})
    return out


def _proposals(cfg: Dict[str, Any], bed_state: Optional[Dict[str, Any]],
               advice: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    state = bed_state or {}
    live = state.get("cells", {})
    out: List[Dict[str, Any]] = []
    planted = []
    dry = []
    # With water in the tank pipes the soil is kept moist from below: only what was
    # just sown, whose roots do not reach yet, is watered from above (clean water).
    wicking = (state.get("tank") or {}).get("litres", 0) >= TANK_DEAD_L
    hopper = (state.get("hopper") or {}).get("grams", 0)
    box = cfg.get("wormBin") or {}
    grams = min(int(advice["grams"]) if advice else PORTION_G, int(hopper))
    if box.get("cols") and grams > 0:
        spot = next_spot(cfg, state)
        out.append({"id": "feed", "kind": "feed", "title": f"Feed the worms: {grams} g from the hopper",
                    "detail": f'spot {spot + 1} of {box.get("spots", 1)} across the box, in rotation; {int(hopper)} g in the hopper'
                              + (f'. Why this much: {advice["why"]}' if advice else ""),
                    "spec": {"kind": "feed", "grams": grams, "spot": spot}})
    for c in cfg["cells"]:
        key = f'{c["row"]}_{c["col"]}'
        status = live.get(key, {}).get("status", c["status"])
        where = f'row {c["row"] + 1}, column {c["col"] + 1}'
        if status == "CellPlanned" and c.get("job"):
            j = c["job"]
            out.append({"id": f"plant-{key}", "kind": "plant", "title": f'Sow {c["cropName"]} at {where}',
                        "detail": f'{j["depthMm"]} mm deep, {j["seeds"]} seeds, keep {j["spacingMm"]} mm clear',
                        "spec": {"kind": "plant", "row": c["row"], "col": c["col"], "crop": c["crop"],
                                 "depthMm": j["depthMm"], "seeds": j["seeds"]}})
        elif status in ("CellSown", "CellGrowing"):
            planted.append([c["row"], c["col"]])
            moisture = live.get(key, {}).get("moisture")
            if (moisture is not None and moisture < DRY_BELOW_PCT and c.get("waterMlPerDay")
                    and not (wicking and status == "CellGrowing")):
                dry.append([c["row"], c["col"], c["waterMlPerDay"]])
    if dry:
        out.append({"id": "water", "kind": "water", "title": f"Water {len(dry)} dry cells",
                    "detail": f"soil under {DRY_BELOW_PCT:.0f} %; {sum(d[2] for d in dry)} ml in all, a day's need each",
                    "spec": {"kind": "water", "cells": dry}})
    if planted:
        out.append({"id": "probe", "kind": "probe", "title": f"Probe the soil of {len(planted)} planted cells",
                    "detail": "moisture and temperature per cell", "spec": {"kind": "probe", "cells": planted}})
        out.append({"id": "photo", "kind": "photo", "title": f"Photograph {len(planted)} planted cells",
                    "detail": "one picture per cell, from above", "spec": {"kind": "photo", "cells": planted}})
    return out


def main(argv: List[str] | None = None) -> int:
    from .core.version import __version__

    ap = argparse.ArgumentParser(prog="permtek5-twin-edge",
                                 description=__doc__.strip().splitlines()[0])
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="list what stops a habitat from being simulated")
    p.add_argument("file", nargs="?", help="the habitat's Turtle (default: CityDemo)")
    p = sub.add_parser("world", help="write the Webots world and bed.json into the workspace")
    p.add_argument("file", nargs="?", help="the habitat's Turtle (default: CityDemo)")
    p.add_argument("--out", help="folder to write into (default: the workspace's guilds/main/)")
    p.add_argument("--guild", help="which Perma5Guild of the habitat (default: its first)")
    p = sub.add_parser("jobs", help="the jobs proposed for the prepared bed")
    p.add_argument("--out", help="folder the world was written into")
    args = ap.parse_args(argv)

    try:
        if args.command == "jobs":
            cfg = config(args.out)
            if cfg is None:
                print("  ! no world prepared yet: run `permtek5-twin-edge world` first", file=sys.stderr)
                return 1
            json.dump([{k: p[k] for k in ("id", "title", "detail")} for p in proposals(cfg)], sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        ttl = habitat_ttl(args.file)
        errors = check(ttl)
        for message in errors:
            print(f"  ! {message}", file=sys.stderr)
        if args.command == "check":
            if not errors:
                print("  ready to simulate")
            return 1 if errors else 0
        if errors:
            return 1
        json.dump(prepare(ttl, args.out, guild=args.guild), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    except ValueError as exc:
        print(f"  ! {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
