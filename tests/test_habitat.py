# SPDX-License-Identifier: GPL-3.0-or-later
"""The habitat reader and the world generator, on the invented CityDemo."""

from __future__ import annotations

import json
from pathlib import Path

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
    assert (bed.substrate, bed.tank_pipes, bed.tank_pipe_mm, bed.worm_bin_cm, bed.worm_bin_cols, bed.tank_litres) == ("LightweightRoofSubstrate", 8, 110, 40, 4, 202)
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


def test_the_worm_box_takes_the_last_columns_and_the_world_shows_the_system(demo, tmp_path):
    h, lib = demo
    out = world.generate(h, lib, tmp_path)
    cfg = json.loads(open(out["config"]).read())
    assert cfg["tank"] == {"pipes": 8, "pipeMm": 110, "litres": 202}
    assert cfg["wormBin"]["fromCol"] == 26 and cfg["wormBin"]["x"] == [pytest.approx(2.6), pytest.approx(3.0)] and cfg["wormBin"]["spots"] == 4
    assert cfg["hopper"]["x"] < 0
    wbt = (tmp_path / "roofbed.wbt").read_text()
    assert wbt.count("# tank pipe") == 8 and "# worm box" in wbt and "# hopper" in wbt and "# fill pipe" in wbt
    assert "DEF WORM_LID Pose" in wbt and "DEF WORM_WRAP Pose" in wbt, "the lid opens and the wrapping goes on in the world"
    assert cfg["world"]["lidOpenRad"] > 1 and cfg["world"]["wrapOffZ"] < 0 < cfg["world"]["wrapZ"]
    # two bins: the sump as high as the soil, the working bin above it, and the parked tool clears the lid
    assert cfg["wormBin"]["sumpLitres"] == pytest.approx(14.4) and cfg["wormBin"]["areaM2"] == pytest.approx(0.48)
    assert cfg["world"]["boxTopZ"] < cfg["world"]["tipHome"] and cfg["world"]["boxTopZ"] > cfg["world"]["soilZ"] + 0.3
    bad = hb.load_citydemo()
    bad.cells[0].col = 27
    assert any("worm box" in p for p in hb.problems(bad, lib))


def test_a_bed_drawn_without_box_and_tank_gets_the_wormbed5_parts_and_says_so(demo, tmp_path):
    h, lib = demo
    plain = hb.load_citydemo()
    plain.beds[0].tank_pipes, plain.beds[0].worm_bin_cm = 0, 0
    out = world.generate(plain, lib, tmp_path)
    cfg = json.loads(open(out["config"]).read())
    assert cfg["tank"]["litres"] == 202 and cfg["wormBin"]["cols"] == 4 and "dropX" in cfg["wormBin"]
    assert len(cfg["assumed"]) == 2 and "worm box of 40 cm" in cfg["assumed"][0] and "8 tank pipes" in cfg["assumed"][1]
    wbt = (tmp_path / "roofbed.wbt").read_text()
    assert "# tank pipe" in wbt and "# worm box" in wbt
    full = world.generate(hb.load_citydemo(), lib, tmp_path)
    assert json.loads(open(full["config"]).read())["assumed"] == [], "a complete map is taken as it is"


def test_a_worm_box_is_not_assumed_over_planted_cells(demo, tmp_path):
    h, lib = demo
    planted = hb.load_citydemo()
    planted.beds[0].worm_bin_cm = 0
    planted.cells[0].col = 28
    cfg = json.loads(open(world.generate(planted, lib, tmp_path)["config"]).read())
    assert cfg["wormBin"]["cols"] == 0 and "planted on your map" in cfg["assumed"][0]


def test_citydemo_roof_is_a_wormbed5_guild_and_a_guild_is_picked_by_name(demo, tmp_path):
    h, lib = demo
    assert [(g.name, g.type, g.robot_name, g.worms_may_leave) for g in h.guilds] == [("Roof guild", "WormBed5", "Gantry5-gen1", True)]
    guild, bed = hb.pick_guild(h)
    assert guild.name == "Roof guild" and bed.guild_id == guild.id
    assert hb.pick_guild(h, "roof-guild")[0] is guild and hb.pick_guild(h, "Roof Guild")[0] is guild
    with pytest.raises(ValueError, match="Roof guild"):
        hb.pick_guild(h, "balcony")
    out = world.generate(h, lib, tmp_path)
    cfg = json.loads(open(out["config"]).read())
    assert cfg["guild"]["type"] == "WormBed5" and cfg["guild"]["robotName"] == "Gantry5-gen1"
    assert "Roof guild · WormBed5 · Gantry5-gen1" in (tmp_path / "roofbed.wbt").read_text()


def test_a_habitat_from_before_guilds_runs_its_first_bed(demo, tmp_path):
    h, lib = demo
    old = hb.load_citydemo()
    old.guilds, old.beds[0].guild_id = [], None
    assert hb.pick_guild(old) == (None, old.beds[0])
    cfg = json.loads(open(world.generate(old, lib, tmp_path)["config"]).read())
    assert cfg["guild"] is None


def test_the_shape_library_ships_with_the_app_and_lists_every_shape_the_world_uses():
    import re
    from permtek5_twin_edge import engine
    folder = engine.shared_assets()
    lib = json.loads((folder / "library.json").read_text())["shapes"]
    for name, shape in lib.items():
        assert (folder / shape["file"]).stat().st_size > 200 and shape["what"], name
    used = set(re.findall(r'_mesh\(assets, "([a-z_]+)"\)', Path(world.__file__).read_text())) | {"plant_grown", "plant_seedling"}
    assert used <= set(lib), used - set(lib)
    assert sum((folder / s["file"]).stat().st_size for s in lib.values()) < 300_000, "the library stays small"
