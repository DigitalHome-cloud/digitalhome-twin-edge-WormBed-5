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
                                     {"op": "hopper", "grams" | "mix": {kind: grams}} a person filled the hopper, and with what
                                     {"op": "tank", "litres"}  a person refilled (or emptied) the tank
                                     {"op": "valve", "id" | "from", "to", "open"}  a tap or an edge valve opened or closed (water.py)
                                     {"op": "store", "id" | "kind", "litres"}  a barrel's or a garden tank's level (scenarios)
                                     {"op": "care", "what": "bedding", "grams"} | {"what": "moisten", "ml"}  the robot cared for the box
                                     {"op": "topdress", "row", "col", "grams"}  finished compost from the box onto a cell
                                     {"op": "weeds", "count", "large"} a flush of weeds | {"op": "weeds", "pull": "large" | "all"} pulled by hand
                                     {"op": "stamp", "x", "y"}  the stamp pressed down at a place
                                     {"op": "dry", "percent" | "to"}  hot, dry days in one go (scenarios)
                                     {"op": "box", "do": "insulate", "on"} | {"do": "bedding", "grams"} | {"do": "moisten", "ml"}
                                       | {"do": "takeout", "grams"} | {"do": "dump", "grams"} | {"do": "lid", "open"}
                                       | {"do": "tap"} (the sump runs on into the tank) | {"do": "reset"} (a settled box again)
                                       | {"do": "helper", "on"} a stand-in for daily care over a long run: each morning the box
                                         gets a day's eating (counted as the robot's feeding, so the feeder sees it), bedding
                                         or water when it needs them, and the finished compost goes out when it is full
                                       what a person did at the worm box

