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
    assert "webots_port:=1234" in cmd[2] and "ROS_DOMAIN_ID" not in cmd[2]
    second = sim.launch_command(Path("/data/twin"), gui=False, port=8783, webots_port=1244, ros_domain=20)[2]
    assert "export ROS_DOMAIN_ID=20" in second and "webots_port:=1244" in second and "port:=8783" in second


def test_status_when_nothing_runs():
    s = sim.status()
    assert s["running"] is False and s["twin"] is None


def test_the_child_keeps_ros_out_of_this_python(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/x/lib")
    assert "PYTHONPATH" not in sim._env()


def test_a_leftover_simulation_is_stopped_by_its_remembered_group(tmp_path):
    import subprocess, sys, time
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    (tmp_path / "sim.pid").write_text(str(old.pid))
    assert sim.stop_leftover(tmp_path, timeout=3.0) is True
    assert old.wait(5) is not None and not (tmp_path / "sim.pid").exists()
    assert sim.stop_leftover(tmp_path) is False                      # nothing left, nothing to do
    (tmp_path / "sim.pid").write_text("not a number")
    assert sim.stop_leftover(tmp_path) is False


def test_two_guilds_on_one_computer_share_nothing(tmp_path):
    from permtek5_twin_edge import instance
    try:
        instance.configure(None, 8772)
        first = instance.describe()
        assert (first["name"], first["gatewayPort"], first["webotsPort"], first["rosDomain"]) == ("main", 8773, 1234, 10)
        assert instance.workspace(tmp_path) == tmp_path / "guilds" / "main"
        instance.configure("Balcony guild", 8782)
        second = instance.describe()
        assert (second["name"], second["gatewayPort"], second["webotsPort"], second["rosDomain"]) == ("balcony-guild", 8783, 1244, 20)
        assert all(first[k] != second[k] for k in ("name", "appPort", "gatewayPort", "webotsPort", "rosDomain"))
    finally:
        instance.configure(None, 8772)


def test_a_guilds_workspace_is_run_by_one_edge(tmp_path, monkeypatch):
    from permtek5_twin_edge import instance
    assert instance.claim(tmp_path) is None and instance.claim(tmp_path) is None, "our own mark does not stop us"
    (tmp_path / "edge.pid").write_text("999999999 8790")
    assert instance.claim(tmp_path) is None, "a mark left by an edge that is gone is taken over"
