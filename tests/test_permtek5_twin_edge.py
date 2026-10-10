# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""The twin's engine and routes, without ROS or Webots: the simulation is stood in for."""

from __future__ import annotations

import pytest

from permtek5_twin_edge import app as web
from permtek5_twin_edge import engine, sim
from permtek5_twin_edge.core import version
from permtek5_twin_edge.core import workspace as ws


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "UPDATE_CHECK", False)
    monkeypatch.setitem(ws.FOLDERS, ws.USER, tmp_path / "user")
    monkeypatch.setitem(ws.FOLDERS, ws.SHARED, tmp_path / "shared")
    ws.configure()
    web.app.config["TESTING"] = True
    return web.app.test_client()


@pytest.fixture
def twin(monkeypatch):
    """A stand-in for the running simulation: remembers what the gateway was asked."""
    calls = []
    state = {"running": True, "missing": [], "gui": False, "started": 1.0,
             "twin": {"gantry": {"x": 0.0, "y": 0.0, "z": 0.0}, "cameras": ["overview", "tool"], "job": None, "history": [],
                      "bed": {"cells": {"5_13": {"status": "CellPlanned", "crop": "crop-rocket", "moisture": 35.0},
                                        "1_1": {"status": "CellGrowing", "crop": "crop-lettuce", "moisture": 12.0}}}}}
    monkeypatch.setattr(sim, "status", lambda: state)
    monkeypatch.setattr(sim, "running", lambda: state["running"])
    monkeypatch.setattr(sim, "gateway", lambda path, body=None, timeout=3.0: calls.append((path, body)) or {"id": "j1"})
    return state, calls


# ---------------------------------------------------------------- engine ----

def test_citydemo_is_ready_and_bad_turtle_says_so():
    assert engine.check(engine.habitat_ttl(None)) == []
    assert any("not valid Turtle" in e for e in engine.check("this is not turtle"))
    assert any("no growing bed" in e for e in engine.check("@prefix x: <http://x/> . x:a x:b x:c ."))


def test_prepare_writes_the_world_into_the_folder_given(tmp_path):
    out = engine.prepare(engine.habitat_ttl(None), tmp_path)
    assert (tmp_path / "roofbed.wbt").exists() and (tmp_path / "bed.json").exists()
    assert out["prepared"] and out["bed"]["rows"] == 12 and out["cells"] == 28
    assert out["byStatus"] == {"CellGrowing": 13, "CellSown": 12, "CellPlanned": 3}


