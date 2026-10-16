# SPDX-License-Identifier: GPL-3.0-or-later
"""A twin run recorded as it goes (track.py), and written as an OpenUSD stage to replay (usd_stage.py)."""

from __future__ import annotations

import json

import pytest

from permtek5_twin_edge import habitat as hb, track, usd_stage, world

pxr = pytest.importorskip("pxr", reason="usd-core checks the stages (requirements-dev.txt)")
from pxr import Usd, UsdGeom  # noqa: E402


def status(day: float, cells: dict, job: str = "", gx: float = 0.0) -> dict:
    return {"running": True, "twin": {"gantry": {"x": gx, "y": 0.2, "z": 0.0}, "job": {"state": job} if job else None,
                                      "bed": {"env": {"day": int(day), "hour": (day % 1) * 24, "date": "2026-04-%02d" % int(day), "clock": f"day {int(day)}", "sun": 0.7},
                                              "cells": cells}}}


def test_the_recorder_writes_an_hourly_sample_and_only_what_changed(tmp_path):
    cfg = {"bed": {"id": "bed-1", "name": "Roof bed"}, "period": {"name": "Summer"}, "start": "clean"}
    rec = track.Recorder(tmp_path, lambda: {}, lambda: cfg)
    rec.start()
    rec.stop()
    planned = {"0_0": {"status": "CellPlanned"}, "0_1": {"status": "CellPlanned"}}
    assert rec.look(status(1.0, planned))["cells"].keys() == {"0_0", "0_1"}, "the first look writes every cell"
    assert rec.look(status(1.01, planned)) is None, "nothing changed and not an hour yet"
    assert rec.look(status(1.01, planned, job="running", gx=0.5))["gantry"][0] == 0.5, "the gantry at work is followed"
    assert "cells" not in rec.look(status(1.06, planned)), "an hour on: a sample, no cells"
    sown = {**planned, "0_0": {"status": "CellSown"}}
    assert rec.look(status(1.07, sown))["cells"] == {"0_0": {"status": "CellSown", "growth": 0.0, "up": False, "ready": False}}
    tr = track.read(tmp_path)
    assert tr["head"]["bed"] == "bed-1" and tr["head"]["period"]["name"] == "Summer" and len(tr["samples"]) == 4
    s = track.summary(tmp_path)
    assert s["byStatus"] == {"CellSown": 1, "CellPlanned": 1} and s["from"] == "2026-04-01"
    rec.start()
    assert track.read(tmp_path)["samples"] == [], "a new run, a new track"


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    out = tmp_path_factory.mktemp("guild")
    world.generate(hb.load_citydemo(), hb.load_library(), out)
    return json.loads((out / "bed.json").read_text())


def run_of(cfg) -> dict:
    keys = sorted(f'{c["row"]}_{c["col"]}' for c in cfg["cells"])
    k = next(k for k in keys if next(c for c in cfg["cells"] if f'{c["row"]}_{c["col"]}' == k)["status"] == "CellPlanned")
    first = {x: {"status": next(c["status"] for c in cfg["cells"] if f'{c["row"]}_{c["col"]}' == x), "growth": 0.5, "up": False, "ready": False}
             for x in keys}
    day = 1440.0
    return {"head": {"bed": cfg["bed"]["id"], "start": "as-is"}, "samples": [
        {"t": 0.0, "date": "2026-04-01", "gantry": [0.0, 0.0, 0.0], "sun": 0.4, "cells": first},
        {"t": 60.0, "date": "2026-04-01", "gantry": [1.2, 0.5, 0.08], "cells": {k: {"status": "CellSown", "growth": 0.0, "up": False, "ready": False}}},
        {"t": 10 * day, "date": "2026-04-11", "gantry": [0.0, 0.0, 0.0], "cells": {k: {"status": "CellGrowing", "growth": 0.1, "up": True, "ready": False}}},
        {"t": 60 * day, "date": "2026-05-31", "gantry": [0.0, 0.0, 0.0], "cells": {k: {"status": "CellGrowing", "growth": 1.0, "up": True, "ready": True}}},
        {"t": 70 * day, "date": "2026-06-10", "gantry": [0.0, 0.0, 0.0], "sun": 0.9,
         "cells": {k: {"status": "CellHarvested", "growth": 0.0, "up": False, "ready": False}}}]}, k


