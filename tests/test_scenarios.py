# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenarios: the files are sound, readings and checks are right, a run goes step by step."""

from __future__ import annotations

import time

import pytest

from permtek5_twin_edge import app as web
from permtek5_twin_edge import engine, scenarios, sim
from permtek5_twin_edge.core import version
from permtek5_twin_edge.core import workspace as ws

TWIN = {"gantry": {"x": 0, "y": 0, "z": 0}, "cameras": ["overview", "tool"], "job": None, "history": [],
        "bed": {"stopped": False, "tank": {"litres": 120.0, "capacity": 202.0}, "hopper": {"grams": 0},
                "wormBin": {"tempC": 18.0, "moisture": 70.0, "fillPct": 30.0, "feedings": 0},
                "cells": {"1_1": {"status": "CellGrowing", "moisture": 44.0}, "5_13": {"status": "CellPlanned", "moisture": 35.0}}}}


@pytest.fixture(autouse=True)
def fresh():
    scenarios.stop()
    yield
    scenarios.stop()


def test_every_shipped_scenario_is_sound():
    found = scenarios.load_all()
    assert {"feed-worms", "sow-planned", "dry-spell", "stop"} <= set(found)
    assert len(found) == len(list(scenarios.FOLDER.glob("*.json"))), "a broken file would be left out silently"
    for s in found.values():
        assert 2 <= len(s["steps"]) <= 8 and len(s["about"]) > 60


def test_a_broken_scenario_says_everything_that_is_wrong():
    bad = {"title": "x", "steps": [{"title": "a", "why": "b", "do": {"type": "fly"}},
                                   {"title": "a", "why": "b", "do": {"type": "estop"}, "expect": [{"read": "x", "is": "~", "value": 1, "say": "no value"}]}]}
    found = scenarios.problems(bad)
    assert any("about" in p for p in found) and any("do.type" in p for p in found)
    assert any("label" in p for p in found) and any("expectation" in p for p in found)


def test_readings():
    since = time.time()
    twin = {**TWIN, "history": [{"kind": "plant", "state": "failed", "started": since - 100}, {"kind": "plant", "state": "done", "started": since + 1}]}
    props = [{"id": "plant-5_13"}, {"id": "plant-5_15"}, {"id": "feed"}]
    r = lambda path: scenarios.read(path, twin, props, ["refill"], since)       # noqa: E731
    assert r("bed.tank.litres") == 120.0 and r("bed.wormBin.feedings") == 0 and r("bed.nothing.here") is None
    assert r("last.plant.state") == "done" and r("last.photo.state") is None, "only jobs since the step began count"
    assert r("proposals.plant") == 2 and r("count.CellGrowing") == 1 and r("driest") == 44.0 and r("notices") == 1


def test_needs_decide_what_a_bed_can_run():
    s = scenarios.load_all()
    assert scenarios.available(s["feed-worms"], None) == "prepare a world first"
    assert "worm box" in scenarios.available(s["feed-worms"], {"wormBin": {"cols": 0}, "tank": {"litres": 100}})
    assert "tank" in scenarios.available(s["dry-spell"], {"wormBin": {"cols": 4}, "tank": {"litres": 0}})
    assert scenarios.available(s["stop"], {"wormBin": {"cols": 0}, "tank": {"litres": 0}}) is None


def test_a_run_goes_step_by_step_and_waits_for_what_it_expects():
    cfg = {"wormBin": {"cols": 4, "spots": 4}, "tank": {"litres": 202}}
    scenarios.start("feed-worms", cfg)
    st = scenarios.state(TWIN, [], [])
    assert st["index"] == 0 and st["step"]["type"] == "event" and not st["did"] and not st["ready"]
    assert scenarios.action([]) == {"events": [{"op": "hopper", "grams": 1000}]}
    scenarios.mark_done()
    assert not scenarios.state(TWIN, [], [])["ready"], "done, but the hopper does not show it yet"
    filled = {**TWIN, "bed": {**TWIN["bed"], "hopper": {"grams": 1000}}}
    assert scenarios.state(filled, [], [])["ready"]
    assert scenarios.advance() is True
    with pytest.raises(ValueError, match="does not propose"):
        scenarios.action([])                                             # no feed proposal: nothing is sent
    feed = [{"id": "feed", "spec": {"kind": "feed", "grams": 300, "spot": 0}}]
    assert scenarios.action(feed) == {"job": {"kind": "feed", "grams": 300, "spot": 0}}
    scenarios.mark_done()
    working = {**filled, "job": {"kind": "feed", "state": "running"}}
    assert scenarios.state(working, feed, [])["busy"] and not scenarios.state(working, feed, [])["ready"]


