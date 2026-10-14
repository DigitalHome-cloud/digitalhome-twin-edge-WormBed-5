# SPDX-License-Identifier: GPL-3.0-or-later
"""The robot window's buttons become the same events the edge page sends: plain Python, no ROS."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin"))
from roofbed_twin import robot_window  # noqa: E402

from permtek5_twin_edge import world  # noqa: E402

NOW = {"lidOpen": False, "insulated": True, "tankCap": 180.0, "stopped": False}


def test_only_known_acts_pass():
    assert robot_window.parse('{"act": "lid"}') == "lid"
    assert robot_window.parse("init robot window") is None
    assert robot_window.parse('{"act": "jog-x"}') is None, "the gantry is never moved from the window"
    assert robot_window.parse('["lid"]') is None


def test_the_acts_toggle_against_the_twin_and_match_the_pages_events():
    assert robot_window.event("lid", NOW) == {"op": "box", "do": "lid", "open": True}
    assert robot_window.event("wrap", NOW) == {"op": "box", "do": "insulate", "on": False}
    assert robot_window.event("tank", NOW) == {"op": "tank", "litres": 180.0}
    assert robot_window.event("barrel", NOW) == {"op": "valve", "from": "RainBarrel", "to": "TankPipes", "open": True}
    assert robot_window.event("hopper", NOW)["mix"] == {"vegetable": 600, "fruit": 300, "coffee": 100}
    assert robot_window.event("estop", {**NOW, "stopped": True}) == {"op": "estop", "on": False}
    assert all(robot_window.event(a, NOW) for a in robot_window.ACTS)


def test_the_world_brings_the_windows_files_to_its_project_the_folder_above(tmp_path):
    world._robot_window(tmp_path / "guild")
    got = sorted(p.name for p in (tmp_path / "plugins" / "robot_windows" / "wormbed5").iterdir())
    assert got == ["wormbed5.css", "wormbed5.html", "wormbed5.js"]
