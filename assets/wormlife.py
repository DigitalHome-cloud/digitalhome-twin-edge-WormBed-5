# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
wormlife.py — the inside of the worm box for the films: bedding, food, cocoons and crawling worms (bpy).

Used by demo.py; runs only in the bpy environment. Everything is made here, from
procedural shapes and shader nodes (nothing downloaded), and is meant to look as
real as a CPU render allows at close range:

  bedding    the working bin's surface: dark, moist, crumbly castings, lumpy
  scraps     torn newspaper and cardboard (the "browns"), lettuce, carrot peel,
             banana skin, crushed eggshell and used coffee grounds
  cocoons    lemon-shaped, from straw yellow (new) to olive brown (near hatching)
  worms      Eisenia fetida: purple-red with the pale bands between segments that
             give it the name "tiger worm", a swollen orange-pink clitellum a
             third of the way back, wet and a little translucent. Each crawls
             along its own path (a Curve modifier on a ringed tube), the head
             feeling from side to side, and some dip into the bedding.

    surface = Bedding(x0, x1, y0, y1, z, seed)        # the box's inner floor area
    add_scraps(surface, ...); add_cocoons(surface, n); add_worms(surface, n, frames)

Sizes follow the literature: adults 6–10 cm long and about 4–5 mm thick,
cocoons about 3–4 mm. Worms crawl about 1–2 cm a minute at rest and faster when
disturbed; the films speed this up a little so something happens in a few seconds.
"""
import math
import random

import bpy

import bmesh
from mathutils import Matrix, Vector, noise

# ----------------------------------------------------------------- shading --


def _nodes(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    return m, nt, bsdf


def _set(bsdf, **inputs):
    """Principled BSDF inputs by name; one a Blender version lacks is skipped."""
    for key, value in inputs.items():
        sock = bsdf.inputs.get(key.replace("_", " "))
        if sock is not None:
            sock.default_value = value


def _ramp(nt, stops, fac):
    r = nt.nodes.new("ShaderNodeValToRGB")
    cr = r.color_ramp
    cr.elements[0].position, cr.elements[0].color = stops[0][0], (*stops[0][1], 1.0)
    cr.elements[1].position, cr.elements[1].color = stops[-1][0], (*stops[-1][1], 1.0)
    for pos, rgb in stops[1:-1]:
        e = cr.elements.new(pos)
        e.color = (*rgb, 1.0)
    nt.links.new(fac, r.inputs["Fac"])
    return r


def _noise(nt, scale, detail=6.0, rough=0.6, coord=None):
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    n.inputs["Roughness"].default_value = rough
    if coord is not None:
        nt.links.new(coord, n.inputs["Vector"])
    return n


def _bump(nt, height, strength, distance=0.002, normal=None):
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = strength
    if "Distance" in b.inputs:
        b.inputs["Distance"].default_value = distance
    nt.links.new(height, b.inputs["Height"])
    if normal is not None:
        nt.links.new(normal, b.inputs["Normal"])
    return b


def _object_coords(nt):
    return nt.nodes.new("ShaderNodeTexCoord").outputs["Object"]


def mat_castings():
    """Worm castings and moist bedding: near-black brown, crumbly, wet where it is lower."""
    m, nt, bsdf = _nodes("castings")
    co = nt.nodes.new("ShaderNodeTexCoord").outputs["Generated"]
    big, fine = _noise(nt, 18.0, 8, 0.65, co), _noise(nt, 160.0, 4, 0.7, co)
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.inputs["Scale"].default_value = 260.0
    nt.links.new(co, vor.inputs["Vector"])
    col = _ramp(nt, [(0.30, (0.010, 0.0065, 0.0045)), (0.55, (0.026, 0.017, 0.011)), (0.78, (0.06, 0.04, 0.025))], big.outputs["Fac"])
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    wet = _ramp(nt, [(0.40, (0.55, 0.55, 0.55)), (0.62, (0.95, 0.95, 0.95))], fine.outputs["Fac"])
    nt.links.new(wet.outputs["Color"], bsdf.inputs["Roughness"])
    crumbs = _bump(nt, vor.outputs["Distance"], 0.2, 0.002)
    lumps = _bump(nt, big.outputs["Fac"], 0.35, 0.006, crumbs.outputs["Normal"])
    nt.links.new(lumps.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, Specular_IOR_Level=0.3)
    return m


def mat_paper(rgb=(0.50, 0.48, 0.43), name="newspaper"):
    """Torn, damp newspaper: off-white with grey print lines, darker where it soaked."""
    m, nt, bsdf = _nodes(name)
    co = nt.nodes.new("ShaderNodeTexCoord").outputs["UV"]
    lines = nt.nodes.new("ShaderNodeTexWave")
    lines.inputs["Scale"].default_value = 60.0
    lines.inputs["Distortion"].default_value = 1.5
    nt.links.new(co, lines.inputs["Vector"])
    soak = _noise(nt, 4.0, 6, 0.6, co)
    base = nt.nodes.new("ShaderNodeMix")
    base.data_type = "RGBA"
    base.inputs["Factor"].default_value = 0.0
    nt.links.new(lines.outputs["Fac"], base.inputs["Factor"])
    base.inputs["A"].default_value = (*rgb, 1)
    base.inputs["B"].default_value = (rgb[0] * 0.55, rgb[1] * 0.55, rgb[2] * 0.55, 1)
    damp = nt.nodes.new("ShaderNodeMix")
    damp.data_type = "RGBA"
    damp.blend_type = "MULTIPLY"
    nt.links.new(soak.outputs["Fac"], damp.inputs["Factor"])
    nt.links.new(base.outputs["Result"], damp.inputs["A"])
    damp.inputs["B"].default_value = (0.45, 0.40, 0.33, 1)
    nt.links.new(damp.outputs["Result"], bsdf.inputs["Base Color"])
    _set(bsdf, Roughness=0.85, Subsurface_Weight=0.15, Subsurface_Radius=(0.002, 0.002, 0.002))
    return m


def mat_plain(name, rgb, rough=0.6, sss=0.0, coat=0.0, spots=None, scale=40.0):
    """A food scrap or eggshell: a colour with a little mottling, optionally waxy or wet."""
    m, nt, bsdf = _nodes(name)
    n = _noise(nt, scale, 5, 0.6, _object_coords(nt))
    dark = spots or tuple(c * 0.6 for c in rgb)
    col = _ramp(nt, [(0.35, rgb), (0.75, dark)], n.outputs["Fac"])
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    b = _bump(nt, n.outputs["Fac"], 0.25, 0.001)
    nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, Roughness=rough, Subsurface_Weight=sss, Subsurface_Radius=(0.004, 0.003, 0.002),
         Coat_Weight=coat, Coat_Roughness=0.1)
    return m


def mat_worm(young=False):
    """Eisenia fetida's skin, along the worm's length u (0 head .. 1 tail) from its UVs."""
    m, nt, bsdf = _nodes("worm young" if young else "worm")
    uv = nt.nodes.new("ShaderNodeTexCoord").outputs["UV"]
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv, sep.inputs["Vector"])
    u = sep.outputs["X"]

    def math_node(op, a, b=None, clamp=False):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = op
        n.use_clamp = clamp
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                nt.links.new(v, n.inputs[i])
        return n.outputs[0]

    # about 100 segments: a pale band in each groove between them
    phase = math_node("MULTIPLY", u, 2 * math.pi * 95)
    wave = math_node("SINE", phase)
    groove = math_node("POWER", math_node("MULTIPLY", math_node("ADD", wave, 1.0), 0.5), 18.0)
    # the clitellum, a third of the way back: a smooth band from 0.24 to 0.35
    band = math_node("MULTIPLY", math_node("GREATER_THAN", u, 0.24), math_node("LESS_THAN", u, 0.35))
    body = (0.13, 0.007, 0.016) if not young else (0.32, 0.05, 0.06)
    tiger = (0.42, 0.24, 0.06) if not young else (0.52, 0.30, 0.15)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(math_node("MULTIPLY", groove, 0.65), mix.inputs["Factor"])
    # the head end is darker and redder, the tail paler
    head = _ramp(nt, [(0.0, (body[0] * 0.8, body[1] * 0.7, body[2])), (0.5, body), (1.0, (body[0] * 1.25, body[1] * 1.6, body[2] * 1.3))], u)
    nt.links.new(head.outputs["Color"], mix.inputs["A"])
    mix.inputs["B"].default_value = (*tiger, 1)
    clit = nt.nodes.new("ShaderNodeMix")
    clit.data_type = "RGBA"
    nt.links.new(band, clit.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], clit.inputs["A"])
    clit.inputs["B"].default_value = (0.30, 0.075, 0.045, 1) if not young else (0.55, 0.22, 0.16, 1)
    mottle = _noise(nt, 120.0, 3, 0.5, _object_coords(nt))
    final = nt.nodes.new("ShaderNodeMix")
    final.data_type = "RGBA"
    final.blend_type = "OVERLAY"
    final.inputs["Factor"].default_value = 0.25
    nt.links.new(clit.outputs["Result"], final.inputs["A"])
    nt.links.new(mottle.outputs["Color"], final.inputs["B"])
    nt.links.new(final.outputs["Result"], bsdf.inputs["Base Color"])
    # the grooves are a little deeper than the skin between them
    b = _bump(nt, math_node("MULTIPLY", groove, -1.0), 0.45, 0.0006)
    nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
    _set(bsdf, Roughness=0.38, Subsurface_Weight=0.15, Subsurface_Radius=(0.004, 0.0008, 0.0005), Subsurface_Scale=0.6,
         Coat_Weight=0.55, Coat_Roughness=0.08, Specular_IOR_Level=0.45)
    return m


# ---------------------------------------------------------------- the floor --


class Bedding:
    """The surface of the working bin's contents, with its height at any point (for placing things on it)."""

    def __init__(self, x0, x1, y0, y1, z, seed=5, res=0.004, lump=0.006):
        self.x0, self.x1, self.y0, self.y1, self.z, self.lump = x0, x1, y0, y1, z, lump
        self.seed = seed
        self.rng = random.Random(seed)
        self.offset = Vector((seed * 3.1, seed * 1.7, 0.0))
        nx, ny = max(4, int((x1 - x0) / res)), max(4, int((y1 - y0) / res))
        bm = bmesh.new()
        verts = [[bm.verts.new((x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny, 0.0)) for j in range(ny + 1)] for i in range(nx + 1)]
        uv = bm.loops.layers.uv.new("UVMap")
        for i in range(nx):
            for j in range(ny):
                f = bm.faces.new((verts[i][j], verts[i + 1][j], verts[i + 1][j + 1], verts[i][j + 1]))
                for loop in f.loops:
                    loop[uv].uv = ((loop.vert.co.x - x0) / (x1 - x0), (loop.vert.co.y - y0) / (y1 - y0))
        for v in bm.verts:
            v.co.z = self.height(v.co.x, v.co.y) - z
        me = bpy.data.meshes.new("bedding")
        bm.to_mesh(me)
        bm.free()
        for p in me.polygons:
            p.use_smooth = True
        self.obj = bpy.data.objects.new("bedding", me)
        self.obj.location.z = z
        self.obj.data.materials.append(mat_castings())
        bpy.context.collection.objects.link(self.obj)

    def height(self, x, y):
        """The bedding's height at (x, y): lumps of a few centimetres and crumbs of a few millimetres."""
        p = Vector((x * 9.0, y * 9.0, 0.0)) + self.offset
        q = Vector((x * 70.0, y * 70.0, 0.0)) + self.offset
        rim = min(x - self.x0, self.x1 - x, y - self.y0, self.y1 - y)
        edge = 0.008 * max(0.0, 1.0 - rim / 0.03)           # it heaps up a little against the walls
        return self.z + self.lump * noise.noise(p) + 0.0015 * noise.noise(q) + edge

    def normal(self, x, y, d=0.002):
        dx = self.height(x + d, y) - self.height(x - d, y)
        dy = self.height(x, y + d) - self.height(x, y - d)
        return Vector((-dx, -dy, 2 * d)).normalized()

    def spot(self, around=None, spread=None):
        """A random point on the surface, or one near `around` (x, y)."""
        if around is None:
            return (self.rng.uniform(self.x0 + 0.01, self.x1 - 0.01), self.rng.uniform(self.y0 + 0.01, self.y1 - 0.01))
        x = min(self.x1 - 0.01, max(self.x0 + 0.01, self.rng.gauss(around[0], spread)))
        y = min(self.y1 - 0.01, max(self.y0 + 0.01, self.rng.gauss(around[1], spread)))
        return (x, y)

    def lay(self, obj, x, y, sink=0.0, turn=None, tilt=0.25):
        """Put `obj` on the surface at (x, y), following its slope, sunk in by `sink` metres."""
        n = self.normal(x, y)
        obj.location = (x, y, self.height(x, y) - sink)
        rot = n.to_track_quat("Z", "Y").to_euler()
        rot.z = self.rng.uniform(0, 2 * math.pi) if turn is None else turn
        rot.x += self.rng.uniform(-tilt, tilt)
        rot.y += self.rng.uniform(-tilt, tilt)
        obj.rotation_euler = rot
        return obj


