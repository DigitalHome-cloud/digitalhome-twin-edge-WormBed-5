# SPDX-License-Identifier: GPL-3.0-or-later
"""A synced habitat as the twin's source, and a sown cell written back into it."""

from __future__ import annotations

import datetime

import pytest
from rdflib import URIRef
from rdflib.namespace import RDF

from permtek5_twin_edge import habitat as hb
from permtek5_twin_edge import habitat_store as store
from permtek5_twin_edge.edge import knowledge as kn

BED = "https://permtek-5.digitalhome.cloud/id/bed-b-citybed"


@pytest.fixture
def kb(tmp_path):
    k = kn.KnowledgeBase(tmp_path / "abox")
    k.change("shared", lambda g: g.parse(hb.CITYDEMO, format="turtle"))      # as if synced from the cloud
    return k


def test_only_areas_with_a_bed_are_offered(kb):
    kb.change("notes", lambda g: None)
    assert store.habitats(kb) == [{"graph": "shared", "beds": 1, "cells": 28}]
    assert "GrowingBed" in store.ttl_of("shared", kb)
    with pytest.raises(ValueError):
        store.ttl_of("elsewhere", kb)


def test_a_planned_cell_becomes_sown_with_its_date(kb):
    cell = store.mark_sown("shared", BED, 5, 13, "crop-rocket", datetime.date(2026, 10, 10), kb)
    g = kb.load("shared")
    assert g.value(URIRef(cell), hb.PERMA.cellStatus) == hb.PERMA.CellSown
    assert str(g.value(URIRef(cell), hb.PERMA.sownOn)) == "2026-10-10"
    assert len(list(g.subjects(RDF.type, hb.PERMA.PlantingCell))) == 28, "the planned cell is updated, not doubled"


def test_sowing_a_free_spot_adds_a_cell_the_shapes_accept(kb):
    cell = store.mark_sown("shared", BED, 11, 0, "crop-radish", kb=kb)
    assert cell.startswith(store.ID_SPACE + "cell-c-")
    h = hb.load(kb.load("shared").serialize(format="turtle"))
    assert len(h.cells) == 29 and hb.problems(h, hb.load_library()) == []


def test_what_the_shapes_refuse_is_not_written(kb):
    with pytest.raises(kn.Invalid):
        store.mark_sown("shared", "https://permtek-5.digitalhome.cloud/id/no-such-bed", 0, 0, "crop-radish", kb=kb)
    assert len(list(kb.load("shared").subjects(RDF.type, hb.PERMA.PlantingCell))) == 28


def test_results_are_recorded_once_and_never_for_citydemo(kb, tmp_path):
    history = [{"id": "j1", "kind": "plant", "state": "done", "result": {"sown": True}, "spec": {"row": 5, "col": 13, "crop": "crop-rocket"}},
               {"id": "j2", "kind": "plant", "state": "failed", "result": {"sown": False}, "spec": {"row": 5, "col": 15}},
               {"id": "j3", "kind": "water", "state": "done", "result": {}, "spec": {}}]
    store.remember_source(tmp_path, None)                       # CityDemo
    assert store.record_results(tmp_path, BED, history, kb) == []
    store.remember_source(tmp_path, "shared")
    assert len(store.record_results(tmp_path, BED, history, kb)) == 1
    assert store.record_results(tmp_path, BED, history, kb) == [], "a job is written once"
    assert store.source(tmp_path)["recorded"] == ["j1"]
