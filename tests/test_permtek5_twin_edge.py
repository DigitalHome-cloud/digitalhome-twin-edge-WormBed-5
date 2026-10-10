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


def test_only_the_two_cameras_exist(client, monkeypatch):
    monkeypatch.setattr(sim, "GATEWAY_PORT", 9)          # never a twin that happens to run on this machine
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


def test_a_kind_of_job_the_keeper_holds_is_not_proposed(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    kinds = {p["kind"] for p in engine.proposals(cfg, LIVE)}
    assert "water" in kinds
    assert "water" not in {p["kind"] for p in engine.proposals(cfg, LIVE, holds=["water"])}


def test_chaos_from_the_page_goes_to_the_twin(client, twin):
    state, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    assert client.post("/api/twin/disturb", json={"id": "storm"}).status_code == 200
    assert calls[-1][0] == "/event" and calls[-1][1]["op"] == "disturb" and calls[-1][1]["id"] == "storm"
    assert client.post("/api/twin/disturb", json={"id": "locusts"}).status_code == 400
    assert client.post("/api/twin/time", json={"factor": 30}).status_code == 200
    assert calls[-1] == ("/event", {"op": "time", "factor": 30.0})
    assert client.post("/api/twin/chaos", json={"rate": 4}).status_code == 200
    assert calls[-1][1]["op"] == "chaos"
    assert client.post("/api/twin/calm", json={}).status_code == 200
    assert calls[-1] == ("/event", {"op": "calm"})
    listed = client.get("/api/twin/chaos").get_json()["disturbances"]
    assert {"cold-snap", "storm", "network-loss"} <= {d["id"] for d in listed}


def test_network_loss_is_the_apps_own_trouble(client, twin):
    _, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    before = len(calls)
    assert client.post("/api/twin/disturb", json={"id": "network-loss"}).status_code == 200
    assert len(calls) == before, "nothing is sent to the simulation"
    t = client.get("/api/twin").get_json()
    assert "offline" in t["keeper"]["active"]
    client.post("/api/twin/calm", json={})
    assert "offline" not in client.get("/api/twin").get_json()["keeper"]["active"]


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


def test_a_job_is_refused_while_the_wind_locks_the_gantry_out(client, twin):
    state, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    state["twin"]["bed"]["env"] = {"airC": 15.0, "windMs": 21.0, "rainMmH": 0.0, "clock": "day 1 · 10:00", "disturbances": []}
    before = len(calls)
    r = client.post("/api/twin/job", json={"spec": {"kind": "photo"}})
    assert r.status_code == 409 and "wind" in r.get_json()["errors"][0].lower()
    assert len(calls) == before, "nothing reached the gantry"
    assert client.get("/api/twin").get_json()["proposals"] == []
    state["twin"]["bed"]["env"]["windMs"] = 2.0
    assert client.post("/api/twin/job", json={"spec": {"kind": "photo"}}).status_code == 200


def _box_twin(state, **hopper):
    """Give the stand-in twin a worm box with sensors, as the simulation reports it."""
    state["twin"]["bed"].update({
        "env": {"day": 1, "hour": 10.0, "airC": 16.0, "windMs": 2.0, "rainMmH": 0.0, "clock": "day 1 · 10:00", "disturbances": []},
        "tank": {"litres": 120.0, "capacity": 202.0},
        "wormBin": {"tempC": 18.0, "edgeC": 17.0, "moisture": 70.0, "weightKg": 6.2, "gas": 0.0, "pH": 6.9, "sumpL": 0.1,
                    "sumpCapacity": 14.4, "lidOpen": False, "fillPct": 31.0, "fedGrams": 0, "feedings": 0, "lastFeed": None},
        "hopper": {"grams": 1000, "jammed": False, **hopper}})


def test_the_feeding_guide_refuses_a_load_with_cooked_food_and_lets_a_good_one_through(client, twin):
    state, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    web._diary.clear()
    web._guide_seen.update(now=0.0, feedN=0, load=None)
    _box_twin(state, mix={"vegetable": 500, "cooked": 200, "citrus": 300})
    t = client.get("/api/twin").get_json()
    assert "guide-load" in t["keeper"]["active"] and "feed" in t["keeper"]["holds"]
    assert any("cooked or salted food" in n for n in t["notices"])
    assert "feed" not in {p["kind"] for p in t["proposals"]}
    r = client.post("/api/twin/job", json={"spec": {"kind": "feed", "grams": 300, "spot": 0}})
    assert r.status_code == 409 and "cooked" in r.get_json()["errors"][0], "a feed job sent anyway is refused"
    assert t["guide"]["week"]["refusedLoads"] == 1
    _box_twin(state, mix={"vegetable": 600, "fruit": 300, "coffee": 100})
    t = client.get("/api/twin").get_json()
    assert "guide-load" not in t["keeper"]["active"] and "feed" in {p["kind"] for p in t["proposals"]}


def test_a_feeding_goes_into_the_diary_and_the_next_one_waits_half_a_day(client, twin):
    state, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    web._diary.clear()
    web._guide_seen.update(now=0.0, feedN=0, load=None)
    _box_twin(state, mix={"vegetable": 850})
    box = state["twin"]["bed"]["wormBin"]
    box.update(feedings=1, fedGrams=150, lastFeed={"n": 1, "grams": 150, "spot": 0, "mix": {"vegetable": 150}, "atMin": 600.0})
    t = client.get("/api/twin").get_json()
    assert t["guide"]["feed"]["rule"] == "rest" and t["guide"]["week"]["scrapsG"] == 150
    assert "feed" not in {p["kind"] for p in t["proposals"]} and t["notices"] == [], "it waits, without nagging"
    assert client.post("/api/twin/job", json={"spec": {"kind": "feed", "grams": 150, "spot": 1}}).status_code == 409
    state["twin"]["bed"]["env"].update(day=2, hour=0.0)                       # fourteen hours later
    t = client.get("/api/twin").get_json()
    assert t["guide"]["feed"]["ok"] and "feed" in {p["kind"] for p in t["proposals"]}


def test_the_page_can_say_what_went_into_the_hopper(client, twin):
    _, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    assert client.post("/api/twin/hopper", json={"mix": {"vegetable": 600, "fruit": 300}}).status_code == 200
    assert calls[-1] == ("/event", {"op": "hopper", "mix": {"vegetable": 600.0, "fruit": 300.0}, "grams": 900.0})
    g = client.get("/api/twin/guide").get_json()
    assert {"vegetable", "citrus", "meat", "paper"} <= {k["id"] for k in g["kinds"]} and any("12 hours" in r for r in g["rules"])


def _care_state(**box):
    return {"cells": {"1_1": {"status": "CellGrowing", "crop": "crop-lettuce", "moisture": 45.0, "compostG": 0},
                      "1_4": {"status": "CellGrowing", "crop": "crop-lettuce", "moisture": 45.0, "compostG": 50},
                      "5_13": {"status": "CellPlanned", "crop": "crop-rocket", "moisture": 35.0, "compostG": 0}},
            "tank": {"litres": 120.0, "capacity": 202.0}, "hopper": {"grams": 0, "brownsGrams": 800},
            "wormBin": {"weightKg": 6.2, "moisture": 70.0, "sumpL": 1.0, "sumpCapacity": 14.4, "feedings": 3,
                        "harvest": {"side": None, "ready": False, "readyAfterDays": 10.0, "nextSide": 1}, **box}}


def test_the_box_gets_its_care_proposed_bedding_water_the_tap_a_rest_and_compost_back(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    ids = lambda state, **kw: {p["id"]: p for p in engine.proposals(cfg, state, **kw)}   # noqa: E731
    calm = ids(_care_state())
    assert "rest" in calm and calm["rest"]["spec"] == {"kind": "rest", "side": 1}
    assert not {"bedding", "moisten", "tap", "topdress"} & set(calm)
    due = ids(_care_state(), guide={"brownsDue": 450})
    assert due["bedding"]["spec"] == {"kind": "care", "what": "bedding", "grams": 450, "spot": 3}
    assert "moisten" in ids(_care_state(), active=["box-dry"])
    assert "tap" in ids(_care_state(sumpL=8.0)) and "tap" not in ids({**_care_state(sumpL=8.0), "tank": {"litres": 202.0, "capacity": 202.0}})
    resting = _care_state(harvest={"side": 1, "ready": True, "readyAfterDays": 10.0, "nextSide": 1})
    found = ids(resting)
    assert "rest" not in found and found["topdress"]["spec"]["cells"] == [[1, 1], [1, 4]], "the cell that has had least comes first"
    # while one half rests, food goes only to the other half's spots
    assert [engine.next_spot(cfg, {"wormBin": {"feedings": n, "harvest": {"side": 1}}}) for n in range(4)] == [0, 1, 0, 1]
    assert [engine.next_spot(cfg, {"wormBin": {"feedings": n, "harvest": {"side": None}}}) for n in range(4)] == [0, 1, 2, 3]
    assert "bedding" not in {p["id"] for p in engine.proposals(cfg, _care_state(), guide={"brownsDue": 450}, holds=["care"])}


def test_the_tap_and_the_resting_half_are_switches_not_gantry_jobs(client, twin):
    _, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    assert client.post("/api/twin/job", json={"spec": {"kind": "tap"}}).status_code == 200
    assert calls[-1] == ("/event", {"op": "box", "do": "tap"})
    assert client.post("/api/twin/job", json={"spec": {"kind": "rest", "side": 1}}).status_code == 200
    assert calls[-1] == ("/event", {"op": "box", "do": "rest", "side": 1})
    assert client.post("/api/twin/hopper", json={"browns": 1000}).status_code == 200
    assert calls[-1] == ("/event", {"op": "hopper", "browns": 1000.0})


def test_small_weeds_clear_of_plants_are_stamped_the_rest_are_for_hands(tmp_path):
    engine.prepare(engine.habitat_ttl(None), tmp_path)
    cfg = engine.config(tmp_path)
    grown = next(c for c in cfg["cells"] if c["status"] == "CellGrowing")
    px, py = (grown["col"] + 0.5) * 0.1, (grown["row"] + 0.5) * 0.1
    found = [{"x": 2.2, "y": 0.05, "sizeMm": 15}, {"x": 2.3, "y": 0.06, "sizeMm": 52}, {"x": px + 0.05, "y": py, "sizeMm": 12}]
    sorted_ = engine.sort_weeds(cfg, None, found)
    assert [(w["do"], w["why"]) for w in sorted_] == [("stamp", ""), ("hand", "large"), ("hand", "close")]
    state = {**LIVE, "sim": {"weeds": 3}}
    ps = {p["id"]: p for p in engine.proposals(cfg, state, weeds={"found": sorted_, "when": "day 1 · 10:00"})}
    assert ps["stamp"]["spec"] == {"kind": "stamp", "places": [[2.2, 0.05]]} and "day 1" in ps["scan"]["detail"]
    assert "stamp" not in {p["id"] for p in engine.proposals(cfg, state, weeds={"found": [], "when": ""})}
    assert "scan" not in {p["id"] for p in engine.proposals(cfg, LIVE)}, "a twin without weeds is not looked over"


def test_a_look_over_the_bed_is_remembered_and_stamping_takes_weeds_off_the_list(client, twin):
    state, calls = twin
    web.sim.running = lambda: False
    client.post("/api/twin/prepare", json={})
    web._weeds.update(scan=None, stamp=None, found=[], when="")
    state["twin"]["bed"].update({"sim": {"weeds": 3, "weedsLarge": 1},
                                 "env": {"day": 1, "hour": 10.0, "airC": 16.0, "windMs": 2.0, "rainMmH": 0.0, "clock": "day 1 · 10:00", "disturbances": []}})
    t = client.get("/api/twin").get_json()
    assert t["weeds"]["scanned"] is False and "scan" in {p["id"] for p in t["proposals"]}
    state["twin"]["history"] = [{"id": "j1", "kind": "scan", "state": "done", "started": 10.0, "spec": {"kind": "scan"},
                                 "result": {"pictures": 27, "weeds": [{"x": 2.2, "y": 0.05, "sizeMm": 15}, {"x": 2.3, "y": 0.06, "sizeMm": 52}]}}]
    t = client.get("/api/twin").get_json()
    assert (t["weeds"]["small"], t["weeds"]["byHand"]) == (1, 1) and "weeds" in t["keeper"]["active"]
    assert any("too big for the stamp" in n and "worm box" in n for n in t["notices"])
    stamp = next(p for p in t["proposals"] if p["id"] == "stamp")
    state["twin"]["history"].append({"id": "j2", "kind": "stamp", "state": "done", "started": 20.0, "spec": stamp["spec"], "result": {"stamped": 1, "skipped": 0}})
    t = client.get("/api/twin").get_json()
    assert (t["weeds"]["small"], t["weeds"]["byHand"]) == (0, 1) and "stamp" not in {p["id"] for p in t["proposals"]}
