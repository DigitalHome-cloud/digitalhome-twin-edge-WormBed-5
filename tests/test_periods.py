# SPDX-License-Identifier: GPL-3.0-or-later
"""A bed's year in periods (PermTek-5 ADR 0029): the twin rehearses one period's target, from a clean bed or as the
bed stands; the plants live; the robot sows only what the period plans and the weather allows; a person harvests,
clears and moves on. A rehearsal writes nothing into the habitat."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS

from permtek5_twin_edge import engine, habitat as hb, world
from permtek5_twin_edge import habitat_store as store
from permtek5_twin_edge.edge import knowledge as kn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "roofbed_twin"))
from roofbed_twin.plants import Plants  # noqa: E402

ID = "https://permtek-5.digitalhome.cloud/id/"
BED = ID + "bed-b-citybed"
P = hb.PERMA


def with_periods(ttl: str) -> str:
    """CityDemo with a year: spring (its cells as they are, now the spring target), summer (two cells of its own)
    and a winter rest."""
    from rdflib import Graph
    g = Graph().parse(data=ttl, format="turtle")
    bed = URIRef(BED)
    for pid, name, kind, a, b in (("period-spring", "Spring", "GrowingKind", 3, 6), ("period-summer", "Summer", "GrowingKind", 7, 9),
                                  ("period-winter", "Winter", "RestKind", 10, 2)):
        s = URIRef(ID + pid)
        for t in ((RDF.type, P.GrowingPeriod), (RDFS.label, Literal(name)), (P.ofBed, bed), (P.fromMonth, Literal(a)),
                  (P.toMonth, Literal(b)), (P.periodKind, P[kind])):
            g.add((s, *t))
    for c in g.subjects(P.inBed, bed):
        g.add((c, P.inPeriod, URIRef(ID + "period-spring")))
    for i, (row, col, crop) in enumerate(((0, 0, "crop-tomato"), (6, 6, "crop-basil"))):
        c = URIRef(f"{ID}cell-summer-{i}")
        for t in ((RDF.type, P.PlantingCell), (P.inBed, bed), (P.row, Literal(row)), (P.col, Literal(col)),
                  (P.grows, URIRef(hb.CROP_IRI + crop)), (P.cellStatus, P.CellPlanned), (P.inPeriod, URIRef(ID + "period-summer"))):
            g.add((c, *t))
    return g.serialize(format="turtle")


@pytest.fixture(scope="module")
def year():
    return with_periods(hb.CITYDEMO.read_text(encoding="utf-8"))


def test_periods_and_a_cells_period_are_read(year):
    h = hb.load(year)
    assert [(p.name, p.kind, p.months) for p in hb.periods_of(h, BED)] == [
        ("Spring", "GrowingKind", [3, 4, 5, 6]), ("Summer", "GrowingKind", [7, 8, 9]), ("Winter", "RestKind", [10, 11, 12, 1, 2])]
    assert hb.period_at(h, BED, 1).name == "Winter"
    assert sum(1 for c in h.cells if c.period_id == ID + "period-summer") == 2


def test_a_clean_start_plans_the_whole_target_and_every_period_goes_along(year, tmp_path):
    h, lib = hb.load(year), hb.load_library()
    world.generate(h, lib, tmp_path, period=ID + "period-spring", start="clean")
    cfg = json.loads((tmp_path / "bed.json").read_text())
    assert cfg["period"]["name"] == "Spring" and cfg["start"] == "clean" and cfg["rehearsal"] is True
    assert {c["status"] for c in cfg["cells"]} == {"CellPlanned"}, "a clean bed: nothing sown yet"
    assert "DEF CELL_" not in (tmp_path / "roofbed.wbt").read_text(), "and nothing stands in the world"
    assert len(cfg["targets"][ID + "period-summer"]) == 2
    assert cfg["crops"]["crop-tomato"]["frostTender"] is True and cfg["crops"]["crop-tomato"]["daysToHarvest"]


def test_as_the_bed_stands_keeps_the_statuses_and_the_month_chooses_the_period(year, tmp_path):
    h, lib = hb.load(year), hb.load_library()
    world.generate(h, lib, tmp_path, start="as-is", month=8)
    cfg = json.loads((tmp_path / "bed.json").read_text())
    assert cfg["period"]["name"] == "Summer" and len(cfg["cells"]) == 2
    world.generate(h, lib, tmp_path, period=ID + "period-spring", start="as-is")
    cfg = json.loads((tmp_path / "bed.json").read_text())
    assert "CellGrowing" in {c["status"] for c in cfg["cells"]}


def test_the_target_is_shown_before_a_world_is_made(year):
    t = engine.preview(year, month=8)
    assert t["period"]["name"] == "Summer" and len(t["cells"]) == 2 and t["bed"]["binFrom"] < t["bed"]["cols"]
    assert engine.preview(year, period="")["cells"] == [], "every cell has a period: nothing stands outside one"


def test_a_planned_period_is_written_into_its_period(tmp_path, year):
    kb = kn.KnowledgeBase(tmp_path / "abox")
    kb.change("shared", lambda g: g.parse(data=year, format="turtle"))
    plans = [{"bedId": BED, "periodId": ID + "period-summer", "cells": [{"row": 0, "col": 0, "cropId": "crop-tomato"},
                                                                       {"row": 1, "col": 3, "cropId": "crop-lettuce"}]}]
    assert store.plan_cells("shared", plans, kb) == 1, "the summer tomato already stands in the summer target"
    g = kb.load("shared")
    assert len(list(g.subjects(P.inPeriod, URIRef(ID + "period-summer")))) == 3
    with pytest.raises(ValueError):
        store.plan_cells("shared", [{**plans[0], "periodId": ID + "period-nope"}], kb)


# -- the plants ------------------------------------------------------------------

CROPS = {"crop-lettuce": {"germinationDays": 7, "daysToHarvest": 55, "sowMinTempC": 5, "frostTender": False},
         "crop-basil": {"germinationDays": 10, "daysToHarvest": 60, "sowMinTempC": 15, "frostTender": True}}


def run(plants, cells, days, mean=18.0, air=None, moisture=None):
    for _ in range(int(days * 24)):
        if moisture is not None:
            for c in cells.values():
                c["moisture"] = moisture
        plants.step(cells, 60, mean, mean if air is None else air)


def test_a_seed_comes_up_after_its_days_and_is_ready_after_its_days_to_harvest():
    plants = Plants(CROPS, seed=1)
    cells = {"0_0": {"status": "CellSown", "moisture": 40.0}}
    plants.sow("0_0", "crop-lettuce")
    plants.p["0_0"]["dud"] = False
    run(plants, cells, 6.5)
    assert cells["0_0"]["status"] == "CellSown"
    run(plants, cells, 1)
    assert cells["0_0"]["status"] == "CellGrowing"
    run(plants, cells, 54)
    assert not plants.state("0_0")["ready"]
    run(plants, cells, 2)
    assert plants.state("0_0")["ready"]


def test_cold_soil_keeps_a_warm_crop_down_until_it_is_lost():
    plants = Plants(CROPS, seed=1)
    cells = {"0_0": {"status": "CellSown", "moisture": 40.0}}
    plants.sow("0_0", "crop-basil")
    run(plants, cells, 31, mean=9.0)
    assert cells["0_0"]["status"] == "CellLost" and plants.state("0_0")["why"] == "cold"


def test_frost_kills_a_tender_crop_and_drought_any():
    plants = Plants(CROPS, seed=1)
    cells = {"0_0": {"status": "CellGrowing", "moisture": 40.0}, "0_1": {"status": "CellGrowing", "moisture": 40.0}}
    plants.grown("0_0", "crop-basil")
    plants.grown("0_1", "crop-lettuce")
    run(plants, cells, 0.1, mean=4.0, air=-1.0)
    assert cells["0_0"]["status"] == "CellLost" and cells["0_1"]["status"] == "CellGrowing", "lettuce stands a light frost"
    run(plants, cells, 2.1, moisture=8.0)
    assert cells["0_1"]["status"] == "CellLost" and plants.state("0_1")["why"] == "dry"


def test_the_same_seed_gives_the_same_bed():
    def bed(seed):
        plants = Plants(CROPS, seed=seed)
        cells = {f"0_{i}": {"status": "CellSown", "moisture": 40.0} for i in range(40)}
        for k in cells:
            plants.sow(k, "crop-lettuce")
        run(plants, cells, 30)
        return {k: c["status"] for k, c in cells.items()}
    assert bed(3) == bed(3)
    assert "CellLost" in bed(3).values(), "some sowings never come up"


# -- what the robot proposes, and what a person does -----------------------------

def cfg_for(year, tmp_path, **kw):
    world.generate(hb.load(year), hb.load_library(), tmp_path, **kw)
    return json.loads((tmp_path / "bed.json").read_text())


def test_sowing_waits_for_warmth_and_a_rest_sows_nothing(year, tmp_path):
    cfg = cfg_for(year, tmp_path, period=ID + "period-summer", start="clean")
    warm = {"env": {"meanC": 20.0, "month": 7}, "cells": {}}
    cold = {"env": {"meanC": 8.0, "month": 7}, "cells": {}}
    sows = lambda state, period=None: [p["id"] for p in engine.proposals(cfg, state, period=period) if p["kind"] == "plant"]  # noqa: E731
    assert sows(warm) == ["plant-0_0", "plant-6_6"]
    assert sows(cold) == [], "tomato and basil want warmer days"
    assert [w["crop"] for w in engine.sowing_waits(cfg, cold)] == ["crop-basil", "crop-tomato"]
    winter = next(p for p in cfg["periods"] if p["kind"] == "RestKind")
    assert sows(warm, winter) == []


def test_a_lost_cell_is_sown_again(year, tmp_path):
    cfg = cfg_for(year, tmp_path, period=ID + "period-summer", start="clean")
    state = {"env": {"meanC": 20.0, "month": 7}, "cells": {"0_0": {"status": "CellLost", "why": "frost", "moisture": 30}}}
    resow = next(p for p in engine.proposals(cfg, state) if p["id"] == "plant-0_0")
    assert resow["title"].startswith("Resow") and "frost" in resow["detail"]


def test_a_person_harvests_what_is_ready_and_moves_on_when_the_period_is_over(year, tmp_path):
    cfg = cfg_for(year, tmp_path, period=ID + "period-summer", start="clean")
    summer = cfg["period"]
    ready = {"env": {"meanC": 20.0, "month": 8}, "cells": {"0_0": {"status": "CellGrowing", "crop": "crop-tomato", "ready": True, "moisture": 40}}}
    harvest = next(p for p in engine.proposals(cfg, ready, period=summer) if p["kind"] == "harvest")
    assert harvest["who"] == "person" and harvest["spec"] == {"kind": "harvest", "ready": True}
    over = {"env": {"meanC": 12.0, "month": 10}, "cells": {"0_0": {"status": "CellGrowing", "crop": "crop-tomato", "moisture": 40}}}
    kinds = {p["kind"]: p for p in engine.proposals(cfg, over, period=summer)}
    assert "clear" in kinds and kinds["period"]["spec"]["id"] == ID + "period-winter"
    winter = engine.period_of(cfg, ID + "period-winter")
    assert engine.target(cfg, winter) == [], "the rest has no cells of its own"
    spring = engine.period_of(cfg, ID + "period-spring")
    assert len(engine.target(cfg, spring)) == len(cfg["targets"][ID + "period-spring"])
