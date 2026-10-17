# SPDX-License-Identifier: GPL-3.0-or-later
"""The guild's care, told to the cloud (PermTek-5 docs/adr/0031): when, what, and the site's Done presses."""

from __future__ import annotations

import pytest

from permtek5_twin_edge import app as web
from permtek5_twin_edge import care_report, engine, habitat_store, maintenance
from permtek5_twin_edge.edge import cloud as edge_cloud

from test_logbook import client, twin  # noqa: F401  (fixtures)


def rep(today="2026-04-02", done=None, active=None):
    return care_report.report("space-1", "guild-1", today, {"done": done or {}}, active)


def test_a_report_goes_when_the_care_changed_or_an_hour_went_by():
    r = care_report.Reporter()
    calls = []
    call = lambda path, body: calls.append((path, body)) or {}
    assert r.wanted(rep(), 0.0)
    r.send(call, rep(), lambda t: True, 0.0)
    assert calls[0][0] == "care" and calls[0][1]["lastDone"] == {} and calls[0][1]["applied"] == []
    assert not r.wanted(rep(), 60.0), "nothing new"
    assert r.wanted(rep(done={"hopper": "2026-04-02"}), 60.0), "a task was done"
    assert r.wanted(rep(active=["frost"]), 60.0), "the keeper reacted"
    assert r.wanted(rep(), care_report.EVERY_S), "hourly in any case"


def test_a_done_from_the_site_is_carried_out_and_named_in_the_next_report():
    r = care_report.Reporter()
    calls, did = [], []
    answers = iter([{"done": ["hopper", "nonsense"]}, {"done": []}])
    call = lambda path, body: calls.append(body) or next(answers)
    r.send(call, rep(), lambda t: did.append(t) or t == "hopper", 0.0)
    assert did == ["hopper", "nonsense"]
    assert r.wanted(rep(), 1.0), "the cloud must hear what was applied"
    r.send(call, rep(), lambda t: True, 1.0)
    assert calls[1]["applied"] == ["hopper", "nonsense"], "a refused one too, so the cloud drops it"
    assert not r.wanted(rep(), 2.0)


def test_a_failed_report_is_tried_again_after_a_few_minutes():
    r = care_report.Reporter()

    def down(path, body):
        raise edge_cloud.CloudError("offline")

    with pytest.raises(edge_cloud.CloudError):
        r.send(down, rep(), lambda t: True, 0.0)
    assert not r.wanted(rep(), 10.0) and r.wanted(rep(), care_report.RETRY_S)


def test_only_a_run_of_the_real_bed_reports_never_a_rehearsal(client, twin, monkeypatch):  # noqa: F811
    state, _ = twin
    state["running"] = False
    client.post("/api/twin/prepare", json={})
    state["running"] = True
    sent = []
    monkeypatch.setattr(web, "_report_care", lambda graph, guild, out, kept: sent.append((graph, guild, kept["care"]["today"])))
    monkeypatch.setattr(habitat_store, "source", lambda out: {"graph": "our-garden"})
    monkeypatch.setattr(edge_cloud, "linked_profiles", lambda: ["stage"])
    cfg = engine.config()
    assert cfg.get("guild", {}).get("id"), "CityDemo's world has a guild"
    client.get("/api/twin")
    assert sent == [], "a rehearsal writes nothing anywhere"
    monkeypatch.setattr(engine, "config", lambda out=None: {**cfg, "rehearsal": False})
    client.get("/api/twin")
    assert sent and sent[0][:2] == ("our-garden", cfg["guild"]["id"])


def test_a_done_from_the_site_is_a_persons_task_only_and_goes_into_the_logbook(client, twin):  # noqa: F811
    state, _ = twin
    state["running"] = False
    client.post("/api/twin/prepare", json={})
    state["running"] = True
    client.get("/api/twin")                                    # the care record is started
    assert web._care_done_from_site("hopper") is True
    assert web._care_done_from_site("feed") is False, "the robot's tasks are done by its jobs"
    assert web._care_done_from_site("nothing") is False
    assert "hopper" in maintenance.read(engine.workspace())["done"]
    people = [e["text"] for e in client.get("/api/logbook?who=person").get_json()["entries"]]
    assert "done (from the site): Fill the hopper" in people
