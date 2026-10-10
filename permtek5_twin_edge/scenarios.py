# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
scenarios.py — guided walkthroughs of the twin.

A scenario is a small JSON file in scenarios/: a title, what it is about, and
steps. Each step says what happens and why, has at most one thing to do, and
names the readings to watch with what they should become:

    {"title": "...", "why": "...",
     "do": {"type": "event",    "event": {"op": "hopper", "grams": 1000}, "label": "..."}   a person's act at the bed
         | {"type": "events",   "events": [...], "label": "..."}
         | {"type": "proposal", "id": "feed", "label": "...", "limit": 3}    a robot job, as proposed (id or its prefix)
         | {"type": "estop",    "on": true, "label": "..."}
         | {"type": "disturb",  "id": "cold-snap", "calmFirst": true, "label": "..."}   a chaos monkey (disturbances.py)
         | {"type": "calm",     "label": "..."}                             end every disturbance
         | {"type": "time",     "factor": 30, "label": "..."}               simulated minutes per real second
         | {"type": "none"},
     "expect": [{"read": "bed.hopper.grams", "is": ">=", "value": 1000, "say": "hopper: {v} g"}],
     "noWait": true}                       the step is complete while its job still runs

Readings: bed.<path> (the twin's /bed/state), job.state, last.<kind>.state
(the latest job of that kind since the step began), proposals.<id prefix>
(how many), count.<cell status>, driest (planted cells' lowest moisture, %),
notices (how many), keeper.<rule> (true while that reaction is in force),
holds.<kind> (true while jobs of that kind are on hold), lockout (true while
the gantry is locked out). A step marked "scored": true counts towards the
run's score: how many scored steps came out as expected.

Starting a scenario is the person's choice; doing a step is a click (or, with
"run every step", their one confirmation for the whole scenario). The runner
keeps no state on disk: a restart of the app ends a run. Standard library only.
"""

from __future__ import annotations

import json
import operator
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FOLDER = Path(__file__).resolve().parent / "scenarios"
OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq, "!=": operator.ne}
DO_TYPES = ("event", "events", "proposal", "estop", "disturb", "calm", "time", "none")

_run: Dict[str, Any] = {}
_score: Dict[str, Any] = {}


def problems(s: Any) -> List[str]:
    """Everything wrong with a scenario file, at once."""
    if not isinstance(s, dict):
        return ["a scenario is a JSON object"]
    out = [f"{k} is missing" for k in ("title", "about", "steps") if not s.get(k)]
    for i, step in enumerate(s.get("steps") or [], 1):
        if not isinstance(step, dict) or not step.get("title") or not step.get("why"):
            out.append(f"step {i} needs a title and a why")
            continue
        do = step.get("do") or {}
        if do.get("type") not in DO_TYPES:
            out.append(f"step {i}: do.type is one of {', '.join(DO_TYPES)}")
        elif do["type"] != "none" and not do.get("label"):
            out.append(f"step {i}: its button needs a label")
        for e in step.get("expect") or []:
            if e.get("is") not in OPS or "read" not in e or "value" not in e or "{v}" not in str(e.get("say", "")):
                out.append(f"step {i}: an expectation needs read, is ({' '.join(OPS)}), value and say with {{v}}")
    return out


def load_all(folder: Path = FOLDER) -> Dict[str, Dict[str, Any]]:
    """Every valid scenario by id (its file name). A broken file is left out."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(folder.glob("*.json")):
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not problems(s):
            out[path.stem] = s
    return out


def available(s: Dict[str, Any], cfg: Optional[Dict[str, Any]]) -> Optional[str]:
    """None when the prepared bed can run it, else why not."""
    if not cfg:
        return "prepare a world first"
    for need in s.get("needs") or []:
        if need == "wormBin" and not (cfg.get("wormBin") or {}).get("cols"):
            return "this bed has no worm box"
        if need == "tank" and not (cfg.get("tank") or {}).get("litres"):
            return "this bed has no tank pipes"
    return None


# ------------------------------------------------------------------ readings --

def read(path: str, twin: Dict[str, Any], proposals: List[Dict[str, Any]], notices: List[str], since: float,
         kept: Optional[Dict[str, Any]] = None) -> Any:
    bed = twin.get("bed") or {}
    kept = kept or {}
    if path.startswith("keeper."):
        return path.split(".", 1)[1] in kept.get("active", [])
    if path.startswith("holds."):
        return path.split(".", 1)[1] in kept.get("holds", [])
    if path == "lockout":
        return bool(kept.get("lockout"))
    cells = [c for c in (bed.get("cells") or {}).values()]
    if path.startswith("bed."):
        v: Any = bed
        for key in path.split(".")[1:]:
            v = v.get(key) if isinstance(v, dict) else None
        return v
    if path == "job.state":
        return (twin.get("job") or {}).get("state")
    if path.startswith("last."):
        kind = path.split(".")[1]
        jobs = [j for j in [*(twin.get("history") or []), twin.get("job") or {}]
                if j.get("kind") == kind and (j.get("started") or 0) >= since - 0.5]
        return jobs[-1].get("state") if jobs else None
    if path.startswith("proposals."):
        return sum(1 for p in proposals if p["id"].startswith(path.split(".", 1)[1]))
    if path.startswith("count."):
        return sum(1 for c in cells if c.get("status") == path.split(".", 1)[1])
    if path == "driest":
        planted = [c["moisture"] for c in cells if c.get("status") in ("CellSown", "CellGrowing") and "moisture" in c]
        return min(planted) if planted else None
    if path == "notices":
        return len(notices)
    return None


def checks(step: Dict[str, Any], twin, proposals, notices, since: float, kept=None) -> List[Dict[str, Any]]:
    out = []
    for e in step.get("expect") or []:
        v = read(e["read"], twin, proposals, notices, since, kept)
        try:
            ok = v is not None and bool(OPS[e["is"]](v, e["value"]))
        except TypeError:
            ok = False
        shown = "–" if v is None else (f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v).lower() if isinstance(v, bool) else str(v))
        out.append({"say": e["say"].replace("{v}", shown), "ok": ok})
    return out


