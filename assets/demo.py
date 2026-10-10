# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
demo.py — a rendered film of the prepared roof bed, made with Blender (bpy).

    .venv-bpy/bin/python assets/demo.py [--bed mydocs/twin/bed.json] [--out mydocs/twin/demo]
                                        [--frames 72] [--samples 16] [--still-only]

Runs ONLY in the bpy environment (scripts/setup-bpy.sh). It reads bed.json,
the same file the simulation runs on, so the film shows that bed: its soil,
every planted cell in its crop's colour, the gantry travelling along it, and
the garden's trees behind the roof. The camera circles the bed once.

Writes poster.png (one still) and film.mp4 (the turntable; needs ffmpeg on
the PATH, otherwise the frames stay in frames/). Rendered with Cycles on the
CPU: no graphics card is needed, and a run takes minutes, not seconds. It
prints "frame i/N" so a caller can show progress.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bpy  # first: it puts mathutils on the path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402  the same shapes the simulation uses

FPS = 24


def material(name, rgb, rough=0.9, metal=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    return m


def box(name, centre, size, mat, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=centre)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    o.data.materials.append(mat)
    if parent is not None:
        o.parent = parent
    return o


def place(obj, mat, location, scale=(1, 1, 1), parent=None):
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    obj.location = location
    obj.scale = scale
    if parent is not None:
        obj.parent = parent
    return obj


def copy_of(proto, name):
    o = proto.copy()
    o.data = proto.data.copy()
    o.name = name
    bpy.context.collection.objects.link(o)
    return o


def scene_from(cfg, frames):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bed, w, g, garden = cfg["bed"], cfg["world"], cfg.get("gantry", {}), cfg.get("garden", {})
    L, W, cell = bed["length_cm"] / 100.0, bed["width_cm"] / 100.0, w["cellM"]
    soil_z = w["soilZ"]
    rail_z, rail_out, frame_t = g.get("railZ", 0.95), g.get("railOut", 0.10), g.get("frameT", 0.03)
    margin = garden.get("roofMargin", 1.5)

    m_soil = material("soil", (0.10, 0.07, 0.05), 1.0)
    m_wood = material("wood", (0.36, 0.24, 0.14), 0.8)
    m_roof = material("roof", (0.42, 0.42, 0.44), 0.9)
    m_alu = material("aluminium", (0.78, 0.80, 0.82), 0.35, 0.9)
    m_dark = material("carriage", (0.05, 0.05, 0.06), 0.5, 0.2)
    m_tool = material("tool", (0.79, 0.58, 0.31), 0.4, 0.3)
    m_grass = material("grass", (0.16, 0.30, 0.12), 1.0)
    m_bark = material("bark", (0.20, 0.14, 0.09), 1.0)

    # the roof, the bed's frame and its soil
    box("roof", (L / 2, W / 2, -0.05), (L + 2 * margin, W + 2 * margin, 0.10), m_roof)
    box("substrate", (L / 2, W / 2, soil_z / 2 - 0.01), (L, W, soil_z - 0.02), m_soil)
    place(build.bed_soil(), m_soil, (0, 0, soil_z), (L, W, 1))
    h = soil_z + 0.03
    for name, c, s in (("south", (L / 2, -frame_t / 2, h / 2), (L + 2 * frame_t, frame_t, h)),
                       ("north", (L / 2, W + frame_t / 2, h / 2), (L + 2 * frame_t, frame_t, h)),
                       ("west", (-frame_t / 2, W / 2, h / 2), (frame_t, W, h)),
                       ("east", (L + frame_t / 2, W / 2, h / 2), (frame_t, W, h))):
        box(f"frame {name}", c, s, m_wood)

    # every planted cell, in its crop's colour
    grown, seedling = build.plant_grown(), build.plant_seedling()
    crop_mats = {}
    for c in cfg["cells"]:
        if c["status"] not in ("CellSown", "CellGrowing"):
            continue
        mat = crop_mats.setdefault(c["crop"], material(c["crop"], [v * 0.8 for v in c.get("rgb", (0.3, 0.6, 0.3))], 0.7))
        big = c["status"] == "CellGrowing"
        spacing = (c.get("job") or {}).get("spacingMm", cell * 1000) / 1000.0
        k = (max(0.02, spacing * 0.38) if big else 0.012) / 0.1
        o = copy_of(grown if big else seedling, f'cell {c["row"]} {c["col"]}')
        place(o, mat, ((c["col"] + 0.5) * cell, (c["row"] + 0.5) * cell, soil_z), (k, k, k))
        o.rotation_euler.z = (c["row"] * 7 + c["col"] * 13) % 6          # no two plants turned alike
    for proto in (grown, seedling):
        bpy.data.objects.remove(proto)

    # the gantry: rails and posts stand still, the bridge travels along x
    x0, x1 = w["x"]
    for i, y in enumerate((-rail_out, W + rail_out)):
        box(f"rail {i}", ((x0 + x1) / 2, y, rail_z), (x1 - x0 + 0.2, 0.04, 0.04), m_alu)
        for x in (x0 - 0.08, x1 + 0.08):
            box("post", (x, y, rail_z / 2), (0.04, 0.04, rail_z), m_alu)
    rig = bpy.data.objects.new("bridge rig", None)
    bpy.context.collection.objects.link(rig)
    place(build.gantry_bridge(), m_alu, (0, W / 2, rail_z + 0.04), (1, W + 2 * rail_out, 1), rig)
    carriage_y = W * 0.45
    box("carriage", (0, carriage_y, rail_z + 0.04), (0.10, 0.10, 0.08), m_dark, rig)
    tip = w["tipHome"]
    box("z column", (0, carriage_y, (tip + rail_z) / 2 + 0.02), (0.03, 0.03, rail_z - tip + 0.04), m_alu, rig)
    box("tool", (0, carriage_y, tip + 0.015), (0.024, 0.024, 0.03), m_tool, rig)
    for frame, x in ((1, 0.25), (frames // 2, L - 0.25), (frames, 0.25)):        # along the bed and back
        rig.location = (x, 0, 0)
        rig.keyframe_insert("location", frame=frame)

    # the garden behind and below the roof
    if garden.get("trees"):
        gz, gy0, th = garden["z"], garden["y0"], garden["treeHeight"]
        box("garden", (L / 2, gy0 + 8, gz - 0.05), (60, 40, 0.10), m_grass)
        trunk, canopy = build.tree_trunk(), build.tree_canopy()
        for t in garden["trees"]:
            leaf = material(f'leaves {t["name"]}', [0.8 * a + 0.2 * b for a, b in zip((0.10, 0.26, 0.08), t["rgb"])], 1.0)
            place(copy_of(trunk, f'trunk {t["name"]}'), m_bark, (t["x"], gy0 + t["y"], gz), (th, th, th))
            place(copy_of(canopy, f'crown {t["name"]}'), leaf, (t["x"], gy0 + t["y"], gz), (th, th, th))
        for proto in (trunk, canopy):
            bpy.data.objects.remove(proto)
    box("street", (L / 2, W / 2, garden.get("z", -2.6) - 0.06), (80, 80, 0.10), m_grass)

    # light: a low sun and a pale sky
    world = bpy.data.worlds.new("sky")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.55, 0.72, 0.95, 1.0)
    bg.inputs["Strength"].default_value = 0.9
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.2
    sun.data.angle = math.radians(3)
    sun.rotation_euler = (math.radians(52), 0, math.radians(-38))
    bpy.context.collection.objects.link(sun)

    # the camera circles the bed once, always looking at it
    target = bpy.data.objects.new("look at", None)
    target.location = (L / 2, W / 2, soil_z + 0.12)
    bpy.context.collection.objects.link(target)
    orbit = bpy.data.objects.new("orbit", None)
    orbit.location = target.location
    bpy.context.collection.objects.link(orbit)
    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    cam.data.lens = 32
    cam.parent = orbit
    cam.location = (0, -(L * 0.62 + 1.9), 1.35)
    look = cam.constraints.new("TRACK_TO")
    look.target = target
    look.track_axis, look.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    bpy.context.collection.objects.link(cam)
    scene.camera = cam
    for frame, angle in ((1, math.radians(-28)), (frames + 1, math.radians(-28) + 2 * math.pi)):
        orbit.rotation_euler = (0, 0, angle)
        orbit.keyframe_insert("rotation_euler", frame=frame)
    for obj in (orbit, rig):                                  # steady speed, no easing
        action = obj.animation_data.action if obj.animation_data else None
        for fc in _fcurves(action):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR" if obj is orbit else "SINE"
    scene.frame_start, scene.frame_end = 1, frames
    return scene


def _fcurves(action):
    """An action's curves, in Blender 4 (action.fcurves) and Blender 5 (layers and channel bags)."""
    if action is None:
        return []
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    out = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                out.extend(bag.fcurves)
    return out


def setup_render(scene, width, height, samples):
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.max_bounces = 4
    try:
        scene.cycles.use_denoising = True
    except Exception:                       # a build without a denoiser: more samples would be the answer
        pass
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.fps = FPS
    scene.view_settings.view_transform = "Standard"


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--bed", default="mydocs/twin/bed.json")
    ap.add_argument("--out", default="mydocs/twin/demo")
    ap.add_argument("--frames", type=int, default=72)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--still-only", action="store_true")
    args = ap.parse_args()
    cfg = json.loads(Path(args.bed).read_text(encoding="utf-8"))
    out = Path(args.out)
    frames_dir = out / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    scene = scene_from(cfg, args.frames)
    setup_render(scene, args.width, args.height, args.samples)
    started = time.time()

    scene.frame_set(1)
    scene.cycles.samples = max(args.samples, 64)
    scene.render.filepath = str(out / "poster.png")
    bpy.ops.render.render(write_still=True)
    print(f"poster {time.time() - started:.1f}s", flush=True)
    if args.still_only:
        return 0

    scene.cycles.samples = args.samples
    for f in range(1, args.frames + 1):
        scene.frame_set(f)
        scene.render.filepath = str(frames_dir / f"f_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
        print(f"frame {f}/{args.frames}", flush=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("no ffmpeg on the PATH: the frames stay in " + str(frames_dir), flush=True)
        return 0
    film = out / "film.mp4"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames_dir / "f_%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(film) + ".tmp.mp4"],
                   check=True, stdin=subprocess.DEVNULL)
    Path(str(film) + ".tmp.mp4").replace(film)
    shutil.rmtree(frames_dir)
    print(f"film {film} {time.time() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