A sown cell gets a seedling in the world. Soil moisture is a simple number per
cell (percent): watering and rain raise it; sun, warmth and wind lower it
(environment.py: a clock that runs faster than the wall, a day and a night,
weather, and disturbances laid over it). With tank pipes under the
bed and water in them, the soil is kept moist from below and the tank slowly
empties (a wicking bed). The worm box is a small living model of its own
(wormbox.py): worms eat what is fed, the box gets lighter, liquid drains to
its sump (a tap lets it on to the tank), uneaten food heats the core. Its sensors read with noise and can
fail. Rain fills the tank up to its overflow. /bed/state carries "env",
"tank", "wormBin" (sensor readings, and under "sim" what only a simulation
can know) and "hopper"
beside the cells. Further events: {"op": "disturb", "id", "effect", "minutes"},
{"op": "calm"}, {"op": "time", "factor"}, {"op": "chaos", "rate", "pool"}.
"""
import json
import math
import os
import random

import numpy as np
import rclpy
from geometry_msgs.msg import Point, PointStamped
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String

from .environment import Environment
from .spin import Drain
from .water import WaterSystem
from .wormbox import FULL_G, Sensors, WormBox
from .soil import SoilLife

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
# Weeds: what comes up where nothing was planted. Only the simulation knows them; the robot has to find them (weeds.py).
WEEDS_PER_M2_DAY = 0.4          # how many come up on bare, moist soil
WEED_START_MM, WEED_MAX_MM, WEED_GROWS_MM_PER_H = 8.0, 80.0, 0.4
WEED_LIMIT = 60
STAMPABLE_BELOW_MM = 35.0       # a bigger weed springs back: it has to be pulled
WORMS_SHOWN = 30                # at most this many adults in view; the young and the cocoons fewer
WORM_M, COCOON_M = 0.08, 0.004  # an adult red wiggler about 8 cm long, a cocoon 3-4 mm
WORM_HIDDEN_Z = -5.0            # where a worm waits while it is not in view
STAMP_REACH_M = 0.025
COMPOST_FROM_G, COMPOST_KEEPS = 30.0, 0.85   # from this much compost on a cell, its soil loses this share of the usual
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
        self.water = WaterSystem(self.cfg.get("water"))         # the roof's rain, a barrel, a garden tank, and the valves (water.py)
        self.has_bin = bool((self.cfg.get("wormBin") or {}).get("cols"))
        self.hopper_g = 0.0
        seed = int(os.environ.get("PERMTEK5_TWIN_SEED", "5"))
        self.env = Environment(seed=seed)
        self.seed = seed
        self.mulch_cm = float(self.cfg["bed"].get("mulch_cm") or 0)
        self.new_box()
        self.soil_life = SoilLife()       # how alive the bed's soil is: an index, not a count (soil.py)
        self.overflowing = False
        self.shown = {}                  # what the world shows of the box: lid open, wrapping on
        self.weeds = {}                  # id -> {"x", "y", "mm", "shown"}
        self.weed_n = 0
        self.weed_rng = random.Random(seed * 31 + 7)
        self.worm_rng = random.Random(seed * 53 + 11)
        self.worms_shown = {}            # kind -> [[x, y, heading], ...] of the worms in view
        soil_to = (self.cfg.get("wormBin") or {}).get("x")
        self.soil_x = float(soil_to[0]) if soil_to else self.cfg["bed"]["cols"] * self.cfg["world"]["cellM"]
        self.soil_y = float(self.cfg["world"]["y"][1])
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
            if op == "hopper":                                # a person filled it, and says with what: {"mix": {kind: grams}}
                if "browns" in e:                             # the second compartment: dry bedding
                    self.hopper_browns_g = max(0.0, min(float(e["browns"]), 3000.0))
                    if "mix" not in e and "grams" not in e:
                        return
                mix = {str(k): max(0.0, float(g)) for k, g in (e.get("mix") or {}).items()}
                if not mix:
                    mix = {"vegetable": max(0.0, float(e.get("grams", 0)))}
                over = max(1.0, sum(mix.values()) / 5000.0)
                self.hopper_mix = {k: g / over for k, g in mix.items() if g > 0}
                self.hopper_g = sum(self.hopper_mix.values())
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
                for c in self.cells.values():             # by so many percent, or down to a level whatever it was
                    c["moisture"] = max(5.0, c["moisture"] - float(e.get("percent", 0)))
                    if "to" in e:
                        c["moisture"] = min(c["moisture"], max(5.0, float(e["to"])))
                return
            if op == "tank":
                self.tank_l = max(0.0, min(float(e.get("litres", self.tank_cap)), self.tank_cap))
                return
            if op == "valve":                                 # a tap or an edge valve: by its id, or by the kinds it joins
                vid = e.get("id") or self.water.valve_between(e.get("from"), e.get("to"))
                if vid:
                    self.water.open(vid, bool(e.get("open", True)))
                return
            if op == "store":                                 # a scenario sets a barrel's, a garden tank's or the sump's level
                if e.get("kind") == "WormBoxSump":
                    if self.has_bin:
                        self.box.sump_l = max(0.0, min(float(e.get("litres", 0)), self.box.sump_cap_l))
                    return
                sid = e.get("id") or self.water.of_kind(e.get("kind"))
                if sid:
                    self.water.set_level(sid, float(e.get("litres", 0)))
                return
            if op == "feed":
                if not self.has_bin:
                    return
                if self.env.flag("hopperJam"):
                    return                                    # the scoop came back empty
                grams = max(0.0, min(float(e.get("grams", 0)), self.hopper_g))
                if grams <= 0:
                    return
                # the scoop takes a share of everything that is in the hopper; what the food is like is the guide's numbers
                share = grams / self.hopper_g
                part = {k: g * share for k, g in self.hopper_mix.items()}
                self.hopper_mix = {k: g - part[k] for k, g in self.hopper_mix.items()}
                self.hopper_g -= grams
                kinds = self.cfg.get("feedingKinds") or {}
                num = lambda k: kinds.get(k) or {"wet": 1.0, "heat": 1.0, "acid": 0.0}      # noqa: E731
                browns = sum(g for k, g in part.items() if num(k)["wet"] < 0)
                food = {k: g for k, g in part.items() if num(k)["wet"] >= 0}
                total = sum(food.values())
                if browns:
                    self.box.add_bedding(browns)
                if total:
                    avg = lambda key: sum(g * num(k)[key] for k, g in food.items()) / total   # noqa: E731
                    self.box.feed(total, wet=avg("wet"), heat=avg("heat"), acid=avg("acid"))
                self.last_feed = {"n": self.box.feedings, "grams": round(grams), "spot": int(e.get("spot", 0)),
                                  "mix": {k: round(g) for k, g in part.items() if g >= 0.5}, "atMin": round(self.env.minutes, 1)}
                return
            if op == "weeds":                                 # a flush of weeds comes up, or a person pulled some
                if e.get("pull"):
                    for key in [k for k, w in self.weeds.items() if e["pull"] == "all" or w["mm"] >= STAMPABLE_BELOW_MM]:
                        self.remove_weed(key)
                    return
                large = int(e.get("large", 0))
                for i in range(max(0, min(int(e.get("count", 0)), WEED_LIMIT))):
                    self.spawn_weed(self.weed_rng.uniform(45.0, 60.0) if i < large else self.weed_rng.uniform(10.0, 22.0))
                return
            if op == "stamp":                                 # the stamp presses down: small weeds go under, big ones spring back
                x, y = float(e["x"]), float(e["y"])
                for key in [k for k, w in self.weeds.items()
                            if w["mm"] < STAMPABLE_BELOW_MM and math.hypot(w["x"] - x, w["y"] - y) <= STAMP_REACH_M + w["mm"] / 2000.0]:
                    self.remove_weed(key)
                return
            if op == "care":                                  # the robot gave the box bedding or water
                if not self.has_bin:
                    return
                if e.get("what") == "bedding":
                    grams = max(0.0, min(float(e.get("grams", 0)), self.hopper_browns_g))
                    self.hopper_browns_g -= grams
                    self.box.add_bedding(grams)
                    self.robot_bedding_g += grams
                    self.care_n += 1
                    self.last_care = {"n": self.care_n, "what": "bedding", "amount": round(grams), "atMin": round(self.env.minutes, 1)}
                elif e.get("what") == "moisten":
                    self.box.moisten(max(0.0, float(e.get("ml", 0))))
                return
            if op == "topdress":                              # finished compost from the box, onto a cell
                row, col = int(e["row"]), int(e["col"])
                if self.has_bin and f"{row}_{col}" in self.cells:
                    c = self.cells[f"{row}_{col}"]
                    c["compostG"] = c.get("compostG", 0.0) + self.box.take_castings(max(0.0, float(e.get("grams", 0))))
                return
            if op == "box":                                   # what a person did at the worm box
                if not self.has_bin:
                    return
                do = e.get("do")
                if do == "reset":                             # a scenario starts from a settled box
                    self.new_box()
                elif do == "insulate":
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
                elif do == "rest":                            # start a harvest: no food for one half; None ends it
                    self.box.rest(e.get("side"))
                elif do == "helper":
                    self.helper, self.helper_day = bool(e.get("on", True)), None
                elif do == "tap":                             # the sump runs on into the tank, as far as there is room
                    self.tank_l += self.box.drain(self.tank_cap - self.tank_l)
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

    # -- weeds -------------------------------------------------------------------

    def planted_points(self):
        m = self.cfg["world"]["cellM"]
        return [((int(k.split("_")[1]) + 0.5) * m, (int(k.split("_")[0]) + 0.5) * m)
                for k, c in self.cells.items() if c["status"] in ("CellSown", "CellGrowing")]

    def spawn_weed(self, mm=WEED_START_MM):
        """A weed comes up somewhere on bare soil, away from what is planted."""
        if len(self.weeds) >= WEED_LIMIT:
            return
        points = self.planted_points()
        for _ in range(40):
            x = self.weed_rng.uniform(0.04, self.soil_x - 0.04)
            y = self.weed_rng.uniform(0.04, self.soil_y - 0.04)
            if all(math.hypot(x - px, y - py) > 0.09 for px, py in points) and \
                    all(math.hypot(x - w["x"], y - w["y"]) > 0.06 for w in self.weeds.values()):
                break
        else:
            return
        self.weed_n += 1
        self.weeds[self.weed_n] = {"x": x, "y": y, "mm": float(mm), "shown": 0.0}
        k = mm / 1000.0
        look = "PBRAppearance { baseColor 0.45 0.72 0.18 roughness 0.9 metalness 0 }"
        self.robot.getRoot().getField("children").importMFNodeFromString(
            -1, f"DEF WEED_{self.weed_n} Pose {{ translation {x:.4f} {y:.4f} {self.cfg['world']['soilZ'] + 0.004:.4f} children [ "
                f"DEF WEED_{self.weed_n}_S Transform {{ scale {k:.4f} {k:.4f} {k * 0.5:.4f} children [ "
                f"Shape {{ appearance {look} geometry Sphere {{ radius 0.5 subdivision 2 }} }} ] }} ] }}")
        self.weeds[self.weed_n]["shown"] = float(mm)

    def remove_weed(self, key):
        node = self.robot.getFromDef(f"WEED_{key}")
        if node is not None:
            node.remove()
        self.weeds.pop(key, None)

    def grow_weeds(self, dmin, rain_mm):
        wet = 1.5 if rain_mm > 0 else 1.0
        cover = max(0.2, 1.0 - 0.25 * self.mulch_cm)                    # a mulch keeps most of them down
        expected = WEEDS_PER_M2_DAY * self.soil_x * self.soil_y * dmin / 1440.0 * wet * cover
        while expected > 0 and self.weed_rng.random() < min(1.0, expected):
            self.spawn_weed()
            expected -= 1.0
        for key, w in self.weeds.items():
            w["mm"] = min(WEED_MAX_MM, w["mm"] + WEED_GROWS_MM_PER_H * dmin / 60.0 * wet)
            if w["mm"] - w["shown"] >= 1.0:
                node = self.robot.getFromDef(f"WEED_{key}_S")
                if node is not None:
                    k = w["mm"] / 1000.0
                    node.getField("scale").setSFVec3f([k, k, k * 0.5])
                w["shown"] = w["mm"]

    def new_box(self):
        """A settled worm box with nothing wrong, as at the start (also a scenario's fresh start)."""
        bin_cfg = self.cfg.get("wormBin") or {}
        self.box = WormBox(may_leave=bool((self.cfg.get("guild") or {}).get("wormsMayLeave", True)), air_c=self.env.air_c(),
                           sump_cap_l=float(bin_cfg.get("sumpLitres") or 14.0), area_m2=float(bin_cfg.get("areaM2") or 0.48),
                           mulch_cm=self.mulch_cm)
        self.probes = Sensors(seed=self.seed)
        self.lid_left_open = False       # by a person; a disturbance can open it too
        self.hopper_g, self.hopper_mix, self.last_feed = 0.0, {}, None
        self.hopper_browns_g, self.robot_bedding_g, self.care_n, self.last_care = 0.0, 0.0, 0, None
        self.helper, self.helper_day = False, None

    def show_box(self, x):
        """Open or shut the lid and put the wrapping on or off in the world, when that changed. The feed
        hatch stands open while the tool head (at `x`) is over the box."""
        w = self.cfg["world"]
        over = bool(self.cfg["wormBin"].get("cols")) and x >= self.cfg["wormBin"]["x"][0] - 0.02
        for key, now, name in (("lid", self.box.lid_open, "WORM_LID"), ("wrap", self.box.insulated, "WORM_WRAP"),
                               ("hatch", over and not self.box.lid_open, "WORM_HATCH")):
            if self.shown.get(key) == now:
                continue
            self.shown[key] = now
            node = self.robot.getFromDef(name)
            if node is None:
                continue
            if key in ("lid", "hatch"):
                node.getField("rotation").setSFRotation([0, 1, 0, w.get(f"{key}OpenRad", 1.2) if now else 0.0])
            else:
                at = node.getField("translation").getSFVec3f()
                node.getField("translation").setSFVec3f([at[0], at[1], w.get("wrapZ", 0.1) if now else w.get("wrapOffZ", -5.0)])

    def daily_care(self):
        """The helper's morning round (a scenario's stand-in for a month of confirmed jobs): feed what the
        worms eat in a day, less what still lies there; keep the bin damp, not wet; harvest when it is full."""
        box = self.box
        box.feed(max(0.0, box.eats_per_day() - 0.5 * box.food_g), wet=0.6)     # not in the feeding guide's diary: no hopper, no spot
        if box.moisture > 80.0:
            box.add_bedding(300.0)
            self.robot_bedding_g += 300.0
        elif box.moisture < 65.0:
            box.moisten(500.0)
        if box.weight_g() > 0.7 * FULL_G:                         # the finished compost goes out (counted, so it is no jump)
            out = max(0.0, box.castings_g - 1000.0)
            box.castings_g -= out
            box.taken_g += out

    # -- the worms you can see ---------------------------------------------------
    # Thousands of worms cannot be drawn, and in a closed box none would show. The world shows a SAMPLE on
    # the bedding while the lid is open: as many as a log of the real count, so a crowded box and a dying
    # one look different. They lie in the fed half when one half rests, and lie still when the box is cold.

    def worm_counts(self):
        p = self.box.population()
        sample = lambda n, per, k, top: max(0, min(top, round(k * math.log10(max(n, 1) / per))))   # noqa: E731
        return {"adult": sample(p["adults"], 50, 10, WORMS_SHOWN), "young": sample(p["young"], 200, 6, WORMS_SHOWN // 2),
                "cocoon": sample(p["cocoons"], 100, 5, WORMS_SHOWN // 3)}

    def place_worm(self, kind, i):
        """Where one shown worm lies: in the bin, in the fed half when one rests."""
        x0, x1 = self.cfg["wormBin"]["x"]
        width = self.cfg["world"]["y"][1]
        y0, y1 = 0.06 * width, 0.94 * width
        rest = self.box.rest_side
        if rest is not None:                                          # the halves lie across the bed, as the feeding spots do
            y0, y1 = (width / 2 + 0.02, y1) if rest == 0 else (y0, width / 2 - 0.02)
        return [self.worm_rng.uniform(x0 + 0.06 * (x1 - x0), x1 - 0.06 * (x1 - x0)), self.worm_rng.uniform(y0, y1),
                self.worm_rng.uniform(0.0, 2 * math.pi)]

    def show_worms(self):
        w = self.cfg["world"]
        if not self.cfg["wormBin"].get("cols"):
            return
        open_ = self.box.lid_open
        want = self.worm_counts() if open_ else {"adult": 0, "young": 0, "cocoon": 0}
        z = float(w.get("binSurfaceZ", w.get("boxTopZ", 0.5) - 0.1))
        looks = {"adult": ("0.55 0.16 0.14", WORM_M, "worm"), "young": ("0.80 0.40 0.38", WORM_M * 0.45, "worm"),
                 "cocoon": ("0.78 0.62 0.25", COCOON_M, "cocoon")}
        for kind, n in want.items():
            have = self.worms_shown.setdefault(kind, [])
            while len(have) < n:                                      # bring more into view, made once and reused
                i = len(have)
                name = f"WORM_{kind.upper()}_{i}"
                if self.robot.getFromDef(name) is None:
                    rgb, size, mesh = looks[kind]
                    url = self.shape_url(mesh)
                    geom = f'Mesh {{ url [ "{url}" ] }}' if url else f"Capsule {{ radius 0.03 height 0.9 }}"
                    turn = "" if url else " rotation 0 1 0 1.5708"
                    self.robot.getRoot().getField("children").importMFNodeFromString(
                        -1, f"DEF {name} Pose {{ translation 0 0 {WORM_HIDDEN_Z} rotation 0 0 1 0 children [ Transform {{ scale {size:.4f} {size:.4f} {size:.4f}{turn} "
                            f"children [ Shape {{ appearance PBRAppearance {{ baseColor {rgb} roughness 0.4 metalness 0 }} geometry {geom} }} ] }} ] }}")
                at = self.place_worm(kind, i)
                have.append(at)
                self.move_worm(name, at, z)
            while len(have) > n:                                      # and out of view again
                have.pop()
                self.move_worm(f"WORM_{kind.upper()}_{len(have)}", None, z)
        # now and then a few of the adults and young turn and crawl a little; not in a cold box
        if open_ and self.box.core_c > 8.0 and self.worm_rng.random() < 0.3:
            for kind in ("adult", "young"):
                for i, at in enumerate(self.worms_shown.get(kind, [])):
                    if self.worm_rng.random() < 0.3:
                        step = WORM_M * 0.15 * (1.0 if kind == "adult" else 0.5)
                        at[2] += self.worm_rng.uniform(-0.6, 0.6)
                        at[0] += step * math.cos(at[2])
                        at[1] += step * math.sin(at[2])
                        x0, x1 = self.cfg["wormBin"]["x"]
                        if not (x0 + 0.03 < at[0] < x1 - 0.03) or not (0.03 < at[1] < w["y"][1] - 0.03):
                            at[:] = self.place_worm(kind, i)
                        self.move_worm(f"WORM_{kind.upper()}_{i}", at, z)

    def move_worm(self, name, at, z):
        node = self.robot.getFromDef(name)
        if node is None:
            return
        if at is None:
            node.getField("translation").setSFVec3f([0.0, 0.0, WORM_HIDDEN_Z])
            return
        node.getField("translation").setSFVec3f([at[0], at[1], z + 0.002])
        node.getField("rotation").setSFRotation([0, 0, 1, at[2]])

    def shape_url(self, name):
        """The shape library's file for `name` (world.py puts the paths in the config), or None."""
        return (self.cfg.get("shapes") or {}).get(name)

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
            mulched = max(0.6, 1.0 - 0.1 * self.mulch_cm)
            self.grow_weeds(dmin, rain_mm)
            for c in self.cells.values():
                # a top-dressing of compost shades the soil a little: it dries more slowly; so does a mulch
                c["moisture"] = max(5.0, c["moisture"] - loss * mulched * (COMPOST_KEEPS if c.get("compostG", 0) >= COMPOST_FROM_G else 1.0))
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
            self.water.rain(rain_mm)                                             # the roof beside the bed fills the barrel
            if self.cells:
                live = [c for c in self.cells.values() if c["status"] in ("CellSown", "CellGrowing")]
                soil_cells = max(1.0, self.soil_x * self.soil_y / self.cfg["world"]["cellM"] ** 2)
                self.soil_life.step(dmin, sum(c["moisture"] for c in self.cells.values()) / len(self.cells), env.air_c(),
                                    sum(c.get("compostG", 0.0) for c in self.cells.values()) / max(0.1, self.soil_x * self.soil_y),
                                    self.mulch_cm, min(1.0, len(live) / soil_cells))
            box = self.box
            box.lid_open = self.lid_left_open or env.flag("lidOpen")
            if self.has_bin:
                self.show_box(now["x"])
                self.show_worms()
                box.step(dmin, env.air_c(), env.rain_mm_h())      # its liquid collects in the sump; the tap lets it on
            if self.tank_cap > 0:                                 # open valves run: barrel -> tank pipes, sump -> tank pipes or garden
                self.tank_l = max(0.0, min(self.water.step(dmin, self.tank_l, self.tank_cap, box if self.has_bin else None), self.tank_cap))
                if self.helper and self.helper_day != env.day:
                    self.helper_day = env.day
                    self.daily_care()
            self.pub_bed.publish(String(data=json.dumps({
                "time": self.robot.getTime(), "stopped": self.stopped, "soilTempC": SOIL_TEMP_C,
                "env": env.snapshot(),
                # what only a simulation knows: the robot finds weeds with its camera, it is not told
                "sim": {"soilLife": round(self.soil_life.value, 2), "weeds": len(self.weeds), "weedsLarge": sum(1 for w in self.weeds.values() if w["mm"] >= STAMPABLE_BELOW_MM),
                        "weedsAt": [[round(w["x"], 3), round(w["y"], 3), round(w["mm"])] for w in self.weeds.values()]},
                "tank": {"litres": round(self.tank_l, 2), "capacity": round(self.tank_cap, 1), "overflowing": self.overflowing},
                "water": self.water.state(),
                "wormBin": ({**self.probes.read(box, env.sensor),
                             "fillPct": round(100.0 * box.weight_g() / FULL_G, 1), "fedGrams": round(box.fed_g),
                             "feedings": box.feedings, "wormsMayLeave": box.may_leave, "insulated": box.insulated, "sumpCapacity": round(box.sump_cap_l, 1), "lastFeed": self.last_feed, "lastCare": self.last_care,
                             "beddingGrams": round(self.robot_bedding_g), "takenGrams": round(box.taken_g), "harvest": box.harvest(),
                             "sim": box.truth()}
                            if self.has_bin else None),
                "hopper": {"grams": round(self.hopper_g), "jammed": env.flag("hopperJam"), "brownsGrams": round(self.hopper_browns_g),
                           "mix": {k: round(g) for k, g in self.hopper_mix.items() if g >= 0.5}},
                "cells": {k: {"status": c["status"], "crop": c["crop"], "moisture": round(c["moisture"], 1),
                              "compostG": round(c.get("compostG", 0.0))}
                          for k, c in self.cells.items()}})))
