# SPDX-License-Identifier: GPL-3.0-or-later
"""A guild's water beyond the bed's tank: rain caught on a roof, a barrel, a garden tank, and the valves between
them and the bed's tank pipes and the worm box's sump (PermTek-5 ADR 0025). Plain Python: no ROS, no Webots.

The stores are known by their kind; the tank pipes (the driver's tank) and the sump (the worm box's) stay where
they are, and this module moves water into and out of them through two small adapters:

    w = WaterSystem(cfg["water"])                          # from bed.json (habitat.py: water_for)
    w.rain(mm)                                             # each look: the roof's rain into the barrel
    w.open("valve id", True)                               # a person opened a tap, or the edge an edge valve
    w.open("mist id", True, litres=0.5)                    # a dose: it closes once that much has run
    tank_l = w.step(minutes, tank_l, tank_cap, box)        # open valves move water, as far as there is room
    w.state()                                              # for /bed/state: stores, valves, rain lost

A valve lets FLOW_L_PER_MIN through while it is open and closes itself when its source is empty or its target
full (a person or the edge would close it then), or when the dose it was opened for has run. The worm box's bin
(WormBoxBin) is a place water goes to, not a store: the MIST LINE under its lid wets the bedding at
MIST_L_PER_MIN and stops at MIST_STOP_PCT moisture, whatever is left of its dose. Worm liquid that reaches the garden tank is counted apart, so
the page can say what share of it is worm liquid. Every number is a rule of thumb.
"""

FLOW_L_PER_MIN = 5.0          # a gravity tap with a hose: about a bucket every two minutes
MIST_L_PER_MIN = 0.5          # a drip or mist hose under the worm box's lid
MIST_STOP_PCT = 85.0          # the bedding's moisture at which the mist line stops: wet enough
L_PER_PCT = 0.1               # litres that raise the simulated box's moisture one point (wormbox.py: moisten)