# ------------------------------------------------------------------- the run --

def start(scenario_id: str, cfg: Optional[Dict[str, Any]]) -> None:
    s = load_all().get(scenario_id)
    if s is None:
        raise ValueError("no such scenario")
    why = available(s, cfg)
    if why:
        raise ValueError(why)
    _run.clear()
    _run.update(id=scenario_id, index=0, did=False, since=time.time(), results=[])
    _score.clear()


def stop() -> None:
    _run.clear()


def current() -> Optional[Dict[str, Any]]:
    s = load_all().get(_run.get("id", ""))
    return None if s is None else {**_run, "scenario": s, "step": s["steps"][_run["index"]]}


def action(proposals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the current step asks to be done, as plain data for the caller to carry out:
    {"events": [...]} | {"job": spec} | {"estop": bool} | {}. Raises ValueError when it cannot be done now."""
    run = current()
    if run is None:
        raise ValueError("no scenario is running")
    do = run["step"]["do"]
    if do["type"] == "event":
        return {"events": [do["event"]]}
    if do["type"] == "events":
        return {"events": list(do["events"])}
    if do["type"] == "estop":
        return {"estop": bool(do.get("on", True))}
    if do["type"] == "disturb":
        return {"disturb": do["id"], "calm": bool(do.get("calmFirst")), **({"minutes": do["minutes"]} if "minutes" in do else {})}
    if do["type"] == "calm":
        return {"calm": True}
    if do["type"] == "time":
        return {"factor": float(do.get("factor", 1))}
    if do["type"] == "proposal":
        p = next((p for p in proposals if p["id"] == do["id"] or (do["id"].endswith("-") and p["id"].startswith(do["id"]))), None)
        if p is None:
            raise ValueError("the twin does not propose that job now")
        spec = dict(p["spec"])
        if do.get("limit") and isinstance(spec.get("cells"), list):
            spec["cells"] = spec["cells"][: int(do["limit"])]
        return {"job": spec}
    return {}


def mark_done() -> None:
    _run.update(did=True, since=_run.get("since", time.time()))


def state(twin: Optional[Dict[str, Any]], proposals: List[Dict[str, Any]], notices: List[str],
          kept: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The run for the page: the step, whether it was done, its checks, and whether the next step may follow."""
    run = current()
    if run is None:
        return None
    twin = twin or {}
    step, s = run["step"], run["scenario"]
    found = checks(step, twin, proposals, notices, run["since"], kept)
    nothing_to_do = step["do"]["type"] == "none"
    busy = (twin.get("job") or {}).get("state") == "running" and not step.get("noWait")
    did = run["did"] or nothing_to_do
    return {"id": run["id"], "title": s["title"], "about": s["about"], "index": run["index"], "total": len(s["steps"]),
            "step": {"title": step["title"], "why": step["why"], "label": step["do"].get("label"), "type": step["do"]["type"]},
            "did": did, "checks": found, "busy": busy, "scored": bool(step.get("scored")),
            "ready": did and not busy and all(c["ok"] for c in found),
            "last": run["index"] == len(s["steps"]) - 1}


def last_score() -> Optional[Dict[str, Any]]:
    """The finished run's score, for scenarios with scored steps: {"title", "passed", "of", "failed": [titles]}."""
    return dict(_score) if _score else None


def advance(ok: bool = True) -> bool:
    """To the next step; False when that was the last one (the run ends). `ok`: the step came out as
    expected (False when it was skipped); scored steps are counted."""
    run = current()
    if run is None:
        return False
    if run["step"].get("scored"):
        _run.setdefault("results", []).append({"title": run["step"]["title"], "ok": bool(ok)})
    if run["index"] + 1 >= len(run["scenario"]["steps"]):
        results = _run.get("results") or []
        stop()
        if results:
            _score.update(title=run["scenario"]["title"], passed=sum(1 for r in results if r["ok"]), of=len(results),
                          failed=[r["title"] for r in results if not r["ok"]])
        return False
    _run.update(index=run["index"] + 1, did=False, since=time.time())
    return True
