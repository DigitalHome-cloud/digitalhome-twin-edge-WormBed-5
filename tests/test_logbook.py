# SPDX-License-Identifier: GPL-3.0-or-later
"""The guild's logbook (who did what, when) and the autopilot that does a person's part in a rehearsal."""

from __future__ import annotations

import pytest

from permtek5_twin_edge import app as web
from permtek5_twin_edge import engine, logbook, sim
from permtek5_twin_edge.core import version
from permtek5_twin_edge.core import workspace as ws


def test_entries_are_numbered_kept_in_order_and_filtered(tmp_path):
    logbook.write(tmp_path, "person", "hopper", "filled the hopper: 1000 g", "2026-04-02 · day 2 · 10:00")
    logbook.write(tmp_path, "robot", "job.done", "plant at row 1, column 1: done")
    logbook.write(tmp_path, "nobody", "x", "who is not known: the edge")
    all_ = logbook.read(tmp_path)
    assert [e["n"] for e in all_] == [1, 2, 3] and all_[0]["sim"].startswith("2026-04-02") and all_[2]["who"] == "edge"
    assert [e["n"] for e in logbook.read(tmp_path, after=1)] == [2, 3]
    assert [e["who"] for e in logbook.read(tmp_path, who=["robot"])] == ["robot"]
    assert logbook.last(tmp_path) == 3


def test_the_follower_writes_what_is_new_and_nothing_on_its_first_look(tmp_path):
    f = logbook.Follower()
    cfg = {"cells": []}
    twin = {"history": [{"id": "j1", "kind": "plant", "state": "done", "spec": {"row": 0, "col": 0, "crop": "crop-basil"}}],
            "bed": {"env": {"clock": "day 1 · 10:00"}, "cells": {"0_0": {"status": "CellSown", "crop": "crop-basil"}}}}
    assert f.follow(tmp_path, cfg, twin, ["the tank is low"]) == [], "what was there before is not news"
    twin["history"].append({"id": "j2", "kind": "water", "state": "failed", "spec": {}})
    twin["bed"]["cells"]["0_0"] = {"status": "CellGrowing", "crop": "crop-basil", "up": True}
    twin["bed"]["cells"]["0_1"] = {"status": "CellSown", "crop": "crop-basil"}
    new = f.follow(tmp_path, cfg, twin, ["the tank is low", "the box is cold"], lambda c: "Basil")
    assert [(e["who"], e["what"]) for e in new] == [("robot", "job.failed"), ("plants", "plant.up"), ("keeper", "notice")]
    assert new[1]["text"] == "Basil came up at row 1, column 1"
    twin["bed"]["cells"]["0_0"] = {"status": "CellLost", "crop": "crop-basil", "why": "frost", "up": True}
    assert f.follow(tmp_path, cfg, twin, [], lambda c: "Basil")[0]["text"] == "Basil was lost at row 1, column 1 (frost)"


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
    calls = []
    state = {"running": True, "missing": [], "gui": False, "started": 1.0,
             "twin": {"gantry": {"x": 0.0, "y": 0.0, "z": 0.0}, "cameras": [], "job": None, "history": [],
                      "bed": {"env": {"day": 1, "clock": "day 1 · 10:00"}, "hopper": {"grams": 0, "brownsGrams": 1000},
                              "tank": {"litres": 150.0, "capacity": 202.0},
                              "cells": {"5_13": {"status": "CellPlanned", "crop": "crop-rocket", "moisture": 35.0}}}}}
    monkeypatch.setattr(sim, "status", lambda: state)
    monkeypatch.setattr(sim, "running", lambda: state["running"])
    monkeypatch.setattr(sim, "gateway", lambda path, body=None, timeout=3.0: calls.append((path, body)) or {"id": "j1"})
    return state, calls


def test_a_persons_acts_through_the_page_go_into_the_logbook(client, twin):
    state, _ = twin
    state["running"] = False
    assert client.post("/api/twin/prepare", json={}).status_code == 200
    state["running"] = True
    assert client.post("/api/twin/hopper", json={"mix": {"vegetable": 600, "fruit": 400}}).status_code == 200
    assert client.post("/api/twin/tank", json={}).status_code == 200
    assert client.post("/api/twin/hopper", json={"grams": "x"}).status_code == 400
    texts = [(e["who"], e["text"]) for e in client.get("/api/logbook").get_json()["entries"]]
    assert ("person", "prepared a world from CityDemo, start clean") in texts
    assert ("person", "filled the hopper: vegetable 600 g, fruit 400 g") in texts
    assert any(t.startswith("refilled the tank") for _, t in texts)
    assert len([t for _, t in texts if "hopper" in t]) == 1, "a refused request is not written"


def test_the_autopilot_does_a_persons_part_then_the_robots_jobs_and_stops_with_the_twin(client, twin):
    state, calls = twin
    state["running"] = False
    client.post("/api/twin/prepare", json={})
    assert client.post("/api/twin/autopilot", json={"on": True}).status_code == 409, "not while nothing runs"
    state["running"] = True
    web._auto.update(on=True, seen={})            # on, without its thread: the test ticks it by hand
    web._auto_tick()
    assert calls[-1][1]["op"] == "hopper", "the empty hopper first"
    state["twin"]["bed"]["hopper"]["grams"] = 1000
    for _ in range(8):                             # the person's care tasks due, one a tick, then the robot's jobs
        web._auto_tick()
    assert ("/job", {"kind": "plant", "row": 5, "col": 13, "crop": "crop-rocket", "depthMm": 5, "seeds": 3}) in calls or \
        any(c[0] == "/job" for c in calls), "then the robot's proposals"
    autopilot = [e["text"] for e in client.get("/api/logbook?who=autopilot").get_json()["entries"]]
    assert autopilot[0].startswith("filled the hopper")
    assert any(t.startswith("done: ") for t in autopilot), "a care task that is yours"
    assert any(t.startswith("confirmed: ") for t in autopilot)
    state["running"] = False
    web._auto_tick()
    assert web._auto["on"] is False and "stopped" in web._auto["why"]
    assert client.get("/api/twin").get_json()["autopilot"]["on"] is False


def test_the_autopilot_never_runs_on_a_real_bed(client, twin, monkeypatch):
    state, _ = twin
    state["running"] = False
    client.post("/api/twin/prepare", json={})
    state["running"] = True
    real = {**engine.config(), "rehearsal": False}
    monkeypatch.setattr(engine, "config", lambda out=None: real)
    assert client.post("/api/twin/autopilot", json={"on": True}).status_code == 409


def test_the_monitoring_skill_ships_with_the_plugin_and_the_repository_alike():
    from pathlib import Path
    root = Path(web.__file__).resolve().parent.parent
    plugin = (root / "plugin" / "skills" / "permtek5-twin-logbook" / "SKILL.md").read_text(encoding="utf-8")
    assert plugin == (root / ".claude" / "skills" / "permtek5-twin-logbook" / "SKILL.md").read_text(encoding="utf-8")
    assert plugin.startswith("---\nname: permtek5-twin-logbook\ndescription: ")
    for tool in ("logbook(", "twin()", "/api/logbook"):
        assert tool in plugin
