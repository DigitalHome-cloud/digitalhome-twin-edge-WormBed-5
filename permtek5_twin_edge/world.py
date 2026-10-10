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

    mesh = _mesh(assets, "gantry_bridge")        # modelled 1 m long along y
    bridge = (f'Transform {{ scale 1 {_f(span)} 1 children [ Shape {{ appearance {alu} geometry Mesh {{ url [ "{mesh}" ] }} }} ] }}'
              if mesh else f"Shape {{ appearance {alu} geometry Box {{ size 0.06 {_f(span)} 0.04 }} }}")

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
            {bridge} ] }}
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


BIN_ABOVE = 0.12         # the worm box stands this far above the soil
PIPE_STUB = 0.06         # the tank pipes' capped ends stick out of the bed's near end


def _system(bed: hb.Bed, length: float, width: float, planted: float, hopper_y: float, assets: Optional[Path]) -> List[str]:
    """The bed as a system: tank pipes under the soil, the worm box at the far end, the hopper at the near end."""
    out: List[str] = []
    pipe = "PBRAppearance { baseColor 0.85 0.45 0.12 roughness 0.6 metalness 0 }"       # drain-pipe orange
    if bed.tank_pipes:
        r = min(bed.tank_pipe_mm / 2000.0, SOIL_Z / 2 - 0.02)
        gap = width / bed.tank_pipes
        for i in range(bed.tank_pipes):
            y = gap * (i + 0.5)
            # only the capped ends show; the pipes themselves lie under the soil along the bed
            out.append(f"Pose {{ translation {_f(-FRAME_T - PIPE_STUB / 2)} {_f(y)} {_f(r + 0.02)} rotation 0 1 0 1.5708 children [ "
                       f"Shape {{ appearance {pipe} geometry Cylinder {{ radius {_f(r)} height {_f(PIPE_STUB)} }} }} ] }}  # tank pipe {i + 1}")
        # the fill pipe, with its cap, at the near corner
        out.append(f"Pose {{ translation 0.06 0.06 {_f(SOIL_Z / 2 + 0.1)} children [ Shape {{ appearance {pipe} "
                   f"geometry Cylinder {{ radius 0.025 height {_f(SOIL_Z + 0.2)} }} }} ] }}  # fill pipe")
    if bed.worm_bin_cm:
        bin_m = length - planted
        box_h = SOIL_Z + BIN_ABOVE
        mesh = _mesh(assets, "worm_box")
        dark = "PBRAppearance { baseColor 0.30 0.21 0.13 roughness 0.9 metalness 0 }"
        if mesh:                                 # modelled 1 x 1 x 1, standing on z = 0
            out.append(f'Pose {{ translation {_f(planted)} 0 0 children [ Transform {{ scale {_f(bin_m)} {_f(width)} {_f(box_h)} children [ '
                       f'Shape {{ appearance {dark} geometry Mesh {{ url [ "{mesh}" ] }} }} ] }} ] }}  # worm box')
        else:
            out.append(_box("worm box", (planted + bin_m / 2, width / 2, box_h / 2), (bin_m, width, box_h), (0.30, 0.21, 0.13)))
            out.append(_box("worm box lid", (planted + bin_m / 2, width / 2, box_h + 0.012), (bin_m + 0.02, width + 0.02, 0.024), (0.62, 0.60, 0.52)))
    # the hopper a person fills, on a stand at the near end where the head parks
    out.append(_box("hopper stand", (-PARK, hopper_y, 0.16), (0.05, 0.05, 0.32), (0.72, 0.74, 0.76), 0.35, 0.8))
    out.append(_box("hopper", (-PARK, hopper_y, 0.37), (0.16, 0.22, 0.10), (0.20, 0.36, 0.24)))
    return out


TREE_HEIGHT = 3.0        # a garden tree in the scenery, metres
GARDEN_Z = -2.6          # the garden lies a storey below the roof
ROOF_MARGIN = 1.5        # the roof slab reaches this far beyond the bed