def _link(name, bm, mat):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    o = bpy.data.objects.new(name, me)
    o.data.materials.append(mat)
    bpy.context.collection.objects.link(o)
    return o


def _sheet(name, length, width, rng, crumple=0.004, curl=0.0, nx=14, ny=5, jag=0.25):
    """A torn, crumpled sheet (paper, a leaf, a peel) lying in x, with UVs."""
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    off = Vector((rng.random() * 50, rng.random() * 50, 0))
    rows = []
    for i in range(nx + 1):
        row = []
        for j in range(ny + 1):
            u, v = i / nx, j / ny
            x = (u - 0.5) * length
            y = (v - 0.5) * width * (1.0 + jag * (rng.random() - 0.5) * (j in (0, ny)))   # torn edges
            z = crumple * noise.noise(Vector((u * 4, v * 4, 0)) + off) + curl * (2 * v - 1) ** 2 * width
            row.append(bm.verts.new((x, y, z)))
        rows.append(row)
    for i in range(nx):
        for j in range(ny):
            f = bm.faces.new((rows[i][j], rows[i + 1][j], rows[i + 1][j + 1], rows[i][j + 1]))
            for loop in f.loops:
                loop[uv].uv = (loop.vert.co.x / length + 0.5, loop.vert.co.y / width + 0.5)
    bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.0004)
    return bm