def test_a_limit_shortens_a_long_job_and_a_prefix_takes_the_first_proposal():
    scenarios.start("sow-planned", {"bed": {}})
    scenarios.advance()
    props = [{"id": "plant-5_13", "spec": {"kind": "plant", "row": 5, "col": 13}}, {"id": "plant-5_15", "spec": {"kind": "plant", "row": 5, "col": 15}},
             {"id": "photo", "spec": {"kind": "photo", "cells": [[1, 1], [1, 4], [1, 7], [4, 1], [4, 4], [4, 7]]}}]
    assert scenarios.action(props)["job"]["col"] == 13
    scenarios.advance(); scenarios.advance()
    assert len(scenarios.action(props)["job"]["cells"]) == 4 and len(props[2]["spec"]["cells"]) == 6, "the proposal itself is not changed"


def test_the_last_step_ends_the_run():
    scenarios.start("stop", {"bed": {}})
    assert scenarios.advance() and scenarios.advance()
    assert scenarios.state(TWIN, [], [])["last"] is True
    assert scenarios.advance() is False and scenarios.current() is None


# ---------------------------------------------------------------- routes ----

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "UPDATE_CHECK", False)
    monkeypatch.setitem(ws.FOLDERS, ws.USER, tmp_path / "user")
    monkeypatch.setitem(ws.FOLDERS, ws.SHARED, tmp_path / "shared")
    ws.configure()
    web.app.config["TESTING"] = True
    return web.app.test_client()


def test_the_routes_walk_a_scenario_against_a_stood_in_twin(client, monkeypatch):
    calls = []
    twin = {k: (dict(v) if isinstance(v, dict) else v) for k, v in TWIN.items()}
    twin["bed"] = {**TWIN["bed"], "hopper": {"grams": 0}}
    state = {"running": True, "missing": [], "gui": False, "started": 1.0, "twin": twin}

    def gateway(path, body=None, timeout=3.0):
        calls.append((path, body))
        if path == "/event" and body.get("op") == "hopper":
            twin["bed"]["hopper"] = {"grams": body["grams"]}
        return {"ok": True}

    monkeypatch.setattr(sim, "status", lambda: state)
    monkeypatch.setattr(sim, "gateway", gateway)
    monkeypatch.setattr(sim, "running", lambda: False)
    client.post("/api/twin/prepare", json={})
    assert client.post("/api/twin/scenario", json={"id": "feed-worms"}).status_code == 409        # not running
    monkeypatch.setattr(sim, "running", lambda: True)
    assert client.post("/api/twin/scenario", json={"id": "nope"}).status_code == 400
    v = client.post("/api/twin/scenario", json={"id": "feed-worms"}).get_json()
    assert v["run"]["index"] == 0 and v["run"]["ready"] is False
    assert client.post("/api/twin/scenario/next", json={}).status_code == 409, "a step is not skipped by accident"
    v = client.post("/api/twin/scenario/do", json={}).get_json()
    assert calls[-1] == ("/event", {"op": "hopper", "grams": 1000}) and v["run"]["ready"] is True
    v = client.post("/api/twin/scenario/next", json={}).get_json()
    assert v["run"]["index"] == 1 and v["run"]["step"]["type"] == "proposal"
    v = client.post("/api/twin/scenario/do", json={}).get_json()
    assert calls[-1] == ("/job", {"kind": "feed", "grams": 300, "spot": 0})
    assert client.post("/api/twin/scenario/stop", json={}).get_json()["run"] is None
    listed = client.get("/api/twin/scenarios").get_json()["scenarios"]
    assert all(s["unavailable"] is None for s in listed if s["id"] != "year-of-periods"), "CityDemo has a tank and a worm box"
    assert next(s for s in listed if s["id"] == "year-of-periods")["unavailable"], "but no periods"
