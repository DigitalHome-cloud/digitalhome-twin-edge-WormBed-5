# SPDX-License-Identifier: GPL-3.0-or-later
"""Webots driver plugin of the roof-bed gantry: its axes, cameras and the bed, as ROS 2 topics.

It knows nothing about jobs. On the real gantry a hardware driver offers the
same topics, under /gantry/:

  out  position                     geometry_msgs/PointStamped  x, y in bed metres; z = how far the tool is lowered (m)
       tool_camera/compressed       sensor_msgs/CompressedImage  looking down from the carriage, a few frames a second
       overview_camera/compressed   sensor_msgs/CompressedImage  the whole bed
  in   target                       geometry_msgs/Point          where the axes should go (same units as position)
       estop                        std_msgs/Bool                true: hold where it is and ignore targets

and the bed itself, which the simulation has to play (a real bed just is):

  out  /bed/state   std_msgs/String  JSON {"time": s, "cells": {"row_col": {"status", "moisture", "crop"}}}
  in   /bed/event   std_msgs/String  JSON {"op": "sow", "row", "col", "crop"} | {"op": "water", "row", "col", "ml"}

                                     {"op": "feed", "grams", "spot"} the worm box gets a portion from the hopper
                                     {"op": "hopper", "grams"} a person filled the hopper
                                     {"op": "tank", "litres"}  a person refilled (or emptied) the tank

A sown cell gets a seedling in the world. Soil moisture is a simple number per
cell (percent): watering raises it, time lowers it. With tank pipes under the
bed and water in them, the soil is kept moist from below and the tank slowly
empties (a wicking bed). The worm box has a temperature, a moisture and a
fill level; feeding it warms it a little and sends liquid down into the tank.
/bed/state carries "tank", "wormBin" and "hopper" beside the cells.
"""
import json
import os

import numpy as np
import rclpy
from geometry_msgs.msg import Point, PointStamped
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String

from .spin import Drain

CAMERAS = ("tool_camera", "overview_camera")
CAMERA_EVERY_MS = 200
STATE_EVERY_MS = 500
MOISTURE_START = 35.0
MOISTURE_PER_ML = 0.08          # 250 ml on one plant: +20 %
MOISTURE_LOSS_PER_S = 0.01
SOIL_TEMP_C = 14.0
WICK_TO = 45.0                  # the moisture the wick holds the soil at while the tank has water
WICK_GAIN_PER_S = 0.4           # how fast a drier cell is brought back up (percent per second)
WICK_L_PER_CELL_S = 0.00002     # what that costs the tank, per planted cell and second
LIQUID_L_PER_G = 0.0003         # liquid a portion of food sends down to the tank
BIN_FULL_G = 20000.0            # the worm box is "full" at this much food and bedding
AIR_TEMP_C = 16.0


