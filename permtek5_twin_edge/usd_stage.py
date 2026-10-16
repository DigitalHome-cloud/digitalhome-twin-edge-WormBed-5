# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
usd_stage.py — a twin run as an OpenUSD stage: the guild in 3D, with the gantry and every plant over time.

    write(cfg, track, out, shapes=SHAPES, frames=None)    -> the .usda file written

A derived view, like the Webots world: the habitat's A-Box stays the truth; the stage is made from the prepared
guild (bed.json) and a recorded run (track.py), never edited and never stored in the A-Box. One self-contained text
file (USD ASCII, z up, metres): the shape library's meshes (shapes/*.usda, made by assets/build.py) are inlined as
classes and referenced, so the file opens anywhere: usdview, Blender, any USD tool.

    /Guild                      kind assembly; customData: the bed, the period, the start, rehearsal
    /Guild/Bed                  the substrate, the raked soil, the frame
    /Guild/WormBox              the two-bin box at the bed's far end, its lid closed
    /Guild/Gantry/Bridge        travels along x (time samples); /Carriage across y; /Tool down by z
    /Guild/Plants/cell_<r>_<c>  each cell's plant: visibility and scale over time, its crop's colour
    /Guild/Sun                  a distant light, as bright as the run's sun

Time codes are the simulated hours since the run began (24 a second: a day a second), or with `frames` the run
spread over that many (24 a second: a film of frames/24 seconds). The placement is bed.json's, the numbers the
Webots world was made with. Standard library only: the film script loads it in Blender's own Python.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SHAPES = Path(__file__).resolve().parent / "shapes"
USED = ("bed_soil", "gantry_bridge", "plant_grown", "worm_box", "worm_box_holes", "worm_box_lid")
BIN_ABOVE = 0.30            # world.py: the working bin stands this high above the soil (bed.json has its bedding level)
SEED_SIZE = 0.06            # a sowing not come up yet, against the grown plant
UP_FROM = 0.15              # a plant that just came up, against the grown plant


def _f(x: float) -> str:
    """A number for the file: 7 significant digits, so a time code just before another stays apart from it."""
    return f"{x:.7g}" if abs(x) >= 1e-6 or x == 0 else "0"


def _v(v) -> str:
    return "(" + ", ".join(_f(float(x)) for x in v) + ")"


def _shape(shapes: Path, name: str) -> str:
    """A shape of the library as a class prim of this stage (its header dropped, its root renamed _<name>)."""
    text = (shapes / f"{name}.usda").read_text(encoding="utf-8")
    m = re.search(r'^def (\w+) "' + re.escape(name) + r'"', text, re.M)
    if not m:
        raise ValueError(f"{name}.usda has no root prim {name}")
    return f'class {m.group(1)} "_{name}"' + text[m.end():].rstrip() + "\n"


def _xform(name: str, translate=None, scale=None, rotate_y: Optional[float] = None, rotate_z: Optional[float] = None,
           body: str = "", kind: str = "Xform", ref: Optional[str] = None, extra: str = "", indent: int = 1,
           moves: Optional[List[Tuple[float, Any]]] = None, sizes: Optional[List[Tuple[float, Any]]] = None,
           shown: Optional[List[Tuple[float, str]]] = None) -> str:
    """One prim. `moves`, `sizes` and `shown` are time samples of its translate, scale and visibility."""
    pad = "    " * indent
    meta = f" (\n{pad}    prepend references = </_{ref}>\n{pad})" if ref else ""
    keys = lambda ks, val: "{" + "".join(f" {_f(t)}: {val(v)}," for t, v in ks) + " }"    # noqa: E731
    ops, lines = [], []
    if translate is not None:
        lines.append(f"double3 xformOp:translate = {_v(translate)}")
        if moves:
            lines.append(f"double3 xformOp:translate.timeSamples = {keys(moves, _v)}")
        ops.append('"xformOp:translate"')
    if rotate_y is not None:
        lines.append(f"float xformOp:rotateY = {_f(rotate_y)}")
        ops.append('"xformOp:rotateY"')
    if rotate_z is not None:
        lines.append(f"float xformOp:rotateZ = {_f(rotate_z)}")
        ops.append('"xformOp:rotateZ"')
    if scale is not None:
        lines.append(f"float3 xformOp:scale = {_v(scale)}")
        if sizes:
            lines.append(f"float3 xformOp:scale.timeSamples = {keys(sizes, _v)}")
        ops.append('"xformOp:scale"')
    if shown:
        lines.append(f"token visibility.timeSamples = {keys(shown, str)}")
    if ops:
        lines.append(f"uniform token[] xformOpOrder = [{', '.join(ops)}]")
    inner = "".join(f"{pad}    {x}\n" for x in [*lines, *[e for e in extra.splitlines() if e.strip()]])
    return f'{pad}def {kind} "{name}"{meta}\n{pad}{{\n{inner}{body}{pad}}}\n'