def _tree(name: str, x: float, y: float, rgb, assets: Optional[Path]) -> str:
    """A garden plant as scenery: a trunk and a crown in the crop's colour."""
    trunk, canopy = _mesh(assets, "tree_trunk"), _mesh(assets, "tree_canopy")
    bark = "PBRAppearance { baseColor 0.33 0.25 0.18 roughness 1 metalness 0 }"
    green = (0.22, 0.42, 0.18)                    # foliage, tinted a little by the crop's map colour
    mix = [0.8 * g + 0.2 * c for g, c in zip(green, rgb)]
    leaf = f"PBRAppearance {{ baseColor {_f(mix[0])} {_f(mix[1])} {_f(mix[2])} roughness 1 metalness 0 }}"
    k = TREE_HEIGHT
    if trunk and canopy:
        body = (f'Transform {{ scale {_f(k)} {_f(k)} {_f(k)} children [ '
                f'Shape {{ appearance {bark} geometry Mesh {{ url [ "{trunk}" ] }} }} '
                f'Shape {{ appearance {leaf} geometry Mesh {{ url [ "{canopy}" ] }} }} ] }}')
    else:
        body = (f"Pose {{ translation 0 0 {_f(k * 0.25)} children [ Shape {{ appearance {bark} geometry Cylinder {{ radius 0.08 height {_f(k * 0.5)} }} }} ] }} "
                f"Pose {{ translation 0 0 {_f(k * 0.7)} children [ Shape {{ appearance {leaf} geometry Sphere {{ radius {_f(k * 0.32)} subdivision 3 }} }} ] }}")
    return f"Pose {{ translation {_f(x)} {_f(y)} {_f(GARDEN_Z)} children [ {body} ] }}  # {name}"


