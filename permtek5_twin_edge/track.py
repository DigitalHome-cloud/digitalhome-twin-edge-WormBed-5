# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
track.py — a twin run, recorded as it goes: where the gantry was and how every plant stood, over the simulation's time.

The logbook (logbook.py) says what happened; the track says where everything was. From it usd_stage.py writes the
run as an OpenUSD stage to replay (usdview, Blender) and the season film renders it (assets/demo.py --track).

    Recorder(folder, status).start()     follows sim.status() every 2 s of wall time while the twin runs
    read(folder)                         the header and the samples, in order
    summary(folder)                      how many samples, the simulated span, the cells by status at the end

run-track.jsonl in the guild's folder: a header line (the bed, the period, the start), then one sample a simulated
hour, and one whenever a cell changes or the gantry works a job. A sample: {"t" (simulated minutes since the run
began), "date", "clock", "gantry": [x, y, z], "sun", "box" (the worm box's fill, %), "cells": {"r_c": {"status",
"growth", "up", "ready", "why"}} with only the cells that changed}. A new start begins a new track.

Standard library only: the film script loads it in Blender's own Python.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

FILE = "run-track.jsonl"
EVERY_S = 2.0               # how often the recorder looks (wall clock)
HOUR_MIN = 60.0             # a sample at least every simulated hour
GROWTH_STEP = 0.05          # a plant's growth is written again after it changed this much


def _file(folder: Path) -> Path:
    return Path(folder) / FILE


def sim_minutes(env: Dict[str, Any]) -> Optional[float]:
    """The simulation's time in minutes since its day 1, 00:00."""
    if "day" not in env:
        return None
    return (float(env["day"]) - 1.0) * 1440.0 + float(env.get("hour", 0.0)) * 60.0


def cell_state(c: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": c.get("status"), "growth": round(float(c.get("growth") or 0.0), 2), "up": bool(c.get("up")),
            "ready": bool(c.get("ready")), **({"why": c["why"]} if c.get("why") else {})}


class Recorder:
    """Follows the running twin and writes its track. One per guild; start() with the simulation."""

    def __init__(self, folder: Path, status: Callable[[], Dict[str, Any]], config: Callable[[], Optional[Dict[str, Any]]]):
        self.folder, self.status, self.config = Path(folder), status, config
        self.on = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def start(self) -> None:
        """A new track for a new run: the header now, the samples as the twin runs."""
        self.stop()
        cfg = self.config() or {}
        head = {"track": 1, "bed": (cfg.get("bed") or {}).get("id"), "bedName": (cfg.get("bed") or {}).get("name"),
                "period": cfg.get("period"), "start": cfg.get("start"), "rehearsal": cfg.get("rehearsal", True),
                "began": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _file(self.folder).parent.mkdir(parents=True, exist_ok=True)
        _file(self.folder).write_text(json.dumps(head) + "\n", encoding="utf-8")
        self.last_t: Optional[float] = None
        self.cells: Dict[str, Dict[str, Any]] = {}
        self.on = True
        self.thread = threading.Thread(target=self._loop, name="track", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.on = False

    def _loop(self) -> None:
        seen_running = False
        while self.on:
            try:
                st = self.status()
                if st.get("running"):
                    seen_running = True
                    self.look(st)
                elif seen_running:                    # the twin stopped: the track is complete
                    self.on = False
                    break
            except Exception:                         # one failed look never ends the record
                pass
            time.sleep(EVERY_S)

    def look(self, st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Write a sample when an hour passed, a cell changed or the gantry works; returns it (None: nothing new)."""
        twin = st.get("twin") or {}
        bed = twin.get("bed") or {}
        env = bed.get("env") or {}
        t = sim_minutes(env)
        if t is None:
            return None
        changed: Dict[str, Dict[str, Any]] = {}
        for key, c in (bed.get("cells") or {}).items():
            now, before = cell_state(c), self.cells.get(key)
            if before is None or now["status"] != before["status"] or now["up"] != before["up"] or now["ready"] != before["ready"] \
                    or abs(now["growth"] - before["growth"]) >= GROWTH_STEP:
                changed[key] = now
                self.cells[key] = now
        working = ((twin.get("job") or {}).get("state") == "running")
        if self.last_t is not None and not changed and not working and t - self.last_t < HOUR_MIN:
            return None
        g = twin.get("gantry") or {}
        sample = {"t": round(t, 1), **{k: env[k] for k in ("date", "clock") if env.get(k)},
                  "gantry": [round(float(g.get(a, 0.0)), 3) for a in "xyz"], "sun": env.get("sun"),
                  **({"box": (bed.get("wormBin") or {}).get("fillPct")} if bed.get("wormBin") else {}),
                  **({"cells": changed} if changed else {})}
        with self.lock:
            with _file(self.folder).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample) + "\n")
        self.last_t = t
        return sample


def read(folder: Path) -> Dict[str, Any]:
    """{"head": the header (None without a track), "samples": [...] in order of their simulated time}."""
    head, samples = None, []
    try:
        with _file(folder).open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if "track" in x:
                    head = x
                elif "t" in x:
                    samples.append(x)
    except OSError:
        pass
    return {"head": head, "samples": samples}


def summary(folder: Path) -> Dict[str, Any]:
    tr = read(folder)
    s = tr["samples"]
    cells: Dict[str, str] = {}
    for x in s:
        for key, c in (x.get("cells") or {}).items():
            cells[key] = c.get("status")
    by: Dict[str, int] = {}
    for status in cells.values():
        by[status] = by.get(status, 0) + 1
    return {"recorded": tr["head"] is not None, "head": tr["head"], "samples": len(s),
            "from": (s[0].get("date") or s[0].get("clock")) if s else None, "to": (s[-1].get("date") or s[-1].get("clock")) if s else None,
            "days": round((s[-1]["t"] - s[0]["t"]) / 1440.0, 1) if len(s) > 1 else 0, "byStatus": by}