def _cube(name: str, centre, size, colour, indent: int = 2) -> str:
    """A box from its centre and its size (USD's Cube is 2 across: scaled by half the size)."""
    return _xform(name, centre, [s / 2.0 for s in size], kind="Cube", indent=indent,
                  extra=f"color3f[] primvars:displayColor = [{_v(colour)}]")


def _held(keys: List[Tuple[float, Any]], tc: float, value: Any) -> None:
    """Add a key; a jump from or to nothing is held until just before (no growing out of thin air between samples)."""
    if keys and keys[-1][1] == value:
        return
    if keys and keys[-1][0] >= tc:                        # the same moment: the newer value stands
        keys[-1] = (keys[-1][0], value)
        return
    nothing = ((0.0, 0.0, 0.0), '"invisible"')
    if keys and (keys[-1][1] in nothing or value in nothing) and tc - 0.01 > keys[-1][0]:
        keys.append((tc - 0.01, keys[-1][1]))
    keys.append((tc, value))


def timeline(cfg: Dict[str, Any], track: Dict[str, Any], frames: Optional[int] = None) -> Dict[str, Any]:
    """The run as time samples: each cell's size (0..its grown width, as the twin's meshes grow) and whether it shows,
    the gantry's x/y/z, the sun and the dates. Time codes are simulated hours since the run began, or with `frames`
    the run spread over 1..frames. The stage (write) and the edge page's replay both read it."""
    world = cfg["world"]
    cell = world["cellM"]
    samples = track.get("samples") or []
    t0 = samples[0]["t"] if samples else 0.0
    t1 = samples[-1]["t"] if samples else 0.0
    if frames:
        tc = lambda t: 1.0 + (t - t0) / max(1e-6, t1 - t0) * (frames - 1)     # noqa: E731
        start, end = 1.0, float(frames)
    else:
        tc = lambda t: (t - t0) / 60.0                                         # noqa: E731
        start, end = 0.0, (t1 - t0) / 60.0

    # the cells: the prepared ones, and every period's target the run may move on to
    cells: Dict[str, Dict[str, Any]] = {}
    for c in [*cfg.get("cells", []), *[x for t in (cfg.get("targets") or {}).values() for x in t]]:
        cells.setdefault(f'{c["row"]}_{c["col"]}', c)
    full = {k: max(0.02, (c.get("job") or {}).get("spacingMm", cell * 1000) / 1000.0 * 0.38) / 0.1 for k, c in cells.items()}

    def look(state: Optional[Dict[str, Any]], k: str) -> Tuple[float, str]:
        st = state or {}
        status = st.get("status") or cells[k].get("status")
        if status == "CellGrowing" or st.get("up"):
            # a recorded plant by its growth; one standing when the run began (no record yet) as half grown
            growth = float(st.get("growth") or 0.0) if state else 0.6
            return full[k] * (UP_FROM + (1.0 - UP_FROM) * min(1.0, growth)), '"inherited"'
        if status == "CellSown":
            return full[k] * SEED_SIZE, '"inherited"'
        return 0.0, '"invisible"'

    sizes: Dict[str, List[Tuple[float, Any]]] = {k: [] for k in cells}
    shown: Dict[str, List[Tuple[float, Any]]] = {k: [] for k in cells}
    for k in cells:                                          # as the run began
        s, v = look(None, k)
        sizes[k].append((start, (s, s, s)))
        shown[k].append((start, v))
    gantry, sun, dates = [], [], []
    for x in samples:
        at = tc(x["t"])
        for k, st in (x.get("cells") or {}).items():
            if k not in cells:
                continue
            s, v = look(st, k)
            _held(sizes[k], at, (s, s, s))
            _held(shown[k], at, v)
        gantry.append((at, tuple(x.get("gantry") or (0.0, 0.0, 0.0))))
        if x.get("sun") is not None:
            sun.append((at, 3.0 * float(x["sun"])))
        if x.get("date") and (not dates or dates[-1][1] != x["date"]):
            dates.append((at, x["date"]))
    return {"start": start, "end": max(start, end), "cells": cells, "full": full, "sizes": sizes, "shown": shown,
            "gantry": gantry, "sun": sun, "dates": dates}


