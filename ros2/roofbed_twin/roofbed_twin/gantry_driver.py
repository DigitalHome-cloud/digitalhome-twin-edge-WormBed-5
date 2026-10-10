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
                                     {"op": "dry", "percent"}  hot, dry days in one go (scenarios)
                                     {"op": "box", "do": "insulate", "on"} | {"do": "bedding", "grams"} | {"do": "moisten", "ml"}
                                       | {"do": "takeout", "grams"} | {"do": "dump", "grams"} | {"do": "lid", "open"}
                                       what a person did at the worm box

A sown cell gets a seedling in the world. Soil moisture is a simple number per
cell (percent): watering and rain raise it; sun, warmth and wind lower it
(environment.py: a clock that runs faster than the wall, a day and a night,
weather, and disturbances laid over it). With tank pipes under the
bed and water in them, the soil is kept moist from below and the tank slowly
empties (a wicking bed). The worm box is a small living model of its own
(wormbox.py): worms eat what is fed, the box gets lighter, liquid drains to
the tank, uneaten food heats the core. Its sensors read with noise and can
fail. Rain fills the tank up to its overflow. /bed/state carries "env",
"tank", "wormBin" (sensor readings, and under "sim" what only a simulation
can know) and "hopper"
beside the cells. Further events: {"op": "disturb", "id", "effect", "minutes"},
{"op": "calm"}, {"op": "time", "factor"}, {"op": "chaos", "rate", "pool"}.
"""
import json
import os

import numpy as np
import rclpy
from geometry_msgs.msg import Point, PointStamped
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String

from .environment import Environment
from .spin import Drain
from .wormbox import FULL_G, Sensors, WormBox

CAMERAS = ("tool_camera", "overview_camera")
CAMERA_EVERY_MS = 200
STATE_EVERY_MS = 500
MOISTURE_START = 35.0
MOISTURE_PER_ML = 0.08          # 250 ml on one plant: +20 %
SOIL_TEMP_C = 14.0
# Rates are per SIMULATED minute; the environment's time factor says how many pass in a real second.
MOISTURE_LOSS_PER_MIN = 0.01    # at an evaporation of 1.0 (a mild, still, overcast hour)
WICK_TO = 45.0                  # the moisture the wick holds the soil at while the tank has water
WICK_GAIN_PER_MIN = 0.4         # how fast a drier cell is brought back up (percent per minute)
WICK_L_PER_CELL_MIN = 0.00002   # what that costs the tank, per planted cell and minute
TANK_DEAD_L = 1.0               # under a litre the wick does not reach the water (the worm box's liquid trickles in)
RAIN_TO_TANK = 0.5              # the share of rain on the bed that reaches the tank pipes
RAIN_WETS_PER_MM = 2.0          # percent of soil moisture a millimetre of rain adds



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
        self.hopper_g = 0.0
        seed = int(os.environ.get("PERMTEK5_TWIN_SEED", "5"))
        self.env = Environment(seed=seed)
        self.box = WormBox(may_leave=bool((self.cfg.get("guild") or {}).get("wormsMayLeave", True)), air_c=self.env.air_c())
        self.probes = Sensors(seed=seed)
        self.lid_left_open = False       # by a person; a disturbance can open it too
        self.overflowing = False
        b = self.cfg["bed"]
        self.bed_m2 = b["length_cm"] * b["width_cm"] / 10000.0

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
            if op == "disturb":                               # a chaos monkey: an effect over the weather, for a while
                self.env.apply(str(e["id"]), dict(e.get("effect") or {}), float(e.get("minutes", 60)), e.get("title"))
                return
            if op == "calm":
                self.env.calm(e.get("id"))
                return
            if op == "time":
                self.env.factor = max(0.1, min(float(e.get("factor", 1)), 600.0))
                return
            if op == "chaos":
                self.env.chaos_rate = max(0.0, float(e.get("rate", 0)))
                self.env.chaos_pool = [(p["id"], p.get("title"), dict(p.get("effect") or {}), float(p.get("minutes", 60)))
                                       for p in e.get("pool") or []]
                return
            if op == "dry":                                   # a scenario's hot, dry days in one go
                for c in self.cells.values():
                    c["moisture"] = max(5.0, c["moisture"] - float(e.get("percent", 0)))
                return
            if op == "tank":
                self.tank_l = max(0.0, min(float(e.get("litres", self.tank_cap)), self.tank_cap))
                return
            if op == "feed":
                if not self.has_bin:
                    return
                if self.env.flag("hopperJam"):
                    return                                    # the scoop came back empty
                grams = max(0.0, min(float(e.get("grams", 0)), self.hopper_g))
                self.hopper_g -= self.box.feed(grams)
                return
            if op == "box":                                   # what a person did at the worm box
                if not self.has_bin:
                    return
                do = e.get("do")
                if do == "insulate":
                    self.box.insulated = bool(e.get("on", True))
                elif do == "bedding":
                    self.box.add_bedding(min(float(e.get("grams", 500)), 5000.0))
                elif do == "moisten":
                    self.box.moisten(min(float(e.get("ml", 500)), 5000.0))
                elif do == "takeout":
                    self.box.take_out(float(e.get("grams", 0)))
                elif do == "dump":
                    self.box.feed(min(float(e.get("grams", 0)), 10000.0), by_robot=False)
                elif do == "lid":
                    self.lid_left_open = bool(e.get("open", True))
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
            dmin = self.env.step(STATE_EVERY_MS / 1000.0)        # simulated minutes since the last look
            env = self.env
            loss = MOISTURE_LOSS_PER_MIN * env.evaporation() * dmin
            rain_mm = env.rain_mm_h() * dmin / 60.0
            wicking = self.tank_cap > 0 and self.tank_l >= TANK_DEAD_L
            for c in self.cells.values():
                c["moisture"] = max(5.0, c["moisture"] - loss)
                if rain_mm:
                    c["moisture"] = min(60.0, c["moisture"] + rain_mm * RAIN_WETS_PER_MM)
                if wicking and c["status"] != "CellPlanned" and c["moisture"] < WICK_TO:
                    c["moisture"] = min(WICK_TO, c["moisture"] + WICK_GAIN_PER_MIN * dmin)
                    self.tank_l = max(0.0, self.tank_l - WICK_L_PER_CELL_MIN * dmin)
            if self.tank_cap > 0:
                self.tank_l -= env.effect("tankLeakLPerMin") * dmin
                self.tank_l += rain_mm * self.bed_m2 * RAIN_TO_TANK              # 1 mm on 1 m² is a litre
                self.overflowing = self.tank_l >= self.tank_cap and rain_mm > 0  # the rest leaves by the overflow
                self.tank_l = max(0.0, min(self.tank_l, self.tank_cap))
            box = self.box
            box.lid_open = self.lid_left_open or env.flag("lidOpen")
            if self.has_bin:
                made = box.step(dmin, env.air_c(), env.rain_mm_h())
                self.tank_l = min(self.tank_cap, self.tank_l + made["liquidL"])
            self.pub_bed.publish(String(data=json.dumps({
                "time": self.robot.getTime(), "stopped": self.stopped, "soilTempC": SOIL_TEMP_C,
                "env": env.snapshot(),
                "tank": {"litres": round(self.tank_l, 2), "capacity": round(self.tank_cap, 1), "overflowing": self.overflowing},
                "wormBin": ({**self.probes.read(box, env.sensor),
                             "fillPct": round(100.0 * box.weight_g() / FULL_G, 1), "fedGrams": round(box.fed_g),
                             "feedings": box.feedings, "wormsMayLeave": box.may_leave, "insulated": box.insulated,
                             "sim": box.truth()}
                            if self.has_bin else None),
                "hopper": {"grams": round(self.hopper_g), "jammed": env.flag("hopperJam")},
                "cells": {k: {"status": c["status"], "crop": c["crop"], "moisture": round(c["moisture"], 1)}
                          for k, c in self.cells.items()}})))
