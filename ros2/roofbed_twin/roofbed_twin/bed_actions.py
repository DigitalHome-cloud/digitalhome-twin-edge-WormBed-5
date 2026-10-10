# SPDX-License-Identifier: GPL-3.0-or-later
"""bed_actions: what the gantry does to cells, as ROS 2 actions.

    /bed/plant_cell   roofbed_interfaces/action/PlantCell
    /bed/water_cells  roofbed_interfaces/action/WaterCells
    /bed/probe_cells  roofbed_interfaces/action/ProbeCells
    /bed/photo_pass   roofbed_interfaces/action/PhotoPass
    /bed/feed_worms   roofbed_interfaces/action/FeedWorms

It talks to the gantry only through its driver's topics (/gantry/target,
/gantry/position, the tool camera) and tells the bed what happened
(/bed/event), so the same node can drive the real axes later. One job at a
time; a second goal is refused while one runs. Every move lifts the tool
first. An e-stop (/gantry/estop true) aborts the running job.

Bed geometry comes from bed.json (PERMTEK5_TWIN_BED), written with the world.
"""
import json
import os
import threading
import time

import rclpy
from geometry_msgs.msg import Point, PointStamped
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String

from roofbed_interfaces.action import FeedWorms, PhotoPass, PlantCell, ProbeCells, WaterCells

TOLERANCE_M = 0.002
MOVE_TIMEOUT_S = 90.0
WATER_ABOVE_M = 0.05        # nozzle height over the soil
PROBE_INTO_M = 0.03
FLOW_ML_PER_S = 40.0
MAX_ML_PER_CELL = 1000.0
MAX_PORTION_G = 500.0       # what the scoop carries in one go
SCOOP_INTO_M = 0.08         # how far the tool dips into the hopper


class Aborted(Exception):
    pass


