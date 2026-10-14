# SPDX-License-Identifier: GPL-3.0-or-later
"""The ROS side's Python parses: its nodes run only inside Webots and ROS 2, so a slip would show only there."""

import ast
from pathlib import Path

import pytest

ROS = Path(__file__).resolve().parent.parent / "ros2" / "roofbed_twin" / "roofbed_twin"


@pytest.mark.parametrize("path", sorted(ROS.glob("*.py")), ids=lambda p: p.name)
def test_it_parses(path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
