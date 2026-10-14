# SPDX-License-Identifier: GPL-3.0-or-later
"""The film job without Blender: a stand-in renderer that prints the same progress lines."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from permtek5_twin_edge import film
from permtek5_twin_edge.edge.jobs import Stopped

FAKE = "import sys,time,pathlib\nout=pathlib.Path(sys.argv[1])\nfor i in range(1,4):\n print(f'frame {i}/3',flush=True); time.sleep(0.6)\n(out/'poster-box.png').write_bytes(b'p'); (out/'film-box.mp4').write_bytes(b'f')\n"


def test_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(film, "PYTHON", tmp_path / "nope")
    assert any("setup-bpy.sh" in m for m in film.missing())
    with pytest.raises(RuntimeError):
        film.render(tmp_path / "bed.json", tmp_path, "bed", "draft", lambda *a: None, lambda: False)


def test_the_command_names_the_bed_the_folder_the_shot_and_the_worms():
    cmd = film.command(film.ROOT / "bed.json", film.ROOT / "out", "box", "draft", adults=1511)
    assert cmd[1].endswith("assets/demo.py")
    assert cmd[cmd.index("--shot") + 1] == "box" and cmd[cmd.index("--quality") + 1] == "draft" and cmd[-2:] == ["--adults", "1511"]
    assert "--adults" not in film.command(film.ROOT / "bed.json", film.ROOT / "out")


def test_an_unknown_shot_is_refused(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    with pytest.raises(RuntimeError, match="no such film"):
        film.render(bed, tmp_path, "zoom", "good", lambda *a: None, lambda: False, cmd=[sys.executable, "-c", "pass"])


def test_progress_follows_the_renderers_frames_and_the_result_names_the_files(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    seen = []
    out = film.render(bed, tmp_path, "box", "draft", lambda phase, done: seen.append((phase, done)), lambda: False,
                      cmd=[sys.executable, "-c", FAKE, str(tmp_path)])
    assert seen[0] == ("the still", 0.0) and seen[-2:] == [("frame 3 of 3", 1.0), ("the whole film", 1.0)]
    box = next(x for x in out["shots"] if x["id"] == "box")
    assert box["film"]["bytes"] == 1 and box["poster"] and out["frames"] == 3 and out["shot"] == "box"


def test_cancel_stops_the_renderer(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    calls = []
    with pytest.raises(Stopped):
        film.render(bed, tmp_path, "box", "draft", lambda *a: calls.append(a), lambda: len(calls) >= 2,
                    cmd=[sys.executable, "-c", FAKE, str(tmp_path)])
    assert not (tmp_path / "film-box.mp4").exists()


def test_a_failing_renderer_reports_its_last_lines(tmp_path):
    bed = tmp_path / "bed.json"
    bed.write_text("{}")
    with pytest.raises(RuntimeError, match="no such crop"):
        film.render(bed, tmp_path, "bed", "good", lambda *a: None, lambda: False,
                    cmd=[sys.executable, "-c", "import sys; print('no such crop'); sys.exit(3)"])


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_the_whole_film_cross_fades_the_shots_there_are(tmp_path):
    def clip(shot, seconds):
        subprocess.run([shutil.which("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=24:duration={seconds}",
                        "-pix_fmt", "yuv420p", str(tmp_path / f"film-{shot}.mp4")], check=True)
    clip("box", 3)
    assert film.stitch(tmp_path) is None and not (tmp_path / film.WHOLE).exists(), "one shot: no whole film"
    clip("bed", 4)
    clip("flight", 2)
    whole = film.stitch(tmp_path)
    assert whole and film.status(tmp_path)["whole"]["name"] == film.WHOLE
    assert abs(film._seconds(shutil.which("ffprobe"), whole) - (4 + 2 + 3 - 2 * film.FADE_S)) < 0.2
