# SPDX-License-Identifier: GPL-3.0-or-later
"""The gantry's robot window in Webots: what a person does at the bed, pressed in the 3D view. Plain Python: no ROS.

Webots opens a robot window beside the 3D view (double-click the gantry, or right-click > Show Robot Window).
Ours, `wormbed5` (robot_windows/wormbed5/), has a button for each thing a PERSON does at the bed: the lid, the
wrapping, the sump's tap, dry bedding, water, the hopper, the tank, the barrel's tap, and the e-stop. It sends
{"act": key} and the driver turns that into the very event the edge page sends (`event`), so the keeper, the
care calendar and the Operator screen see an act made in Webots like one made on the page. The gantry is not
jogged from here: its jobs stay proposals a person confirms on the edge's page.

    act = parse('{"act": "lid"}')   # "lid", or None for anything else
    ev = event(act, now)            # {"op": "box", "do": "lid", "open": True}

`now` is the driver's state the acts toggle against: {"lidOpen", "insulated", "tankCap", "stopped"}.
"""
import json

ACTS = ("lid", "wrap", "tap", "bedding", "moisten", "hopper", "tank", "barrel", "estop")
USUAL_MIX = {"vegetable": 600, "fruit": 300, "coffee": 100}       # the page's "Hopper filled (1 kg, usual mix)"


def parse(message):
    """The act a window message asks for, or None ("init robot window" and anything else)."""
    try:
        m = json.loads(message)
    except (TypeError, ValueError):
        return None
    act = m.get("act") if isinstance(m, dict) else None
    return act if act in ACTS else None


def event(act, now):
    """The /bed/event the act stands for. The e-stop is the driver's own switch: {"op": "estop", "on"}."""
    if act == "lid":
        return {"op": "box", "do": "lid", "open": not now.get("lidOpen")}
    if act == "wrap":
        return {"op": "box", "do": "insulate", "on": not now.get("insulated")}
    if act == "tap":
        return {"op": "box", "do": "tap"}
    if act == "bedding":
        return {"op": "box", "do": "bedding", "grams": 500}
    if act == "moisten":
        return {"op": "box", "do": "moisten", "ml": 500}
    if act == "hopper":
        return {"op": "hopper", "mix": dict(USUAL_MIX)}
    if act == "tank":
        return {"op": "tank", "litres": now.get("tankCap", 0)}
    if act == "barrel":
        return {"op": "valve", "from": "RainBarrel", "to": "TankPipes", "open": True}
    if act == "estop":
        return {"op": "estop", "on": not now.get("stopped")}
    return None
