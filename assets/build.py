# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
build.py — the twin's meshes, made with Blender as a Python module (bpy).

    .venv-bpy/bin/python assets/build.py [--out mydocs/twin/assets] [--force]

Runs ONLY in the bpy environment (scripts/setup-bpy.sh): bpy needs its own
Python version, and neither the app nor the simulation imports it. It writes
plain OBJ files (no materials, nothing downloaded) that world.py uses for
looks when they exist; collisions and motion never depend on them.

Every mesh is modelled at a unit size and scaled by the world:

    plant_seedling.obj, plant_grown.obj   0.2 m across, standing on z = 0
    bed_soil.obj                          a 1 x 1 m surface with small bumps, z about 0
    gantry_bridge.obj                     an aluminium profile 1 m long along y, centred
    tree_trunk.obj, tree_canopy.obj       a tree 1 m tall, standing on z = 0
    worm_box.obj, worm_box_lid.obj        a slatted box in a unit cube, standing on z = 0; its lid, hinged at its origin

The same script and parameters give the same files; a hash of this file in
the output folder lets a second run skip the work.
"""
import argparse
import hashlib
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
    rng = random.Random(SEED)
    bm = bmesh.new()
    golden = math.radians(137.5)
    for i in range(13):
        k = i / 12                                              # inner (0) to outer (1)
        leaf(bm, length=0.035 + 0.065 * k, width=0.018 + 0.022 * k, angle=i * golden + rng.uniform(-0.1, 0.1),
             tilt=math.radians(62 - 50 * k), curl=0.35 + 0.3 * k)
    return new_object("plant_grown", bm)


def plant_seedling():
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
    nx, ny = 120, 40
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
    """A slatted box, open at the top, in a unit cube: x and y 0..1, standing on z = 0. Its lid is worm_box_lid."""
    blocks = [(0.04, 0.96, 0.02, 0.98, 0.0, 0.80)]                 # what is in it: bedding and castings
    for x0, x1 in ((0.03, 0.06), (0.94, 0.97)):                    # the insulated walls, up to the rim
        blocks.append((x0, x1, 0.02, 0.98, 0.0, 0.88))
    for y0, y1 in ((0.02, 0.05), (0.95, 0.98)):
        blocks.append((0.03, 0.97, y0, y1, 0.0, 0.88))
    for i in range(5):                                             # slats on the two long faces, with air gaps
        z0 = 0.03 + i * 0.17
        blocks += [(0.0, 0.04, 0.0, 1.0, z0, z0 + 0.13), (0.96, 1.0, 0.0, 1.0, z0, z0 + 0.13)]
    return _blocks("worm_box", blocks)


def worm_box_lid():
    """The box's lid, overhanging against rain, with a handle. Its hinge is the y axis at x = 0, z = 0:
    the lid lies from x = -1.06 to 0, so turning it about y opens it."""
    return _blocks("worm_box_lid", [(-1.06, 0.0, -0.02, 1.02, 0.0, 0.08), (-0.61, -0.45, 0.35, 0.65, 0.08, 0.12)])


BUILDERS = (plant_grown, plant_seedling, bed_soil, gantry_bridge, tree_trunk, tree_canopy, worm_box, worm_box_lid)


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
    ap.add_argument("--out", default="mydocs/twin/assets")
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
    for build in BUILDERS:
        obj = build()
        export(obj, out)
        print(f"  {obj.name}.obj  {len(obj.data.vertices)} vertices")
    stamp.write_text(digest)
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
