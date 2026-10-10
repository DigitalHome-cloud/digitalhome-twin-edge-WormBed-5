# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
habitat.py — a PermTek-5 habitat as the twin needs it.

Reads the habitat's A-Box (Turtle: zones, growing beds, planting cells, garden
plants; PermTek-5 ADR 0016) and the shared crop library, and turns a cell into
what a seeder does there. sowing_job() is the same rule as sowingJob() in
PermTek-5's packages/core, so the twin, the app and later the real gantry
agree on where a cell is:

    bed coordinates in mm, origin at the outer corner of row 0 / column 0,
    x along the bed's length (columns), y across its width (rows),
    the point is the cell's centre, depth is below the substrate surface.

Needs rdflib only (no ROS, no Webots).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

PERMA = Namespace("https://permaculture.digitalhome.cloud/ontology#")
CROP_IRI = "https://permaculture.digitalhome.cloud/id/"
DATA = Path(__file__).resolve().parent / "data"
CITYDEMO = DATA / "citydemo-habitat.ttl"
LIBRARY = DATA / "crops.ttl"


@dataclass
class Crop:
    id: str
    names: Dict[str, str]
    woody: bool = False
    spacing_cm: Optional[int] = None
    sowing_depth_mm: Optional[int] = None
    seeds_per_point: Optional[int] = None
    germination_days: Optional[int] = None
    water_mm_per_week: Optional[float] = None

    def name(self, lang: str = "en") -> str:
        return self.names.get(lang) or self.names.get("en") or self.id


@dataclass
class Zone:
    id: str
    name: str
    kind: str
    offset_c: float = 0.0
    area_m2: Optional[float] = None


ROBOT_NAMES = {"Gantry5Gen1": "Gantry5-gen1"}


@dataclass
class Guild:
    """A Perma5Guild (PermTek-5 ADR 0020): an area's working unit of bed, worm box, tank, sensors and robot."""
    id: str
    name: str
    zone_id: str
    type: str = "WormBed5"
    robot: Optional[str] = None
    worms_may_leave: bool = True

    @property
    def robot_name(self) -> str:
        return ROBOT_NAMES.get(self.robot or "", self.robot or "")


