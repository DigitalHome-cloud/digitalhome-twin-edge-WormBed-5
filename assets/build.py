# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
build.py — the twin's meshes, made with Blender as a Python module (bpy).

    .venv-bpy/bin/python assets/build.py [--out permtek5_twin_edge/shapes] [--force]

Runs ONLY in the bpy environment (scripts/setup-bpy.sh): bpy needs its own
Python version, and neither the app nor the simulation imports it. It writes
plain OBJ files (no materials, nothing downloaded) that world.py uses for
looks when they exist; collisions and motion never depend on them.

Every mesh is modelled at a unit size and scaled by the world:

    plant_seedling.obj, plant_grown.obj   0.2 m across, standing on z = 0
    bed_soil.obj                          a 1 x 1 m surface with small bumps, z about 0
    gantry_bridge.obj                     an aluminium profile 1 m long along y, centred
    tree_trunk.obj, tree_canopy.obj       a tree 1 m tall, standing on z = 0
    worm_box*.obj                         the two-bin worm box in a unit cube; its lid, feed hatch and drilled holes
    worm.obj, cocoon.obj                  a red wiggler and its cocoon, 1 m long along x (scaled to 8 cm and 3 mm)

The same script and parameters give the same files; a hash of this file in
the output folder lets a second run skip the work.
"""
import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import bpy  # first: it puts bmesh and mathutils on the path

import bmesh  # noqa: E402
from mathutils import Matrix, Vector, noise  # noqa: E402

SEED = 5


def new_object(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def leaf(bm, length, width, angle, tilt, curl, base=0.0):
    """One leaf: a pointed oval sheet from the plant's centre outwards, bent up then drooping."""
    rows, cols = 7, 4
    grid = []
    turn = Matrix.Rotation(angle, 4, "Z")
    for i in range(rows + 1):
        t = i / rows
        half = width * math.sin(math.pi * min(1.0, t * 1.08)) ** 0.7 * (1.0 - 0.25 * t)
        r = base + length * t
        z = math.tan(tilt) * r * (1.0 - curl * t)
        line = []
        for j in range(cols + 1):
            s = j / cols * 2 - 1
            cup = 0.18 * half * (1 - s * s)                    # a shallow gutter along the midrib
            line.append(bm.verts.new(turn @ Vector((r, s * half, z - cup + 0.004))))
        grid.append(line)
    for i in range(rows):
        for j in range(cols):
            bm.faces.new((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))


def plant_grown():
    """A grown leafy plant: a rosette of leaves about 0.2 m across, standing on z = 0."""
    rng = random.Random(SEED)
    bm = bmesh.new()
    golden = math.radians(137.5)
    for i in range(13):
        k = i / 12                                              # inner (0) to outer (1)
        leaf(bm, length=0.035 + 0.065 * k, width=0.018 + 0.022 * k, angle=i * golden + rng.uniform(-0.1, 0.1),
             tilt=math.radians(62 - 50 * k), curl=0.35 + 0.3 * k)
    return new_object("plant_grown", bm)


def plant_seedling():
    """A seedling: a short stem with two seed leaves, modelled 0.2 m across (scaled down in the world)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.006, radius2=0.004, depth=0.06,
                          matrix=Matrix.Translation((0, 0, 0.03)))
    for side in (0.0, math.pi):
        top = len(bm.verts)
        leaf(bm, length=0.095, width=0.03, angle=side, tilt=math.radians(12), curl=0.2, base=0.003)
        for v in list(bm.verts)[top:]:
            v.co.z += 0.058
    return new_object("plant_seedling", bm)


def bed_soil():
    """A unit square of raked soil: low ridges along the length and fine clods, flat at the edges."""
    bm = bmesh.new()
    nx, ny = 60, 20
    grid = []
    for j in range(ny + 1):
        line = []
        for i in range(nx + 1):
            x, y = i / nx, j / ny
            edge = min(x, 1 - x, y, 1 - y)
            fade = min(1.0, edge / 0.03)
            z = (0.004 * math.sin(y * math.pi * 24) + 0.006 * noise.noise(Vector((x * 30, y * 12, 0.0)))
                 + 0.003 * noise.noise(Vector((x * 120, y * 48, 3.1)))) * fade
            line.append(bm.verts.new((x, y, z)))
        grid.append(line)
    for j in range(ny):
        for i in range(nx):
            bm.faces.new((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]))
    return new_object("bed_soil", bm)


def gantry_bridge():
    """A slotted aluminium profile, 60 x 40 mm, 1 m long along y."""
    w, h, slot, depth = 0.03, 0.02, 0.005, 0.007
    outline = [(-w, -h), (-slot, -h), (-slot, -h + depth), (slot, -h + depth), (slot, -h), (w, -h),
               (w, -slot), (w - depth, -slot), (w - depth, slot), (w, slot), (w, h),
               (slot, h), (slot, h - depth), (-slot, h - depth), (-slot, h), (-w, h),
               (-w, slot), (-w + depth, slot), (-w + depth, -slot), (-w, -slot)]
    bm = bmesh.new()
    ends = [[bm.verts.new((x, y, z)) for x, z in outline] for y in (-0.5, 0.5)]
    n = len(outline)
    for i in range(n):
        bm.faces.new((ends[0][i], ends[0][(i + 1) % n], ends[1][(i + 1) % n], ends[1][i]))
    bm.faces.new(ends[0])
    bm.faces.new(list(reversed(ends[1])))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = new_object("gantry_bridge", bm)
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj


def tree_trunk():
    """The trunk of a tree 1 m tall, standing on z = 0."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.045, radius2=0.022, depth=0.55,
                          matrix=Matrix.Translation((0, 0, 0.275)))
    for i, (yaw, pitch, length) in enumerate(((0.3, 0.9, 0.22), (2.4, 1.0, 0.2), (4.3, 0.8, 0.24))):   # three main branches
        m = Matrix.Translation((0, 0, 0.42 + 0.03 * i)) @ Matrix.Rotation(yaw, 4, "Z") @ Matrix.Rotation(pitch, 4, "Y") \
            @ Matrix.Translation((0, 0, length / 2))
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.018, radius2=0.008, depth=length, matrix=m)
    return new_object("tree_trunk", bm)