def test_the_stage_opens_and_holds_the_guild(cfg, tmp_path):
    run, _ = run_of(cfg)
    stage = Usd.Stage.Open(str(usd_stage.write(cfg, run, tmp_path / "run.usda")))
    assert stage.GetDefaultPrim().GetPath() == "/Guild" and UsdGeom.GetStageUpAxis(stage) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert [p.GetName() for p in stage.GetPrimAtPath("/Guild").GetChildren()] == ["Bed", "WormBox", "Gantry", "Plants", "Sun"]
    assert len(stage.GetPrimAtPath("/Guild/Plants").GetChildren()) == len(cfg["cells"])
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert len(meshes) > len(cfg["cells"]), "the shapes resolve: every plant, the soil, the box and the bridge have their mesh"
    assert stage.GetPrimAtPath("/Guild").GetCustomDataByKey("rehearsal") is True
    assert stage.GetEndTimeCode() == pytest.approx(70 * 24)
    first = cfg["cells"][0]
    placed = stage.GetPrimAtPath(f'/Guild/Plants/cell_{first["row"]}_{first["col"]}').GetAttribute("xformOp:translate").Get()
    assert placed[2] == pytest.approx(world.SOIL_Z), "on the soil, where the Webots world has it"


def test_a_plant_comes_grows_and_goes_with_the_run_and_the_gantry_moves(cfg, tmp_path):
    run, k = run_of(cfg)
    stage = Usd.Stage.Open(str(usd_stage.write(cfg, run, tmp_path / "run.usda")))
    p = stage.GetPrimAtPath(f"/Guild/Plants/cell_{k}")
    seen = lambda t: UsdGeom.Imageable(p).ComputeVisibility(t)                  # noqa: E731
    size = lambda t: p.GetAttribute("xformOp:scale").Get(t)[0]                 # noqa: E731
    assert seen(0) == "invisible", "planned: nothing stands"
    assert seen(1) == "inherited" and 0 < size(1) < size(240) < size(1440), "sown, then up and growing"
    assert seen(70 * 24) == "invisible" and size(70 * 24) == 0, "harvested"
    assert size(69 * 24) == pytest.approx(size(1440)), "held until the harvest: it does not shrink in between"
    bridge = stage.GetPrimAtPath("/Guild/Gantry/Bridge").GetAttribute("xformOp:translate")
    assert bridge.Get(1)[0] == pytest.approx(1.2) and bridge.Get(0.5)[0] == pytest.approx(0.6)


def test_a_film_spreads_the_run_over_its_frames(cfg, tmp_path):
    run, k = run_of(cfg)
    stage = Usd.Stage.Open(str(usd_stage.write(cfg, run, tmp_path / "film.usda", frames=720)))
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode(), stage.GetTimeCodesPerSecond()) == (1.0, 720.0, 24.0)
    p = stage.GetPrimAtPath(f"/Guild/Plants/cell_{k}")
    assert UsdGeom.Imageable(p).ComputeVisibility(1) == "invisible" and UsdGeom.Imageable(p).ComputeVisibility(720) == "invisible"
    assert UsdGeom.Imageable(p).ComputeVisibility(400) == "inherited"


def test_every_shape_of_the_library_is_there_as_usd():
    shapes = json.loads((usd_stage.SHAPES / "library.json").read_text())["shapes"]
    for name, s in shapes.items():
        stage = Usd.Stage.Open(str(usd_stage.SHAPES / s["usd"]))
        assert stage.GetDefaultPrim().GetName() == name and UsdGeom.GetStageUpAxis(stage) == "Z", name


def test_the_page_replays_the_same_timeline(monkeypatch, tmp_path, cfg):
    """/api/twin/replay: the bed's measures and the run's timeline, each plant's size a share of its grown size."""
    from permtek5_twin_edge import app as web, engine
    from permtek5_twin_edge.core import version, workspace as ws
    monkeypatch.setattr(version, "UPDATE_CHECK", False)
    monkeypatch.setitem(ws.FOLDERS, ws.USER, tmp_path / "user")
    monkeypatch.setitem(ws.FOLDERS, ws.SHARED, tmp_path / "shared")
    ws.configure()
    client = web.app.test_client()
    assert client.get("/api/twin/replay").status_code == 400, "nothing prepared"
    run, k = run_of(cfg)
    monkeypatch.setattr(engine, "config", lambda out=None: cfg)
    monkeypatch.setattr(track, "read", lambda folder: run)
    r = client.get("/api/twin/replay").get_json()
    assert r["bed"]["soilM"] == pytest.approx(world.SOIL_Z) and r["end"] == pytest.approx(70 * 24)
    plant = next(c for c in r["cells"] if c["key"] == k)
    assert plant["shown"][0] == [0, 0] and [0.0, 0] not in plant["shown"][1:2]
    assert max(s for _, s in plant["sizes"]) == pytest.approx(1.0), "grown is the whole of its size"
    assert plant["heightM"] > 0 and r["gantry"][1][1:] == [1.2, 0.5, 0.08]
