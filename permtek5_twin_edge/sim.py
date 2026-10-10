# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
sim.py — start, stop and talk to the simulation.

The simulation is a ROS 2 launch (ros2/roofbed_twin: Webots, the gantry driver,
bed_actions, the gateway) in its own process group, started through a shell
that sources ROS. This app's own Python stays free of ROS: it only speaks HTTP
to the gateway on 127.0.0.1.

The child's output goes to a file, never a pipe: a pipe nobody reads fills up
and hangs the child (docs/edge.md, "traps"). Standard library only.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
ROS_WS = ROOT / "ros2"
GATEWAY_PORT = int(os.environ.get("PERMTEK5_TWIN_GATEWAY_PORT", "8773"))

_child: Dict[str, Any] = {}


def ros_setup() -> Optional[str]:
    """The ROS 2 distribution's setup file, newest first; None without ROS."""
    found = sorted(glob.glob("/opt/ros/*/setup.bash"), reverse=True)
    wanted = os.environ.get("ROS_DISTRO")
    for f in found:
        if wanted and f"/{wanted}/" in f:
            return f
    return found[0] if found else None


def missing() -> List[str]:
    """What stops the simulation from starting, as things a person can fix. Empty: ready."""
    out: List[str] = []
    if ros_setup() is None:
        out.append("ROS 2 is not installed under /opt/ros")
    if shutil.which("webots") is None:
        out.append("Webots is not on the PATH")
    if not (ROS_WS / "roofbed_twin").is_dir():
        out.append("the ROS 2 packages are not here: run from a source checkout (ros2/)")
    elif not (ROS_WS / "install" / "setup.bash").exists():
        out.append("the ROS 2 packages are not built yet: build them first")
    return out


def _env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def build(log: Path) -> int:
    """colcon build of ros2/. Returns the exit code; output in `log`."""
    setup = ros_setup()
    if setup is None:
        raise RuntimeError("ROS 2 is not installed under /opt/ros")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as fh:
        return subprocess.call(["bash", "-c", f'source "{setup}" && colcon build --symlink-install'],
                               cwd=ROS_WS, stdout=fh, stderr=subprocess.STDOUT, env=_env())


def launch_command(out_dir: Path, gui: bool, port: int) -> List[str]:
    return ["bash", "-c",
            f'source "{ros_setup()}" && source "{ROS_WS / "install" / "setup.bash"}" && '
            f'exec ros2 launch roofbed_twin roofbed.launch.py out:="{out_dir}" gui:={"true" if gui else "false"} port:={port}']


def running() -> bool:
    p = _child.get("proc")
    return p is not None and p.poll() is None


def start(out_dir: Path, gui: bool = False, port: int = GATEWAY_PORT) -> Dict[str, Any]:
    """Start the launch on the world in out_dir. Already running: says so, starts nothing."""
    if running():
        return status()
    bad = missing()
    if bad:
        raise RuntimeError("; ".join(bad))
    out_dir = Path(out_dir).resolve()
    log = out_dir / "sim.log"
    fh = open(log, "wb")
    proc = subprocess.Popen(launch_command(out_dir, gui, port), cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=_env(), start_new_session=True)
    _child.update(proc=proc, log=log, fh=fh, port=port, out=out_dir, started=time.time(), gui=gui)
    return status()


def stop(timeout: float = 15.0) -> Dict[str, Any]:
    """Stop the launch and everything it started (Webots included)."""
    p = _child.get("proc")
    if p is not None and p.poll() is None:
        for sig, wait in ((signal.SIGINT, timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(p.pid, sig)
            except ProcessLookupError:
                break
            try:
                p.wait(wait)
                break
            except subprocess.TimeoutExpired:
                continue
    fh = _child.pop("fh", None)
    if fh:
        fh.close()
    _child.pop("proc", None)
    return status()


def log_tail(lines: int = 30) -> List[str]:
    log = _child.get("log")
    if not log or not Path(log).exists():
        return []
    with open(log, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - 16_000))
        return fh.read().decode("utf-8", "replace").splitlines()[-lines:]


def gateway(path: str, body: Optional[dict] = None, timeout: float = 3.0) -> Any:
    """One call to the gateway. Raises RuntimeError with its message when it refuses or is not there."""
    url = f"http://127.0.0.1:{_child.get('port', GATEWAY_PORT)}{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            raise RuntimeError(json.loads(e.read()).get("error", str(e))) from None
        except ValueError:
            raise RuntimeError(str(e)) from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"the simulation does not answer ({e})") from None


def gateway_url(path: str) -> str:
    return f"http://127.0.0.1:{_child.get('port', GATEWAY_PORT)}{path}"


def status() -> Dict[str, Any]:
    out: Dict[str, Any] = {"running": running(), "missing": missing(), "gui": _child.get("gui", False),
                           "started": _child.get("started") if running() else None, "twin": None}
    if out["running"]:
        try:
            out["twin"] = gateway("/state", timeout=1.5)
        except RuntimeError:
            out["twin"] = None                  # still starting
    else:
        p = _child.get("proc")
        if p is not None:
            out["exit"] = p.returncode
            out["log"] = log_tail(12)
    return out


def wait_ready(timeout: float = 60.0) -> bool:
    """True once the gantry reports its position and both cameras send pictures."""
    end = time.time() + timeout
    while time.time() < end and running():
        try:
            s = gateway("/state", timeout=1.5)
            if s.get("gantry") and len(s.get("cameras", [])) >= 2:
                return True
        except RuntimeError:
            pass
        time.sleep(0.5)
    return False


def run_job(spec: dict, timeout: float = 180.0) -> Dict[str, Any]:
    """Send one job and wait for it to end. For the command line and tests; the page polls instead."""
    gateway("/job", spec)
    end = time.time() + timeout
    while time.time() < end:
        job = gateway("/state").get("job") or {}
        if job.get("state") != "running":
            return job
        time.sleep(0.3)
    raise RuntimeError("the job did not end in time")
