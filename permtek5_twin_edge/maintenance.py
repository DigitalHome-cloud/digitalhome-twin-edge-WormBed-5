# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
maintenance.py — the guild's care calendar, followed.

The calendar itself is data and belongs to PermTek-5
(packages/ontology/guides/wormbed5-care.json; scripts/sync-ontology.sh copies
it here, and packages/core/src/maintenance.ts shows it in the cloud). Each
task says who does it (the robot or a person), how often (every so many
days) or when (months of the year), and which reactions of the keeper bring
it forward. This edge keeps the RECORD of when each task was last done, in
care.json in the guild's folder, and says what is due:

    due(guide, record, today, active)     every task with "due" and why
    done(folder, task_id, today)          a person did it (or the robot's job finished)
    task_for_job(guide, spec)             which task a robot job does
    autopilot_policy(guide)               what the autopilot does in a rehearsal, and in which order

The dates are the simulation's: day 1 of the twin is the day the record was
started (`start`), so a fast clock runs through the year. The same rules as
maintenance.ts; keep the two alike.

Pure functions over plain data, and a small JSON file. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

GUIDE = Path(__file__).resolve().parent / "data" / "wormbed5-care.json"
RECORD = "care.json"


def load(path: Path = GUIDE) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _day(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def _season_start(months: List[int], today: dt.date) -> Optional[dt.date]:
    """The first day of the run of the task's months that `today` lies in, or None outside its months."""
    if today.month not in months:
        return None
    first, year = today.month, today.year
    for _ in range(11):
        prev = 12 if first == 1 else first - 1
        if prev not in months:
            break
        if prev == 12:
            year -= 1
        first = prev
    return dt.date(year, first, 1)


def due(guide: Dict[str, Any], record: Dict[str, Any], today: Any, active: Optional[List[str]] = None,
        lang: str = "en") -> List[Dict[str, Any]]:
    """Every task with whether it is due today: {"id", "who", "due", "reason", "nextOn", "overdueDays", "job"}.
    reason: never | rhythm | season | brought (a keeper's reaction in `active`), or None."""
    now, active = _day(today), active or []
    last_of = (record or {}).get("done") or {}
    out = []
    for t in guide["tasks"]:
        last = _day(last_of[t["id"]]) if last_of.get(t["id"]) else None
        reason, next_on, overdue = None, None, 0
        if t.get("everyDays"):
            nxt = last + dt.timedelta(days=int(t["everyDays"])) if last else now
            next_on = nxt.isoformat()
            if not last:
                reason = "never"
            elif (now - nxt).days >= 0:
                reason, overdue = "rhythm", (now - nxt).days
        if not reason and t.get("months"):
            start = _season_start(t["months"], now)
            if start and (not last or last < start):
                reason = "season"
        if not reason and any(r in active for r in t.get("bringForwardOn") or []) and (not last or (now - last).days >= 1):
            reason = "brought"
        out.append({"id": t["id"], "who": t["who"], "due": reason is not None, "reason": reason, "nextOn": next_on,
                    "overdueDays": overdue, "job": t.get("job"), "label": t["label"].get(lang) or t["label"].get("en"), "why": t["why"].get(lang) or t["why"].get("en")})
    return out


def sim_date(record: Dict[str, Any], env: Dict[str, Any]) -> dt.date:
    """The simulation's date: the record's start plus the twin's days."""
    start = _day(record.get("start") or dt.date.today())
    return start + dt.timedelta(days=max(0, int(env.get("day", 1)) - 1))


def read(folder: Path) -> Dict[str, Any]:
    try:
        return json.loads((Path(folder) / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def begin(folder: Path, guide: Dict[str, Any], today: Optional[dt.date] = None) -> Dict[str, Any]:
    """A fresh record: the simulation starts today, and every task that comes round by a rhythm counts as just
    done (a settled guild, not one where everything is overdue). Seasonal tasks wait for their months."""
    today = today or dt.date.today()
    record = {"start": today.isoformat(),
              "done": {t["id"]: today.isoformat() for t in guide["tasks"] if t.get("everyDays")}}
    _write(folder, record)
    return record


def done(folder: Path, task_id: str, today: Any) -> Dict[str, Any]:
    record = read(folder)
    record.setdefault("done", {})[task_id] = _day(today).isoformat()
    _write(folder, record)
    return record


def task_for_job(guide: Dict[str, Any], spec: Dict[str, Any]) -> Optional[str]:
    """The task a robot job does: a job "care" with what "bedding" is "care:bedding"."""
    kind = str(spec.get("kind") or "")
    names = {kind, f'{kind}:{spec.get("what")}'} if spec.get("what") else {kind}
    return next((t["id"] for t in guide["tasks"] if t.get("job") in names), None)


AUTOPILOT_DEFAULTS: Dict[str, Any] = {
    "order": ["harvest", "clear", "period", "water", "valve", "feed", "care", "rest", "tap", "topdress", "plant",
              "stamp", "scan", "probe", "photo"],
    "daily": ["scan", "probe", "photo"],
    "refills": {"hopperGrams": 300, "brownsGrams": 200, "tankShare": 0.2},
    "fills": {"hopper": {"vegetable": 600, "fruit": 300, "coffee": 100}, "brownsGrams": 1000},
}


def autopilot_policy(guide: Dict[str, Any]) -> Dict[str, Any]:
    """The guide's "autopilot" block (PermTek-5 docs/adr/0031), each part falling back to the defaults:
    order (proposal kinds, most needed first), daily (looking jobs, once a simulated day), refills (the levels
    under which the hopper, the bedding and the tank are refilled) and fills (what goes in). As autopilotPolicy
    in maintenance.ts."""
    a = guide.get("autopilot") or {}
    d = AUTOPILOT_DEFAULTS
    return {"order": list(a.get("order") or d["order"]), "daily": list(a.get("daily") or d["daily"]),
            "refills": {**d["refills"], **(a.get("refills") or {})}, "fills": {**d["fills"], **(a.get("fills") or {})}}


def _write(folder: Path, record: Dict[str, Any]) -> None:
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / RECORD).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass
