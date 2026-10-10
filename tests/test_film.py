# SPDX-License-Identifier: GPL-3.0-or-later
"""The film job without Blender: a stand-in renderer that prints the same progress lines."""

from __future__ import annotations

import sys

import pytest

from permtek5_twin_edge import film
from permtek5_twin_edge.edge.jobs import Stopped

FAKE = "import sys,time,pathlib\nout=pathlib.Path(sys.argv[1])\nfor i in range(1,4):\n print(f'frame {i}/3',flush=True); time.sleep(0.6)\n(out/'poster.png').write_bytes(b'p'); (out/'film.mp4').write_bytes(b'f')\n"


def test_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(film, "PYTHON", tmp_path / "nope")
    assert any("setup-bpy.sh" in m for m in film.missing())
    with pytest.raises(RuntimeError):
        film.render(tmp_path / "bed.json", tmp_path, 72, lambda *a: None, lambda: False)


def test_the_command_names_the_bed_the_folder_and_the_frames():
    cmd = film.command(film.ROOT / "bed.json", film.ROOT / "out", 48)
    assert cmd[1].endswith("assets/demo.py") and cmd[-2:] == ["--frames", "48"]


def test_progress_follows_the_renderers_frames_and_the_result_names_the_files(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    seen = []
    out = film.render(bed, tmp_path, 3, lambda phase, done: seen.append((phase, done)), lambda: False,
                      cmd=[sys.executable, "-c", FAKE, str(tmp_path)])
    assert seen[0] == ("the still", 0.0) and seen[-1] == ("frame 3 of 3", 1.0)
    assert out["film"]["bytes"] == 1 and out["poster"] and out["frames"] == 3


def test_cancel_stops_the_renderer(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    calls = []
    with pytest.raises(Stopped):
        film.render(bed, tmp_path, 3, lambda *a: calls.append(a), lambda: len(calls) >= 2,
                    cmd=[sys.executable, "-c", FAKE, str(tmp_path)])
    assert not (tmp_path / "film.mp4").exists()


def test_a_failing_renderer_reports_its_last_lines(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    with pytest.raises(RuntimeError, match="no such crop"):
        film.render(bed, tmp_path, 3, lambda *a: None, lambda: False,
                    cmd=[sys.executable, "-c", "import sys; print('no such crop'); sys.exit(3)"])
