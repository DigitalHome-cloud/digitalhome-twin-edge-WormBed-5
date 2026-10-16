# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
logbook.py — everything that happens in the guild, one line each, in the order it happened.

Who did what, when (the wall clock and the simulation's own date): a person's act at the bed, a confirmed job
and how the robot's job ended, the autopilot's acts, the plants coming up, ripening and being lost, the keeper's
notices, a world prepared, a run started or stopped, a scenario's steps. It is a JSON-lines file in the guild's
folder (logbook.jsonl), never sent anywhere; the page shows it (Logbook) and the MCP tool logbook() reads it.

    write(folder, who, what, text, sim=None, **extra)    one entry; who: person | autopilot | robot | plants | keeper | edge
    read(folder, after=0, who=None, what=None, limit=200)  the entries after number `after`, oldest first
    Follower().follow(folder, cfg, twin, notices, sim)     what the twin's state shows as new since the last look

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILE = "logbook.jsonl"
WHO = ("person", "autopilot", "robot", "plants", "keeper", "edge")
KEEP = 20000                     # lines kept; older ones are dropped when the file grows past this
_lock = threading.Lock()
_count: Dict[str, int] = {}      # the last entry's number per file


def _file(folder: Path) -> Path:
    return Path(folder) / FILE


def _last_n(path: Path) -> int:
    key = str(path)
    if key not in _count:
        n = 0
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        n = max(n, int(json.loads(line).get("n", 0)))
                    except ValueError:
                        continue
        except OSError:
            pass
        _count[key] = n
    return _count[key]


def sim_when(env: Optional[Dict[str, Any]]) -> Optional[str]:
    """The simulation's own time, as the page shows it: the date when it has seasons, and the clock."""
    if not env:
        return None
    return " · ".join(x for x in (env.get("date"), env.get("clock")) if x) or None


def write(folder: Path, who: str, what: str, text: str, sim: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    """Append one entry and return it."""
    path = _file(folder)
    with _lock:
        n = _last_n(path) + 1
        _count[str(path)] = n
        entry = {"n": n, "t": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 **({"sim": sim} if sim else {}), "who": who if who in WHO else "edge", "what": what, "text": text, **extra}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if n % 500 == 0:
            _trim(path)
    return entry


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > KEEP:
        path.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")


def read(folder: Path, after: int = 0, who: Optional[Iterable[str]] = None, what: Optional[Iterable[str]] = None,
         limit: int = 200) -> List[Dict[str, Any]]:
    """The entries numbered above `after` (oldest first), the last `limit` of them; filtered by who and what."""
    whos, whats = set(who or []), set(what or [])
    out: List[Dict[str, Any]] = []
    try:
        with _file(folder).open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("n", 0) <= after or (whos and e.get("who") not in whos) or (whats and e.get("what") not in whats):
                    continue
                out.append(e)
    except OSError:
        return []
    return out[-max(1, min(int(limit), 5000)):]


def last(folder: Path) -> int:
    """The newest entry's number (0 for an empty logbook)."""
    with _lock:
        return _last_n(_file(folder))


PLANT_SAYS = {"up": "{crop} came up at {where}", "ready": "{crop} is ready to harvest at {where}",
              "lost": "{crop} was lost at {where} ({why})", "harvested": "{crop} harvested at {where}"}
JOB_SAYS = {"done": "{title}: done", "failed": "{title}: failed", "aborted": "{title}: aborted", "cancelled": "{title}: cancelled",
            "refused": "{title}: refused"}


class Follower:
    """What is new in the twin since the last look: finished robot jobs, plants that came up, ripened or were
    lost, the keeper's notices. One per running guild; reset() when a world is prepared."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.jobs: set = set()
        self.cells: Dict[str, tuple] = {}
        self.notices: set = set()
        self.primed = False

    def follow(self, folder: Path, cfg: Optional[Dict[str, Any]], twin: Dict[str, Any], notices: List[str],
               crop_name=lambda c: c) -> List[Dict[str, Any]]:
        bed = twin.get("bed") or {}
        if not cfg or not bed:
            return []
        sim = sim_when(bed.get("env"))
        out: List[Dict[str, Any]] = []
        with self.lock:
            first = not self.primed           # the first look only learns what is there: nothing is new yet
            self.primed = True
            for job in twin.get("history") or []:
                if job.get("id") in self.jobs or job.get("state") in ("running", "queued"):
                    continue
                self.jobs.add(job.get("id"))
                if first:
                    continue
                spec = job.get("spec") or {}
                where = f' at row {spec["row"] + 1}, column {spec["col"] + 1}' if "row" in spec and "col" in spec else ""
                title = f'{job.get("kind", "job")}{where}' + (f' ({crop_name(spec["crop"])})' if spec.get("crop") else "")
                say = JOB_SAYS.get(job.get("state"), "{title}: {state}")
                out.append(write(folder, "robot", f'job.{job.get("state")}', say.format(title=title, state=job.get("state")), sim,
                                 job=job.get("kind"), **({"message": job["message"]} if job.get("message") else {})))
            for key, c in (bed.get("cells") or {}).items():
                now = (c.get("status"), bool(c.get("up")), bool(c.get("ready")))
                before = self.cells.get(key)
                self.cells[key] = now
                if first or before is None or before == now:
                    continue
                r, k = (int(x) for x in key.split("_"))
                said = {"crop": crop_name(c.get("crop", "")), "where": f"row {r + 1}, column {k + 1}", "why": c.get("why") or "?"}
                event = ("lost" if now[0] == "CellLost" and before[0] != "CellLost" else
                         "harvested" if now[0] == "CellHarvested" and before[0] != "CellHarvested" else
                         "ready" if now[2] and not before[2] else
                         "up" if now[1] and not before[1] else None)
                if event:
                    out.append(write(folder, "plants", f"plant.{event}", PLANT_SAYS[event].format(**said), sim, cell=key))
            for text in notices:
                if text in self.notices:
                    continue
                self.notices.add(text)
                if not first:
                    out.append(write(folder, "keeper", "notice", text, sim))
            self.notices &= set(notices)       # a notice that goes and comes again is written again
        return out