def add_scraps(bed, near=None, spread=0.06, papers=60, cards=14, seed=1):
    """The browns (newspaper, cardboard) all over; a feeding's scraps in a heap near `near` (x, y)."""
    rng = random.Random(seed)
    news, card = mat_paper(), mat_paper((0.30, 0.19, 0.10), "cardboard")
    for i in range(papers):
        bm = _sheet("paper", rng.uniform(0.025, 0.07), rng.uniform(0.006, 0.014), rng, crumple=0.003, curl=rng.uniform(-0.3, 0.3))
        bed.lay(_link(f"paper {i}", bm, news), *bed.spot(), sink=rng.uniform(0.0, 0.0025))
    for i in range(cards):
        bm = _sheet("card", rng.uniform(0.02, 0.04), rng.uniform(0.015, 0.03), rng, crumple=0.002, nx=6, ny=4)
        bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.002)
        bed.lay(_link(f"cardboard {i}", bm, card), *bed.spot(), sink=rng.uniform(0.0, 0.003))
    if near is None:
        return
    lettuce = mat_plain("lettuce", (0.32, 0.55, 0.12), 0.45, sss=0.4, coat=0.3, spots=(0.42, 0.40, 0.10))
    carrot = mat_plain("carrot peel", (0.80, 0.30, 0.04), 0.5, sss=0.3, coat=0.2)
    banana = mat_plain("banana skin", (0.36, 0.26, 0.07), 0.6, spots=(0.06, 0.04, 0.02), scale=25.0)
    shell = mat_plain("eggshell", (0.86, 0.80, 0.70), 0.65, spots=(0.78, 0.70, 0.58))
    coffee = mat_plain("coffee grounds", (0.035, 0.022, 0.014), 0.9, scale=300.0)
    for i in range(6):
        bm = _sheet("leaf", rng.uniform(0.03, 0.05), rng.uniform(0.02, 0.035), rng, crumple=0.006, nx=10, ny=8, jag=0.6)
        bed.lay(_link(f"lettuce {i}", bm, lettuce), *bed.spot(near, spread * 0.6), sink=0.001)
    for i in range(5):
        bm = _sheet("peel", rng.uniform(0.04, 0.07), rng.uniform(0.006, 0.01), rng, crumple=0.002, curl=0.25)
        bed.lay(_link(f"carrot peel {i}", bm, carrot), *bed.spot(near, spread * 0.7), sink=0.001, tilt=0.08)
    for i in range(3):
        bm = _sheet("banana", rng.uniform(0.05, 0.08), rng.uniform(0.015, 0.025), rng, crumple=0.004, curl=0.12)
        bed.lay(_link(f"banana skin {i}", bm, banana), *bed.spot(near, spread * 0.6), sink=0.001)
    for i in range(14):
        bm = _sheet("shell", rng.uniform(0.006, 0.014), rng.uniform(0.005, 0.01), rng, crumple=0.0005, curl=0.6, nx=4, ny=4, jag=0.9)
        bed.lay(_link(f"eggshell {i}", bm, shell), *bed.spot(near, spread), sink=rng.uniform(0, 0.002), tilt=0.6)
    # a dusting of coffee grounds: many tiny grains
    bm = bmesh.new()
    cx, cy = near[0] + spread * 0.5, near[1] - spread * 0.3
    for _ in range(1400):
        x, y = bed.spot((cx, cy), spread * 0.35)
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=rng.uniform(0.0004, 0.0008),
                                   matrix=Matrix.Translation((x, y, bed.height(x, y) + 0.0002)))
    _link("coffee grounds", bm, coffee)


