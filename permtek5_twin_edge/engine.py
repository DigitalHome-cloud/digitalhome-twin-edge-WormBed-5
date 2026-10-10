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


def workspace() -> Path:
    """Where generated worlds and run data go: the person's folder, never the repository."""
    from .core import workspace as ws
    return ws.base_dir(ws.USER) / "twin"


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


def prepare(ttl: str, out: Optional[Path] = None, assets: Optional[Path] = None) -> Dict[str, Any]:
    """Write the world and bed.json for the habitat's first bed. Raises ValueError with what is wrong."""
    from . import habitat as hb
    from . import world
    errors = check(ttl)
    if errors:
        raise ValueError("; ".join(errors))
    out = Path(out) if out else workspace()
    if assets is None and (out / "assets").is_dir():
        assets = out / "assets"
    paths = world.generate(hb.load(ttl), hb.load_library(), out, assets=assets)
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
                                      "cellCm": b["cell_cm"], "rows": b["rows"], "cols": b["cols"]},
            "cells": len(cfg["cells"]), "byStatus": by, "meshes": cfg["world"].get("meshes", False)}


def proposals(cfg: Optional[Dict[str, Any]], bed_state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The jobs the gantry could do now. Proposals only: a person confirms each before anything moves.

    Sowing: every planned cell not sown yet. Watering: planted cells whose soil
    is dry, each with what its crop wants for a day. A probe pass and a photo
    pass over what is planted. `bed_state` is the running twin's /bed/state.
    Whether this is the right MONTH to sow is PermTek-5's calendar, not decided here.
    """
    if not cfg:
        return []
    live = (bed_state or {}).get("cells", {})
    out: List[Dict[str, Any]] = []
    planted = []
    dry = []
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
            if moisture is not None and moisture < DRY_BELOW_PCT and c.get("waterMlPerDay"):
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
    p.add_argument("--out", help="folder to write into (default: the workspace's twin/)")
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
        json.dump(prepare(ttl, args.out), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    except ValueError as exc:
        print(f"  ! {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