def load_bed():
    path = os.environ.get("PERMTEK5_TWIN_BED", "")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class GantryDriver:
    def init(self, webots_node, properties):
        self.robot = webots_node.robot
        self.ts = int(self.robot.getBasicTimeStep())
        self.cfg = load_bed()
        dev = self.robot.getDevice
        self.motors = {a: dev(f"{a}_motor") for a in "xyz"}
        self.sensors = {a: dev(f"{a}_sensor") for a in "xyz"}
        for s in self.sensors.values():
            s.enable(self.ts)
        self.cams = {}
        for name in CAMERAS:
            cam = dev(name)
            cam.enable(CAMERA_EVERY_MS)
            self.cams[name] = cam
        self.stopped = False
        self.cells = {f'{c["row"]}_{c["col"]}': {"status": c["status"], "crop": c["crop"], "moisture": MOISTURE_START}
                      for c in self.cfg["cells"]}
        self.rgb = {c["crop"]: c.get("rgb", [0.3, 0.6, 0.3]) for c in self.cfg["cells"]}
        self.elapsed_ms = 0
        tank = self.cfg.get("tank") or {}
        self.tank_cap = float(tank.get("litres") or 0)
        self.tank_l = self.tank_cap * 0.6                       # starts partly filled
        self.has_bin = bool((self.cfg.get("wormBin") or {}).get("cols"))
        self.bin = {"tempC": AIR_TEMP_C + 2.0, "moisture": 70.0, "grams": 6000.0, "fedGrams": 0.0, "feedings": 0}
        self.hopper_g = 0.0

        if not rclpy.ok():
            rclpy.init(args=None)
        self.node = n = rclpy.create_node("driver", namespace="/gantry")
        self.drain = Drain(n)
        self.pub_pos = n.create_publisher(PointStamped, "position", 10)
        self.pub_cam = {name: n.create_publisher(CompressedImage, f"{name}/compressed", 2) for name in CAMERAS}
        self.pub_bed = n.create_publisher(String, "/bed/state", 10)
        n.create_subscription(Point, "target", self.drain.wrap(self.on_target), 10)
        n.create_subscription(Bool, "estop", self.drain.wrap(self.on_estop), 10)
        n.create_subscription(String, "/bed/event", self.drain.wrap(self.on_event), 50)

    # -- axes ------------------------------------------------------------------

    def where(self):
        return {a: self.sensors[a].getValue() for a in "xyz"}

    def on_target(self, msg):
        if self.stopped:
            return
        for a, v in (("x", msg.x), ("y", msg.y), ("z", msg.z)):
            m = self.motors[a]
            m.setPosition(min(max(v, m.getMinPosition()), m.getMaxPosition()))

    def on_estop(self, msg):
        self.stopped = bool(msg.data)
        if self.stopped:
            now = self.where()
            for a in "xyz":
                if now[a] == now[a]:                      # not NaN (before the first step)
                    self.motors[a].setPosition(now[a])

    # -- the bed ---------------------------------------------------------------

    def cell(self, row, col, crop=""):
        return self.cells.setdefault(f"{row}_{col}", {"status": "CellPlanned", "crop": crop, "moisture": MOISTURE_START})

    def on_event(self, msg):
        try:
            e = json.loads(msg.data)
        except ValueError:
            return
        if not isinstance(e, dict):
            return
        op = e.get("op")
        try:
            if op == "hopper":
                self.hopper_g = max(0.0, min(float(e.get("grams", 0)), 5000.0))
                return
            if op == "tank":
                self.tank_l = max(0.0, min(float(e.get("litres", self.tank_cap)), self.tank_cap))
                return
            if op == "feed":
                if not self.has_bin:
                    return
                grams = max(0.0, min(float(e.get("grams", 0)), self.hopper_g))
                self.hopper_g -= grams
                self.bin["grams"] = min(BIN_FULL_G, self.bin["grams"] + grams)
                self.bin["fedGrams"] += grams
                self.bin["feedings"] += 1
                self.bin["tempC"] += grams / 400.0                      # fresh food warms the box a little
                self.bin["moisture"] = min(95.0, self.bin["moisture"] + grams / 200.0)
                self.tank_l = min(self.tank_cap, self.tank_l + grams * LIQUID_L_PER_G)
                return
            row, col = int(e["row"]), int(e["col"])
        except (ValueError, KeyError, TypeError):
            return
        b = self.cfg["bed"]
        if not (0 <= row < b["rows"] and 0 <= col < b["cols"]):
            return
        c = self.cell(row, col, e.get("crop", ""))
        if e.get("op") == "water":
            c["moisture"] = min(100.0, c["moisture"] + float(e.get("ml", 0)) * MOISTURE_PER_ML)
        elif e.get("op") == "sow":
            c["crop"] = e.get("crop") or c["crop"]
            c["status"] = "CellSown"
            self.seedling(row, col, c["crop"])

    def seedling(self, row, col, crop):
        old = self.robot.getFromDef(f"CELL_{row}_{col}")
        if old is not None:
            old.remove()
        w = self.cfg["world"]
        x, y = (col + 0.5) * w["cellM"], (row + 0.5) * w["cellM"]
        r, g, b = self.rgb.get(crop, [0.3, 0.6, 0.3])
        look = f"PBRAppearance {{ baseColor {r:.3f} {g:.3f} {b:.3f} roughness 0.9 metalness 0 }}"
        mesh = w.get("seedlingMesh")
        if mesh:                                   # modelled 0.2 m across: a seedling is about 2.5 cm
            shape = f'Transform {{ scale 0.12 0.12 0.12 children [ Shape {{ appearance {look} geometry Mesh {{ url [ "{mesh}" ] }} }} ] }}'
            z = w["soilZ"]
        else:
            shape = f"Shape {{ appearance {look} geometry Sphere {{ radius 0.012 subdivision 2 }} }}"
            z = w["soilZ"] + 0.012
        self.robot.getRoot().getField("children").importMFNodeFromString(
            -1, f"DEF CELL_{row}_{col} Pose {{ translation {x:.4f} {y:.4f} {z:.4f} children [ {shape} ] }}")

    # -- one step --------------------------------------------------------------

    def step(self):
        self.drain()
        self.elapsed_ms += self.ts
        now = self.where()
        if now["x"] != now["x"]:
            return
        stamp = self.node.get_clock().now().to_msg()
        msg = PointStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = "bed"
        msg.point.x, msg.point.y, msg.point.z = now["x"], now["y"], now["z"]
        self.pub_pos.publish(msg)

        if self.elapsed_ms % CAMERA_EVERY_MS < self.ts:
            for name, cam in self.cams.items():
                raw = cam.getImage()
                if not raw:
                    continue
                import cv2
                img = np.frombuffer(raw, np.uint8).reshape((cam.getHeight(), cam.getWidth(), 4))
                ok, jpg = cv2.imencode(".jpg", img[:, :, :3], [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    out = CompressedImage()
                    out.header.stamp = stamp
                    out.header.frame_id = name
                    out.format = "jpeg"
                    out.data = jpg.tobytes()
                    self.pub_cam[name].publish(out)

        if self.elapsed_ms % STATE_EVERY_MS < self.ts:
            dt = STATE_EVERY_MS / 1000.0
            loss = MOISTURE_LOSS_PER_S * dt
            wicking = self.tank_cap > 0 and self.tank_l > 0.0
            for c in self.cells.values():
                c["moisture"] = max(5.0, c["moisture"] - loss)
                if wicking and c["status"] != "CellPlanned" and c["moisture"] < WICK_TO:
                    c["moisture"] = min(WICK_TO, c["moisture"] + WICK_GAIN_PER_S * dt)
                    self.tank_l = max(0.0, self.tank_l - WICK_L_PER_CELL_S * dt)
            b = self.bin                                            # the worm box drifts back to the air's temperature
            b["tempC"] += (AIR_TEMP_C + 2.0 - b["tempC"]) * min(1.0, 0.02 * dt)
            b["moisture"] = max(40.0, b["moisture"] - 0.002 * dt)
            self.pub_bed.publish(String(data=json.dumps({
                "time": self.robot.getTime(), "stopped": self.stopped, "soilTempC": SOIL_TEMP_C,
                "tank": {"litres": round(self.tank_l, 2), "capacity": round(self.tank_cap, 1)},
                "wormBin": ({"tempC": round(b["tempC"], 1), "moisture": round(b["moisture"], 1),
                             "fillPct": round(100.0 * b["grams"] / BIN_FULL_G, 1), "fedGrams": round(b["fedGrams"]),
                             "feedings": b["feedings"]} if self.has_bin else None),
                "hopper": {"grams": round(self.hopper_g)},
                "cells": {k: {"status": c["status"], "crop": c["crop"], "moisture": round(c["moisture"], 1)}
                          for k, c in self.cells.items()}})))
