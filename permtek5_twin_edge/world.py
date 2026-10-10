# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
world.py — a Webots world of one roof bed and its gantry, from a habitat.

    generate(habitat, library, out_dir) -> {"world": path, "config": path}

Writes roofbed.wbt and bed.json into out_dir (the workspace, never the
repository). Both are outputs: change the habitat or this generator and run
it again, never edit them by hand.

World frame = bed frame, in metres: origin at the outer corner of row 0 /
column 0 on the roof, x along the bed's length, y across its width, z up. The
substrate surface is at z = SOIL_Z. The gantry is one Robot with three slider
joints (x, y, z); a joint's position is the bed coordinate itself, so
"x_motor to 0.45" puts the head over x = 450 mm. Nothing has physics: axes
move kinematically, plants are scenery the bed supervisor swaps.

The world is self-contained: no downloaded assets. Looks come from meshes in
`assets` when they exist (assets/build.py, Blender), from boxes and cylinders
when they do not.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import habitat as hb

SOIL_Z = 0.30            # substrate surface above the roof
FRAME_T = 0.03           # bed frame boards
RAIL_Z = 0.95            # gantry rails above the roof
RAIL_OUT = 0.10          # rails outside the bed's long sides
TIP_HOME = SOIL_Z + 0.25  # tool tip at z joint = 0
CAM_Z = SOIL_Z + 0.50     # tool camera above the soil (on the carriage, so the view does not bob)
AXIS_SPEED = 0.25        # m/s
PARK = 0.12              # the head parks this far outside the bed along x
CAM_DX = 0.07            # the tool camera sits this far beside the tool, along x


crop_rgb = hb.crop_rgb


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if "." in f"{x:.4f}" else f"{x:.4f}"


