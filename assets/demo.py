# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
demo.py — films of the prepared guild, rendered with Blender (bpy).

    .venv-bpy/bin/python assets/demo.py [--bed mydocs/twin/bed.json] [--out mydocs/twin/demo]
                                        [--shot bed|box|flight] [--quality draft|good|best] [--frames N]
                                        [--worms 40 | --adults N] [--still-only]

Runs ONLY in the bpy environment (scripts/setup-bpy.sh). It reads bed.json,
the same file the simulation runs on, so a film shows that guild: its soil and
every planted cell in its crop's colour, the gantry, the garden's trees behind
the roof, and the worm box at the bed's far end with its lid open. Inside the
box is what wormlife.py makes: moist castings, newspaper and cardboard, the
last feeding's scraps, cocoons and crawling red wigglers (--worms of them).

The shots:

    bed     the camera circles the bed once, rising and sinking, while the
            gantry travels along it and back (20 s)
    box     over the open worm box, closing in on the worms at the food, then
            drifting along them; shallow focus (15 s)
    flight  from the whole bed, along it, down into the open worm box, and a
            while with the worms (18 s)

Writes poster-<shot>.png (one still) and film-<shot>.mp4 (needs ffmpeg on the
PATH, otherwise the frames stay in frames-<shot>/). Rendered with Cycles on the
CPU: no graphics card is needed, and a film takes a long while: on a 4-core
laptop under an hour in draft, one to three hours in good quality. Draft and
good render every second frame and let ffmpeg make the ones between (the
worms move slowly; it halves the time). It prints "frame i/N" so a caller can
show progress.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import bpy  # first: it puts mathutils on the path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402  the same shapes the simulation uses
import wormlife  # noqa: E402  the inside of the worm box

FPS = 24
SHOTS = ("bed", "box", "flight")
FRAMES = {"bed": 480, "box": 360, "flight": 432}          # 20, 15 and 18 seconds
# draft, good, best: size, samples, and every how many frames one is rendered (ffmpeg makes the ones between)
QUALITY = {"draft": (640, 360, 8, 2), "good": (960, 540, 12, 2), "best": (1280, 720, 24, 1)}

# ---------------------------------------------------------------- materials --


