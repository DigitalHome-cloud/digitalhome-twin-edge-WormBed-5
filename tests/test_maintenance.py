# SPDX-License-Identifier: GPL-3.0-or-later
"""The care calendar, followed on the edge (the same rules as PermTek-5's maintenance.ts)."""

from __future__ import annotations

import datetime as dt

from permtek5_twin_edge import maintenance as m

G = m.load()


def by_id(tasks):
    return {t["id"]: t for t in tasks}


def test_the_guide_came_from_permtek5_and_every_robot_task_names_its_job():
    assert G["id"] == "wormbed5-care" and len(G["tasks"]) >= 15
    jobs = {t["id"]: t.get("job") for t in G["tasks"] if t["who"] == "robot"}
    assert jobs["bedding"] == "care:bedding" and jobs["sump"] == "tap" and jobs["harvest"] == "topdress"
    assert all(jobs.values())


def test_rhythm_season_and_brought_forward_like_the_cloud():
    d = by_id(m.due(G, {"done": {"hopper": "2026-10-01", "buffer": "2026-10-01"}}, "2026-10-08"))
    assert d["hopper"]["reason"] == "rhythm" and d["hopper"]["nextOn"] == "2026-10-08"
    assert not d["buffer"]["due"] and d["buffer"]["nextOn"] == "2026-10-31"
    assert by_id(m.due(G, {"done": {"wrap": "2025-11-02"}}, "2026-10-14"))["wrap"]["reason"] == "season"
    assert not by_id(m.due(G, {"done": {"wrap": "2026-10-20"}}, "2026-11-05"))["wrap"]["due"]
    assert by_id(m.due(G, {"done": {"sump": "2026-10-12"}}, "2026-10-14", ["sump-full"]))["sump"]["reason"] == "brought"


def test_a_fresh_record_is_a_settled_guild_and_done_is_kept(tmp_path):
    rec = m.begin(tmp_path, G, dt.date(2026, 7, 1))
    tasks = m.due(G, rec, "2026-07-01")
    assert not any(t["due"] for t in tasks), "nothing overdue on day one in July"
    week = by_id(m.due(G, rec, "2026-07-08"))
    assert week["hopper"]["due"] and week["weeds"]["due"] and not week["buffer"]["due"]
    m.done(tmp_path, "hopper", "2026-07-08")
    assert not by_id(m.due(G, m.read(tmp_path), "2026-07-08"))["hopper"]["due"]
    assert m.sim_date(m.read(tmp_path), {"day": 31}) == dt.date(2026, 7, 31)


def test_which_task_a_robot_job_does():
    assert m.task_for_job(G, {"kind": "care", "what": "bedding", "grams": 200}) == "bedding"
    assert m.task_for_job(G, {"kind": "care", "what": "moisten"}) is None
    assert m.task_for_job(G, {"kind": "scan"}) == "weeds"
    assert m.task_for_job(G, {"kind": "plant", "row": 1, "col": 1}) is None


def test_the_autopilot_follows_the_care_guides_policy():
    """PermTek-5 docs/adr/0031: the order, the daily looks and the refill levels are the guide's data, the same the
    cloud reads; they are what the autopilot always did."""
    from permtek5_twin_edge import app as web
    guide = m.load()
    assert guide["version"] >= 2 and "autopilot" in guide
    assert web.AUTO_ORDER == ("harvest", "clear", "period", "water", "valve", "feed", "care", "rest", "tap", "topdress",
                              "plant", "stamp", "scan", "probe", "photo")
    assert web.AUTO_DAILY == ("scan", "probe", "photo")
    assert (web.HOPPER_LOW_G, web.BROWNS_LOW_G, web.TANK_LOW) == (300, 200, 0.2)
    assert web.AUTO["fills"] == {"hopper": {"vegetable": 600, "fruit": 300, "coffee": 100}, "brownsGrams": 1000}
    own = m.autopilot_policy({"tasks": [], "autopilot": {"refills": {"tankShare": 0.5}}})
    assert own["refills"] == {"hopperGrams": 300, "brownsGrams": 200, "tankShare": 0.5}, "an unset level keeps its default"
    assert m.autopilot_policy({"tasks": []}) == m.AUTOPILOT_DEFAULTS
