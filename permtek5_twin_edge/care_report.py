# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
care_report.py — the guild's care, told to the cloud (PermTek-5 docs/adr/0031).

The edge keeps the care record (maintenance.py). So that the site can show
what is due today, the edge sends the cloud its record, its care date and
the keeper's reactions on POST /edge/v1/care; the cloud works out what is due
with the same rules (maintenance.ts careDue) and keeps it with this edge.

The answer carries the chores a person pressed Done on in the site. They are
done here like a Done on this edge, and named in the next report (`applied`),
so the cloud forgets them only once they are in the record. The cloud never
asks for a robot job: those are confirmed on this edge.

    Reporter.wanted(report, now)   is a report due: the care changed, or an hour went by
    Reporter.send(call, report, apply, now)   one report; apply(task_id) does a returned Done
                                              (every returned id is named back, done or refused)

Never in a rehearsal, and only with a linked cloud: the caller checks both.
A failed report is tried again after a few minutes. Standard library only.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

EVERY_S = 3600.0          # at least hourly, so the site can tell a quiet guild from a silent one
RETRY_S = 300.0           # after a failed report


def report(space_id: str, guild_id: str, today: str, record: Dict[str, Any], active: Optional[List[str]]) -> Dict[str, Any]:
    """What one report says: the space and guild, the edge's care date, when each task was last done, and the
    keeper's reactions in force."""
    return {"spaceId": space_id, "guildId": guild_id, "today": today,
            "lastDone": dict(record.get("done") or {}), "active": sorted(active or [])}


class Reporter:
    def __init__(self) -> None:
        self.sent: Optional[str] = None       # what the last report said
        self.at = 0.0                         # when it was sent, or last tried
        self.failed = False
        self.applied: List[str] = []          # Done presses from the site, carried out here, to name next time
        self.last: Optional[Dict[str, Any]] = None   # the cloud's last answer, for the page

    @staticmethod
    def _key(rep: Dict[str, Any]) -> str:
        return json.dumps(rep, sort_keys=True)

    def wanted(self, rep: Dict[str, Any], now: float) -> bool:
        if self.failed:
            return now - self.at >= RETRY_S
        return bool(self.applied) or self._key(rep) != self.sent or now - self.at >= EVERY_S

    def send(self, call: Callable[[str, Dict[str, Any]], Dict[str, Any]], rep: Dict[str, Any],
             apply: Callable[[str], bool], now: float) -> Dict[str, Any]:
        """Send one report; carry out the Done presses it brings back. Raises what `call` raises."""
        self.at = now
        try:
            answer = call("care", {**rep, "applied": list(self.applied)})
        except Exception:
            self.failed = True
            raise
        self.failed = False
        self.sent = self._key(rep)
        self.applied = []
        for task_id in answer.get("done") or []:
            apply(str(task_id))                   # one this edge cannot do is named too: the cloud drops it
            self.applied.append(str(task_id))
        self.last = answer
        return answer
