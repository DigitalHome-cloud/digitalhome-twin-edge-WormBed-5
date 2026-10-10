# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding weeds in a picture: drawn pictures, so the geometry is known exactly."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2", reason="OpenCV lives in the ROS environment, not in the app's")
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2" / "roofbed_twin"))
from roofbed_twin import weeds  # noqa: E402

VIEW = {"heightM": 0.5, "fovRad": 1.0, "xMax": 2.6, "yMax": 1.2}
SOIL, LEAF, PURPLE = (28, 38, 51), (60, 190, 130), (200, 80, 170)        # BGR


def picture(camera, dots):
    """A 320 x 240 picture of soil with round plants at bed places: [(x, y, diameter_m, colour)]."""
    img = np.full((240, 320, 3), SOIL, np.uint8)
    k = weeds.metres_per_pixel(VIEW, 320)
    for x, y, d, colour in dots:
        u, v = 160 - (y - camera[1]) / k, 120 - (x - camera[0]) / k
        cv2.circle(img, (round(u), round(v)), max(1, round(d / k / 2)), colour, -1)
    return img


def test_a_pixel_is_a_place_in_the_bed():
    assert weeds.to_bed(160, 120, (240, 320), (1.0, 0.5), VIEW) == (1.0, 0.5)
    x, y = weeds.to_bed(160, 0, (240, 320), (1.0, 0.5), VIEW)
    assert x > 1.0 and y == 0.5, "up in the picture is further along the bed"
    assert weeds.to_bed(0, 120, (240, 320), (1.0, 0.5), VIEW)[1] > 0.5, "left in the picture is across the bed"
    assert weeds.metres_per_pixel(VIEW, 320) == pytest.approx(2 * 0.5 * math.tan(0.5) / 320)


def test_green_off_the_planted_points_is_a_weed_sized_small_or_large():
    cam = (1.0, 0.6)
    img = picture(cam, [(1.05, 0.55, 0.016, LEAF), (0.92, 0.70, 0.05, LEAF), (1.10, 0.75, 0.04, LEAF), (0.95, 0.5, 0.03, PURPLE)])
    found = weeds.find(img, cam, VIEW, planted=[(1.10, 0.75, 0.05)])
    assert [(round(w["x"], 2), round(w["y"], 2), w["kind"]) for w in found] == [(1.05, 0.55, "small"), (0.92, 0.7, "large")] or \
        sorted((round(w["x"], 2), round(w["y"], 2), w["kind"]) for w in found) == [(0.92, 0.7, "large"), (1.05, 0.55, "small")]
    by = {w["kind"]: w for w in found}
    assert abs(by["small"]["sizeMm"] - 16) <= 4 and abs(by["large"]["sizeMm"] - 50) <= 5
    assert len(found) == 2, "the plant at its planted point is the crop; the purple one is not leaf-green"


def test_bare_soil_has_no_weeds_and_the_edges_do_not_count():
    assert weeds.find(picture((1.0, 0.6), []), (1.0, 0.6), VIEW, []) == []
    cut = picture((1.0, 0.6), [(1.0 + 0.2, 0.6, 0.04, LEAF)])              # at the picture's upper edge: half of it
    assert weeds.find(cut, (1.0, 0.6), VIEW, []) == []
    outside = picture((0.05, 0.6), [(-0.05, 0.6, 0.03, LEAF)])              # beyond the bed's near end
    assert weeds.find(outside, (0.05, 0.6), VIEW, []) == []


def test_the_same_weed_in_two_pictures_is_one_and_the_stops_cover_the_soil():
    twice = [{"x": 1.0, "y": 0.5, "sizeMm": 14, "kind": "small"}, {"x": 1.008, "y": 0.505, "sizeMm": 16, "kind": "small"},
             {"x": 1.3, "y": 0.5, "sizeMm": 40, "kind": "large"}]
    assert [w["sizeMm"] for w in weeds.merge(twice)] == [16, 40]
    at = weeds.stops(VIEW)
    wide = 2 * 0.5 * math.tan(0.5)
    assert min(x for x, _ in at) - wide * 0.75 / 2 <= 0.001 and max(x for x, _ in at) + wide * 0.75 / 2 >= 2.6 - 0.001
    assert min(y for _, y in at) - wide / 2 <= 0.001 and max(y for _, y in at) + wide / 2 >= 1.2 - 0.001
    assert len(at) <= 30
