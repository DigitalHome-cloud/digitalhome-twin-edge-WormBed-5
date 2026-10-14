# SPDX-License-Identifier: GPL-3.0-or-later
"""The home screen's loop: which stage the twin is in, what Prepare waits for, and the Chaos Monkey's debts."""

from __future__ import annotations

from permtek5_twin_edge import disturbances, loop_view

ENV = {"day": 3, "clock": "day 3 · 14:00", "factor": 30, "date": "2026-04-03", "season": "spring", "night": False,
       "airC": 12.0, "chaosRate": 0, "disturbances": []}


def running(**over):
    state = {"running": True, "prepared": True, "started": 0.0,
             "twin": {"gantry": {"x": 0}, "cameras": ["tool", "overview"], "bed": {"env": dict(ENV)}},
             "keeper": {"active": [], "holds": [], "lockout": None, "notices": []},
             "operator": {"count": {"ok": 20, "watch": 0, "alarm": 0}}, "care": {"tasks": []}, "proposals": []}
    state.update(over)
    return state


def now(state, scenario=None):
    return loop_view.loop(state, scenario, disturbances.CATALOGUE, loop_view.readiness(state, [], 10.0))["now"]


def test_not_running_is_prepare_and_a_calm_running_twin_is_run():
    assert now({"running": False, "prepared": True}) == "prepare"
    assert now(running()) == "run"


def test_a_twin_without_its_gantry_is_still_preparing_and_says_what_it_waits_for():
    state = running(twin={"cameras": []})
    ready = loop_view.readiness(state, ["... Cannot connect to Webots instance, retrying ..."], 200.0)
    assert ready["next"] == "gantry" and not ready["ready"]
    assert "temporary folders" in ready["slow"], "after 90 s it names the cause it sees in the log"
    assert now(state) == "prepare"


def test_a_start_that_ended_by_itself_says_why():
    state = {"running": False, "prepared": True, "exit": 0,
             "log": ["[INFO] [webots-1]: process has finished cleanly [pid 12]"]}
    ready = loop_view.readiness(state, [], 0.0)
    assert ready["failed"].startswith("Webots closed") and ready["log"]


def test_a_reading_near_a_limit_is_observe_and_an_alarm_or_a_persons_task_is_control():
    assert now(running(operator={"count": {"ok": 19, "watch": 1, "alarm": 0}})) == "observe"
    alarm = {"active": ["frost"], "holds": ["feed"], "lockout": None,
             "notices": [{"id": "frost", "text": "Frost: wrap the box", "level": "alert", "holds": ["feed"]}]}
    view = loop_view.loop(running(keeper=alarm), None, disturbances.CATALOGUE)
    assert view["now"] == "control" and view["control"]["issues"][0]["id"] == "frost" and view["control"]["holds"] == ["feed"]
    care = {"tasks": [{"id": "wrap", "who": "person", "due": True, "label": "Wrap the box"},
                      {"id": "feed", "who": "robot", "due": False}]}
    view = loop_view.loop(running(care=care), None, disturbances.CATALOGUE)
    assert view["now"] == "control" and [t["id"] for t in view["control"]["maintenance"]] == ["wrap"]


def test_a_scenario_step_to_watch_is_observe():
    assert now(running(), {"run": {"title": "A normal year", "ready": False}}) == "observe"
    assert now(running(), {"run": {"title": "A normal year", "ready": True}}) == "run"


def test_the_chaos_monkey_owes_until_the_keeper_reacts():
    env = {**ENV, "chaosRate": 2, "disturbances": [{"id": "cold-snap", "title": "Cold snap", "leftMin": 300}]}
    state = running(twin={"gantry": {}, "cameras": ["a", "b"], "bed": {"env": env}})
    view = loop_view.loop(state, None, disturbances.CATALOGUE)
    owed = view["chaos"]["active"][0]["owes"]
    assert view["chaos"]["rate"] == 2 and [(o["rule"], o["came"]) for o in owed] == [("frost", False)] and owed[0]["say"]
    assert view["now"] == "control", "a reaction still owed is the person's to see to"
    state["keeper"] = {"active": ["frost"], "holds": [], "lockout": None, "notices": [{"id": "frost", "text": "t", "level": "note"}]}
    view = loop_view.loop(state, None, disturbances.CATALOGUE)
    assert view["chaos"]["pending"] == 0 and view["chaos"]["active"][0]["owes"][0]["came"]


def test_run_shows_the_clock_and_the_season_and_a_paused_clock():
    view = loop_view.loop(running(), None, disturbances.CATALOGUE)
    assert view["run"]["season"] == "spring" and view["run"]["date"] == "2026-04-03" and not view["run"]["paused"]
    paused = running(twin={"gantry": {}, "cameras": ["a", "b"], "bed": {"env": {**ENV, "factor": 0}}})
    assert loop_view.loop(paused, None, disturbances.CATALOGUE)["run"]["paused"]


def test_a_notice_the_robot_can_see_to_shows_its_proposal_and_leaves_the_list():
    kept = {"active": ["box-dry"], "holds": [], "lockout": None,
            "notices": [{"id": "box-dry", "text": "The worm box is drying out", "level": "note"}]}
    proposals = [{"id": "valve-mist", "title": "Mist the worm box from the rain barrel: 1.5 l", "kind": "valve"},
                 {"id": "probe", "title": "Probe", "kind": "probe"}]
    ctl = loop_view.loop(running(keeper=kept, proposals=proposals), None, disturbances.CATALOGUE)["control"]
    assert ctl["issues"][0]["proposal"]["id"] == "valve-mist"
    assert [p["id"] for p in ctl["proposals"]] == ["probe"] and ctl["proposalCount"] == 1