class WaterSystem:
    def __init__(self, cfg):
        cfg = cfg or {}
        self.catchments = list(cfg.get("catchments") or [])
        self.stores = {s["id"]: dict(s) for s in cfg.get("stores") or []}
        self.valves = {v["id"]: {**v, "open": False} for v in cfg.get("valves") or []}
        self.levels = {}                              # litres in the stores this module keeps: barrel, garden tank
        self.worm_l = {}                              # of which worm liquid
        for sid, s in self.stores.items():
            if s["kind"] in ("RainBarrel", "GardenTank"):
                self.levels[sid] = float(s.get("capacityL") or 0) * (0.5 if s["kind"] == "RainBarrel" else 0.2)
                self.worm_l[sid] = 0.0
        self.lost_l = 0.0                             # rain the barrel could not take
        self.overflowing = set()
        self.to_bed_l = 0.0                           # worm liquid given to the bed's tank pipes, and to the garden, in all
        self.to_garden_l = 0.0
        self.to_bin_l = 0.0                           # clean water the mist line gave the worm box, in all

    # -- what is where ----------------------------------------------------------------

    def of_kind(self, kind):
        return next((sid for sid, s in self.stores.items() if s["kind"] == kind), None)

    def cap(self, sid):
        return float(self.stores[sid].get("capacityL") or 0)

    def litres(self, sid):
        return self.levels.get(sid, 0.0)

    def valve_between(self, from_kind, to_kind):
        """The valve from a store of one kind to a store of another, or None."""
        for vid, v in self.valves.items():
            if self.stores.get(v["from"], {}).get("kind") == from_kind and self.stores.get(v["to"], {}).get("kind") == to_kind:
                return vid
        return None

    # -- what happens -------------------------------------------------------------------

    def rain(self, mm):
        """The roof's rain into the store its gutter fills; what does not fit overflows and is lost."""
        self.overflowing = set()
        for c in self.catchments:
            sid = c.get("storeId")
            if sid not in self.levels or mm <= 0:
                continue
            self.levels[sid] += mm * float(c.get("m2") or 0) * float(c.get("runoff") if c.get("runoff") is not None else 0.8)
            over = self.levels[sid] - self.cap(sid)
            if over > 0:
                self.lost_l += over
                self.levels[sid] = self.cap(sid)
                self.overflowing.add(sid)

    def open(self, vid, on=True, litres=None):
        if vid in self.valves:
            self.valves[vid]["open"] = bool(on)
            self.valves[vid]["dose"] = float(litres) if on and litres else None
            return True
        return False

    def set_level(self, sid, litres):
        if sid in self.levels:
            self.levels[sid] = max(0.0, min(float(litres), self.cap(sid)))
            self.worm_l[sid] = min(self.worm_l[sid], self.levels[sid])

    def step(self, minutes, tank_l, tank_cap, box=None):
        """Let the open valves run for `minutes`. Returns the tank pipes' new litres."""
        for vid, v in self.valves.items():
            if not v["open"]:
                continue
            src, dst = self.stores.get(v["from"]), self.stores.get(v["to"])
            if not src or not dst:
                v["open"] = False
                continue
            have = (box.sump_l if box is not None else 0.0) if src["kind"] == "WormBoxSump" else \
                tank_l if src["kind"] == "TankPipes" else self.litres(v["from"])
            if dst["kind"] == "WormBoxBin":           # the mist line: as long as the dose lasts and the bedding is not wet
                room = 0.0 if box is None else max(0.0, (MIST_STOP_PCT - box.moisture) * L_PER_PCT)
            else:
                room = tank_cap - tank_l if dst["kind"] == "TankPipes" else \
                    ((box.sump_cap_l - box.sump_l) if box is not None else 0.0) if dst["kind"] == "WormBoxSump" else self.cap(v["to"]) - self.litres(v["to"])
            if v.get("dose") is not None:
                room = min(room, v["dose"])
            rate = MIST_L_PER_MIN if dst["kind"] == "WormBoxBin" else FLOW_L_PER_MIN
            move = max(0.0, min(rate * minutes, have, room))
            worm = src["kind"] == "WormBoxSump" or (src["kind"] in self.worm_l and self.litres(v["from"]) > 0
                                                    and self.worm_l[v["from"]] / self.litres(v["from"]) > 0.5)
            # out of the source
            if src["kind"] == "WormBoxSump" and box is not None:
                box.sump_l -= move
            elif src["kind"] == "TankPipes":
                tank_l -= move
            else:
                share = self.worm_l[v["from"]] / self.litres(v["from"]) if self.litres(v["from"]) > 0 else 0.0
                self.levels[v["from"]] -= move
                self.worm_l[v["from"]] = max(0.0, self.worm_l[v["from"]] - move * share)
            # into the target
            if dst["kind"] == "TankPipes":
                tank_l += move
                if worm:
                    self.to_bed_l += move
            elif dst["kind"] == "WormBoxSump" and box is not None:
                box.sump_l += move
            elif dst["kind"] == "WormBoxBin":
                if box is not None:
                    box.moisten(move * 1000.0)
                self.to_bin_l += move
            else:
                self.levels[v["to"]] += move
                if worm:
                    self.worm_l[v["to"]] += move
                    if dst["kind"] == "GardenTank":
                        self.to_garden_l += move
            if v.get("dose") is not None:
                v["dose"] -= move
            if move <= 1e-6 or have - move <= 0.05 or room - move <= 0.005:
                v["open"], v["dose"] = False, None    # empty, full or the dose given: the valve is closed again
        return tank_l

    def state(self):
        stores = []
        for sid, s in self.stores.items():
            row = {"id": sid, "name": s.get("name", ""), "kind": s["kind"], "capacity": round(self.cap(sid), 1), "area": s.get("area")}
            if sid in self.levels:
                row["litres"] = round(self.levels[sid], 2)
                row["wormL"] = round(self.worm_l[sid], 2)
                row["overflowing"] = sid in self.overflowing
            stores.append(row)
        return {"stores": stores,
                "valves": [{"id": vid, "name": v.get("name", ""), "kind": v["kind"], "from": v["from"], "to": v["to"], "open": v["open"]}
                           for vid, v in self.valves.items()],
                "lostL": round(self.lost_l, 1), "wormToBedL": round(self.to_bed_l, 2), "wormToGardenL": round(self.to_garden_l, 2),
                "mistedL": round(self.to_bin_l, 2)}
