# SPDX-License-Identifier: GPL-3.0-or-later
"""The habitat reader and the world generator, on the invented CityDemo."""

from __future__ import annotations

import json

import pytest

from permtek5_twin_edge import habitat as hb
from permtek5_twin_edge import world


@pytest.fixture(scope="module")
def demo():
    return hb.load_citydemo(), hb.load_library()


def test_citydemo_reads(demo):
    h, lib = demo
    assert [z.kind for z in h.zones] == ["GroundZone", "RoofZone"]
    bed = h.beds[0]
    assert (bed.length_cm, bed.width_cm, bed.cell_cm, bed.rows, bed.cols) == (300, 120, 10, 12, 30)
    assert len(h.cells) == 28 and len(h.plants) == 3
    assert hb.problems(h, lib) == []
    assert lib["crop-pear"].woody and not lib["crop-lettuce"].woody


def test_sowing_job_is_the_clouds_rule(demo):
    """The same numbers as PermTek-5 packages/core/__tests__/calendar.test.mjs."""
    h, lib = demo
    lettuce = next(c for c in h.cells if c.crop_id == "crop-lettuce" and (c.row, c.col) == (1, 4))
    job = hb.sowing_job(h, lettuce, lib)
    assert (job["xMm"], job["yMm"], job["depthMm"], job["seeds"], job["spacingMm"], job["checkAfterDays"]) == (450, 150, 5, 3, 250, 7)
    deeper = next(c for c in h.cells if c.depth_mm is not None)
    assert deeper.crop_id == "crop-radish" and hb.sowing_job(h, deeper, lib)["depthMm"] == 15


def test_water_per_plant(demo):
    h, lib = demo
    assert hb.water_ml_per_day(lib["crop-lettuce"], h.beds[0]) == 223       # 0.25 m² × 25 mm/week ÷ 7


def test_a_cell_outside_its_bed_stops_the_world(demo, tmp_path):
    h, lib = demo
    bad = hb.load_citydemo()
    bad.cells[0].row = 12
    assert any("outside its bed" in p for p in hb.problems(bad, lib))
    with pytest.raises(ValueError):
        world.generate(bad, lib, tmp_path)


def test_world_and_config_are_written(demo, tmp_path):
    h, lib = demo
    out = world.generate(h, lib, tmp_path)
    wbt = (tmp_path / "roofbed.wbt").read_text()
    assert wbt.startswith("#VRML_SIM R2025a utf8")
    for device in ("x_motor", "y_motor", "z_motor", "x_sensor", "tool_camera", "overview_camera"):
        assert f'name "{device}"' in wbt, device
    assert "https://" not in wbt and "EXTERNPROTO" not in wbt, "self-contained: nothing is downloaded"
    assert wbt.count("{") == wbt.count("}") and wbt.count("[") == wbt.count("]")
    cfg = json.loads(open(out["config"]).read())
    assert cfg["bed"]["cols"] == 30 and len(cfg["cells"]) == 28
    growing = [c for c in cfg["cells"] if c["status"] in ("CellSown", "CellGrowing")]
    assert wbt.count("DEF CELL_") == len(growing)
    assert cfg["world"]["x"][1] == pytest.approx(3.05) and cfg["world"]["meshes"] is False


def test_meshes_are_used_when_they_exist_and_never_needed(demo, tmp_path):
    h, lib = demo
    plain = (tmp_path / "plain")
    world.generate(h, lib, plain)
    assert "Mesh {" not in (plain / "roofbed.wbt").read_text()
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("plant_grown", "plant_seedling", "bed_soil", "gantry_bridge", "tree_trunk", "tree_canopy"):
        (assets / f"{name}.obj").write_text("o x\n")
    out = world.generate(h, lib, tmp_path / "meshed", assets=assets)
    wbt = (tmp_path / "meshed" / "roofbed.wbt").read_text()
    for name in ("plant_grown", "bed_soil", "gantry_bridge", "tree_canopy"):
        assert f"{name}.obj" in wbt, name
    cfg = json.loads(open(out["config"]).read())
    assert cfg["world"]["meshes"] is True and cfg["world"]["seedlingMesh"].endswith("plant_seedling.obj")
    assert wbt.count("{") == wbt.count("}") and "https://" not in wbt


def test_the_garden_trees_stand_behind_the_roof(demo, tmp_path):
    h, lib = demo
    world.generate(h, lib, tmp_path)
    wbt = (tmp_path / "roofbed.wbt").read_text()
    assert "# Pear by the wall" in wbt and "# Plum" in wbt and "# garden" in wbt