class BedActions(Node):
    def __init__(self):
        super().__init__("bed_actions")
        with open(os.environ["PERMTEK5_TWIN_BED"], encoding="utf-8") as fh:
            self.cfg = json.load(fh)
        self.w, self.bed = self.cfg["world"], self.cfg["bed"]
        self.pos = None
        self.bed_state = {}
        self.frame = None
        self.stopped = False
        self.busy = threading.Lock()
        g = ReentrantCallbackGroup()
        self.pub_target = self.create_publisher(Point, "/gantry/target", 10)
        self.pub_event = self.create_publisher(String, "/bed/event", 50)
        self.create_subscription(PointStamped, "/gantry/position", self.on_pos, 10, callback_group=g)
        self.create_subscription(String, "/bed/state", self.on_bed, 10, callback_group=g)
        self.create_subscription(CompressedImage, "/gantry/tool_camera/compressed", self.on_frame, 2, callback_group=g)
        self.create_subscription(Bool, "/gantry/estop", lambda m: setattr(self, "stopped", bool(m.data)), 10, callback_group=g)
        for name, kind, run in (("plant_cell", PlantCell, self.plant), ("water_cells", WaterCells, self.water),
                                ("probe_cells", ProbeCells, self.probe), ("photo_pass", PhotoPass, self.photo),
                                ("feed_worms", FeedWorms, self.feed)):
            ActionServer(self, kind, f"/bed/{name}", execute_callback=self.guarded(run, kind), callback_group=g,
                         goal_callback=self.accept, cancel_callback=lambda _goal: CancelResponse.ACCEPT)

    def on_pos(self, msg):
        self.pos = (msg.point.x, msg.point.y, msg.point.z)

    def on_bed(self, msg):
        self.bed_state = json.loads(msg.data)

    def on_frame(self, msg):
        self.frame = (time.monotonic(), msg)

    # -- one job at a time -------------------------------------------------------

    def accept(self, _goal):
        if self.busy.locked():
            self.get_logger().warning("a job is running: goal refused")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def guarded(self, run, kind):
        def execute(goal_handle):
            result = kind.Result()
            if not self.busy.acquire(blocking=False):
                goal_handle.abort()
                result.message = "busy"
                return result
            try:
                run(goal_handle, result)
                goal_handle.succeed()
            except Aborted as why:
                result.message = str(why)
                self.lift(quiet=True)
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                else:
                    goal_handle.abort()
            finally:
                self.busy.release()
            return result
        return execute

    # -- moving ------------------------------------------------------------------

    def cell_xy(self, row, col):
        if not (0 <= row < self.bed["rows"] and 0 <= col < self.bed["cols"]):
            raise Aborted(f"row {row + 1}, column {col + 1} is outside the bed (soft limit)")
        if col >= self.cfg.get("wormBin", {}).get("fromCol", self.bed["cols"]):
            raise Aborted(f"row {row + 1}, column {col + 1} is the worm box, not soil")
        return (col + 0.5) * self.w["cellM"], (row + 0.5) * self.w["cellM"]

    def move(self, goal_handle, x=None, y=None, z=None):
        deadline = time.monotonic() + 10.0
        while self.pos is None:
            if time.monotonic() > deadline:
                raise Aborted("the gantry does not report its position")
            time.sleep(0.05)
        tx = self.pos[0] if x is None else min(max(x, self.w["x"][0]), self.w["x"][1])
        ty = self.pos[1] if y is None else min(max(y, self.w["y"][0]), self.w["y"][1])
        tz = self.pos[2] if z is None else min(max(z, 0.0), self.w["zMax"])
        target = Point(x=tx, y=ty, z=tz)
        deadline, sent = time.monotonic() + MOVE_TIMEOUT_S, 0.0
        while True:
            if self.stopped:
                raise Aborted("e-stop")
            if goal_handle is not None and goal_handle.is_cancel_requested:
                raise Aborted("cancelled")
            if time.monotonic() - sent > 0.5:
                self.pub_target.publish(target)
                sent = time.monotonic()
            px, py, pz = self.pos
            if abs(px - tx) < TOLERANCE_M and abs(py - ty) < TOLERANCE_M and abs(pz - tz) < TOLERANCE_M:
                return
            if time.monotonic() > deadline:
                raise Aborted("the gantry did not arrive in time")
            time.sleep(0.02)

    def lift(self, goal_handle=None, quiet=False):
        try:
            self.move(None if quiet else goal_handle, z=0.0)
        except Aborted:
            if not quiet:
                raise

    def over(self, goal_handle, row, col, dx=0.0):
        x, y = self.cell_xy(row, col)
        self.lift(goal_handle)
        self.move(goal_handle, x=x + dx, y=y)

    def event(self, **e):
        self.pub_event.publish(String(data=json.dumps(e)))

    def pause(self, goal_handle, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stopped:
                raise Aborted("e-stop")
            if goal_handle.is_cancel_requested:
                raise Aborted("cancelled")
            time.sleep(0.02)

    def pairs(self, rows, cols):
        if len(rows) != len(cols):
            raise Aborted("rows and cols differ in length")
        return list(zip(rows, cols))

    # -- the actions ---------------------------------------------------------------

    def plant(self, gh, result):
        g = gh.request
        depth = max(0.0, min(float(g.depth_mm), 150.0)) / 1000.0
        say = lambda phase: gh.publish_feedback(PlantCell.Feedback(phase=phase))   # noqa: E731
        say("moving")
        self.over(gh, g.row, g.col)
        say("lowering")
        self.move(gh, z=(self.w["tipHome"] - self.w["soilZ"]) + depth)
        say("sowing")
        self.pause(gh, 0.4 * max(1, g.seeds))
        self.event(op="sow", row=g.row, col=g.col, crop=g.crop)
        say("lifting")
        self.lift(gh)
        result.sown, result.seeds_dropped, result.message = True, max(1, g.seeds), "sown"

    def water(self, gh, result):
        g = gh.request
        cells = self.pairs(g.rows, g.cols)
        if len(g.millilitres) != len(cells):
            raise Aborted("millilitres and cells differ in length")
        for i, ((row, col), ml) in enumerate(zip(cells, g.millilitres)):
            gh.publish_feedback(WaterCells.Feedback(index=i))
            ml = max(0.0, min(float(ml), MAX_ML_PER_CELL))
            self.over(gh, row, col)
            self.move(gh, z=(self.w["tipHome"] - self.w["soilZ"]) - WATER_ABOVE_M)
            self.pause(gh, ml / FLOW_ML_PER_S)
            self.event(op="water", row=row, col=col, ml=ml)
            result.given_ml.append(ml)
        self.lift(gh)
        result.message = f"{len(cells)} cells watered"

    def probe(self, gh, result):
        g = gh.request
        for i, (row, col) in enumerate(self.pairs(g.rows, g.cols)):
            gh.publish_feedback(ProbeCells.Feedback(index=i))
            self.over(gh, row, col)
            self.move(gh, z=(self.w["tipHome"] - self.w["soilZ"]) + PROBE_INTO_M)
            self.pause(gh, 0.6)                      # one fresh /bed/state
            cell = self.bed_state.get("cells", {}).get(f"{row}_{col}", {})
            result.moisture_pct.append(float(cell.get("moisture", 35.0)))
            result.temperature_c.append(float(self.bed_state.get("soilTempC", 14.0)))
        self.lift(gh)
        result.message = f"{len(result.moisture_pct)} cells probed"

    def feed(self, gh, result):
        """One portion from the hopper into the worm box, at one of its spots across the bed."""
        g = gh.request
        box, hopper = self.cfg.get("wormBin") or {}, self.cfg.get("hopper") or {}
        if not box.get("cols"):
            raise Aborted("this bed has no worm box")
        if (self.bed_state.get("hopper") or {}).get("jammed"):
            raise Aborted("the scoop came back empty: the hopper is jammed")
        have = float((self.bed_state.get("hopper") or {}).get("grams", 0))
        grams = min(max(0.0, float(g.grams)), MAX_PORTION_G, have)
        if grams <= 0:
            raise Aborted("the hopper is empty: a person fills it first")
        spots = int(box.get("spots", 1))
        spot = int(g.spot) % spots
        width = self.w["y"][1]
        say = lambda phase: gh.publish_feedback(FeedWorms.Feedback(phase=phase))   # noqa: E731
        say("to the hopper")
        self.lift(gh)
        self.move(gh, x=hopper["x"], y=hopper["y"])
        say("scooping")
        self.move(gh, z=SCOOP_INTO_M)
        self.pause(gh, 0.5)
        self.lift(gh)
        say("to the worm box")
        self.move(gh, x=box["dropX"], y=width * (spot + 0.5) / spots)
        say("dropping")
        self.pause(gh, 0.5)
        self.event(op="feed", grams=grams, spot=spot)
        result.fed, result.grams_dropped, result.message = True, grams, f"{grams:.0f} g at spot {spot + 1} of {spots}"

    def photo(self, gh, result):
        g = gh.request
        cells = self.pairs(g.rows, g.cols)
        if not cells:                                # the whole planted bed
            cells = sorted(tuple(int(v) for v in k.split("_")) for k, c in self.bed_state.get("cells", {}).items()
                           if c.get("status") in ("CellSown", "CellGrowing"))
        for i, (row, col) in enumerate(cells):
            gh.publish_feedback(PhotoPass.Feedback(index=i))
            self.over(gh, row, col, dx=-self.w.get("camOffsetX", 0.07))   # the camera sits beside the tool
            arrived = time.monotonic()
            deadline = arrived + 5.0
            while self.frame is None or self.frame[0] < arrived + 0.25:
                if time.monotonic() > deadline:
                    raise Aborted("the tool camera sends no pictures")
                self.pause(gh, 0.05)
            result.rows.append(row)
            result.cols.append(col)
            result.images.append(self.frame[1])
        result.message = f"{len(cells)} pictures"


def main():
    rclpy.init()
    node = BedActions()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