def test_proposals_sow_what_is_planned_and_look_after_what_is_planted(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    ps = {p["id"]: p for p in engine.proposals(cfg)}
    assert {"plant-5_13", "plant-5_15", "plant-8_22", "probe", "photo"} == set(ps)
    assert ps["plant-5_13"]["spec"] == {"kind": "plant", "row": 5, "col": 13, "crop": "crop-rocket", "depthMm": 5, "seeds": 3}
    assert "row 6, column 14" in ps["plant-5_13"]["title"]
    assert len(ps["photo"]["spec"]["cells"]) == 25


def test_a_sown_cell_is_not_proposed_again_and_dry_cells_are_watered(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    live = {"cells": {"5_13": {"status": "CellSown", "moisture": 34.0}, "1_1": {"status": "CellGrowing", "moisture": 12.0}}}
    ps = {p["id"]: p for p in engine.proposals(cfg, live)}
    assert "plant-5_13" not in ps
    assert ps["water"]["spec"]["cells"] == [[1, 1, 223]]            # lettuce: a day's need
    assert [5, 13] in ps["probe"]["spec"]["cells"]


def test_no_world_no_proposals(tmp_path):
    assert engine.config(tmp_path) is None and engine.proposals(None) == []


def test_the_command_line(tmp_path, capsys):
    assert engine.main(["check"]) == 0
    assert engine.main(["jobs", "--out", str(tmp_path)]) == 1          # nothing prepared there
    assert engine.main(["world", "--out", str(tmp_path)]) == 0
    assert engine.main(["jobs", "--out", str(tmp_path)]) == 0
    assert "Sow Rocket at row 6, column 14" in capsys.readouterr().out


# ---------------------------------------------------------------- routes ----

def test_the_page_and_the_status_before_anything_is_prepared(client):
    assert client.get("/").status_code == 200
    t = client.get("/api/twin").get_json()
    assert t["prepared"] is False and t["running"] is False and t["proposals"] == []


def test_start_needs_a_world(client):
    r = client.post("/api/twin/start", json={})
    assert r.status_code == 400 and "prepare a world first" in r.get_json()["errors"][0]


def test_prepare_then_the_status_carries_the_bed_and_the_proposals(client):
    assert client.post("/api/twin/prepare", json={}).status_code == 200
    t = client.get("/api/twin").get_json()
    assert t["prepared"] and t["bed"]["cols"] == 30 and len(t["config"]["cells"]) == 28
    assert any(p["id"] == "plant-5_13" for p in t["proposals"])


def test_a_bad_habitat_is_refused_with_what_is_wrong(client):
    r = client.post("/api/twin/prepare", json={"ttl": "nonsense"})
    assert r.status_code == 400 and "not valid Turtle" in r.get_json()["errors"][0]


def test_confirming_a_proposal_sends_exactly_its_job(client, twin):
    _, calls = twin
    client.post("/api/twin/prepare", json={})          # refused while "running"
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    r = client.post("/api/twin/job", json={"proposal": "plant-5_13"})
    assert r.status_code == 200 and r.get_json() == {"id": "j1"}
    assert calls == [("/job", {"kind": "plant", "row": 5, "col": 13, "crop": "crop-rocket", "depthMm": 5, "seeds": 3})]


def test_a_proposal_that_is_no_longer_current_is_refused(client, twin):
    state, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    state["twin"]["bed"]["cells"]["5_13"]["status"] = "CellSown"      # someone sowed it meanwhile
    r = client.post("/api/twin/job", json={"proposal": "plant-5_13"})
    assert r.status_code == 409 and calls == []


def test_nothing_is_sent_without_a_proposal_or_a_spec(client, twin):
    _, calls = twin
    assert client.post("/api/twin/job", json={}).status_code == 400 and calls == []


def test_only_the_two_cameras_exist(client):
    assert client.get("/api/twin/camera/other.jpg").status_code == 404
    assert client.get("/api/twin/camera/tool.png").status_code == 404
    assert client.get("/api/twin/camera/tool.jpg").status_code == 503      # no simulation: no pictures


def test_a_synced_habitat_is_the_source_and_gets_the_sowing_written_back(client, twin):
    """Prepared from an area this edge holds: a finished sowing lands in that area's A-Box."""
    from rdflib.namespace import RDF
    from permtek5_twin_edge import habitat as hb
    from permtek5_twin_edge.edge import knowledge as kn
    state, _ = twin
    web.sim.running = lambda: False
    kn.KnowledgeBase().change("shared", lambda g: g.parse(hb.CITYDEMO, format="turtle"))
    assert client.post("/api/twin/prepare", json={"graph": "elsewhere"}).status_code == 400
    assert client.post("/api/twin/prepare", json={"graph": "shared"}).status_code == 200
    t = client.get("/api/twin").get_json()
    assert t["source"] == "shared" and t["habitats"] == [{"graph": "shared", "beds": 1, "cells": 28}] and t["recorded"] == []
    state["twin"]["history"] = [{"id": "j9", "kind": "plant", "state": "done", "result": {"sown": True},
                                 "spec": {"row": 5, "col": 13, "crop": "crop-rocket"}}]
    assert len(client.get("/api/twin").get_json()["recorded"]) == 1
    assert client.get("/api/twin").get_json()["recorded"] == []            # once
    g = kn.KnowledgeBase().load("shared")
    sown = [c for c in g.subjects(RDF.type, hb.PERMA.PlantingCell)
            if int(g.value(c, hb.PERMA.row)) == 5 and int(g.value(c, hb.PERMA.col)) == 13]
    assert g.value(sown[0], hb.PERMA.cellStatus) == hb.PERMA.CellSown and g.value(sown[0], hb.PERMA.sownOn) is not None


def test_how_it_works_is_served_and_the_shell_links_it(client):
    how = client.get("/how")
    assert how.status_code == 200 and b"How the PermTek-5 twin edge works" in how.data
    page = client.get("/").get_data(as_text=True)
    assert 'href="/how"' in page and 'id="rail"' in page and 'id="tab-connections"' in page
    assert "/edge-static/edge.js" in page and "/edge-static/edge.css" in page, "the edge layer's panels need their script"
    assert client.get("/favicon.ico").status_code == 200
    model = client.get("/model")
    assert model.status_code == 200 and b"The PermTek-5 data model" in model.data and 'href="/model"' in page


def test_the_drawing_and_its_source_agree():
    """how.html is delivered from docs/how-it-works.architecture.json; a changed source needs a new delivery."""
    import json
    from pathlib import Path
    root = Path(web.__file__).resolve().parent
    spec = json.loads((root.parent / "docs" / "how-it-works.architecture.json").read_text(encoding="utf-8"))
    html = (root / "static" / "how.html").read_text(encoding="utf-8")
    for c in spec["components"]:
        assert c["label"] in html, c["label"]


def test_the_film_needs_a_prepared_world_and_serves_only_its_two_files(client, tmp_path):
    f = client.get("/api/twin/film").get_json()
    assert f["prepared"] is False and f["film"] is None and f["job"] is None
    assert client.post("/api/twin/film", json={}).status_code == 400
    assert client.get("/api/twin/film/film.mp4").status_code == 404
    assert client.get("/api/twin/film/other.txt").status_code == 404
    out = engine.workspace() / "demo"
    out.mkdir(parents=True)
    (out / "poster.png").write_bytes(b"png")
    assert client.get("/api/twin/film/poster.png").data == b"png"
    assert client.get("/api/twin/film").get_json()["poster"]["bytes"] == 3


# -------------------------------------------------- the bed as a system ----

LIVE = {"cells": {"1_1": {"status": "CellGrowing", "moisture": 12.0}, "10_10": {"status": "CellSown", "moisture": 12.0}},
        "tank": {"litres": 120.0, "capacity": 202.0}, "hopper": {"grams": 1000},
        "wormBin": {"tempC": 19.0, "moisture": 70.0, "fillPct": 30.0, "feedings": 5}}


def test_feeding_is_proposed_only_with_scraps_in_the_hopper_and_rotates_its_spot(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    assert "feed" not in {p["id"] for p in engine.proposals(cfg, {**LIVE, "hopper": {"grams": 0}})}
    feed = next(p for p in engine.proposals(cfg, LIVE) if p["id"] == "feed")
    assert feed["spec"] == {"kind": "feed", "grams": 300, "spot": 1}          # 5 feedings so far, 4 spots
    plain = dict(cfg, wormBin={"cols": 0})
    assert "feed" not in {p["id"] for p in engine.proposals(plain, LIVE)}, "a bed without a worm box is never fed"


def test_with_water_in_the_tank_only_what_was_just_sown_is_watered_from_above(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    water = next(p for p in engine.proposals(cfg, LIVE) if p["id"] == "water")
    assert [c[:2] for c in water["spec"]["cells"]] == [[10, 10]], "the grown lettuce drinks from below"
    dry_tank = {**LIVE, "tank": {"litres": 0.0, "capacity": 202.0}}
    water = next(p for p in engine.proposals(cfg, dry_tank) if p["id"] == "water")
    assert sorted(c[:2] for c in water["spec"]["cells"]) == [[1, 1], [10, 10]]


def test_notices_are_for_the_person_not_jobs(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    assert engine.notices(cfg, LIVE) == []
    assert any("low" in n for n in engine.notices(cfg, {**LIVE, "tank": {"litres": 10.0, "capacity": 202.0}}))
    assert any("empty" in n for n in engine.notices(cfg, {**LIVE, "tank": {"litres": 0.0, "capacity": 202.0}}))
    assert any("hot" in n for n in engine.notices(cfg, {**LIVE, "wormBin": {**LIVE["wormBin"], "tempC": 32.0}}))
    assert any("cold" in n for n in engine.notices(cfg, {**LIVE, "wormBin": {**LIVE["wormBin"], "tempC": 3.0}}))
    assert engine.notices(cfg, None) == []


def test_the_persons_buttons_tell_the_twin_what_was_done(client, twin):
    _, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    assert client.post("/api/twin/hopper", json={"grams": 800}).status_code == 200
    assert client.post("/api/twin/tank", json={}).status_code == 200
    assert client.post("/api/twin/tank", json={"litres": 9999}).status_code == 200
    assert calls == [("/event", {"op": "hopper", "grams": 800.0}), ("/event", {"op": "tank", "litres": 202.0}),
                     ("/event", {"op": "tank", "litres": 202.0})], "the tank never holds more than its pipes"
    assert client.post("/api/twin/hopper", json={"grams": "many"}).status_code == 400
