# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 D-LAB-5
"""
instance.py — which Perma5Guild this running edge is for.

One edge runs one guild (PermTek-5 ADR 0020). The same software is started
once per guild:

    ./run.sh                                   # the habitat's first guild, on 8772
    ./run.sh --port 8782 --guild "Balcony guild"

Everything that would collide between two instances on one computer follows
from the app's port, so there is nothing to configure twice:

    gateway port   app port + 1
    Webots port    1234 + (app port - 8772)
    ROS domain     10 + (app port - 8772) % 90     the two simulations cannot hear each other
    workspace      <your folder>/guilds/<guild or "main">/

Standard library only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

BASE_PORT = 8772
_cfg: Dict[str, Any] = {"guild": None, "port": BASE_PORT}


def configure(guild: Optional[str] = None, port: Optional[int] = None) -> None:
    _cfg["guild"] = (guild or "").strip() or None
    if port:
        _cfg["port"] = int(port)


def guild() -> Optional[str]:
    """The guild asked for by name, or None for the habitat's first."""
    return _cfg["guild"]


def name() -> str:
    """This instance's folder name."""
    return re.sub(r"[^a-z0-9]+", "-", (_cfg["guild"] or "main").lower()).strip("-")[:40] or "main"


def app_port() -> int:
    return int(_cfg["port"])


def gateway_port() -> int:
    return app_port() + 1


def webots_port() -> int:
    return 1234 + (app_port() - BASE_PORT)


def ros_domain() -> int:
    return 10 + (app_port() - BASE_PORT) % 90


def workspace(user_folder: Path) -> Path:
    return Path(user_folder) / "guilds" / name()


def describe() -> Dict[str, Any]:
    return {"guild": guild(), "name": name(), "appPort": app_port(), "gatewayPort": gateway_port(),
            "webotsPort": webots_port(), "rosDomain": ros_domain()}
