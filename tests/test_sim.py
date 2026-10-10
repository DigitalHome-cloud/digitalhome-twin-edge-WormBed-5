# SPDX-License-Identifier: GPL-3.0-or-later
"""sim.py without ROS or Webots: what it says is missing, and the command it would run."""

from __future__ import annotations

from pathlib import Path

import pytest

from permtek5_twin_edge import sim


def test_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sim, "ros_setup", lambda: None)
    monkeypatch.setattr(sim.shutil, "which", lambda _: None)
    monkeypatch.setattr(sim, "ROS_WS", tmp_path)
    missing = sim.missing()
    assert any("ROS 2" in m for m in missing) and any("Webots" in m for m in missing) and any("source checkout" in m for m in missing)
    with pytest.raises(RuntimeError):
        sim.start(tmp_path)


def test_not_built_is_its_own_message(monkeypatch, tmp_path):
    (tmp_path / "roofbed_twin").mkdir()
    monkeypatch.setattr(sim, "ROS_WS", tmp_path)
    assert any("not built" in m for m in sim.missing())


def test_launch_command_sources_ros_and_names_the_world_folder(monkeypatch):
    monkeypatch.setattr(sim, "ros_setup", lambda: "/opt/ros/x/setup.bash")
    cmd = sim.launch_command(Path("/data/twin"), gui=False, port=8773)
    assert cmd[:2] == ["bash", "-c"]
    assert 'source "/opt/ros/x/setup.bash"' in cmd[2] and 'out:="/data/twin"' in cmd[2] and "gui:=false" in cmd[2] and "port:=8773" in cmd[2]


def test_status_when_nothing_runs():
    s = sim.status()
    assert s["running"] is False and s["twin"] is None


def test_the_child_keeps_ros_out_of_this_python(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/x/lib")
    assert "PYTHONPATH" not in sim._env()