def add_crumbs(bed, density=140000, seed=4):
    """Loose crumbs of castings scattered over the surface by Geometry Nodes, `density` per m²: what makes
    it read as soil up close. Four lumpy crumb shapes, instanced, so even a whole box stays cheap to render."""
    rng = random.Random(seed)
    coll = bpy.data.collections.new("crumb shapes")
    for k in range(4):
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
        for v in bm.verts:
            v.co *= 1.0 + 0.35 * noise.noise(v.co * 1.7 + Vector((k * 7.0, k * 3.0, 0)))
            v.co.z *= rng.uniform(0.45, 0.7)
        o = _link(f"crumb {k}", bm, bed.obj.data.materials[0])
        bpy.context.collection.objects.unlink(o)
        coll.objects.link(o)
    tree = bpy.data.node_groups.new("crumbs", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, link = tree.nodes, tree.links.new
    gin, gout = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    dist = n.new("GeometryNodeDistributePointsOnFaces")
    dist.inputs["Density"].default_value = density
    dist.inputs["Seed"].default_value = seed
    shapes = n.new("GeometryNodeCollectionInfo")
    shapes.inputs["Collection"].default_value = coll
    shapes.inputs["Separate Children"].default_value = True
    shapes.inputs["Reset Children"].default_value = True
    inst = n.new("GeometryNodeInstanceOnPoints")
    inst.inputs["Pick Instance"].default_value = True
    rot = n.new("FunctionNodeRandomValue")
    rot.data_type = "FLOAT_VECTOR"
    rot.inputs["Max"].default_value = (0.3, 0.3, 6.3)
    size = n.new("FunctionNodeRandomValue")
    size.data_type = "FLOAT"
    for sock in size.inputs:                     # Min and Max of the float kind (the node has one pair per kind)
        if sock.type == "VALUE" and sock.name in ("Min", "Max"):
            sock.default_value = 0.00025 if sock.name == "Min" else 0.0011
    join = n.new("GeometryNodeJoinGeometry")
    link(gin.outputs["Geometry"], dist.inputs["Mesh"])
    link(dist.outputs["Points"], inst.inputs["Points"])
    link(shapes.outputs["Instances"], inst.inputs["Instance"])
    link(next(o for o in rot.outputs if o.type == "VECTOR"), inst.inputs["Rotation"])
    link(next(o for o in size.outputs if o.type == "VALUE"), inst.inputs["Scale"])
    link(gin.outputs["Geometry"], join.inputs["Geometry"])
    link(inst.outputs["Instances"], join.inputs["Geometry"])
    link(join.outputs["Geometry"], gout.inputs["Geometry"])
    mod = bed.obj.modifiers.new("crumbs", "NODES")
    mod.node_group = tree


def add_cocoons(bed, n=12, near=None, spread=0.08, seed=2):
    """Cocoons: lemon shaped, 3–4 mm, glossy; new ones straw yellow, old ones olive brown."""
    rng = random.Random(seed)
    looks = [mat_plain("cocoon new", (0.72, 0.58, 0.18), 0.25, sss=0.5, coat=0.6, scale=200),
             mat_plain("cocoon", (0.55, 0.42, 0.12), 0.3, sss=0.4, coat=0.5, scale=200),
             mat_plain("cocoon old", (0.30, 0.25, 0.08), 0.35, sss=0.3, coat=0.4, scale=200)]
    for i in range(n):
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=1.0)
        for v in bm.verts:                       # a lemon: pointed at both ends, a little tail at one
            t = v.co.x
            r = math.sqrt(max(0.0, 1 - t * t))
            v.co.y *= 0.62 * r ** 0.4
            v.co.z *= 0.62 * r ** 0.4
            v.co.x *= 1.0 + 0.25 * max(0.0, t) ** 6
        k = rng.uniform(0.0016, 0.0021)
        bmesh.ops.scale(bm, vec=(k, k, k), verts=bm.verts)
        o = _link(f"cocoon {i}", bm, looks[rng.randrange(3)])
        bed.lay(o, *bed.spot(near, spread) if near else bed.spot(), sink=rng.uniform(0.0, 0.0012), tilt=0.4)