def material(name, rgb, rough=0.9, metal=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    return m


def textured(name, dark, light, scale=8.0, rough=0.9, bump=0.3, metal=0.0, wave=None):
    """A colour that varies between `dark` and `light` by noise (or wood grain with `wave`), with a matching bump."""
    m, nt, bsdf = wormlife._nodes(name)
    co = nt.nodes.new("ShaderNodeTexCoord").outputs["Object"]
    if wave:                                  # wood: noise stretched along the board's length into grain
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (1.0, wave, wave)
        nt.links.new(co, mapping.inputs["Vector"])
        tex = wormlife._noise(nt, scale, 10, 0.55, mapping.outputs["Vector"])
        fac = tex.outputs["Fac"]
    else:
        fac = wormlife._noise(nt, scale, 8, 0.6, co).outputs["Fac"]
    col = wormlife._ramp(nt, [(0.3, dark), (0.7, light)], fac)
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    b = wormlife._bump(nt, fac, bump, 0.003)
    nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
    wormlife._set(bsdf, Roughness=rough, Metallic=metal)
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


def empty(name, location):
    o = bpy.data.objects.new(name, None)
    o.location = location
    bpy.context.collection.objects.link(o)
    return o


# -------------------------------------------------------------------- scene --


def scene_from(cfg, frames, shot, worms):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bed, w, g, garden = cfg["bed"], cfg["world"], cfg.get("gantry", {}), cfg.get("garden", {})
    L, W, cell = bed["length_cm"] / 100.0, bed["width_cm"] / 100.0, w["cellM"]
    soil_z = w["soilZ"]
    rail_z, rail_out, frame_t = g.get("railZ", 0.95), g.get("railOut", 0.10), g.get("frameT", 0.03)
    margin = garden.get("roofMargin", 1.5)

    m_soil = textured("soil", (0.018, 0.012, 0.008), (0.05, 0.034, 0.022), 30.0, 0.95, 0.5)
    m_wood = textured("wood", (0.20, 0.12, 0.06), (0.38, 0.25, 0.14), 6.0, 0.7, 0.12, wave=18.0)
    m_roof = textured("roof", (0.10, 0.10, 0.105), (0.19, 0.19, 0.20), 60.0, 0.9, 0.4)     # a gravel-topped membrane
    m_alu = textured("aluminium", (0.62, 0.64, 0.66), (0.80, 0.82, 0.84), 200.0, 0.3, 0.05, metal=0.95)
    m_dark = material("carriage", (0.05, 0.05, 0.06), 0.5, 0.2)
    m_tool = material("tool", (0.79, 0.58, 0.31), 0.4, 0.3)
    m_grass = textured("grass", (0.05, 0.12, 0.03), (0.16, 0.28, 0.08), 40.0, 1.0, 0.4)
    m_bark = textured("bark", (0.10, 0.07, 0.05), (0.24, 0.17, 0.11), 30.0, 1.0, 0.6)

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
        rgb = [v * 0.8 for v in c.get("rgb", (0.3, 0.6, 0.3))]
        mat = crop_mats.setdefault(c["crop"], textured(c["crop"], [v * 0.7 for v in rgb], rgb, 60.0, 0.55, 0.2))
        wormlife._set(mat.node_tree.nodes["Principled BSDF"], Subsurface_Weight=0.25, Subsurface_Radius=(0.01, 0.01, 0.005))
        big = c["status"] == "CellGrowing"
        spacing = (c.get("job") or {}).get("spacingMm", cell * 1000) / 1000.0
        k = (max(0.02, spacing * 0.38) if big else 0.012) / 0.1
        o = copy_of(grown if big else seedling, f'cell {c["row"]} {c["col"]}')
        place(o, mat, ((c["col"] + 0.5) * cell, (c["row"] + 0.5) * cell, soil_z), (k, k, k))
        o.rotation_euler.z = (c["row"] * 7 + c["col"] * 13) % 6          # no two plants turned alike
    for proto in (grown, seedling):
        bpy.data.objects.remove(proto)

    # the worm box at the far end, its lid open, and what lives in it
    feed = worm_box(cfg, L, W, soil_z, m_wood, worms, frames)

    # the gantry: rails and posts stand still, the bridge travels along x
    x0, x1 = w["x"]
    for i, y in enumerate((-rail_out, W + rail_out)):
        box(f"rail {i}", ((x0 + x1) / 2, y, rail_z), (x1 - x0 + 0.2, 0.04, 0.04), m_alu)
        for x in (x0 - 0.08, x1 + 0.08):
            box("post", (x, y, rail_z / 2), (0.04, 0.04, rail_z), m_alu)
    rig = empty("bridge rig", (0, 0, 0))
    place(build.gantry_bridge(), m_alu, (0, W / 2, rail_z + 0.04), (1, W + 2 * rail_out, 1), rig)
    carriage_y = W * 0.45
    box("carriage", (0, carriage_y, rail_z + 0.04), (0.10, 0.10, 0.08), m_dark, rig)
    tip = w["tipHome"]
    box("z column", (0, carriage_y, (tip + rail_z) / 2 + 0.02), (0.03, 0.03, rail_z - tip + 0.04), m_alu, rig)
    box("tool", (0, carriage_y, tip + 0.015), (0.024, 0.024, 0.03), m_tool, rig)
    travel = ((1, 0.25), (frames // 2, L - 0.25 - (L - feed["x0"])), (frames, 0.25)) if shot == "bed" else ((1, 0.6), (frames, 1.1))
    for frame, x in travel:                                       # along the bed and back; out of the way for the box
        rig.location = (x, 0, 0)
        rig.keyframe_insert("location", frame=frame)

    # the garden behind and below the roof
    if garden.get("trees"):
        gz, gy0, th = garden["z"], garden["y0"], garden["treeHeight"]
        box("garden", (L / 2, gy0 + 8, gz - 0.05), (60, 40, 0.10), m_grass)
        trunk, canopy = build.tree_trunk(), build.tree_canopy()
        for t in garden["trees"]:
            leaf = textured(f'leaves {t["name"]}', [0.6 * (0.8 * a + 0.2 * b) for a, b in zip((0.10, 0.26, 0.08), t["rgb"])],
                            [0.8 * a + 0.2 * b for a, b in zip((0.10, 0.26, 0.08), t["rgb"])], 12.0, 0.8, 0.5)
            wormlife._set(leaf.node_tree.nodes["Principled BSDF"], Subsurface_Weight=0.2, Subsurface_Radius=(0.05, 0.08, 0.03))
            place(copy_of(trunk, f'trunk {t["name"]}'), m_bark, (t["x"], gy0 + t["y"], gz), (th, th, th))
            place(copy_of(canopy, f'crown {t["name"]}'), leaf, (t["x"], gy0 + t["y"], gz), (th, th, th))
        for proto in (trunk, canopy):
            bpy.data.objects.remove(proto)
    box("street", (L / 2, W / 2, garden.get("z", -2.6) - 0.06), (80, 80, 0.10), m_grass)

    sky(scene)
    cameras(scene, shot, frames, L, W, soil_z, feed, rig)
    scene.frame_start, scene.frame_end = 1, frames
    return scene


def worm_box(cfg, L, W, soil_z, m_wood, worms, frames):
    """The two-bin worm box as the simulation has it (world.py), lid open; inside, wormlife's bedding and worms.
    Returns where the last feeding lies and the inner floor: what the box and flight shots look at."""
    w, wb = cfg["world"], cfg.get("wormBin") or {}
    x0 = (wb.get("x") or [L - 0.4, L])[0]
    bin_m = L - x0
    box_h = w["binSurfaceZ"] / 0.88 if "binSurfaceZ" in w else soil_z + 0.30     # world.py: SOIL_Z + BIN_ABOVE
    scale = (bin_m, W, box_h)
    m_lid = textured("lid", (0.50, 0.48, 0.42), (0.64, 0.62, 0.55), 6.0, 0.8, 0.1)
    m_hole = material("hole", (0.02, 0.015, 0.01), 1.0)
    place(build.worm_box(), m_wood, (x0, 0, 0), scale)
    place(build.worm_box_holes(), m_hole, (x0, 0, 0), scale)
    hinge = empty("lid hinge", (x0 + 1.03 * bin_m, 0, 0.95 * box_h))
    hinge.rotation_euler.y = w.get("lidOpenRad", 1.2)
    place(build.worm_box_lid(), m_lid, (0, 0, 0), scale, hinge)
    place(build.worm_box_lid_holes(), m_hole, (0, 0, 0), scale, hinge)
    place(build.worm_box_hatch(), m_wood, (-0.41 * bin_m, 0, 0), scale, hinge)
    surface = w.get("binSurfaceZ", 0.88 * box_h)
    fx0, fx1, fy0, fy1 = x0 + 0.06 * bin_m, x0 + 0.94 * bin_m, 0.05 * W, 0.95 * W
    bed = wormlife.Bedding(fx0, fx1, fy0, fy1, surface + 0.005, seed=5, res=0.004, lump=0.005)
    food = (wb.get("dropX", (fx0 + fx1) / 2), W * 0.55)
    wormlife.add_crumbs(bed)
    wormlife.add_scraps(bed, near=food, spread=0.05, papers=int(400 * (fx1 - fx0) * (fy1 - fy0)) + 20,
                        cards=int(80 * (fx1 - fx0) * (fy1 - fy0)) + 6)
    wormlife.add_cocoons(bed, 18, near=food, spread=0.12)
    if worms:
        at_food = max(1, int(worms * 0.7))
        wormlife.add_worms(bed, at_food, frames, near=food, spread=0.06, seed=3)
        wormlife.add_worms(bed, worms - at_food, frames, seed=13)
    return {"x0": x0, "food": (food[0], food[1], bed.height(*food)), "floor": (fx0, fx1, fy0, fy1), "boxTop": box_h}


def sky(scene):
    """A clear sky from the sun's position (Nishita) and the sun itself, soft-edged."""
    world = bpy.data.worlds.new("sky")
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    try:
        s = nt.nodes.new("ShaderNodeTexSky")
        for kind in ("NISHITA", "MULTIPLE_SCATTERING", "SINGLE_SCATTERING"):
            try:
                s.sky_type = kind
                break
            except TypeError:
                continue
        s.sun_elevation, s.sun_rotation = math.radians(38), math.radians(-40)
        nt.links.new(s.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.22
    except Exception:                        # no sky texture in this build: a plain pale sky
        bg.inputs["Color"].default_value = (0.55, 0.72, 0.95, 1.0)
        bg.inputs["Strength"].default_value = 0.9
    scene.world = world
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 1.8
    sun.data.angle = math.radians(2)
    sun.rotation_euler = (math.radians(52), 0, math.radians(-40))
    bpy.context.collection.objects.link(sun)


def cameras(scene, shot, frames, L, W, soil_z, feed, rig):
    """The shot's camera: where it is and what it looks at, frame by frame, focused on that."""
    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    bpy.context.collection.objects.link(cam)
    scene.camera = cam
    target = empty("look at", (L / 2, W / 2, soil_z + 0.12))
    look = cam.constraints.new("TRACK_TO")
    look.target = target
    look.track_axis, look.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    cam.data.dof.use_dof = True
    cam.data.dof.focus_object = target
    fx, fy, fz = feed["food"]
    if shot == "bed":
        cam.data.lens = 32
        cam.data.dof.aperture_fstop = 11
        orbit = empty("orbit", target.location)
        cam.parent = orbit
        for frame, angle in ((1, math.radians(-28)), (frames + 1, math.radians(-28) + 2 * math.pi)):
            orbit.rotation_euler = (0, 0, angle)
            orbit.keyframe_insert("rotation_euler", frame=frame)
        _linear(orbit)
        # high at the start, low over the plants halfway round, up again: the bed from above and from its side
        for frame, z, back in ((1, 1.6, 1.9), (frames // 2, 0.85, 1.5), (frames, 1.45, 1.9)):
            cam.location = (0, -(L * 0.62 + back), z)
            cam.keyframe_insert("location", frame=frame)
        _ease(rig)
        return
    # close over the box: a slow push towards the food, the camera low over the box's open side
    near = [((fx - 0.13, fy - 0.26, fz + 0.20), (fx, fy, fz)), ((fx - 0.07, fy - 0.15, fz + 0.12), (fx + 0.005, fy + 0.01, fz))]
    # and then along them: the camera drifts sideways over the worms at the food
    drift = ((fx + 0.03, fy - 0.15, fz + 0.11), (fx + 0.04, fy + 0.02, fz))
    at = lambda share: max(1, int(frames * share))
    if shot == "box":                         # from over the box's rim, closing in, then along the worms
        keys = [(1, (fx - 0.20, fy - 0.38, fz + 0.32), (fx, fy, fz)),
                (at(0.45),) + near[0], (at(0.75),) + near[1], (frames,) + drift]
        lenses = ((1, 40, 5.6), (at(0.45), 50, 4.0), (frames, 50, 4.0))
    else:                                     # flight: from the whole bed, along it, over the box, down into it, a while there
        keys = [(1, (L * 0.15, -2.6, 1.9), (L / 2, W / 2, soil_z + 0.1)),
                (at(0.25), (L * 0.55, -1.6, 1.35), (feed["x0"], W / 2, soil_z + 0.2)),
                (at(0.45), (fx - 0.45, fy - 0.45, fz + 0.62), (fx, fy, fz)),           # inside the rails, over the box
                (at(0.65),) + near[0], (at(0.85),) + near[1], (frames,) + drift]
        lenses = ((1, 35, 8.0), (at(0.45), 40, 5.6), (at(0.65), 50, 4.0), (frames, 50, 4.0))
    for frame, where, aim in keys:
        cam.location, target.location = where, aim
        cam.keyframe_insert("location", frame=frame)
        target.keyframe_insert("location", frame=frame)
    for frame, lens, stop in lenses:          # the lens narrows as it comes close
        cam.data.lens, cam.data.dof.aperture_fstop = lens, stop
        cam.data.keyframe_insert("lens", frame=frame)
        cam.data.dof.keyframe_insert("aperture_fstop", frame=frame)


def _linear(obj):
    for fc in _fcurves(obj.animation_data.action if obj.animation_data else None):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"


def _ease(obj):
    for fc in _fcurves(obj.animation_data.action if obj.animation_data else None):
        for kp in fc.keyframe_points:
            kp.interpolation = "SINE"


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
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.03
    scene.cycles.max_bounces = 6
    scene.cycles.glossy_bounces, scene.cycles.transmission_bounces = 3, 2
    scene.cycles.caustics_reflective = scene.cycles.caustics_refractive = False
    try:
        scene.cycles.use_denoising = True
    except Exception:                       # a build without a denoiser: more samples would be the answer
        pass
    scene.render.use_persistent_data = True  # the scene is built once for all frames
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.fps = FPS
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - High Contrast"
        scene.view_settings.exposure = -1.1
    except TypeError:
        scene.view_settings.view_transform = "Filmic"


def visible_worms(adults):
    """How many worms to show for a population: about as many as the simulation's world shows, more for a crowd."""
    if not adults:
        return 0
    return max(4, min(80, int(16 * math.log10(adults + 1))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--bed", default="mydocs/twin/bed.json")
    ap.add_argument("--out", default="mydocs/twin/demo")
    ap.add_argument("--shot", choices=SHOTS, default="bed")
    ap.add_argument("--frames", type=int, default=None, help="default: 480 (bed), 360 (box) or 432 (flight)")
    ap.add_argument("--quality", choices=tuple(QUALITY), default="good")
    ap.add_argument("--worms", type=int, default=None, help="worms to show (default: from --adults, else 40)")
    ap.add_argument("--adults", type=int, default=None, help="the simulated adult worms, to pick how many show")
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--samples", type=int, default=None)
    ap.add_argument("--still-only", action="store_true")
    args = ap.parse_args()
    cfg = json.loads(Path(args.bed).read_text(encoding="utf-8"))
    qw, qh, qs, step = QUALITY[args.quality]
    args.width, args.height, args.samples = args.width or qw, args.height or qh, args.samples or qs
    args.frames = args.frames or FRAMES[args.shot]
    worms = args.worms if args.worms is not None else visible_worms(args.adults) if args.adults is not None else 40
    out = Path(args.out)
    frames_dir = out / f"frames-{args.shot}"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    started, parent = time.time(), os.getppid()
    scene = scene_from(cfg, args.frames, args.shot, worms)
    setup_render(scene, args.width, args.height, args.samples)
    print(f"scene {time.time() - started:.1f}s, {worms} worms", flush=True)

    scene.frame_set(max(1, args.frames * 2 // 3) if args.shot != "bed" else 1)
    scene.cycles.samples = args.samples * 2
    scene.render.filepath = str(out / f"poster-{args.shot}.png")
    bpy.ops.render.render(write_still=True)
    print(f"poster {time.time() - started:.1f}s", flush=True)
    if os.getppid() != parent:
        print("the caller is gone: stopped", flush=True)
        return 3
    if args.still_only:
        return 0

    scene.cycles.samples = args.samples
    todo = list(range(1, args.frames + 1, step))
    for i, f in enumerate(todo, 1):
        if os.getppid() != parent:            # whoever started us is gone (the edge restarted): stop, nobody follows us.
            print("the caller is gone: stopped", flush=True)   # The frames stay: a new render of this shot may own them now
            return 3
        scene.frame_set(f)
        scene.render.filepath = str(frames_dir / f"f_{i:04d}.png")
        bpy.ops.render.render(write_still=True)
        print(f"frame {i}/{len(todo)}", flush=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("no ffmpeg on the PATH: the frames stay in " + str(frames_dir), flush=True)
        return 0
    film = out / f"film-{args.shot}.mp4"
    between = ["-vf", f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir"] if step > 1 else []
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS / step), "-i", str(frames_dir / "f_%04d.png"), *between,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(film) + ".tmp.mp4"],
                   check=True, stdin=subprocess.DEVNULL)
    Path(str(film) + ".tmp.mp4").replace(film)
    shutil.rmtree(frames_dir)
    print(f"film {film} {time.time() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
