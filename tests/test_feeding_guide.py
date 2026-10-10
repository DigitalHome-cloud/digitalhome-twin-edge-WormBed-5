# SPDX-License-Identifier: GPL-3.0-or-later
"""The feeding guide, rule by rule."""

from __future__ import annotations

from permtek5_twin_edge import feeding_guide as fg

G = fg.load()
DAY = 1440.0


def feed(t, grams=300, spot=0, mix=None):
    return {"t": t, "what": "feed", "grams": grams, "spot": spot, "mix": mix or {"vegetable": grams}}


def test_the_edges_copy_is_the_guide_with_every_number_the_twin_needs():
    assert G["guildType"] == "WormBed5" and {"yes", "limited", "never", "browns"} == set(G["classes"])
    kinds = fg.kinds_for_twin(G)
    assert kinds["citrus"]["acid"] > kinds["vegetable"]["acid"] > kinds["eggshell"]["acid"]
    assert kinds["paper"]["wet"] < 0 < kinds["fruit"]["wet"]


def test_never_kinds_are_refused_by_name_and_unknown_things_too():
    r = fg.check_load(G, {"vegetable": 600, "cooked": 200, "meat": 50}, [], 0.0)
    assert not r["ok"] and r["rule"] == "never" and "cooked or salted food" in r["text"] and "meat and fish" in r["text"]
    assert fg.check_load(G, {"plastic": 10}, [], 0.0)["rule"] == "never"
    assert fg.check_load(G, {"vegetable": 600, "fruit": 300, "coffee": 100}, [], 0.0) == {"ok": True}
    assert fg.check_load(G, {}, [], 0.0) == {"ok": True}
    assert "Nie in die Kiste" in fg.check_load(G, {"dairy": 5}, [], 0.0, lang="de")["text"]


def test_limited_kinds_have_a_weekly_share():
    assert fg.check_load(G, {"vegetable": 900, "citrus": 100}, [], 0.0)["ok"], "exactly a tenth"
    r = fg.check_load(G, {"vegetable": 700, "citrus": 300}, [], 0.0)
    assert r["rule"] == "limited" and "citrus" in r["text"] and "10 %" in r["text"]
    # the week counts: citrus already fed leaves less room
    diary = [feed(100.0, 1000, mix={"vegetable": 800, "citrus": 200})]
    assert not fg.check_load(G, {"vegetable": 950, "citrus": 50}, diary, 200.0)["ok"]
    assert fg.check_load(G, {"vegetable": 950, "citrus": 50}, diary, 200.0 + 8 * DAY)["ok"], "a week later it is forgotten"


def test_when_and_where():
    diary = [feed(1000.0, spot=1)]
    soon = fg.check_feed(G, 300, 2, diary, 1000.0 + 5 * 60, spots=4)
    assert soon["rule"] == "rest" and soon["againInMin"] == 420 and "12 hours" in soon["text"]
    later = 1000.0 + 13 * 60
    assert fg.check_feed(G, 300, 2, diary, later, spots=4)["ok"]
    assert fg.check_feed(G, 300, 1, diary, later, spots=4)["rule"] == "spot"
    assert fg.check_feed(G, 300, 1, diary, later, spots=1)["ok"], "a box with one spot has no other"
    assert fg.check_feed(G, 300, 2, diary, later, core_c=8.0, spots=4)["rule"] == "cold"


def test_how_much():
    ok = fg.check_feed(G, 900, 0, [], 0.0, eats_per_day=400, uneaten_g=100)
    assert ok["ok"] and ok["maxGrams"] == 500, "at most 500 g on one spot"
    assert fg.check_feed(G, 300, 0, [], 0.0, eats_per_day=400, uneaten_g=600)["maxGrams"] == 200, "two days' eating, less what lies there"
    assert fg.check_feed(G, 300, 0, [], 0.0, eats_per_day=400, uneaten_g=790)["rule"] == "amount"


def test_browns_keep_pace_with_scraps():
    diary = [feed(i * DAY, 400, spot=i % 4) for i in range(4)]          # 1.6 kg of scraps, no browns on record
    now = 4 * DAY
    r = fg.check_feed(G, 400, 0, diary, now, spots=4)
    assert r["rule"] == "browns", "the box's starting bedding is used up"
    due = fg.browns_due(G, 400, diary, now)
    assert due == 200 and fg.check_feed(G, 400, 0, diary, now, spots=4, with_browns_g=due)["ok"]
    diary.append({"t": now, "what": "bedding", "grams": 500})
    assert fg.check_feed(G, 400, 0, diary, now, spots=4)["ok"] and fg.browns_due(G, 400, diary, now) == 0
    line = fg.week_line(G, diary + [{"t": now, "what": "load", "verdict": "never"}], now)
    assert line == {"scrapsG": 1600, "brownsG": 500, "feedings": 4, "refusedLoads": 1, "brownsOk": True}
