# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
habitat_store.py — the habitats this edge holds, and what the twin did to them.

A linked edge keeps one A-Box per area of its site (edge/knowledge.py),
synced with PermTek-5. The twin can be prepared from one of them instead of
CityDemo; when the gantry has sown a cell, that is written into the A-Box
(status "sown" and the date), checked with the cloud's shapes, and the next
sync carries it to the cloud, where the map shows it.

Only what happened is written. A simulated sowing is still a statement about
the habitat, so it is recorded only for a source the person chose, and never
for CityDemo.
"""

from __future__ import annotations

import datetime
import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

from . import habitat as hb
from .edge import knowledge as kn

ID_SPACE = "https://permtek-5.digitalhome.cloud/id/"
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def habitats(kb: Optional[kn.KnowledgeBase] = None) -> List[Dict[str, Any]]:
    """The local A-Boxes that hold a growing bed: what the twin could be prepared from."""
    kb = kb or kn.KnowledgeBase()
    out = []
    for name in kb.graphs():
        g = kb.load(name)
        beds = list(g.subjects(RDF.type, hb.PERMA.GrowingBed))
        if beds:
            out.append({"graph": name, "beds": len(beds),
                        "cells": len(list(g.subjects(RDF.type, hb.PERMA.PlantingCell)))})
    return out


def ttl_of(graph: str, kb: Optional[kn.KnowledgeBase] = None) -> str:
    kb = kb or kn.KnowledgeBase()
    if graph not in kb.graphs():
        raise ValueError(f"this edge holds no area '{graph}'")
    return kb.load(graph).serialize(format="turtle")


def mark_sown(graph: str, bed_id: str, row: int, col: int, crop_id: str,
              when: Optional[datetime.date] = None, kb: Optional[kn.KnowledgeBase] = None) -> str:
    """Record that the cell was sown. Returns the cell's IRI. Raises knowledge.Invalid if the shapes refuse it."""
    kb = kb or kn.KnowledgeBase()
    day = Literal((when or datetime.date.today()).isoformat(), datatype=XSD.date)
    P = hb.PERMA
    bed = URIRef(bed_id)

    def apply(g: Graph) -> str:
        cell = next((c for c in g.subjects(P.inBed, bed)
                     if (c, RDF.type, P.PlantingCell) in g
                     and g.value(c, P.row) is not None and int(g.value(c, P.row)) == row
                     and g.value(c, P.col) is not None and int(g.value(c, P.col)) == col), None)
        if cell is None:                                   # sown where nothing was planned: a new cell
            cell = URIRef(f"{ID_SPACE}cell-c-{''.join(secrets.choice(_ALPHABET) for _ in range(10))}")
            g.add((cell, RDF.type, P.PlantingCell))
            g.add((cell, P.inBed, bed))
            g.add((cell, P.row, Literal(row)))
            g.add((cell, P.col, Literal(col)))
        if crop_id:
            g.set((cell, P.grows, URIRef(hb.CROP_IRI + crop_id)))
        g.set((cell, P.cellStatus, P.CellSown))
        g.set((cell, P.sownOn, day))
        return str(cell)

    return kb.change(graph, apply)


def plan_cells(graph: str, plans: List[Dict[str, Any]], kb: Optional[kn.KnowledgeBase] = None) -> int:
    """Write a planting the cloud's planner proposed and a person confirmed (docs/adr/0027 in PermTek-5): each
    plan's cells, "planned", into its bed. A cell where one already stands is skipped. Returns the cells written.
    Raises knowledge.Invalid if the shapes refuse it."""
    kb = kb or kn.KnowledgeBase()
    P = hb.PERMA

    def apply(g: Graph) -> int:
        n = 0
        for plan in plans:
            bed = URIRef(str(plan["bedId"]))
            if (bed, RDF.type, P.GrowingBed) not in g:
                raise ValueError(f"no bed {bed} in '{graph}'")
            taken = {(int(g.value(c, P.row)), int(g.value(c, P.col)), str(g.value(c, P.grows)))
                     for c in g.subjects(P.inBed, bed) if g.value(c, P.row) is not None and g.value(c, P.col) is not None}
            for c in plan.get("cells") or []:
                row, col, crop = int(c["row"]), int(c["col"]), str(c["cropId"])
                if (row, col, hb.CROP_IRI + crop) in taken:
                    continue
                cell = URIRef(f"{ID_SPACE}cell-c-{''.join(secrets.choice(_ALPHABET) for _ in range(10))}")
                for t in ((RDF.type, P.PlantingCell), (P.inBed, bed), (P.row, Literal(row)), (P.col, Literal(col)),
                          (P.grows, URIRef(hb.CROP_IRI + crop)), (P.cellStatus, P.CellPlanned)):
                    g.add((cell, *t))
                n += 1
        return n

    return kb.change(graph, apply)


# -- which habitat the prepared world came from, and which jobs were recorded -----

def _file(out: Path) -> Path:
    return Path(out) / "source.json"


def remember_source(out: Path, graph: Optional[str]) -> None:
    Path(out).mkdir(parents=True, exist_ok=True)
    _file(out).write_text(json.dumps({"graph": graph, "recorded": []}), encoding="utf-8")


def source(out: Path) -> Dict[str, Any]:
    try:
        return json.loads(_file(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"graph": None, "recorded": []}


def record_results(out: Path, bed_id: str, history: List[Dict[str, Any]],
                   kb: Optional[kn.KnowledgeBase] = None) -> List[str]:
    """Write every finished sowing not yet recorded into the source habitat. Returns the cells written."""
    src = source(out)
    if not src.get("graph"):
        return []
    done = set(src.get("recorded", []))
    written = []
    for job in history:
        if job.get("kind") != "plant" or job.get("state") != "done" or job.get("id") in done:
            continue
        if not (job.get("result") or {}).get("sown"):
            continue
        spec = job.get("spec") or {}
        try:
            written.append(mark_sown(src["graph"], bed_id, int(spec["row"]), int(spec["col"]), str(spec.get("crop", "")), kb=kb))
        except (kn.Invalid, KeyError, ValueError):
            continue                                       # left unrecorded: it is tried again on the next look
        done.add(job["id"])
    if written:
        src["recorded"] = sorted(done)
        _file(out).write_text(json.dumps(src), encoding="utf-8")
    return written
