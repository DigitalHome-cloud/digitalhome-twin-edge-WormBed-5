# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
film.py — rendered films of the prepared guild, made with Blender.

The rendering itself is assets/demo.py (and the worm box's insides,
assets/wormlife.py), run in Blender's own environment (.venv-bpy;
scripts/setup-bpy.sh). This module only starts it as a background job,
follows its "frame i/N" lines for progress, stops it on Cancel, and says
where each shot's poster and film are. Its output goes to a file, never a
pipe. Standard library only: this app never imports bpy.

    SHOTS     bed (the camera circles the bed), box (close up over the open
              worm box), flight (from the whole bed down into the box)
    QUALITY   draft, good, best: a bigger picture and more samples take longer
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .edge.jobs import Stopped
from .i18n import _

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "assets" / "demo.py"
PYTHON = ROOT / ".venv-bpy" / "bin" / "python"
FRAME = re.compile(r"^frame (\d+)/(\d+)", re.M)
MAX_FRAMES = 240
SHOTS = {"bed": "The bed", "box": "The worm box, close up", "flight": "Into the worm box"}
QUALITY = {"draft": "draft: about half an hour", "good": "good: an hour or two", "best": "best: several hours"}
NO_PROGRESS_S = 600          # one frame never takes this long: the renderer hangs


def missing() -> List[str]:
    """What stops a film from being rendered. Empty: ready."""
    out: List[str] = []
    if not SCRIPT.exists():
        out.append("the film script is not here: run from a source checkout (assets/demo.py)")
    if not PYTHON.exists():
        out.append("Blender's Python module is not set up: run scripts/setup-bpy.sh once (about 400 MB)")
    return out


def command(bed: Path, out: Path, shot: str = "bed", quality: str = "good", adults: Optional[int] = None,
            frames: Optional[int] = None) -> List[str]:
    cmd = [str(PYTHON), str(SCRIPT), "--bed", str(bed), "--out", str(out), "--shot", shot, "--quality", quality]
    if adults is not None:
        cmd += ["--adults", str(int(adults))]
    if frames:
        cmd += ["--frames", str(int(frames))]
    return cmd


def files(shot: str) -> Dict[str, str]:
    return {"poster": f"poster-{shot}.png", "film": f"film-{shot}.mp4"}


def status(out: Path) -> Dict[str, Any]:
    def info(name: str) -> Optional[Dict[str, Any]]:
        p = Path(out) / name
        return {"name": name, "modified": p.stat().st_mtime, "bytes": p.stat().st_size} if p.exists() else None
    shots = [{"id": k, "title": _(v), **{kind: info(name) for kind, name in files(k).items()}} for k, v in SHOTS.items()]
    return {"missing": [_(m) for m in missing()], "shots": shots, "quality": {k: _(v) for k, v in QUALITY.items()}}


def render(bed: Path, out: Path, shot: str, quality: str, progress: Callable[[str, float], None],
           cancelled: Callable[[], bool], adults: Optional[int] = None, cmd: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run the renderer to the end. Raises Stopped on Cancel, RuntimeError with the log's tail on failure."""
    bad = missing() if cmd is None else []
    if bad:
        raise RuntimeError("; ".join(bad))
    if not Path(bed).exists():
        raise RuntimeError("prepare a world first: the film shows the prepared bed")
    if shot not in SHOTS or quality not in QUALITY:
        raise RuntimeError(f"no such film: {shot}, {quality}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / f"film-{shot}.log"
    started = time.time()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")}
    with open(log, "wb") as fh:
        proc = subprocess.Popen(cmd or command(Path(bed), out, shot, quality, adults), cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, start_new_session=True,
                                preexec_fn=lambda: os.nice(10))       # the simulation comes first
    progress("the still", 0.0)
    seen, last = 0, time.time()
    try:
        while proc.poll() is None:
            if cancelled():
                raise Stopped()
            found = FRAME.findall(log.read_text(encoding="utf-8", errors="replace"))
            if found and int(found[-1][0]) != seen:
                seen, last = int(found[-1][0]), time.time()
                progress(f"frame {seen} of {found[-1][1]}", seen / max(1, int(found[-1][1])))
            elif time.time() - last > NO_PROGRESS_S:
                raise RuntimeError("the renderer stopped making progress")
            time.sleep(0.5)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(10)
        raise
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-4:]
        raise RuntimeError("the renderer failed: " + " | ".join(tail))
    return {**status(out), "shot": shot, "quality": quality, "seconds": round(time.time() - started), "frames": seen}