# ------------------------------------------------------------------- worms --

RINGS, SIDES = 160, 14


def _worm_mesh(length, thick):
    """A worm straight along +x from 0 (head) to `length`, ringed, with u along it and v around it."""
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")

    def radius(t):
        head = min(1.0, (t / 0.035)) ** 0.55                  # the pointed head (prostomium)
        tail = min(1.0, ((1 - t) / 0.12)) ** 0.7               # the tail tapers longer
        clit = 1.0 + 0.16 * math.exp(-((t - 0.295) / 0.045) ** 2)
        return thick / 2 * max(0.08, head * tail) * clit * (0.92 + 0.08 * (1 - t))

    rings = []
    for i in range(RINGS + 1):
        t = i / RINGS
        r = radius(t)
        rings.append([bm.verts.new((t * length, r * math.cos(a), r * math.sin(a) * (0.85 if t > 0.8 else 1.0)))   # the tail is flatter
                      for a in (2 * math.pi * k / SIDES for k in range(SIDES))])
    for i in range(RINGS):
        for k in range(SIDES):
            k2 = (k + 1) % SIDES
            f = bm.faces.new((rings[i][k], rings[i + 1][k], rings[i + 1][k2], rings[i][k2]))
            for loop, (ii, kk) in zip(f.loops, ((i, k), (i + 1, k), (i + 1, k + 1), (i, k + 1))):
                loop[uv].uv = (ii / RINGS, kk / SIDES)
    for ring, at in ((rings[0], 0.0), (rings[-1], length)):     # close the ends
        c = bm.verts.new((at, 0.0, 0.0))
        for k in range(SIDES):
            pair = (ring[k], ring[(k + 1) % SIDES])
            bm.faces.new((c, pair[1], pair[0]) if at == 0.0 else (c, pair[0], pair[1]))
    return bm


