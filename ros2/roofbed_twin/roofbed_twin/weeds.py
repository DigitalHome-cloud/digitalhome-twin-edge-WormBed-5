# SPDX-License-Identifier: GPL-3.0-or-later
"""Find weeds in a picture from the tool camera. Plain Python and OpenCV: no ROS, no Webots.

No trained model. The bed's plan is known, so the rule is simple
(PermTek-5 docs/specs/perma5guild.md, section 5):

    green that is not where something was planted is a weed.

    1. The camera looks straight down from a known height, so a pixel is a
       place in the bed (`to_bed`).
    2. Leaf-green pixels are found by colour (hue, saturation, brightness).
    3. Touching pixels are one plant; its size is its width on the ground.
    4. A plant within reach of a planted point is the crop. Anything else is
       a weed: SMALL ones (under 30 mm) can be pressed into the soil with the
       stamp, LARGE ones are for a person.

What it cannot do: tell a weed from a crop of the same colour that stands
at a planted point, or see a weed under a grown plant's leaves. Neither
matters for stamping: the stamp stays away from planted points anyway.

    found = find(image_bgr, camera_xy=(1.2, 0.5), view=view_of(cfg), planted=planted_of(cfg, bed_state))
    -> [{"x": 1.31, "y": 0.42, "sizeMm": 14, "kind": "small"}, ...]
"""
import math

import cv2
import numpy as np

SMALL_BELOW_MM = 30.0
EDGE_PIXELS = 3
MIN_MM = 5
MIN_PIXELS = 6                    # smaller specks are noise
GREEN_HUE = (22, 95)              # OpenCV hue, 0..179: yellow-green to green
MIN_SATURATION, MIN_VALUE = 70, 50
MERGE_M = 0.02                    # the same weed seen in two overlapping pictures
EDGE_M = 0.015                    # the frame's boards are not soil


def view_of(cfg):
    """How the tool camera sees the soil: its height over it, its angle of view, and where the soil is."""
    w, g, bed = cfg["world"], cfg.get("gantry") or {}, cfg["bed"]
    box = (cfg.get("wormBin") or {}).get("x")
    return {"heightM": float(g.get("camZ", w["soilZ"] + 0.5)) - float(w["soilZ"]), "fovRad": float(w.get("camFovRad", 1.0)),
            "xMax": float(box[0]) if box else bed["cols"] * w["cellM"], "yMax": float(w["y"][1])}


def planted_of(cfg, bed_state):
    """The planted points and how far each one's plant reaches (metres): [(x, y, reach)]."""
    cell_m = cfg["world"]["cellM"]
    live = (bed_state or {}).get("cells") or {}
    out = []
    for c in cfg["cells"]:
        status = (live.get(f'{c["row"]}_{c["col"]}') or {}).get("status", c["status"])
        if status not in ("CellSown", "CellGrowing"):
            continue
        spacing = ((c.get("job") or {}).get("spacingMm") or cell_m * 1000) / 1000.0
        reach = max(0.03, spacing * 0.38) + 0.015 if status == "CellGrowing" else 0.03
        out.append(((c["col"] + 0.5) * cell_m, (c["row"] + 0.5) * cell_m, reach))
    return out


def metres_per_pixel(view, width):
    return 2.0 * view["heightM"] * math.tan(view["fovRad"] / 2.0) / width


def to_bed(u, v, shape, camera_xy, view):
    """A pixel (u to the right, v down) as a place in the bed. The camera's picture has the bed's +x
    upwards and the bed's +y to the left."""
    h, w = shape[:2]
    k = metres_per_pixel(view, w)
    return camera_xy[0] - (v - h / 2.0) * k, camera_xy[1] - (u - w / 2.0) * k


def find(image_bgr, camera_xy, view, planted):
    """The weeds in one picture, in bed coordinates."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (GREEN_HUE[0], MIN_SATURATION, MIN_VALUE), (GREEN_HUE[1], 255, 255))
    k = metres_per_pixel(view, image_bgr.shape[1])
    n, _, stats, centres = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        left, top, wide, high, area = stats[i]
        if area < MIN_PIXELS:
            continue
        if left == 0 or top == 0 or left + wide >= mask.shape[1] or top + high >= mask.shape[0]:
            continue                                  # cut by the picture's edge: the next picture has it whole
        x, y = to_bed(centres[i][0], centres[i][1], mask.shape, camera_xy, view)
        if not (EDGE_M <= x <= view["xMax"] - EDGE_M and EDGE_M <= y <= view["yMax"] - EDGE_M):
            continue                                  # not on the soil
        size = max(1, max(wide, high) - EDGE_PIXELS) * k          # a blob's rim pixels are half soil
        if any(math.hypot(x - px, y - py) <= reach + size / 2.0 for px, py, reach in planted):
            continue                                  # where something was planted: the crop
        mm = round(size * 1000.0)
        if mm < MIN_MM:
            continue                                  # a speck, not a plant
        out.append({"x": round(float(x), 3), "y": round(float(y), 3), "sizeMm": int(mm),
                    "kind": "small" if mm < SMALL_BELOW_MM else "large"})
    return out


def merge(found):
    """Weeds from several pictures, each once: those closer than 2 cm are the same one (the larger size is kept)."""
    out = []
    for w in sorted(found, key=lambda w: -w["sizeMm"]):
        # o is at least as large as w: a speck within the bigger one's reach is part of it (a leaf seen from the side)
        if not any(math.hypot(w["x"] - o["x"], w["y"] - o["y"]) <= MERGE_M + o["sizeMm"] / 2000.0 for o in out):
            out.append(w)
    return sorted(out, key=lambda w: (w["x"], w["y"]))


def stops(view, overlap=0.25):
    """Where the camera stops to see all of the soil: [(x, y)], row by row there and back."""
    wide = 2.0 * view["heightM"] * math.tan(view["fovRad"] / 2.0)          # along the bed's y
    high = wide * 0.75                                                      # along x (a 4:3 picture)
    def line(total, span):
        n = max(1, math.ceil((total - span * overlap) / (span * (1.0 - overlap))))
        return [total / 2.0] if n == 1 else [span / 2.0 + i * (total - span) / (n - 1) for i in range(n)]
    xs, ys = line(view["xMax"], high), line(view["yMax"], wide)
    return [(x, y) for i, x in enumerate(xs) for y in (ys if i % 2 == 0 else ys[::-1])]
