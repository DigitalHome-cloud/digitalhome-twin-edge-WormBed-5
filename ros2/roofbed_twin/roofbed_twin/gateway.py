# SPDX-License-Identifier: GPL-3.0-or-later
"""gateway: the twin for the edge app, over HTTP on this machine only.

The edge app keeps ROS out of its own Python (its packages and ROS's would
shadow each other), so this node stands on the ROS side and offers what the
page needs:

    GET  /state                       gantry position, the bed, the running job, the last jobs
    GET  /camera/tool.jpg             the latest picture (also overview.jpg)
    GET  /camera/tool.mjpg            a live stream (also overview.mjpg)
    POST /job    {"kind": "plant", "row", "col", "crop", "depthMm", "seeds"}
                 {"kind": "water", "cells": [[row, col, ml], ...]}
                 {"kind": "probe", "cells": [[row, col], ...]}
                 {"kind": "photo", "cells": [[row, col], ...]}
                 {"kind": "feed", "grams", "spot"}
                 {"kind": "care", "what": "bedding", "grams", "spot"} | {"kind": "care", "what": "moisten", "ml"}
                 {"kind": "topdress", "cells": [[row, col], ...], "grams"}
                 {"kind": "scan"} | {"kind": "stamp", "places": [[x, y], ...]}   -> {"id": ...} or 409 while one runs
    POST /event  {"op": "hopper", "grams"} | {"op": "tank", "litres"} | {"op": "box", "do", ...}   what a person did at the bed
    POST /cancel                      cancel the running job
    POST /estop  {"on": true|false}

It listens on 127.0.0.1 and refuses requests that carry an Origin (a browser
on another site) or a foreign Host: only the edge app is meant to call it.
A job is sent only when the app posts it, i.e. after a person confirmed it.
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from geometry_msgs.msg import PointStamped
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String

from roofbed_interfaces.action import CareBox, FeedWorms, PhotoPass, PlantCell, ProbeCells, ScanWeeds, StampWeeds, TopDress, WaterCells

CAMERAS = {"tool": "/gantry/tool_camera/compressed", "overview": "/gantry/overview_camera/compressed"}


class Gateway(Node):
    def __init__(self):
        super().__init__("gateway")
        self.pos = None
        self.bed = {}
        self.frames = {}
        self.new_frame = threading.Condition()
        self.job = None
        self.history = []
        self.goal = None
        self.lock = threading.Lock()
        self.out = os.environ.get("PERMTEK5_TWIN_OUT", os.getcwd())
        self.create_subscription(PointStamped, "/gantry/position", self.on_pos, 10)
        self.create_subscription(String, "/bed/state", lambda m: setattr(self, "bed", json.loads(m.data)), 10)
        for name, topic in CAMERAS.items():
            self.create_subscription(CompressedImage, topic, lambda m, name=name: self.on_frame(name, m), 2)
        self.pub_estop = self.create_publisher(Bool, "/gantry/estop", 10)
        self.clients_ = {"plant": ActionClient(self, PlantCell, "/bed/plant_cell"),
                         "water": ActionClient(self, WaterCells, "/bed/water_cells"),
                         "probe": ActionClient(self, ProbeCells, "/bed/probe_cells"),
                         "photo": ActionClient(self, PhotoPass, "/bed/photo_pass"),
                         "feed": ActionClient(self, FeedWorms, "/bed/feed_worms"),
                         "care": ActionClient(self, CareBox, "/bed/care_box"),
                         "topdress": ActionClient(self, TopDress, "/bed/top_dress"),
                         "scan": ActionClient(self, ScanWeeds, "/bed/scan_weeds"),
                         "stamp": ActionClient(self, StampWeeds, "/bed/stamp_weeds")}
        self.pub_event = self.create_publisher(String, "/bed/event", 10)

    def on_pos(self, msg):
        self.pos = {"x": round(msg.point.x, 4), "y": round(msg.point.y, 4), "z": round(msg.point.z, 4)}

    def on_frame(self, name, msg):
        with self.new_frame:
            self.frames[name] = bytes(msg.data)
            self.new_frame.notify_all()

    def state(self):
        return {"gantry": self.pos, "bed": self.bed, "job": self.job, "history": self.history[-20:],
                "cameras": sorted(self.frames)}

    # -- jobs ------------------------------------------------------------------

    def start(self, spec):
        kind = spec.get("kind")
        if kind not in self.clients_:
            raise ValueError("kind must be plant, water, probe, photo, feed, care, topdress, scan or stamp")
        goal = self.goal_for(kind, spec)
        with self.lock:
            if self.job and self.job["state"] == "running":
                return None
            self.job = {"id": f"j{int(time.time() * 1000)}", "kind": kind, "spec": spec, "state": "running",
                        "message": "", "feedback": "", "started": time.time(), "ended": None, "result": None}
        threading.Thread(target=self.run, args=(kind, goal, self.job), daemon=True).start()
        return self.job["id"]

    @staticmethod
    def goal_for(kind, s):
        cells = s.get("cells") or []
        if kind == "plant":
            return PlantCell.Goal(row=int(s["row"]), col=int(s["col"]), crop=str(s.get("crop", "")),
                                  depth_mm=float(s.get("depthMm", 5)), seeds=int(s.get("seeds", 1)))
        if kind == "feed":
            return FeedWorms.Goal(grams=float(s.get("grams", 300)), spot=int(s.get("spot", 0)))
        if kind == "scan":
            return ScanWeeds.Goal()
        if kind == "stamp":
            places = s.get("places") or []
            return StampWeeds.Goal(xs=[float(p[0]) for p in places], ys=[float(p[1]) for p in places])
        if kind == "care":
            what = str(s.get("what", ""))
            return CareBox.Goal(what=what, amount=float(s.get("ml" if what == "moisten" else "grams", 0)), spot=int(s.get("spot", 0)))
        rows, cols = [int(c[0]) for c in cells], [int(c[1]) for c in cells]
        if kind == "topdress":
            return TopDress.Goal(rows=rows, cols=cols, grams_each=float(s.get("grams", 50)))
        if kind == "water":
            return WaterCells.Goal(rows=rows, cols=cols, millilitres=[float(c[2]) for c in cells])
        if kind == "probe":
            return ProbeCells.Goal(rows=rows, cols=cols)
        return PhotoPass.Goal(rows=rows, cols=cols)

    def run(self, kind, goal, job):
        def finish(state, message, result=None):
            job.update(state=state, message=message, result=result, ended=time.time())
            self.history.append({k: job[k] for k in ("id", "kind", "spec", "state", "message", "started", "ended", "result")})
            self.goal = None

        client = self.clients_[kind]
        if not client.wait_for_server(timeout_sec=10.0):
            return finish("failed", "bed_actions is not running")

        def feedback(msg):
            fb = msg.feedback
            job["feedback"] = fb.phase if hasattr(fb, "phase") else f"cell {fb.index + 1}"

        sent = client.send_goal_async(goal, feedback_callback=feedback)
        while not sent.done():
            time.sleep(0.02)
        handle = sent.result()
        if not handle.accepted:
            return finish("failed", "the gantry is busy")
        self.goal = handle
        got = handle.get_result_async()
        while not got.done():
            time.sleep(0.05)
        r = got.result().result
        status = got.result().status          # 4 succeeded, 5 canceled, 6 aborted
        finish({4: "done", 5: "cancelled"}.get(status, "failed"), r.message, self.plain(kind, r, job["id"]))

    def plain(self, kind, r, job_id):
        if kind == "plant":
            return {"sown": r.sown, "seeds": r.seeds_dropped}
        if kind == "feed":
            return {"fed": r.fed, "grams": round(r.grams_dropped)}
        if kind == "scan":
            return {"pictures": r.pictures, "weeds": [{"x": round(x, 3), "y": round(y, 3), "sizeMm": round(mm)}
                                                      for x, y, mm in zip(r.xs, r.ys, r.sizes_mm)]}
        if kind == "stamp":
            return {"stamped": r.stamped, "skipped": r.skipped}
        if kind == "care":
            return {"done": r.done, "amount": round(r.amount_given)}
        if kind == "topdress":
            return {"givenG": [round(v) for v in r.given_g]}
        if kind == "water":
            return {"givenMl": [round(v, 1) for v in r.given_ml]}
        if kind == "probe":
            return {"moisture": [round(v, 1) for v in r.moisture_pct], "temperatureC": [round(v, 1) for v in r.temperature_c]}
        folder = os.path.join(self.out, "photos", job_id)
        os.makedirs(folder, exist_ok=True)
        files = []
        for row, col, img in zip(r.rows, r.cols, r.images):
            path = os.path.join(folder, f"{row}_{col}.jpg")
            with open(path, "wb") as fh:
                fh.write(bytes(img.data))
            files.append({"row": row, "col": col, "file": path})
        return {"photos": files}

    def cancel(self):
        if self.goal is not None:
            self.goal.cancel_goal_async()
            return True
        return False


def handler_for(gw: Gateway):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def allowed(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost") and not self.headers.get("Origin")

        def send_json(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return self.send_json(403, {"error": "local calls only"})
            if self.path == "/state":
                return self.send_json(200, gw.state())
            if self.path.startswith("/camera/"):
                name, _, ext = self.path[len("/camera/"):].partition(".")
                if name not in CAMERAS:
                    return self.send_json(404, {"error": "no such camera"})
                if ext == "jpg":
                    frame = gw.frames.get(name)
                    if frame is None:
                        return self.send_json(503, {"error": "no picture yet"})
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return self.wfile.write(frame)
                if ext == "mjpg":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        while True:
                            with gw.new_frame:
                                gw.new_frame.wait(timeout=5.0)
                                frame = gw.frames.get(name)
                            if frame is None:
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        return
            return self.send_json(404, {"error": "not found"})

        def do_POST(self):
            if not self.allowed():
                return self.send_json(403, {"error": "local calls only"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n < 200_000 else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return self.send_json(400, {"error": "expected a JSON object"})
            if self.path == "/job":
                try:
                    job_id = gw.start(data)
                except (ValueError, KeyError, TypeError, IndexError) as e:
                    return self.send_json(400, {"error": str(e)})
                return self.send_json(200, {"id": job_id}) if job_id else self.send_json(409, {"error": "a job is running"})
            if self.path == "/event":
                # what a person did at the bed: filled the hopper, refilled the tank
                if data.get("op") not in ("hopper", "tank", "dry", "disturb", "calm", "time", "chaos", "box", "weeds"):
                    return self.send_json(400, {"error": "op must be hopper, tank, dry, disturb, calm, time, chaos, box or weeds"})
                keys = ("op", "grams", "litres", "percent", "id", "title", "effect", "minutes", "factor", "rate", "pool",
                        "do", "on", "ml", "open", "mix", "to", "browns", "side", "count", "large", "pull")
                gw.pub_event.publish(String(data=json.dumps({k: data[k] for k in keys if k in data})))
                return self.send_json(200, {"ok": True})
            if self.path == "/cancel":
                return self.send_json(200, {"cancelled": gw.cancel()})
            if self.path == "/estop":
                gw.pub_estop.publish(Bool(data=bool(data.get("on", True))))
                return self.send_json(200, {"estop": bool(data.get("on", True))})
            return self.send_json(404, {"error": "not found"})

    return Handler


def main():
    rclpy.init()
    gw = Gateway()
    port = int(os.environ.get("PERMTEK5_TWIN_GATEWAY_PORT", "8773"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(gw))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    gw.get_logger().info(f"gateway on http://127.0.0.1:{port}")
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(gw)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