def _look(frm: Tuple[float, float, float], at: Tuple[float, float, float]) -> str:
    """Axis-angle that points a Webots camera (x forward, z up) from `frm` to `at`."""
    fx, fy, fz = (at[i] - frm[i] for i in range(3))
    n = math.sqrt(fx * fx + fy * fy + fz * fz)
    fx, fy, fz = fx / n, fy / n, fz / n
    lx, ly, lz = -fy, fx, 0.0                      # left = up(z) × forward
    n = math.hypot(lx, ly) or 1.0
    lx, ly = lx / n, ly / n
    ux, uy, uz = fy * lz - fz * ly, fz * lx - fx * lz, fx * ly - fy * lx   # up = forward × left
    m = [[fx, lx, ux], [fy, ly, uy], [fz, lz, uz]]
    angle = math.acos(max(-1.0, min(1.0, (m[0][0] + m[1][1] + m[2][2] - 1) / 2)))
    if angle < 1e-6:
        return "0 0 1 0"
    s = 2 * math.sin(angle)
    ax, ay, az = (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s
    return f"{_f(ax)} {_f(ay)} {_f(az)} {_f(angle)}"


def _box(name: str, pos, size, rgb, rough: float = 1.0, metal: float = 0.0) -> str:
    return (f'Pose {{ translation {_f(pos[0])} {_f(pos[1])} {_f(pos[2])} children [ Shape {{ '
            f'appearance PBRAppearance {{ baseColor {_f(rgb[0])} {_f(rgb[1])} {_f(rgb[2])} roughness {_f(rough)} metalness {_f(metal)} }} '
            f'geometry Box {{ size {_f(size[0])} {_f(size[1])} {_f(size[2])} }} }} ] }}  # {name}')


def _mesh(assets: Optional[Path], name: str) -> Optional[str]:
    p = assets / f"{name}.obj" if assets else None
    return str(p) if p and p.exists() else None


def plant_node(cell: Dict[str, Any], cell_m: float, assets: Optional[Path] = None) -> str:
    """One planted cell as a node string; also what the bed supervisor imports when a cell is sown."""
    x, y = (cell["col"] + 0.5) * cell_m, (cell["row"] + 0.5) * cell_m
    r, g, b = crop_rgb(cell["crop"])
    growing = cell["status"] == "CellGrowing"
    spacing = (cell.get("job") or {}).get("spacingMm", cell_m * 1000) / 1000.0
    radius = max(0.02, spacing * 0.38) if growing else 0.012
    stage = "plant_grown" if growing else "plant_seedling"
    mesh = _mesh(assets, stage)
    app = f"PBRAppearance {{ baseColor {_f(r)} {_f(g)} {_f(b)} roughness 0.9 metalness 0 }}"
    if mesh:
        k = radius / 0.1                      # the meshes are modelled 0.2 m across
        shape = (f'Transform {{ scale {_f(k)} {_f(k)} {_f(k)} children [ Shape {{ appearance {app} '
                 f'geometry Mesh {{ url [ "{mesh}" ] }} }} ] }}')
    else:
        shape = (f"Transform {{ scale 1 1 {'0.45' if growing else '1'} children [ Shape {{ appearance {app} "
                 f"geometry Sphere {{ radius {_f(radius)} subdivision 2 }} }} ] }}")
    return (f'DEF CELL_{cell["row"]}_{cell["col"]} Pose {{ translation {_f(x)} {_f(y)} {_f(SOIL_Z + (0.0 if mesh else radius * (0.45 if growing else 1)))} '
            f"children [ {shape} ] }}")


def _gantry(length: float, width: float, zmax: float, assets: Optional[Path]) -> str:
    alu = "PBRAppearance { baseColor 0.72 0.74 0.76 roughness 0.35 metalness 0.8 }"
    dark = "PBRAppearance { baseColor 0.12 0.13 0.14 roughness 0.6 metalness 0.2 }"
    span = width + 2 * RAIL_OUT
    x0, x1 = -PARK, length + 0.05

    def part(mesh_name: str, fallback: str) -> str:
        mesh = _mesh(assets, mesh_name)
        return f'Shape {{ appearance {alu} geometry Mesh {{ url [ "{mesh}" ] }} }}' if mesh else fallback

    rail_len = x1 - x0 + 0.2
    rails = "\n    ".join(
        _box(f"rail {i}", ((x0 + x1) / 2, y, RAIL_Z), (rail_len, 0.04, 0.04), (0.72, 0.74, 0.76), 0.35, 0.8)
        for i, y in enumerate((-RAIL_OUT, width + RAIL_OUT)))
    posts = "\n    ".join(
        _box("post", (x, y, RAIL_Z / 2), (0.04, 0.04, RAIL_Z), (0.72, 0.74, 0.76), 0.35, 0.8)
        for x in (x0 - 0.08, x1 + 0.08) for y in (-RAIL_OUT, width + RAIL_OUT))
    overview_at = (length / 2, -1.7, 1.7)
    return f"""Robot {{
  name "gantry"
  controller "<extern>"
  supervisor TRUE
  children [
    {rails}
    {posts}
    Pose {{
      translation {_f(overview_at[0])} {_f(overview_at[1])} {_f(overview_at[2])}
      rotation {_look(overview_at, (length / 2, width / 2, SOIL_Z))}
      children [ Camera {{ name "overview_camera" fieldOfView 1.1 width 480 height 320 }} ]
    }}
    SliderJoint {{
      jointParameters JointParameters {{ axis 1 0 0 minStop {_f(x0)} maxStop {_f(x1)} }}
      device [
        LinearMotor {{ name "x_motor" minPosition {_f(x0)} maxPosition {_f(x1)} maxVelocity {AXIS_SPEED} }}
        PositionSensor {{ name "x_sensor" }}
      ]
      endPoint Solid {{
        name "bridge"
        children [
          Pose {{ translation 0 {_f(width / 2)} {_f(RAIL_Z + 0.04)} children [
            {part("gantry_bridge", f"Shape {{ appearance {alu} geometry Box {{ size 0.06 {_f(span)} 0.04 }} }}")} ] }}
          SliderJoint {{
            jointParameters JointParameters {{ axis 0 1 0 minStop 0 maxStop {_f(width)} }}
            device [
              LinearMotor {{ name "y_motor" minPosition 0 maxPosition {_f(width)} maxVelocity {AXIS_SPEED} }}
              PositionSensor {{ name "y_sensor" }}
            ]
            endPoint Solid {{
              name "carriage"
              children [
                Pose {{ translation 0 0 {_f(RAIL_Z + 0.04)} children [
                  Shape {{ appearance {dark} geometry Box {{ size 0.10 0.10 0.08 }} }} ] }}
                Pose {{
                  translation {CAM_DX} 0 {_f(CAM_Z)}
                  rotation 0 1 0 1.5708
                  children [ Camera {{ name "tool_camera" fieldOfView 1.0 width 320 height 240 }} ]
                }}
                SliderJoint {{
                  jointParameters JointParameters {{ axis 0 0 -1 minStop 0 maxStop {_f(zmax)} }}
                  device [
                    LinearMotor {{ name "z_motor" minPosition 0 maxPosition {_f(zmax)} maxVelocity 0.12 }}
                    PositionSensor {{ name "z_sensor" }}
                  ]
                  endPoint Solid {{
                    name "head"
                    children [
                      Pose {{ translation 0 0 {_f((TIP_HOME + RAIL_Z) / 2 + 0.02)} children [
                        Shape {{ appearance {alu} geometry Box {{ size 0.03 0.03 {_f(RAIL_Z - TIP_HOME + 0.04)} }} }} ] }}
                      Pose {{ translation 0 0 {_f(TIP_HOME + 0.015)} children [
                        Shape {{ appearance {dark} geometry Cylinder {{ radius 0.012 height 0.03 }} }} ] }}
                    ]
                  }}
                }}
              ]
            }}
          }}
        ]
      }}
    }}
  ]
}}"""


def generate(h: hb.Habitat, library: Dict[str, hb.Crop], out_dir: Path, bed_id: Optional[str] = None,
             assets: Optional[Path] = None) -> Dict[str, str]:
    """Write roofbed.wbt and bed.json for one bed (the first, unless bed_id says which)."""
    bad = hb.problems(h, library)
    if bad:
        raise ValueError("; ".join(bad))
    bed = next((b for b in h.beds if bed_id in (None, b.id)), None)
    if bed is None:
        raise ValueError("the habitat has no growing bed to simulate")
    cfg = hb.bed_config(h, bed, library)
    length, width, cell_m = bed.length_cm / 100.0, bed.width_cm / 100.0, bed.cell_cm / 100.0
    depth = min((bed.depth_cm or 25) / 100.0, SOIL_Z - 0.02)
    zmax = (TIP_HOME - SOIL_Z) + min(0.15, depth)
    cfg["world"] = {"soilZ": SOIL_Z, "tipHome": TIP_HOME, "zMax": zmax, "park": PARK, "cellM": cell_m,
                    "x": [-PARK, length + 0.05], "y": [0.0, width], "camOffsetX": CAM_DX, "meshes": bool(assets and _mesh(assets, "plant_grown"))}

    wood, soil = (0.45, 0.33, 0.22), (0.20, 0.15, 0.11)
    w: List[str] = ["#VRML_SIM R2025a utf8", "# generated by permtek5_twin_edge/world.py; self-contained: no assets downloaded", ""]
    view = (length / 2, -2.6, 2.3)
    w.append(f"""WorldInfo {{
  title "Roof bed: {bed.name.replace('"', "'")}"
  basicTimeStep 32
}}
Viewpoint {{
  orientation {_look(view, (length / 2, width / 2, SOIL_Z))}
  position {_f(view[0])} {_f(view[1])} {_f(view[2])}
}}
Background {{ skyColor [ 0.62 0.78 0.92 ] luminosity 1.2 }}
DirectionalLight {{ ambientIntensity 0.7 direction -0.4 0.6 -1 intensity 2.0 castShadows TRUE }}
DirectionalLight {{ ambientIntensity 0.3 direction 0.5 -0.4 -0.6 intensity 0.6 }}""")
    w.append(_box("roof", (length / 2, width / 2, -0.05), (length + 3.0, width + 3.0, 0.10), (0.55, 0.55, 0.57)))
    soil_mesh = _mesh(assets, "bed_soil")
    if soil_mesh:
        w.append(f'Pose {{ translation 0 0 {_f(SOIL_Z)} children [ Transform {{ scale {_f(length)} {_f(width)} 1 children [ Shape {{ '
                 f'appearance PBRAppearance {{ baseColor {_f(soil[0])} {_f(soil[1])} {_f(soil[2])} roughness 1 metalness 0 }} '
                 f'geometry Mesh {{ url [ "{soil_mesh}" ] }} }} ] }} ] }}  # soil surface')
        w.append(_box("substrate", (length / 2, width / 2, SOIL_Z / 2 - 0.01), (length, width, SOIL_Z - 0.02), soil))
    else:
        w.append(_box("substrate", (length / 2, width / 2, SOIL_Z / 2), (length, width, SOIL_Z), soil))
    for name, pos, size in (
            ("frame south", (length / 2, -FRAME_T / 2, (SOIL_Z + 0.03) / 2), (length + 2 * FRAME_T, FRAME_T, SOIL_Z + 0.03)),
            ("frame north", (length / 2, width + FRAME_T / 2, (SOIL_Z + 0.03) / 2), (length + 2 * FRAME_T, FRAME_T, SOIL_Z + 0.03)),
            ("frame west", (-FRAME_T / 2, width / 2, (SOIL_Z + 0.03) / 2), (FRAME_T, width, SOIL_Z + 0.03)),
            ("frame east", (length + FRAME_T / 2, width / 2, (SOIL_Z + 0.03) / 2), (FRAME_T, width, SOIL_Z + 0.03))):
        w.append(_box(name, pos, size, wood))
    for c in cfg["cells"]:
        if c["status"] in ("CellSown", "CellGrowing"):
            w.append(plant_node(c, cell_m, assets))
    w.append(_gantry(length, width, zmax, assets))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    world, config = out_dir / "roofbed.wbt", out_dir / "bed.json"
    world.write_text("\n".join(w) + "\n", encoding="utf-8")
    config.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"world": str(world), "config": str(config)}
