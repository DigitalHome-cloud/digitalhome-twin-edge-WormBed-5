# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
loop_view.py — the twin as one loop: Prepare, then Run, Observe, Control and back to Run, with the Chaos Monkey
in the middle. What the home screen shows, worked out from the same state the other screens use.

    readiness(state, log_lines, now)   Prepare's checklist: the world, Webots, the gantry, the cameras;
                                       what it waits for, and why it failed when it did
    loop(state, scenario, catalogue)   every stage's summary and the stage the twin is in now

The stage now: not running (or not ready) is Prepare; something only a person can do (an alarm, a care task
that is theirs, a disturbance whose reaction has not come) is Control; a reading near a limit or a scenario
step to watch is Observe; otherwise Run.

Pure functions over plain data; standard library only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .i18n import _
from .operator_view import RULES

STAGES = ("prepare", "run", "observe", "control")
# a keeper's notice the robot or a valve can see to: the proposals that do it (engine.py). In Control the notice
# shows its proposal beside it, to confirm, instead of a chore for the person.
COVERS = {"box-dry": ("valve-mist", "moisten"), "heat": ("valve-mist",), "box-wet": ("bedding",), "airless": ("bedding",),
          "overfed": ("bedding",), "sump-full": ("valve-bed", "valve-garden", "tap"), "barrel-refill": ("valve-barrel",),
          "tank-low": ("valve-barrel",), "tank-empty": ("valve-barrel",)}
READY = ("world", "webots", "gantry", "cameras")
WAITING = {"world": "the world to be prepared", "webots": "Webots to start", "gantry": "the gantry to connect to Webots",
           "cameras": "the robot's cameras"}
SLOW_S = 90                       # a start that takes longer than this says what it is waiting for

# what a line of the simulation's log means, for a start that failed or hangs
CAUSES = [
    ("Cannot connect to Webots instance", "Webots runs, but the gantry cannot reach it. Webots and its controller look for each other in different temporary folders: start the edge from a plain terminal, or update it."),
    ("is not in the list of robots with <extern> controllers", "Webots has not loaded the gantry yet."),
    ("process has finished cleanly [pid", "Webots closed. If you closed its window, start again; otherwise try without the window."),
    ("could not open display", "Webots cannot open a window here: start without the window."),
    ("is missing: generate the world first", "The world is missing: prepare it again."),
]


def _cause(lines: List[str]) -> Optional[str]:
    text = "\n".join(lines)
    return next((_(say) for needle, say in CAUSES if needle in text), None)


def readiness(state: Dict[str, Any], log_lines: List[str], now: float) -> Dict[str, Any]:
    """Prepare's checklist. Each step is done, waiting or failed; `next` is the first not done."""
    twin = state.get("twin") or {}
    running = bool(state.get("running"))
    done = {"world": bool(state.get("prepared")),
            "webots": running and (bool(twin) or any("extern controller" in ln for ln in log_lines)),
            "gantry": running and twin.get("gantry") is not None,
            "cameras": running and len(twin.get("cameras") or []) >= 2}
    nxt = next((k for k in READY if not done[k]), None)
    out: Dict[str, Any] = {"steps": [{"id": k, "done": done[k]} for k in READY], "next": nxt, "ready": nxt is None,
                           "running": running, "failed": None, "slow": None}
    if not running and state.get("exit") is not None:            # it ran and stopped by itself
        out["failed"] = _cause(state.get("log") or log_lines) or _("The simulation stopped. Its last lines are in the log.")
        out["log"] = (state.get("log") or log_lines)[-6:]
    elif running and nxt and state.get("started") is not None and now - float(state["started"]) > SLOW_S:
        out["slow"] = _cause(log_lines) or _("Still waiting for {what}.", what=_(WAITING[nxt]))
        out["log"] = log_lines[-6:]
    return out


def _env(state: Dict[str, Any]) -> Dict[str, Any]:
    return ((state.get("twin") or {}).get("bed") or {}).get("env") or state.get("env") or {}


def chaos(state: Dict[str, Any], catalogue: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The Chaos Monkey: its rate, what is in force, and for each whether the keeper's owed reaction came."""
    env = _env(state)
    active = set((state.get("keeper") or {}).get("active") or [])
    now = []
    for d in env.get("disturbances") or []:
        owes = (catalogue.get(d["id"]) or {}).get("owes") or []
        now.append({"id": d["id"], "title": _(d.get("title") or d["id"]), "leftMin": d.get("leftMin"),
                    "owes": [{"rule": r, "say": _(RULES.get(r, r)), "came": r in active} for r in owes]})
    return {"rate": env.get("chaosRate") or 0, "active": now, "pending": sum(1 for d in now for o in d["owes"] if not o["came"])}


def loop(state: Dict[str, Any], scenario: Optional[Dict[str, Any]], catalogue: Dict[str, Dict[str, Any]],
         ready: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    env = _env(state)
    kept = state.get("keeper") or {}
    notices = kept.get("notices") or []
    count = (state.get("operator") or {}).get("count") or {}
    care = (state.get("care") or {}).get("tasks") or []
    due = [t for t in care if t.get("due")]
    proposals = state.get("proposals") or []
    monkey = chaos(state, catalogue)
    ready = ready or readiness(state, [], 0.0)

    by_id = {p["id"]: p for p in proposals}
    issues = []
    for n in notices:
        fix = next((by_id[i] for i in COVERS.get(n["id"], ()) if i in by_id), None)
        issues.append({"id": n["id"], "text": n["text"], "level": n.get("level", "note"), "holds": n.get("holds") or [],
                       "proposal": fix and {"id": fix["id"], "title": fix["title"]}})
    covered = {i["proposal"]["id"] for i in issues if i["proposal"]}
    issues.sort(key=lambda n: n["level"] != "alert")
    person_due = [t for t in due if t.get("who") == "person"]
    run_step = (scenario or {}).get("run") or None

    if not ready.get("ready"):
        now = "prepare"
    elif any(n["level"] == "alert" for n in issues) or kept.get("lockout") or person_due or monkey["pending"]:
        now = "control"
    elif issues or count.get("watch") or count.get("alarm") or (run_step and not run_step.get("ready")):
        now = "observe"
    else:
        now = "run"

    return {
        "now": now,
        "run": {"clock": env.get("clock"), "date": env.get("date"), "season": env.get("season"), "night": env.get("night"),
                "airC": env.get("airC"), "rainMmH": env.get("rainMmH"), "factor": env.get("factor"),
                "sunrise": env.get("sunrise"), "sunset": env.get("sunset"), "paused": env.get("factor") == 0},
        "observe": {"count": count, "attention": issues[:3], "step": run_step,
                    "inWebots": [{"act": a["act"], "clock": a.get("clock")} for a in (((state.get("twin") or {}).get("bed") or {}).get("acts") or [])][-3:]},
        "control": {"maintenance": [{"id": t["id"], "label": t.get("label") or t["id"], "who": t.get("who"), "job": t.get("job"),
                                     "reason": t.get("reason"), "overdueDays": t.get("overdueDays", 0)} for t in due],
                    "issues": issues, "holds": kept.get("holds") or [], "lockout": kept.get("lockout"),
                    "proposals": [{"id": p["id"], "title": p["title"], "kind": p.get("kind")} for p in proposals if p["id"] not in covered][:3],
                    "proposalCount": len(proposals) - len(covered)},
        "chaos": monkey,
    }
