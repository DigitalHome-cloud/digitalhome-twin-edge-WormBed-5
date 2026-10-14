# SPDX-License-Identifier: GPL-3.0-or-later
"""English, German and French: the catalogs are complete, and the page and the server speak the person's language."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from permtek5_twin_edge import app as web
from permtek5_twin_edge import i18n, keeper, operator_view
from permtek5_twin_edge.core import version
from permtek5_twin_edge.core import workspace as ws
from permtek5_twin_edge.i18n import _

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "UPDATE_CHECK", False)
    monkeypatch.setitem(ws.FOLDERS, ws.USER, tmp_path / "user")
    monkeypatch.setitem(ws.FOLDERS, ws.SHARED, tmp_path / "shared")
    ws.configure()
    web.app.config["TESTING"] = True
    return web.app.test_client()


@pytest.fixture(autouse=True)
def english_again():
    yield
    i18n.use("en")


def test_every_text_has_a_german_and_a_french_translation_with_the_same_placeholders(capsys):
    spec = importlib.util.spec_from_file_location("i18n_check", ROOT / "scripts" / "i18n-check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    import sys
    argv, sys.argv = sys.argv, ["i18n-check.py"]
    try:
        assert check.main() == 0, capsys.readouterr().out
    finally:
        sys.argv = argv


def test_the_english_text_is_the_key_and_values_are_filled_in():
    assert _("Feed the worms: {g} g from the hopper", g=300) == "Feed the worms: 300 g from the hopper"
    i18n.use("de")
    assert _("Feed the worms: {g} g from the hopper", g=300) == "Die Würmer füttern: 300 g aus dem Trichter"
    assert _("a text nobody translated") == "a text nobody translated", "missing: it stays English"
    i18n.use("fr")
    assert _("Feeding guide: {text}", text="x") == "Guide d'alimentation : x", "a value named like the text itself"
    assert _("Bed", "de") == "Beet", "a language can be named for one text"


def test_the_language_is_the_persons_choice_else_the_browsers_else_english():
    assert i18n.pick("fr", "de-DE,de;q=0.9") == "fr"
    assert i18n.pick(None, "de-CH,de;q=0.9,en;q=0.8") == "de"
    assert i18n.pick("xx", "es-ES,it;q=0.5") == "en"
    assert i18n.use("klingon") == "en"


def test_the_keeper_and_the_tiles_speak_it():
    i18n.use("de")
    bed = {"env": {"airC": -5.0, "windMs": 2.0, "day": 1, "hour": 3.0}, "tank": {"litres": 100.0, "capacity": 200.0},
           "wormBin": {"tempC": 18.0, "edgeC": 17.0, "moisture": 70.0, "fillPct": 30.0}}
    kept = keeper.evaluate(bed, {})
    assert kept["notices"][0]["text"].startswith("Frost: Füttern und Gießen sind angehalten")
    tiles = {t["key"]: t for t in operator_view.tiles(bed, kept["active"])}
    assert tiles["airC"]["label"] == "Luft" and tiles["airC"]["why"] == "Frost"
    assert operator_view.sensor("tempC")["sensor"] == "Komposttemperatur, Kern"


def test_the_page_comes_in_the_chosen_language(client):
    client.set_cookie("lang", "de")
    page = client.get("/").get_data(as_text=True)
    assert '"lang": "de"' in page and "Beet" in page, "the catalog comes with the page"
    assert client.get("/api/i18n").get_json()["lang"] == "de"
    films = client.get("/api/twin/film").get_json()
    assert [s["title"] for s in films["shots"]][1] == "Die Wurmkiste aus der Nähe"
    client.set_cookie("lang", "fr")
    assert client.get("/api/twin/chaos").get_json()["disturbances"][0]["title"] == "Vague de froid"