def generate(h: hb.Habitat, library: Dict[str, hb.Crop], out_dir: Path, guild: Optional[str] = None,
             assets: Optional[Path] = None) -> Dict[str, str]:
    """Write roofbed.wbt and bed.json for one Perma5Guild's bed: the guild named, or the habitat's first."""
    bad = hb.problems(h, library)
    if bad:
        raise ValueError("; ".join(bad))
    _, bed = hb.pick_guild(h, guild)
    if bed is None:
        raise ValueError("the habitat has no growing bed to simulate")
    cfg = hb.bed_config(h, bed, library)
    length, width, cell_m = bed.length_cm / 100.0, bed.width_cm / 100.0, bed.cell_cm / 100.0
    depth = min((bed.depth_cm or 25) / 100.0, SOIL_Z - 0.02)
    zmax = (TIP_HOME - SOIL_Z) + min(0.15, depth)
    cfg["world"] = {"soilZ": SOIL_Z, "tipHome": TIP_HOME, "zMax": zmax, "park": PARK, "cellM": cell_m,
                    "x": [-PARK, length + 0.05], "y": [0.0, width], "camOffsetX": CAM_DX, "meshes": bool(assets and _mesh(assets, "plant_grown")),
                    "seedlingMesh": _mesh(assets, "plant_seedling")}

    # the garden's plants, for scenery here and for the rendered film (assets/demo.py)
    trees = [p for p in h.plants if p.x_m is not None and p.y_m is not None and p.crop_id in library]
    gy0 = width + ROOF_MARGIN + 1.0
    cfg["garden"] = {"y0": gy0, "z": GARDEN_Z, "treeHeight": TREE_HEIGHT, "roofMargin": ROOF_MARGIN,
                     "trees": [{"name": p.name, "x": p.x_m, "y": p.y_m, "rgb": [round(v, 3) for v in crop_rgb(p.crop_id)]} for p in trees]}
    cfg["gantry"] = {"railZ": RAIL_Z, "railOut": RAIL_OUT, "frameT": FRAME_T, "camZ": CAM_Z}

    # the worm box at the bed's far end, and where the gantry scoops from the hopper and drops into the box
    bin_m = bed.worm_bin_cm / 100.0
    planted = length - bin_m                                 # the substrate ends where the worm box begins
    hopper_y = min(width - 0.15, max(0.15, width * 0.5))
    cfg["wormBin"].update({"x": [planted, length], "dropX": planted + bin_m / 2, "spots": max(1, min(4, bed.rows // 3))} if bin_m else {})
    cfg["hopper"] = {"x": -PARK, "y": hopper_y}

    wood, soil = (0.45, 0.33, 0.22), (0.20, 0.15, 0.11)
    g = cfg.get("guild")
    title = f'{g["name"]} · {g["type"]} · {g["robotName"]}' if g else f"Bed: {bed.name}"
    w: List[str] = ["#VRML_SIM R2025a utf8", "# generated by permtek5_twin_edge/world.py; self-contained: no assets downloaded", ""]
    view = (length / 2, -2.6, 2.3)
    w.append(f"""WorldInfo {{
  title "{title.replace('"', "'")}"
  basicTimeStep 32
}}
Viewpoint {{
  orientation {_look(view, (length / 2, width / 2, SOIL_Z))}
  position {_f(view[0])} {_f(view[1])} {_f(view[2])}
}}
Background {{ skyColor [ 0.62 0.78 0.92 ] luminosity 1.2 }}
DirectionalLight {{ ambientIntensity 0.7 direction -0.4 0.6 -1 intensity 2.0 castShadows TRUE }}
DirectionalLight {{ ambientIntensity 0.3 direction 0.5 -0.4 -0.6 intensity 0.6 }}""")
    w.append(_box("roof", (length / 2, width / 2, -0.05), (length + 2 * ROOF_MARGIN, width + 2 * ROOF_MARGIN, 0.10), (0.55, 0.55, 0.57)))
    soil_mesh = _mesh(assets, "bed_soil")
    if soil_mesh:
        w.append(f'Pose {{ translation 0 0 {_f(SOIL_Z)} children [ Transform {{ scale {_f(planted)} {_f(width)} 1 children [ Shape {{ '
                 f'appearance PBRAppearance {{ baseColor {_f(soil[0])} {_f(soil[1])} {_f(soil[2])} roughness 1 metalness 0 }} '
                 f'geometry Mesh {{ url [ "{soil_mesh}" ] }} }} ] }} ] }}  # soil surface')
        w.append(_box("substrate", (planted / 2, width / 2, SOIL_Z / 2 - 0.01), (planted, width, SOIL_Z - 0.02), soil))
    else:
        w.append(_box("substrate", (planted / 2, width / 2, SOIL_Z / 2), (planted, width, SOIL_Z), soil))
    for name, pos, size in (
            ("frame south", (length / 2, -FRAME_T / 2, (SOIL_Z + 0.03) / 2), (length + 2 * FRAME_T, FRAME_T, SOIL_Z + 0.03)),
            ("frame north", (length / 2, width + FRAME_T / 2, (SOIL_Z + 0.03) / 2), (length + 2 * FRAME_T, FRAME_T, SOIL_Z + 0.03)),
            ("frame west", (-FRAME_T / 2, width / 2, (SOIL_Z + 0.03) / 2), (FRAME_T, width, SOIL_Z + 0.03)),
            ("frame east", (length + FRAME_T / 2, width / 2, (SOIL_Z + 0.03) / 2), (FRAME_T, width, SOIL_Z + 0.03))):
        w.append(_box(name, pos, size, wood))
    w.extend(_system(bed, length, width, planted, hopper_y, assets))
    for c in cfg["cells"]:
        if c["status"] in ("CellSown", "CellGrowing"):
            w.append(plant_node(c, cell_m, assets))
    w.append(_gantry(length, width, zmax, assets))
    # The habitat's garden plants, behind and below the roof: scenery, at their own x/y in the garden.
    if trees:
        side = max(6.0, max(max(p.x_m, p.y_m) for p in trees) + 2.0)
        w.append(_box("garden", (side / 2 - 1.0, gy0 + side / 2 - 1.0, GARDEN_Z - 0.05), (side + 2.0, side + 2.0, 0.10), (0.36, 0.47, 0.27)))
        for p in trees:
            w.append(_tree(p.name.replace("#", ""), p.x_m, gy0 + p.y_m, crop_rgb(p.crop_id), assets))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    world, config = out_dir / "roofbed.wbt", out_dir / "bed.json"
    world.write_text("\n".join(w) + "\n", encoding="utf-8")
    config.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"world": str(world), "config": str(config)}
