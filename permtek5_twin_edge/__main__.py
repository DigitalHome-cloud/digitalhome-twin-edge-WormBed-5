# SPDX-License-Identifier: GPL-3.0-or-later
"""python -m permtek5_twin_edge — start the edge on http://127.0.0.1:8772.

Run this way (an installed package rather than a checkout), its files live
in the platform's own place for application data unless --dir says otherwise:

    Linux    ~/.local/share/permtek5-twin-edge/mydocs   ($XDG_DATA_HOME respected)
    macOS    ~/Library/Application Support/permtek5-twin-edge/mydocs
    Windows  %LOCALAPPDATA%\\permtek5-twin-edge\\mydocs

Written once by the edge layer; the tool owns it afterwards.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_home() -> Path:
    if os.environ.get("PERMTEK5_TWIN_EDGE_EDGE_HOME"):          # the installers set it
        return Path(os.environ["PERMTEK5_TWIN_EDGE_EDGE_HOME"]).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "permtek5-twin-edge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "permtek5-twin-edge"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "permtek5-twin-edge"


def main() -> int:
    args = sys.argv[1:]
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    os.environ.setdefault("PERMTEK5_TWIN_EDGE_EDGE_DATA", str(home))
    if "--dir" not in args and not any(a.startswith("--dir=") for a in args):
        args = ["--dir", str(home / "mydocs"), *args]
    from .app import main as web
    return web(args)


if __name__ == "__main__":
    raise SystemExit(main())