def tree_canopy():
    """A lumpy crown: an icosphere pushed in and out by noise, flatter underneath."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=0.34)
    for v in bm.verts:
        d = v.co.normalized()
        bump = 0.18 * noise.noise(d * 2.2 + Vector((SEED, 0, 0))) + 0.07 * noise.noise(d * 6.0)
        v.co = d * 0.34 * (1.0 + bump)
        if v.co.z < 0:
            v.co.z *= 0.6
        v.co.z += 0.70
    return new_object("tree_canopy", bm)


def _blocks(name, blocks):
    bm = bmesh.new()
    for x0, x1, y0, y1, z0, z1 in blocks:
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
                              @ Matrix.Diagonal((x1 - x0, y1 - y0, z1 - z0, 1.0)))
    obj = new_object(name, bm)
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj


def worm_box():
    """The worm box's two bins in a unit cube (x and y 0..1, standing on z = 0): the lower half is the
    sump with a solid bottom, the upper half the slatted working bin, open at the top."""
    blocks = [(0.02, 0.98, 0.02, 0.98, 0.0, 0.47),                 # bin 2, the sump: plain walls
              (0.0, 1.0, 0.0, 1.0, 0.47, 0.50),                    # the rim bin 1 sits on
              (0.05, 0.95, 0.03, 0.97, 0.50, 0.88)]                # what is in bin 1: bedding and castings
    for x0, x1 in ((0.03, 0.06), (0.94, 0.97)):                    # bin 1's insulated walls, up to its rim
        blocks.append((x0, x1, 0.02, 0.98, 0.50, 0.95))
    for y0, y1 in ((0.02, 0.05), (0.95, 0.98)):
        blocks.append((0.03, 0.97, y0, y1, 0.50, 0.95))
    for i in range(4):                                             # slats on its two long faces, with air gaps
        z0 = 0.52 + i * 0.11
        blocks += [(0.0, 0.04, 0.0, 1.0, z0, z0 + 0.085), (0.96, 1.0, 0.0, 1.0, z0, z0 + 0.085)]
    return _blocks("worm_box", blocks)


def worm_box_lid():
    """The lid in two parts, either side of the feed hatch, overhanging against rain. Its hinge is the y
    axis at x = 0, z = 0: it lies from x = -1.06 to 0, so turning it about y opens it."""
    return _blocks("worm_box_lid", [(-1.06, -0.65, -0.02, 1.02, 0.0, 0.05), (-0.41, 0.0, -0.02, 1.02, 0.0, 0.05),
                                    (-0.25, -0.15, 0.40, 0.60, 0.05, 0.08)])       # and its handle


def worm_box_hatch():
    """The feed hatch in the lid: a flap across the box that the robot's tool opens. Hinged like the
    lid, about the y axis at x = 0, z = 0; it lies from x = -0.24 to 0."""
    return _blocks("worm_box_hatch", [(-0.24, 0.0, 0.0, 1.0, 0.005, 0.045)])


def _dot(bm, x, y, z, axis, r=0.012):
    turn = {"z": Matrix.Identity(4), "y": Matrix.Rotation(math.pi / 2, 4, "X")}[axis]
    bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=r, radius2=r, depth=0.012, matrix=Matrix.Translation((x, y, z)) @ turn)


def worm_box_lid_holes():
    """The vent holes drilled in the lid, as dark dots to lay over it. Same coordinates as worm_box_lid,
    so they turn with it."""
    bm = bmesh.new()
    for x in (-0.98, -0.86, -0.74, -0.34, -0.22, -0.10):           # either side of the hatch
        for j in range(8):
            _dot(bm, x, 0.08 + j * 0.12, 0.051, "z")
    return new_object("worm_box_lid_holes", bm)


def worm_box_holes():
    """What is drilled into the bins, as dark dots to lay over them: air holes high on bin 1's two short
    sides, and the tap low on the sump. Same unit cube as worm_box."""
    bm = bmesh.new()
    for y in (0.0, 1.0):                                           # bin 1: air holes under its rim
        for i in range(7):
            _dot(bm, 0.14 + i * 0.12, y, 0.90, "y")
    bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=0.02, radius2=0.02, depth=0.10,      # the tap
                          matrix=Matrix.Translation((0.5, -0.04, 0.06)) @ Matrix.Rotation(math.pi / 2, 4, "X"))
    return new_object("worm_box_holes", bm)


def _tube(bm, path, radius, sides=8):
    """A closed tube through the points of `path`, radius(t) for t from 0 to 1 along it."""
    rings = []
    for i, p in enumerate(path):
        t = i / (len(path) - 1)
        ahead = Vector(path[min(i + 1, len(path) - 1)]) - Vector(path[max(i - 1, 0)])
        ahead.normalize()
        side = ahead.cross(Vector((0.0, 0.0, 1.0)))
        if side.length < 1e-6:
            side = Vector((1.0, 0.0, 0.0))
        side.normalize()
        up = side.cross(ahead)
        r = radius(t)
        rings.append([bm.verts.new(Vector(p) + r * (math.cos(a) * side + math.sin(a) * up))
                      for a in (2 * math.pi * k / sides for k in range(sides))])
    for one, two in zip(rings, rings[1:]):
        for k in range(sides):
            bm.faces.new((one[k], one[(k + 1) % sides], two[(k + 1) % sides], two[k]))
    for ring, flip in ((rings[0], True), (rings[-1], False)):
        bm.faces.new(ring[::-1] if flip else ring)


def worm():
    """A red wiggler lying on the bedding: 1 m long along x (the world scales it to about 8 cm), a gentle S,
    ringed segments, the swollen band (clitellum) a third of the way back, resting on z = 0."""
    bm = bmesh.new()
    n = 26
    path = [((t := i / (n - 1)) - 0.5, 0.06 * math.sin(2.2 * math.pi * t), 0.0) for i in range(n)]

    def radius(t):
        body = 0.028 * (0.35 + 0.65 * math.sin(math.pi * min(1.0, 0.08 + t * 0.92)) ** 0.5)   # blunt head, thin tail
        rings = 1.0 + 0.06 * math.cos(2 * math.pi * t * 12)                                     # the segments
        band = 1.25 if 0.25 < t < 0.36 else 1.0                                                  # the clitellum
        return body * rings * band

    _tube(bm, path, radius, sides=6)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0.0, 0.0, 0.028))
    return new_object("worm", bm)


def cocoon():
    """A worm cocoon: a small lemon, 1 m long along x (the world scales it to about 3 mm), resting on z = 0."""
    bm = bmesh.new()
    n = 8
    path = [((i / (n - 1)) - 0.5, 0.0, 0.0) for i in range(n)]
    _tube(bm, path, lambda t: 0.36 * math.sin(math.pi * t) ** 0.8 + 0.01, sides=8)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0.0, 0.0, 0.36))
    return new_object("cocoon", bm)


BUILDERS = (plant_grown, plant_seedling, bed_soil, gantry_bridge, tree_trunk, tree_canopy, worm_box, worm_box_lid, worm_box_hatch, worm_box_holes, worm_box_lid_holes, worm, cocoon)


def export(obj, out: Path):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    # Blender and Webots are both z-up: write the coordinates as they are.
    bpy.ops.wm.obj_export(filepath=str(out / f"{obj.name}.obj"), export_selected_objects=True, export_materials=False,
                          export_uv=False, export_normals=True, export_triangulated_mesh=True,
                          forward_axis="Y", up_axis="Z")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--out", default="permtek5_twin_edge/shapes")
    ap.add_argument("--force", action="store_true", help="build even when nothing changed")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(Path(__file__).read_bytes() + bpy.app.version_string.encode()).hexdigest()[:16]
    stamp = out / ".built"
    names = [b.__name__ for b in BUILDERS]
    if not args.force and stamp.exists() and stamp.read_text() == digest and all((out / f"{n}.obj").exists() for n in names):
        print(f"  meshes are up to date in {out}")
        return 0
    bpy.ops.wm.read_factory_settings(use_empty=True)
    library = {}
    for build in BUILDERS:
        obj = build()
        export(obj, out)
        library[obj.name] = {"file": f"{obj.name}.obj", "what": " ".join((build.__doc__ or "").split()),
                             "vertices": len(obj.data.vertices), "builtBy": f"assets/build.py:{build.__name__}"}
        print(f"  {obj.name}.obj  {len(obj.data.vertices)} vertices")
    (out / "library.json").write_text(json.dumps({"about": "The twin's shape library. Made once with Blender (assets/build.py); "
                                                  "Webots loads these files, Blender is not needed to run the twin.",
                                                  "shapes": library}, indent=2) + "\n", encoding="utf-8")
    stamp.write_text(digest)
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