@dataclass
class Bed:
    id: str
    name: str
    zone_id: str
    length_cm: int
    width_cm: int
    cell_cm: int
    depth_cm: Optional[int] = None
    # a wicking bed with a worm box (PermTek-5 docs/specs/roofbed-system.md)
    substrate: Optional[str] = None
    tank_pipes: int = 0
    tank_pipe_mm: int = 110
    worm_bin_cm: int = 0
    guild_id: Optional[str] = None

    @property
    def rows(self) -> int:
        return -(-self.width_cm // self.cell_cm)

    @property
    def cols(self) -> int:
        return -(-self.length_cm // self.cell_cm)

    @property
    def worm_bin_cols(self) -> int:
        """The columns at the bed's far end that the worm box takes."""
        return min(self.cols, -(-self.worm_bin_cm // self.cell_cm)) if self.worm_bin_cm else 0

    @property
    def tank_litres(self) -> int:
        """What the tank pipes hold: the same rule as bedLoad() in PermTek-5's packages/core."""
        if not self.tank_pipes:
            return 0
        bore = self.tank_pipe_mm / 1000.0 * 0.94
        return round(math.pi * (bore / 2) ** 2 * (self.length_cm / 100.0) * self.tank_pipes * 1000)


@dataclass
class Cell:
    id: str
    bed_id: str
    row: int
    col: int
    crop_id: str
    status: str                       # CellPlanned | CellSown | CellGrowing | CellHarvested
    sown_on: Optional[str] = None
    depth_mm: Optional[int] = None
    seeds: Optional[int] = None


@dataclass
class GardenPlant:
    id: str
    name: str
    zone_id: str
    crop_id: str
    x_m: Optional[float] = None
    y_m: Optional[float] = None


@dataclass
class Habitat:
    zones: List[Zone] = field(default_factory=list)
    guilds: List[Guild] = field(default_factory=list)
    beds: List[Bed] = field(default_factory=list)
    cells: List[Cell] = field(default_factory=list)
    plants: List[GardenPlant] = field(default_factory=list)


def _num(g: Graph, s: URIRef, p: str, cast=float):
    v = g.value(s, PERMA[p])
    return None if v is None else cast(v.toPython())


def _local(node) -> str:
    return str(node).split("#")[-1] if node is not None else ""


def load_library(path: Path = LIBRARY) -> Dict[str, Crop]:
    """The crop library by local id ("crop-lettuce")."""
    g = Graph().parse(path, format="turtle")
    out: Dict[str, Crop] = {}
    for c in g.subjects(RDF.type, PERMA.Crop):
        cid = str(c)[len(CROP_IRI):]
        out[cid] = Crop(
            id=cid,
            names={l.language: str(l) for l in g.objects(c, RDFS.label) if l.language},
            woody=_local(g.value(c, PERMA.growthForm)) in ("Tree", "Shrub"),
            spacing_cm=_num(g, c, "spacingCm", int),
            sowing_depth_mm=_num(g, c, "sowingDepthMm", int),
            seeds_per_point=_num(g, c, "seedsPerPoint", int),
            germination_days=_num(g, c, "germinationDays", int),
            water_mm_per_week=_num(g, c, "waterMmPerWeek", float),
        )
    return out


def load(ttl: str) -> Habitat:
    """A habitat from its A-Box in Turtle. Unknown triples are ignored."""
    g = Graph().parse(data=ttl, format="turtle")
    label = lambda s: str(g.value(s, RDFS.label) or "")           # noqa: E731
    crop = lambda s: str(g.value(s, PERMA.grows) or "")[len(CROP_IRI):]  # noqa: E731
    h = Habitat()
    for s in sorted(g.subjects(RDF.type, PERMA.GardenZone)):
        h.zones.append(Zone(str(s), label(s), _local(g.value(s, PERMA.zoneKind)),
                            _num(g, s, "microclimateOffsetC") or 0.0, _num(g, s, "areaM2")))
    for s in sorted(g.subjects(RDF.type, PERMA.Guild)):
        leave = g.value(s, PERMA.wormsMayLeave)
        h.guilds.append(Guild(str(s), label(s), str(g.value(s, PERMA.inZone) or ""), _local(g.value(s, PERMA.guildType)) or "WormBed5",
                              _local(g.value(s, PERMA.robotModel)) or None, True if leave is None else bool(leave.toPython())))
    for s in sorted(g.subjects(RDF.type, PERMA.GrowingBed)):
        h.beds.append(Bed(str(s), label(s), str(g.value(s, PERMA.inZone) or ""),
                          _num(g, s, "lengthCm", int) or 0, _num(g, s, "widthCm", int) or 0,
                          _num(g, s, "cellSizeCm", int) or 10, _num(g, s, "substrateDepthCm", int),
                          _local(g.value(s, PERMA.substrateKind)) or None, _num(g, s, "tankPipeCount", int) or 0,
                          _num(g, s, "tankPipeMm", int) or 110, _num(g, s, "wormBinCm", int) or 0,
                          str(g.value(s, PERMA.inGuild)) if g.value(s, PERMA.inGuild) is not None else None))
    for s in sorted(g.subjects(RDF.type, PERMA.PlantingCell)):
        sown = g.value(s, PERMA.sownOn)
        h.cells.append(Cell(str(s), str(g.value(s, PERMA.inBed) or ""), _num(g, s, "row", int) or 0,
                            _num(g, s, "col", int) or 0, crop(s), _local(g.value(s, PERMA.cellStatus)) or "CellPlanned",
                            str(sown) if sown is not None else None,
                            _num(g, s, "cellSowingDepthMm", int), _num(g, s, "cellSeedsPerPoint", int)))
    for s in sorted(g.subjects(RDF.type, PERMA.GardenPlant)):
        h.plants.append(GardenPlant(str(s), label(s), str(g.value(s, PERMA.inZone) or ""), crop(s),
                                    _num(g, s, "xM"), _num(g, s, "yM")))
    return h


def load_citydemo() -> Habitat:
    """The invented example habitat that ships with the tool."""
    return load(CITYDEMO.read_text(encoding="utf-8"))


def sowing_job(h: Habitat, cell: Cell, library: Dict[str, Crop]) -> Optional[Dict[str, Any]]:
    """What a seeder does at one cell, in bed coordinates (mm). None for an unknown bed or crop."""
    bed = next((b for b in h.beds if b.id == cell.bed_id), None)
    crop = library.get(cell.crop_id)
    if bed is None or crop is None:
        return None
    cell_mm = bed.cell_cm * 10
    return {
        "cellId": cell.id, "bedId": bed.id, "cropId": crop.id,
        "xMm": round((cell.col + 0.5) * cell_mm),
        "yMm": round((cell.row + 0.5) * cell_mm),
        "depthMm": cell.depth_mm if cell.depth_mm is not None else (crop.sowing_depth_mm if crop.sowing_depth_mm is not None else 5),
        "seeds": cell.seeds if cell.seeds is not None else (crop.seeds_per_point or 1),
        "spacingMm": (crop.spacing_cm or bed.cell_cm) * 10,
        "checkAfterDays": crop.germination_days,
    }


def water_ml_per_day(crop: Crop, bed: Bed) -> int:
    """Water one plant wants a day without rain: spacing² × mm/week ÷ 7 (1 mm = 1 l/m²)."""
    spacing_m = (crop.spacing_cm or bed.cell_cm) / 100.0
    return round(spacing_m * spacing_m * (crop.water_mm_per_week or 20.0) / 7.0 * 1000)


def problems(h: Habitat, library: Dict[str, Crop]) -> List[str]:
    """What stops a habitat from being simulated, all at once."""
    out: List[str] = []
    beds = {b.id: b for b in h.beds}
    seen = set()
    for c in h.cells:
        b = beds.get(c.bed_id)
        if b is None:
            out.append(f"cell {c.id} has no bed")
            continue
        if not (0 <= c.row < b.rows and 0 <= c.col < b.cols):
            out.append(f"cell {c.id} lies outside its bed (row {c.row + 1}, column {c.col + 1})")
        elif c.col >= b.cols - b.worm_bin_cols:
            out.append(f"cell {c.id} stands where the worm box is (row {c.row + 1}, column {c.col + 1})")
        if (c.bed_id, c.row, c.col) in seen:
            out.append(f"two plantings on row {c.row + 1}, column {c.col + 1}")
        seen.add((c.bed_id, c.row, c.col))
        if c.crop_id not in library:
            out.append(f"cell {c.id} grows an unknown crop ({c.crop_id or 'none'})")
    for b in h.beds:
        if b.length_cm <= 0 or b.width_cm <= 0:
            out.append(f"bed {b.name or b.id} has no size")
    return out


def crop_rgb(crop_id: str):
    """A stable colour per crop: the same hue rule as the PermTek-5 map."""
    import colorsys
    hue = 0
    for ch in crop_id:
        hue = (hue * 31 + ord(ch)) % 360
    return colorsys.hls_to_rgb(hue / 360.0, 0.52, 0.45)


def slug(name: str) -> str:
    """A name as a folder or option name: lowercase letters, digits and dashes."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "guild"


def pick_guild(h: Habitat, wanted: Optional[str] = None) -> Tuple[Optional[Guild], Optional[Bed]]:
    """The guild an edge instance runs, with its bed. `wanted` is a guild's name or slug; without it the
    first guild. A habitat from before guilds existed has none: its first bed is run as a plain bed."""
    guilds = [g for g in h.guilds if any(b.guild_id == g.id for b in h.beds)]
    if wanted:
        guild = next((g for g in guilds if slug(g.name) == slug(wanted)), None)
        if guild is None:
            raise ValueError(f"this habitat has no Perma5Guild named '{wanted}'"
                             + (": it has " + ", ".join(g.name for g in guilds) if guilds else ""))
    else:
        guild = guilds[0] if guilds else None
    if guild is None:
        return None, (h.beds[0] if h.beds else None)
    return guild, next(b for b in h.beds if b.guild_id == guild.id)


#: What a WormBed5 guild has when the map does not say (PermTek-5 docs/specs/roofbed-system.md, the reference bed)
WORMBED5 = {"worm_bin_cm": 40, "tank_pipes": 8, "tank_pipe_mm": 110}


def as_wormbed5(h: Habitat, bed: Bed) -> List[str]:
    """Give the bed the parts a WormBed5 guild always has, where the map lacks them. Changes `bed` in
    place and returns what was assumed, in plain words. A worm box is not assumed over planted cells."""
    assumed: List[str] = []
    if not bed.worm_bin_cm:
        cols = -(-WORMBED5["worm_bin_cm"] // bed.cell_cm)
        if any(c.bed_id == bed.id and c.col >= bed.cols - cols for c in h.cells):
            assumed.append(f"no worm box: the last {WORMBED5['worm_bin_cm']} cm of the bed are planted on your map")
        else:
            bed.worm_bin_cm = WORMBED5["worm_bin_cm"]
            assumed.append(f"a worm box of {bed.worm_bin_cm} cm at the bed's far end")
    if not bed.tank_pipes:
        bed.tank_pipes, bed.tank_pipe_mm = WORMBED5["tank_pipes"], WORMBED5["tank_pipe_mm"]
        assumed.append(f"{bed.tank_pipes} tank pipes of {bed.tank_pipe_mm} mm under the bed ({bed.tank_litres} l)")
    return assumed


def bed_config(h: Habitat, bed: Bed, library: Dict[str, Crop]) -> Dict[str, Any]:
    """Everything the ROS side needs about one bed, as plain data (it reads no Turtle)."""
    cells = []
    for c in h.cells:
        if c.bed_id != bed.id:
            continue
        crop = library.get(c.crop_id)
        cells.append({
            "id": c.id, "row": c.row, "col": c.col, "crop": c.crop_id,
            "cropName": crop.name() if crop else c.crop_id, "status": c.status, "sownOn": c.sown_on,
            "job": sowing_job(h, c, library),
            "waterMlPerDay": water_ml_per_day(crop, bed) if crop else 0,
            "rgb": [round(v, 3) for v in crop_rgb(c.crop_id)],
        })
    guild = next((g for g in h.guilds if g.id == bed.guild_id), None)
    return {"bed": {**asdict(bed), "rows": bed.rows, "cols": bed.cols}, "cells": cells,
            "guild": ({"id": guild.id, "name": guild.name, "type": guild.type, "robot": guild.robot,
                       "robotName": guild.robot_name, "wormsMayLeave": guild.worms_may_leave} if guild else None),
            "tank": {"pipes": bed.tank_pipes, "pipeMm": bed.tank_pipe_mm, "litres": bed.tank_litres},
            "wormBin": {"cm": bed.worm_bin_cm, "cols": bed.worm_bin_cols, "fromCol": bed.cols - bed.worm_bin_cols}}