MAX_TRAVEL_M = 0.12          # how far a worm crawls in one film, at most


def add_worms(bed, n, frames, near=None, spread=0.08, seed=3, speed=0.01, young_share=0.3, sway=True):
    """`n` worms crawling over the bedding for `frames` frames (24 a second); some of them young.

    Each has its own path, a curve that wanders over the surface and dips into it at its ends; the worm
    slides along it head first by `speed` metres a second (sped up for the film) and its head sways."""
    rng = random.Random(seed)
    adult, young = mat_worm(), mat_worm(young=True)
    seconds = frames / 24.0
    out = []
    for i in range(n):
        is_young = rng.random() < young_share
        length = rng.uniform(0.025, 0.045) if is_young else rng.uniform(0.06, 0.095)
        thick = length * rng.uniform(0.045, 0.055)
        v = speed * rng.uniform(0.4, 1.3) * (0.7 if is_young else 1.0)
        travel = min(v * seconds, MAX_TRAVEL_M)                 # a long film: a worm still stays near where it was
        path_len = length + travel + 0.02
        # the path: a wandering line on the surface, from behind the tail to beyond where the head ends up
        x, y = bed.spot(near, spread) if near else bed.spot()
        heading = rng.uniform(0, 2 * math.pi)
        steps = 40
        pts = []
        bends, bend = rng.uniform(0.8, 2.0), rng.uniform(0.10, 0.22)          # gentle S-curves, as worms lie
        for s in range(steps + 1):
            heading += math.radians(rng.gauss(0, 3)) + bend * math.cos(2 * math.pi * bends * s / steps) * (2 * math.pi * bends / steps)
            if min(x - bed.x0, bed.x1 - x, y - bed.y0, bed.y1 - y) < 0.025:      # near a wall: it turns back in
                inward = math.atan2((bed.y0 + bed.y1) / 2 - y, (bed.x0 + bed.x1) / 2 - x)
                heading += 0.35 * math.atan2(math.sin(inward - heading), math.cos(inward - heading))
            pts.append((x, y))
            x += math.cos(heading) * path_len / steps
            y += math.sin(heading) * path_len / steps
        x0, y0 = pts[0]
        curve = bpy.data.curves.new(f"worm path {i}", "CURVE")
        curve.dimensions = "3D"
        sp = curve.splines.new("NURBS")
        sp.points.add(len(pts) - 1)
        dive_tail, dive_head = rng.random() < 0.35, rng.random() < 0.2
        for k, (px, py) in enumerate(pts):
            t = k / steps
            z = bed.height(px, py) + thick * 0.42
            if dive_tail and t < 0.25:
                z -= thick * 2.2 * (0.25 - t) / 0.25          # its tail still in the bedding
            if dive_head and t > 0.8:
                z -= thick * 2.0 * (t - 0.8) / 0.2            # it goes down at the end
            sp.points[k].co = (px - x0, py - y0, z - bed.z, 1.0)
        sp.order_u = 4
        sp.use_endpoint_u = True
        cobj = bpy.data.objects.new(f"worm path {i}", curve)
        cobj.location = (x0, y0, bed.z)
        bpy.context.collection.objects.link(cobj)
        # the worm: a ringed tube along +x, head at +x so it leads; it slides along the path
        bm = _worm_mesh(length, thick)
        bmesh.ops.scale(bm, vec=(-1, 1, 1), verts=bm.verts)                      # head at x = 0 -> head at the far end
        bmesh.ops.translate(bm, vec=(length, 0, 0), verts=bm.verts)
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        worm = _link(f"worm {i}", bm, young if is_young else adult)
        worm.location = cobj.location
        mod = worm.modifiers.new("crawl", "CURVE")
        mod.object, mod.deform_axis = cobj, "POS_X"
        start = 0.01
        for frame, at in ((1, start), (frames, start + travel)):
            worm.location = (x0 + at, y0, bed.z)
            worm.keyframe_insert("location", index=0, frame=frame)
        worm.location.x = x0 + start
        # the head feels about: the last points of the path sway sideways, a few times a second
        if sway:
            k0 = int(steps * (length + start + travel * 0.3) / path_len)
            swing = thick * rng.uniform(0.8, 1.6)
            hz = rng.uniform(0.6, 1.2)
            base = [tuple(p.co) for p in sp.points]
            for f in range(1, frames + 1, 3):
                ph = 2 * math.pi * hz * f / 24.0
                for k in range(k0, steps + 1):
                    w = (k - k0) / max(1, steps - k0)
                    bx, by, bz, bw = base[k]
                    heading = math.atan2(pts[min(k + 1, steps)][1] - pts[k - 1][1], pts[min(k + 1, steps)][0] - pts[k - 1][0])
                    off = swing * w * w * math.sin(ph - 2.0 * w)
                    sp.points[k].co = (bx - math.sin(heading) * off, by + math.cos(heading) * off, bz + 0.3 * swing * w * max(0.0, math.sin(ph * 0.5)), bw)
                    curve.keyframe_insert(f'splines[0].points[{k}].co', frame=f)
        out.append(worm)
    return out