def write(cfg: Dict[str, Any], track: Dict[str, Any], out: Path, shapes: Path = SHAPES, frames: Optional[int] = None) -> Path:
    """Write the run's stage to `out` and return it. `track` is track.read(); without samples the stage holds the
    prepared bed as it starts."""
    bed, world, gantry = cfg["bed"], cfg["world"], cfg.get("gantry") or {}
    L, W, cell = bed["length_cm"] / 100.0, bed["width_cm"] / 100.0, world["cellM"]
    soil_z, rail_z = world["soilZ"], gantry.get("railZ", 0.95)
    rail_out, frame_t, tip = gantry.get("railOut", 0.10), gantry.get("frameT", 0.03), world.get("tipHome", soil_z + 0.42)
    tl = timeline(cfg, track, frames)
    start, end, cells = tl["start"], tl["end"], tl["cells"]
    scale_keys, vis_keys, sun = tl["sizes"], tl["shown"], tl["sun"]
    bridge = [(at, (gx, 0.0, 0.0)) for at, (gx, gy, gz) in tl["gantry"]]
    carriage = [(at, (0.0, gy, rail_z + 0.04)) for at, (gx, gy, gz) in tl["gantry"]]
    tool = [(at, (0.0, 0.0, tip + 0.015 - gz - (rail_z + 0.04))) for at, (gx, gy, gz) in tl["gantry"]]

    head = track.get("head") or {}
    period = head.get("period") or cfg.get("period") or {}
    custom = (f'string bed = "{bed.get("name", "")}"\n            string period = "{period.get("name", "")}"\n'
              f'            string start = "{head.get("start") or cfg.get("start", "")}"\n            bool rehearsal = {1 if head.get("rehearsal", True) else 0}')
    out = Path(out)
    parts: List[str] = [
        "#usda 1.0\n(\n"
        f'    defaultPrim = "Guild"\n    doc = "A wormbed5.edge twin run (permtek5-twin-edge usd_stage.py): a derived view, never edited."\n'
        f"    metersPerUnit = 1\n    upAxis = \"Z\"\n    startTimeCode = {_f(start)}\n    endTimeCode = {_f(max(start, end))}\n"
        "    timeCodesPerSecond = 24\n    framesPerSecond = 24\n)\n\n"]
    parts += [_shape(shapes, n) + "\n" for n in USED]

    soil, wood, alu = (0.12, 0.08, 0.05), (0.38, 0.25, 0.14), (0.75, 0.77, 0.79)
    planted = ((cfg.get("wormBin") or {}).get("x") or [L, L])[0]
    bed_body = (_cube("substrate", (planted / 2, W / 2, soil_z / 2 - 0.01), (planted, W, soil_z - 0.02), soil)
                + _xform("soil", (0.0, 0.0, soil_z), (planted, W, 1.0), ref="bed_soil", indent=2,
                         extra=f"color3f[] primvars:displayColor = [{_v(soil)}]"))
    h = soil_z + 0.03
    for name, c, s in (("frame_south", (L / 2, -frame_t / 2, h / 2), (L + 2 * frame_t, frame_t, h)),
                       ("frame_north", (L / 2, W + frame_t / 2, h / 2), (L + 2 * frame_t, frame_t, h)),
                       ("frame_west", (-frame_t / 2, W / 2, h / 2), (frame_t, W, h)),
                       ("frame_east", (L + frame_t / 2, W / 2, h / 2), (frame_t, W, h))):
        bed_body += _cube(name, c, s, wood)
    body = _xform("Bed", body=bed_body)
    if (cfg.get("wormBin") or {}).get("cols"):
        bin_m = L - planted
        box_h = world["binSurfaceZ"] / 0.88 if "binSurfaceZ" in world else soil_z + BIN_ABOVE
        sc = (bin_m, W, box_h)
        box_body = (_xform("box", (planted, 0.0, 0.0), sc, ref="worm_box", indent=2, extra=f"color3f[] primvars:displayColor = [{_v(wood)}]")
                    + _xform("holes", (planted, 0.0, 0.0), sc, ref="worm_box_holes", indent=2, extra="color3f[] primvars:displayColor = [(0.02, 0.015, 0.01)]")
                    + _xform("lid", (planted + 1.03 * bin_m, 0.0, 0.95 * box_h), sc, ref="worm_box_lid", indent=2,
                             extra="color3f[] primvars:displayColor = [(0.62, 0.6, 0.52)]"))
        body += _xform("WormBox", body=box_body)

    # the gantry: rails and posts stand; the bridge travels along x, the carriage across it, the tool down
    x0, x1 = world.get("x", [-0.12, L + 0.05])
    g_body = ""
    for i, y in enumerate((-rail_out, W + rail_out)):
        g_body += _cube(f"rail_{i}", ((x0 + x1) / 2, y, rail_z), (x1 - x0 + 0.2, 0.04, 0.04), alu)
        for j, x in enumerate((x0 - 0.08, x1 + 0.08)):
            g_body += _cube(f"post_{i}_{j}", (x, y, rail_z / 2), (0.04, 0.04, rail_z), alu)
    tool = _xform("Tool", (0.0, 0.0, tip + 0.015 - (rail_z + 0.04)), indent=4, moves=tool,
                  body=_cube("tip", (0.0, 0.0, 0.0), (0.024, 0.024, 0.03), (0.79, 0.58, 0.31), indent=5))
    carriage = _xform("Carriage", (0.0, W * 0.45, rail_z + 0.04), indent=3, moves=carriage,
                      body=_cube("block", (0.0, 0.0, 0.0), (0.10, 0.10, 0.08), (0.05, 0.05, 0.06), indent=4) + tool)
    profile = _xform("profile", (0.0, W / 2, rail_z + 0.04), (1.0, W + 2 * rail_out, 1.0), ref="gantry_bridge", indent=3,
                     extra=f"color3f[] primvars:displayColor = [{_v(alu)}]")
    g_body += _xform("Bridge", (0.0, 0.0, 0.0), indent=2, moves=bridge, body=profile + carriage)
    body += _xform("Gantry", body=g_body)

    # the plants, each in its crop's colour, coming and going over the run
    p_body = ""
    for k in sorted(cells, key=lambda k: tuple(int(x) for x in k.split("_"))):
        c = cells[k]
        r, col = (int(x) for x in k.split("_"))
        colour = [0.8 * v for v in (c.get("rgb") or (0.3, 0.6, 0.3))]
        p_body += _xform(f"cell_{k}", ((col + 0.5) * cell, (r + 0.5) * cell, soil_z), scale_keys[k][0][1], ref="plant_grown", indent=2,
                         rotate_z=((r * 7 + col * 13) % 6) * 57.2958, sizes=scale_keys[k], shown=vis_keys[k],
                         extra=f'color3f[] primvars:displayColor = [{_v(colour)}]\ncustom string permtek:crop = "{c.get("crop", "")}"')
    body += _xform("Plants", body=p_body)
    sun_extra = "float inputs:angle = 0.53\nfloat inputs:intensity = 2\n" + (
        "float inputs:intensity.timeSamples = {" + "".join(f" {_f(a)}: {_f(b)}," for a, b in sun) + " }\n" if sun else "")
    body += _xform("Sun", rotate_y=35.0, kind="DistantLight", extra=sun_extra)

    parts.append(f'def Xform "Guild" (\n    kind = "assembly"\n    customData = {{\n            {custom}\n    }}\n)\n{{\n{body}}}\n')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    return out
