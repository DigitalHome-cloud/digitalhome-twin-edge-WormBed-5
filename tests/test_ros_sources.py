# SPDX-License-Identifier: GPL-3.0-or-later
"""The ROS side's Python parses: its nodes run only inside Webots and ROS 2, so a slip would show only there."""

import ast
from pathlib import Path

import pytest

ROS = Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin" / "roofbed_twin"


@pytest.mark.parametrize("path", sorted(ROS.glob("*.py")), ids=lambda p: p.name)
def test_it_parses(path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_every_event_the_edge_sends_passes_the_gateway():
    """The gateway lets only known events through to the bed; an act the edge sends must be one of them
    (a harvest refused here would never reach the twin)."""
    import ast
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    gw = ast.parse((root / "ros2/roofbed_twin/roofbed_twin/gateway.py").read_text())
    allowed = next(ast.literal_eval(n.value) for n in gw.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "EVENT_OPS")
    sent = set()
    for f in ("app.py", "scenarios.py", "disturbances.py"):
        sent |= set(re.findall(r'\{"op": "(\w+)"', (root / "permtek5_twin_edge" / f).read_text()))
    for f in (root / "permtek5_twin_edge" / "scenarios").glob("*.json"):
        sent |= set(re.findall(r'"op":\s*"(\w+)"', f.read_text()))
    sent.discard("care")             # a care task done: app._event keeps it on the edge, it never goes to the twin
    assert sent and sent <= set(allowed), f"the gateway refuses {sorted(sent - set(allowed))}"
